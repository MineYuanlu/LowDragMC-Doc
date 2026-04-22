"""
Markdown semantic block parser.

Parses Markdown text into a list of :class:`Block` objects for translation
tracking.  Each block captures a single semantic unit (heading, paragraph,
table, code block, admonition, list, horizontal rule, or other) together
with language-agnostic signature and content hash.

Design decisions
----------------
* **Line-oriented state machine** – the parser scans the input line-by-line.
  This is simple, avoids full AST dependencies, and is robust against loosely
  formatted Markdown.
* **Empty lines are ignored** – they act only as block separators and never
  produce a block on their own.
* **Admonitions are captured as a single block** – indented content inside an
  ``!!!`` block is consumed verbatim.  Nested structures inside admonitions are
  *not* recursively parsed in this version.
* **Lists are captured as a single block** – nested items (identified by larger
  indentation) are folded into the parent ``LIST`` block.
* **Signatures are structural** – they contain only block-type metadata and
  normalised tokens so that two translations of the same content receive
  matching signatures.
* **Content hash is exact** – based on fully normalised text (lower-cased,
  stripped, unified newlines) so that even whitespace-only changes are detected.

Known limitations
-----------------
* HTML blocks are treated as ``OTHER`` (or split across paragraphs if they
  contain blank lines).
* Front matter (``---`` at the very top of the file) is currently treated as
  ``HORIZONTAL_RULE`` or ``OTHER`` depending on context.  A dedicated
  ``FRONT_MATTER`` block type could be added later if needed.
* Admonition content is not recursively parsed; any tables, code blocks, etc.
  inside an admonition are hidden inside the ``ADMONITION`` block.
"""

import hashlib
import re
from typing import List, Tuple, Any, Optional

from tools.translate_tracker.core_types import Block


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_HR = re.compile(r"^\s*([-*_]\s*){3,}\s*$")
_RE_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_RE_TABLE_SEP = re.compile(r"^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$")
_RE_CODE_FENCE = re.compile(r"^(```|~~~)\s*(\S*).*$")
_RE_ADMONITION = re.compile(r"^!!!\s+(\S+)(?:\s+\"([^\"]*)\")?\s*$")
_RE_LIST_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+(.*)$")
_RE_EMPTY = re.compile(r"^\s*$")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """
    Normalise text for weak matching.

    The text is lower-cased, punctuation characters are removed, and
    consecutive whitespace is collapsed to a single space.  Leading and
    trailing whitespace is stripped.

    Parameters
    ----------
    text:
        Raw text string.

    Returns
    -------
    Normalised text suitable for inclusion in a block signature.
    """
    # Remove punctuation except whitespace so that "Introduction!"
    # becomes "introduction".
    no_punct = re.sub(r"[^\w\s]", "", text.lower())
    # Collapse whitespace.
    return re.sub(r"\s+", " ", no_punct).strip()


def compute_content_hash(lines: List[str]) -> str:
    """
    Compute a SHA-256 hex digest of the normalised block content.

    Normalisation rules:
    1. Strip leading/trailing whitespace from each line.
    2. Join lines with ``\n``.
    3. Lower-case the entire string.

    Parameters
    ----------
    lines:
        Raw lines belonging to the block.

    Returns
    -------
    Hex-encoded SHA-256 hash string.
    """
    normalized = "\n".join(line.strip() for line in lines).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_markdown(content: str) -> List[Block]:
    """
    Parse markdown content into a list of semantic blocks.

    The parser scans the input line-by-line and classifies contiguous
    non-empty lines into block types.  Empty lines separate blocks but are
    never emitted as blocks themselves.

    Parameters
    ----------
    content:
        Full markdown text.  May contain ``\n``, ``\r\n``, or ``\r``.

    Returns
    -------
    List of :class:`Block` objects ordered by appearance in the source.
    """
    # Normalise line endings to \n.
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = content.split("\n")

    blocks: List[Block] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # Skip empty lines.
        if _RE_EMPTY.match(line):
            i += 1
            continue

        block, i = _parse_block(lines, i)
        if block is not None:
            blocks.append(block)

    return blocks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_block(lines: List[str], start: int) -> Tuple[Optional[Block], int]:
    """
    Parse a single block starting at *start*.

    Returns the block and the index of the next line to process.
    If no block can be formed (e.g. only empty lines remain), returns
    ``(None, start)``.
    """
    line = lines[start]

    # Heading
    m = _RE_HEADING.match(line)
    if m:
        return _make_heading(lines, start, m)

    # Horizontal rule
    if _RE_HR.match(line):
        return _make_hr(lines, start)

    # Code block (fenced)
    m = _RE_CODE_FENCE.match(line)
    if m:
        return _make_code_block(lines, start, m)

    # Table
    if _RE_TABLE_ROW.match(line):
        return _make_table(lines, start)

    # Admonition
    m = _RE_ADMONITION.match(line)
    if m:
        return _make_admonition(lines, start, m)

    # List
    m = _RE_LIST_ITEM.match(line)
    if m:
        return _make_list(lines, start, m)

    # Fallback: paragraph or OTHER.
    return _make_paragraph_or_other(lines, start)


def _make_heading(
    lines: List[str], start: int, match: "re.Match[str]"
) -> Tuple[Block, int]:
    level = len(match.group(1))
    text = match.group(2).strip()
    sig = ("HEADING", level, normalize_text(text))
    blk = Block(
        block_type="HEADING",
        start_line=start + 1,
        end_line=start + 1,
        raw_lines=[lines[start]],
        signature=sig,
        content_hash=compute_content_hash([lines[start]]),
    )
    return blk, start + 1


def _make_hr(lines: List[str], start: int) -> Tuple[Block, int]:
    blk = Block(
        block_type="HORIZONTAL_RULE",
        start_line=start + 1,
        end_line=start + 1,
        raw_lines=[lines[start]],
        signature=("HORIZONTAL_RULE",),
        content_hash=compute_content_hash([lines[start]]),
    )
    return blk, start + 1


def _make_code_block(
    lines: List[str], start: int, match: "re.Match[str]"
) -> Tuple[Block, int]:
    fence = match.group(1)
    lang = match.group(2).strip().lower() if match.group(2) else ""
    i = start + 1
    n = len(lines)
    while i < n:
        if lines[i].strip().startswith(fence):
            i += 1
            break
        i += 1
    raw = lines[start:i]
    blk = Block(
        block_type="CODE_BLOCK",
        start_line=start + 1,
        end_line=i,
        raw_lines=raw,
        signature=("CODE_BLOCK", lang),
        content_hash=compute_content_hash(raw),
    )
    return blk, i


def _make_table(lines: List[str], start: int) -> Tuple[Block, int]:
    """
    Consume a table block starting at *start*.

    The first non-empty line is the header, the second may be a separator,
    and subsequent lines are data rows until a non-table line or empty line
    is encountered.
    """
    i = start
    n = len(lines)
    while i < n and not _RE_EMPTY.match(lines[i]) and _RE_TABLE_ROW.match(lines[i]):
        i += 1
    raw = lines[start:i]

    # Extract header columns for signature.
    header_line = raw[0] if raw else ""
    cols = _split_table_columns(header_line)
    normalized_cols = tuple(normalize_text(c) for c in cols)
    num_rows = max(0, len(raw) - 2)  # minus header and optional separator
    # If there is no separator, we treat every line after header as data.
    if len(raw) > 1 and _RE_TABLE_SEP.match(raw[1]):
        pass  # num_rows already computed
    else:
        num_rows = max(0, len(raw) - 1)

    sig = ("TABLE", normalized_cols, num_rows)
    blk = Block(
        block_type="TABLE",
        start_line=start + 1,
        end_line=i,
        raw_lines=raw,
        signature=sig,
        content_hash=compute_content_hash(raw),
    )
    return blk, i


def _split_table_columns(line: str) -> List[str]:
    """
    Split a table row into individual column strings.

    The leading and trailing ``|`` are stripped, and each cell is stripped
    of surrounding whitespace.
    """
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _make_admonition(
    lines: List[str], start: int, match: "re.Match[str]"
) -> Tuple[Block, int]:
    admon_type = match.group(1).lower()
    title = match.group(2) if match.group(2) else ""
    i = start + 1
    n = len(lines)
    # Determine the indentation level of the first content line.
    base_indent = None
    while i < n:
        if _RE_EMPTY.match(lines[i]):
            i += 1
            continue
        base_indent = len(lines[i]) - len(lines[i].lstrip())
        break

    if base_indent is None:
        # Admonition has no content.
        raw = lines[start:i]
        sig = ("ADMONITION", admon_type, normalize_text(title))
        blk = Block(
            block_type="ADMONITION",
            start_line=start + 1,
            end_line=i,
            raw_lines=raw,
            signature=sig,
            content_hash=compute_content_hash(raw),
        )
        return blk, i

    # Consume content lines that are either empty or indented >= base_indent.
    # Also accept lines that are part of a nested block (e.g. code fences)
    # as long as they maintain the visual nesting.
    while i < n:
        if _RE_EMPTY.match(lines[i]):
            # Peek ahead: if the next non-empty line is still indented,
            # keep the blank line inside the admonition.
            j = i + 1
            while j < n and _RE_EMPTY.match(lines[j]):
                j += 1
            if j < n:
                nxt_indent = len(lines[j]) - len(lines[j].lstrip())
                if nxt_indent >= base_indent:
                    i = j
                    continue
            break
        else:
            cur_indent = len(lines[i]) - len(lines[i].lstrip())
            if cur_indent < base_indent:
                # Check for special cases: a nested fenced code block may
                # start at the base indent but its content may be less
                # indented.  We keep the line if it looks like a fence
                # at the base indent.
                if cur_indent == base_indent and _RE_CODE_FENCE.match(lines[i]):
                    pass  # fall through to keep
                else:
                    break
        i += 1

    raw = lines[start:i]
    sig = ("ADMONITION", admon_type, normalize_text(title))
    blk = Block(
        block_type="ADMONITION",
        start_line=start + 1,
        end_line=i,
        raw_lines=raw,
        signature=sig,
        content_hash=compute_content_hash(raw),
    )
    return blk, i


def _make_list(
    lines: List[str], start: int, match: "re.Match[str]"
) -> Tuple[Block, int]:
    """
    Consume a list block starting at *start*.

    A list continues while subsequent non-empty lines are either new list
    items or indented continuation lines / nested items.
    """
    base_indent = len(match.group(1))
    first_item_text = match.group(2).strip()
    list_type = "ordered" if re.match(r"^\d+\.", lines[start].lstrip()) else "unordered"

    i = start + 1
    n = len(lines)
    item_count = 1

    while i < n:
        if _RE_EMPTY.match(lines[i]):
            # Peek ahead: if the next non-empty line is still part of the
            # list (indented or a new item), keep the blank line.
            j = i + 1
            while j < n and _RE_EMPTY.match(lines[j]):
                j += 1
            if j < n:
                nxt = lines[j]
                nxt_indent = len(nxt) - len(nxt.lstrip())
                # Continuation of current item or nested item.
                # A new list item at the same indent continues the list,
                # but a list item at indent 0 on a different marker starts
                # a new list after the blank line separator.
                if nxt_indent > base_indent or _RE_LIST_ITEM.match(nxt):
                    # If the next item is at indent 0 and the marker differs,
                    # treat it as a new list (break).
                    if nxt_indent == 0 and _RE_LIST_ITEM.match(nxt):
                        nxt_match = _RE_LIST_ITEM.match(nxt)
                        nxt_list_type = (
                            "ordered" if re.match(r"^\d+\.", nxt.lstrip()) else "unordered"
                        )
                        if nxt_list_type != list_type:
                            break
                    i = j
                    continue
            break

        cur_indent = len(lines[i]) - len(lines[i].lstrip())
        m = _RE_LIST_ITEM.match(lines[i])
        if m:
            # A new list item at any indent that is >= base_indent counts.
            item_indent = len(m.group(1))
            if item_indent >= base_indent:
                item_count += 1
                i += 1
                continue
            else:
                break
        elif cur_indent > base_indent:
            # Continuation / nested content.
            i += 1
            continue
        else:
            # Same or less indent but not a list item -> end of list.
            break

    raw = lines[start:i]
    sig = ("LIST", list_type, normalize_text(first_item_text), item_count)
    blk = Block(
        block_type="LIST",
        start_line=start + 1,
        end_line=i,
        raw_lines=raw,
        signature=sig,
        content_hash=compute_content_hash(raw),
    )
    return blk, i


def _make_paragraph_or_other(
    lines: List[str], start: int
) -> Tuple[Block, int]:
    """
    Consume a paragraph: one or more consecutive non-empty, non-special lines.
    """
    i = start
    n = len(lines)
    while i < n and not _RE_EMPTY.match(lines[i]):
        # Abort if the next line starts a special block.
        if i > start and _is_block_start(lines[i]):
            break
        i += 1
    raw = lines[start:i]

    # Decide whether this is a plain paragraph or OTHER.
    first_line = raw[0].strip()
    if first_line.startswith("<") and first_line.endswith(">"):
        block_type = "OTHER"
        sig = ("OTHER", normalize_text(first_line))
    else:
        block_type = "PARAGRAPH"
        first_text = normalize_text(raw[0]) if raw else ""
        sig = ("PARAGRAPH", first_text[:10])

    blk = Block(
        block_type=block_type,
        start_line=start + 1,
        end_line=i,
        raw_lines=raw,
        signature=sig,
        content_hash=compute_content_hash(raw),
    )
    return blk, i


def _is_block_start(line: str) -> bool:
    """
    Return ``True`` if *line* begins a new block type (anything other than
    a plain paragraph continuation).
    """
    if _RE_HEADING.match(line):
        return True
    if _RE_HR.match(line):
        return True
    if _RE_CODE_FENCE.match(line):
        return True
    if _RE_TABLE_ROW.match(line):
        return True
    if _RE_ADMONITION.match(line):
        return True
    if _RE_LIST_ITEM.match(line):
        return True
    return False

