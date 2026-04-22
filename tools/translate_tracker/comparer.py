"""
Markdown block weak-match alignment and diff computation.

This module provides:
* ``compute_weak_match_score`` – structural similarity between two block
  signatures, designed to work across languages.
* ``align_blocks`` – global sequence alignment (Needleman–Wunsch) based on
  the weak-match score.
* ``compare_file`` – high-level diffing that turns aligned pairs into
  :class:`BlockDiff` objects together with translation-status heuristics.

Design decisions
----------------
* **Weak matching** – two blocks in different languages rarely share literal
  text, so the score emphasises *structure* (heading level, table column
  count, list type, …) over *content*.
* **Global alignment** – Needleman–Wunsch is used rather than local
  alignment so that the overall document structure is preserved.
* **Gap penalty** – a moderate linear gap penalty (0.3) means that blocks
  with a score below that threshold are preferentially treated as
  insertions/deletions rather than poor matches.
* **Commit-time comparison** – when the content hash differs we look at the
  most-recent commit timestamps of the *entire* block.  This is a pragmatic
  proxy for "which side was edited last".
"""

from typing import List, Tuple, Optional, Dict

from tools.translate_tracker.core_types import Block, BlockDiff, FileReport, AnalysisReport, CommitInfo


# ---------------------------------------------------------------------------
# Signature similarity
# ---------------------------------------------------------------------------

def compute_weak_match_score(src_sig: Tuple, tgt_sig: Tuple) -> float:
    """
    Compute a similarity score between two block signatures.

    Returns a float in ``[0.0, 1.0]`` where ``1.0`` means perfect match.

    Rules
    -----
    * If ``block_type`` differs → ``0.0``.
    * **HEADING** – type + level match gives ``0.5``.  If both have non-empty
      normalised text a small bonus of ``0.1`` is added (we do *not* penalise
      language differences).  The remaining ``0.4`` is available for future
      extensions but kept conservative here.
    * **TABLE** – type + same number of columns gives ``0.6``; header count
      match gives up to ``0.4``.
    * **CODE_BLOCK** – type + language match gives ``1.0``.
    * **ADMONITION** – type + admonition type match gives ``0.7``; title
      presence match gives ``0.3``.
    * **LIST** – type + list_type match gives ``0.6``; item-count ratio
      gives up to ``0.4``.
    * **PARAGRAPH** – type match gives ``0.8``; first-char overlap gives
      up to ``0.2`` (very weak, mainly to break ties).
    * **HORIZONTAL_RULE** – always ``1.0``.
    * **OTHER** – type match gives ``0.5``; first-line similarity gives
      ``0.5``.
    """
    if not src_sig or not tgt_sig:
        return 0.0

    if src_sig[0] != tgt_sig[0]:
        return 0.0

    block_type = src_sig[0]

    if block_type == "HEADING":
        # signature: ("HEADING", level, normalized_text)
        score = 0.0
        if src_sig[1] == tgt_sig[1]:
            score += 0.5
        # Text bonus: tiny reward when both sides have non-empty text.
        src_text = src_sig[2] if len(src_sig) > 2 else ""
        tgt_text = tgt_sig[2] if len(tgt_sig) > 2 else ""
        if src_text and tgt_text:
            score += 0.1
        return min(score, 1.0)

    if block_type == "TABLE":
        # signature: ("TABLE", normalized_cols_tuple, num_rows)
        src_cols = src_sig[1] if len(src_sig) > 1 else ()
        tgt_cols = tgt_sig[1] if len(tgt_sig) > 1 else ()
        score = 0.0
        if len(src_cols) == len(tgt_cols) and len(src_cols) > 0:
            score += 0.6
            # Header overlap – count identical normalised headers.
            matches = sum(1 for sc, tc in zip(src_cols, tgt_cols) if sc == tc)
            ratio = matches / len(src_cols)
            score += 0.4 * ratio
        return min(score, 1.0)

    if block_type == "CODE_BLOCK":
        # signature: ("CODE_BLOCK", language)
        src_lang = src_sig[1] if len(src_sig) > 1 else ""
        tgt_lang = tgt_sig[1] if len(tgt_sig) > 1 else ""
        return 1.0 if src_lang == tgt_lang else 0.5

    if block_type == "ADMONITION":
        # signature: ("ADMONITION", admon_type, normalized_title)
        src_type = src_sig[1] if len(src_sig) > 1 else ""
        tgt_type = tgt_sig[1] if len(tgt_sig) > 1 else ""
        score = 0.0
        if src_type == tgt_type:
            score += 0.7
        src_title = src_sig[2] if len(src_sig) > 2 else ""
        tgt_title = tgt_sig[2] if len(tgt_sig) > 2 else ""
        if bool(src_title) == bool(tgt_title):
            score += 0.3
        return min(score, 1.0)

    if block_type == "LIST":
        # signature: ("LIST", list_type, normalized_first_item, item_count)
        src_ltype = src_sig[1] if len(src_sig) > 1 else ""
        tgt_ltype = tgt_sig[1] if len(tgt_sig) > 1 else ""
        score = 0.0
        if src_ltype == tgt_ltype:
            score += 0.6
        src_cnt = src_sig[3] if len(src_sig) > 3 else 0
        tgt_cnt = tgt_sig[3] if len(tgt_sig) > 3 else 0
        if src_cnt > 0 and tgt_cnt > 0:
            ratio = min(src_cnt, tgt_cnt) / max(src_cnt, tgt_cnt)
            score += 0.4 * ratio
        return min(score, 1.0)

    if block_type == "PARAGRAPH":
        # signature: ("PARAGRAPH", first_text_prefix)
        score = 0.8
        src_prefix = src_sig[1] if len(src_sig) > 1 else ""
        tgt_prefix = tgt_sig[1] if len(tgt_sig) > 1 else ""
        if src_prefix and tgt_prefix:
            # Very weak char-level overlap to break ties.
            overlap = 0
            for a, b in zip(src_prefix, tgt_prefix):
                if a == b:
                    overlap += 1
                else:
                    break
            score += 0.2 * (overlap / max(len(src_prefix), len(tgt_prefix), 1))
        return min(score, 1.0)

    if block_type == "HORIZONTAL_RULE":
        return 1.0

    if block_type == "OTHER":
        # signature: ("OTHER", normalized_first_line)
        score = 0.5
        src_line = src_sig[1] if len(src_sig) > 1 else ""
        tgt_line = tgt_sig[1] if len(tgt_sig) > 1 else ""
        if src_line and tgt_line:
            # Simple token overlap ratio.
            src_tokens = set(src_line.split())
            tgt_tokens = set(tgt_line.split())
            if src_tokens and tgt_tokens:
                inter = len(src_tokens & tgt_tokens)
                union = len(src_tokens | tgt_tokens)
                score += 0.5 * (inter / union)
        return min(score, 1.0)

    # Unknown block type – fall back to exact signature match.
    return 1.0 if src_sig == tgt_sig else 0.0


# ---------------------------------------------------------------------------
# Sequence alignment
# ---------------------------------------------------------------------------

_GAP_PENALTY = 0.3  # Blocks with score < this are treated as gaps.


def align_blocks(
    src_blocks: List[Block], tgt_blocks: List[Block]
) -> List[Tuple[Optional[Block], Optional[Block]]]:
    """
    Align source and target blocks using a Needleman–Wunsch global alignment.

    The similarity matrix is built from :func:`compute_weak_match_score`.
    A linear gap penalty of ``0.3`` is used so that weak matches
    (score < penalty) are preferentially emitted as insertions / deletions.

    Returns
    -------
    List of ``(src_block_or_None, tgt_block_or_None)`` aligned pairs.
    A ``None`` on one side indicates an insertion or deletion.
    """
    m = len(src_blocks)
    n = len(tgt_blocks)

    # Pre-compute score matrix.
    scores = [[0.0] * n for _ in range(m)]
    for i in range(m):
        src = src_blocks[i]
        for j in range(n):
            tgt = tgt_blocks[j]
            scores[i][j] = compute_weak_match_score(src.signature, tgt.signature)

    # DP tables.
    # dp[i][j] = best score for src[0:i] and tgt[0:j]
    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    # Traceback: 0 = match, 1 = delete (gap in tgt), 2 = insert (gap in src)
    trace = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        dp[i][0] = -_GAP_PENALTY * i
        trace[i][0] = 1
    for j in range(1, n + 1):
        dp[0][j] = -_GAP_PENALTY * j
        trace[0][j] = 2

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            match_score = dp[i - 1][j - 1] + scores[i - 1][j - 1]
            delete_score = dp[i - 1][j] - _GAP_PENALTY
            insert_score = dp[i][j - 1] - _GAP_PENALTY

            best = match_score
            best_dir = 0
            if delete_score > best:
                best = delete_score
                best_dir = 1
            if insert_score > best:
                best = insert_score
                best_dir = 2

            dp[i][j] = best
            trace[i][j] = best_dir

    # Traceback.
    aligned: List[Tuple[Optional[Block], Optional[Block]]] = []
    i, j = m, n
    while i > 0 or j > 0:
        direction = trace[i][j]
        if direction == 0 and i > 0 and j > 0:
            aligned.append((src_blocks[i - 1], tgt_blocks[j - 1]))
            i -= 1
            j -= 1
        elif direction == 1 and i > 0:
            aligned.append((src_blocks[i - 1], None))
            i -= 1
        else:
            aligned.append((None, tgt_blocks[j - 1]))
            j -= 1

    aligned.reverse()
    return aligned


# ---------------------------------------------------------------------------
# File-level comparison
# ---------------------------------------------------------------------------

def _block_commit(
    block: Optional[Block],
    line_commits: Dict[int, CommitInfo],
) -> Optional[CommitInfo]:
    """
    Return the most recent commit that touches *block*.

    We look at every line in the block and pick the commit with the largest
    timestamp.  If no commit info is available, ``None`` is returned.
    """
    if block is None:
        return None
    latest: Optional[CommitInfo] = None
    for line_no in range(block.start_line, block.end_line + 1):
        commit = line_commits.get(line_no)
        if commit is not None:
            if latest is None or commit.timestamp > latest.timestamp:
                latest = commit
    return latest


def _needs_stop_tag_warning(
    block: Optional[Block],
    line_commits: Dict[int, CommitInfo],
    stop_tag_commit: Optional[CommitInfo],
) -> bool:
    """
    Return ``True`` if the block's blame history cannot be traced before the
    stop tag.

    Heuristic: if *every* line in the block was modified *after* the stop-tag
    timestamp, we cannot confirm that the translation was done before the tag.
    """
    if block is None or stop_tag_commit is None:
        return False
    for line_no in range(block.start_line, block.end_line + 1):
        commit = line_commits.get(line_no)
        if commit is None:
            # No blame info – treat as uncertain.
            return True
        if commit.timestamp <= stop_tag_commit.timestamp:
            # At least one line is old enough.
            return False
    # All lines are newer than the stop tag.
    return True


def compare_file(
    src_blocks: List[Block],
    tgt_blocks: List[Block],
    src_line_commits: Dict[int, CommitInfo],
    tgt_line_commits: Dict[int, CommitInfo],
    stop_tag_commit: Optional[CommitInfo] = None,
) -> List[BlockDiff]:
    """
    Compare source and target blocks and determine translation status.

    The algorithm:

    1. Align blocks with :func:`align_blocks`.
    2. For each aligned pair:
       * src ``None``, tgt present → ``removed`` (target has extra content).
       * src present, tgt ``None`` → ``added`` (target missing content).
       * Both present:
         * If ``content_hash`` identical → ``unchanged``.
         * If hashes differ → compare commit timestamps.
           * src_commit.time > tgt_commit.time → ``modified`` (outdated).
           * src_commit.time <= tgt_commit.time → ``unchanged``.
    3. If *stop_tag_commit* is given and a block's entire blame history is
       newer than the tag, add a warning note because we cannot confirm
       whether the translation happened before or after the tag.

    Returns
    -------
    List of :class:`BlockDiff` objects, one per aligned pair.
    """
    aligned = align_blocks(src_blocks, tgt_blocks)
    diffs: List[BlockDiff] = []

    for src, tgt in aligned:
        notes: List[str] = []

        if src is None and tgt is not None:
            status = "removed"
        elif src is not None and tgt is None:
            status = "added"
        else:
            # Both present.
            assert src is not None and tgt is not None
            if src.content_hash == tgt.content_hash:
                status = "unchanged"
            else:
                src_commit = _block_commit(src, src_line_commits)
                tgt_commit = _block_commit(tgt, tgt_line_commits)
                if src_commit is not None and tgt_commit is not None:
                    if src_commit.timestamp > tgt_commit.timestamp:
                        status = "modified"
                    else:
                        status = "unchanged"
                else:
                    # No commit info – conservatively mark as modified.
                    status = "modified"

        # Stop-tag warnings.
        if status in ("modified", "added", "removed"):
            if _needs_stop_tag_warning(src, src_line_commits, stop_tag_commit):
                notes.append(
                    f"Git history for source block {src.line_range_str()} "
                    f"cannot be traced before stop-tag."
                )
            if _needs_stop_tag_warning(tgt, tgt_line_commits, stop_tag_commit):
                notes.append(
                    f"Git history for target block {tgt.line_range_str()} "
                    f"cannot be traced before stop-tag."
                )

        diffs.append(
            BlockDiff(
                src_block=src,
                tgt_block=tgt,
                status=status,
                src_commit=_block_commit(src, src_line_commits),
                tgt_commit=_block_commit(tgt, tgt_line_commits),
                notes=notes,
            )
        )

    return diffs
