"""
Core analysis engine for translate-tracker.

Orchestrates file discovery, Markdown parsing, git blame, block comparison,
and report generation into a single :func:`analyze_translation` call.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Set, Tuple, Optional, List

from tools.translate_tracker.core_types import Block, BlockDiff, FileReport, AnalysisReport, CommitInfo
from tools.translate_tracker.markdown_parser import parse_markdown
from tools.translate_tracker.git_tracker import get_line_commits, find_stop_tags, get_commit_for_tag, get_block_commit
from tools.translate_tracker.comparer import compare_file


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Extensions that are not Markdown but should be tracked for sync status.
_MARKDOWN_EXTS = {".md", ".markdown", ".mdown", ".mkd"}

# Extensions that are ignored entirely (no report emitted).
_IGNORED_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".mp4", ".webm", ".mov", ".avi", ".mkv",
    ".pages", ".docx", ".doc", ".pdf", ".xlsx", ".xls", ".pptx", ".ppt",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib",
}


def _is_markdown(path: str) -> bool:
    """Return ``True`` if *path* has a recognised Markdown extension."""
    return Path(path).suffix.lower() in _MARKDOWN_EXTS


def _is_ignored(path: str) -> bool:
    """Return ``True`` if *path* has an extension we ignore completely."""
    return Path(path).suffix.lower() in _IGNORED_EXTS


def _find_git_root(start_dir: str) -> Optional[str]:
    """
    Walk up from *start_dir* looking for a ``.git`` directory.

    Returns:
        Absolute path to the directory containing ``.git``, or ``None``
        if no git repository is found.
    """
    current = Path(start_dir).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return str(parent)
    return None


def discover_files(source_dir: str, target_dir: str) -> Tuple[Set[str], Set[str], Set[str]]:
    """
    Discover all relative file paths in *source_dir* and *target_dir*.

    Hidden files and directories (names starting with ``.``) are skipped.
    Symlinks are followed but cycles are not guarded against (assumed benign
    for typical documentation trees).

    Args:
        source_dir: Root of the source language tree.
        target_dir: Root of the target language tree.

    Returns:
        A 3-tuple of ``(source_files, target_files, all_files)`` where each
        element is a set of relative POSIX-style paths.
    """
    def _walk(root: str) -> Set[str]:
        results: Set[str] = set()
        root_path = Path(root)
        if not root_path.exists():
            return results
        for p in root_path.rglob("*"):
            if p.is_dir():
                continue
            # Skip hidden files / inside hidden directories.
            try:
                rel = p.relative_to(root_path)
            except ValueError:
                continue
            if any(part.startswith(".") for part in rel.parts):
                continue
            results.add(rel.as_posix())
        return results

    source_files = _walk(source_dir)
    target_files = _walk(target_dir)
    all_files = source_files | target_files
    return source_files, target_files, all_files


def _read_text(path: Path) -> Optional[str]:
    """
    Safely read a text file.

    Returns:
        Decoded text, or ``None`` on error (with a warning emitted).
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            warnings.warn(f"Cannot decode '{path}': {exc}")
            return None
    except OSError as exc:
        warnings.warn(f"Cannot read '{path}': {exc}")
        return None


def _classify_status(block_diffs: List[BlockDiff]) -> Tuple[str, str]:
    """
    Derive an overall file status and human-readable summary from block diffs.

    Classification rules:
    * **structural_diff** – at least one block was added or removed (structure mismatch).
    * **warning** – at least one diff carries a warning note but no structural change.
    * **modified** – only content-hash differences (translation outdated, structure aligned).
    * **normal** – all blocks unchanged.

    Returns:
        ``(status, summary)``
    """
    if not block_diffs:
        return "normal", "无内容块"

    has_structural = False
    has_modified = False
    has_warning = False

    for diff in block_diffs:
        if diff.status in ("added", "removed"):
            has_structural = True
        elif diff.status == "modified":
            has_modified = True
        elif diff.status == "moved":
            # Treat moved as structural for simplicity.
            has_structural = True
        if diff.notes:
            has_warning = True

    if has_structural or has_modified:
        return "structural_diff", "结构或内容差异"
    if has_warning:
        return "warning", "检测到警告"
    return "normal", "翻译已同步"


def analyze_translation(
    source_dir: str,
    target_dir: str,
    stop_tag_prefix: Optional[str] = None,
    git_cwd: Optional[str] = None,
    structural_threshold: float = 0.5,
) -> AnalysisReport:
    """
    Main analysis entry point.

    Walks the source and target directories, compares corresponding Markdown
    files at the block level, and produces a comprehensive :class:`AnalysisReport`.

    Args:
        source_dir: Source directory (e.g., ``"docs/en/"``).
        target_dir: Target directory (e.g., ``"docs/zh/"``).
        stop_tag_prefix: If provided, the newest tag matching this prefix is
            used as a stop point.  Only commits *after* the tag are considered
            as having modified the source.
        git_cwd: Git repository root.  If ``None``, auto-detected by walking
            upward from *source_dir* until a ``.git`` folder is found.
        structural_threshold: If the fraction of blocks that are not
            ``'unchanged'`` exceeds this value, the whole file is summarised
            as needing re-translation instead of listing every block.

    Returns:
        :class:`AnalysisReport` with all findings.

    Raises:
        ValueError: If *git_cwd* cannot be determined and auto-detection fails.
    """
    # ------------------------------------------------------------------
    # 1. Resolve git repository root
    # ------------------------------------------------------------------
    if git_cwd is None:
        detected = _find_git_root(source_dir)
        if detected is None:
            raise ValueError(
                f"Cannot find a .git repository root starting from '{source_dir}'. "
                "Please specify --git-cwd explicitly."
            )
        git_cwd = detected

    # ------------------------------------------------------------------
    # 2. Determine stop tag / commit
    # ------------------------------------------------------------------
    stop_commit: Optional[CommitInfo] = None
    if stop_tag_prefix:
        tags = find_stop_tags(stop_tag_prefix, git_cwd=git_cwd)
        if tags:
            newest_tag, stop_commit = tags[0]
            print(f"Stop tag: {newest_tag} ({stop_commit.hash[:8]})", file=sys.stderr)
        else:
            warnings.warn(f"No tags found with prefix '{stop_tag_prefix}'")

    # ------------------------------------------------------------------
    # 3. Discover files
    # ------------------------------------------------------------------
    source_files, target_files, all_files = discover_files(source_dir, target_dir)

    file_reports: List[FileReport] = []
    overall_warnings: List[str] = []

    # ------------------------------------------------------------------
    # 4. Analyse each file
    # ------------------------------------------------------------------
    for rel_path in sorted(all_files):
        src_path = Path(source_dir) / rel_path
        tgt_path = Path(target_dir) / rel_path

        # --- 4a. Non-Markdown asset handling --------------------------------
        if not _is_markdown(rel_path):
            if _is_ignored(rel_path):
                continue
            # Report binary/non-markdown sync status only.
            src_exists = src_path.exists()
            tgt_exists = tgt_path.exists()
            if src_exists and not tgt_exists:
                file_reports.append(FileReport(
                    rel_path=rel_path,
                    status="missing",
                    summary="目标文件缺失（非 Markdown 资源）",
                ))
            elif not src_exists and tgt_exists:
                file_reports.append(FileReport(
                    rel_path=rel_path,
                    status="outdated",
                    summary="源文件已删除（非 Markdown 资源）",
                ))
            else:
                # Both exist – we don't diff binary content, just note sync.
                file_reports.append(FileReport(
                    rel_path=rel_path,
                    status="normal",
                    summary="非 Markdown 资源已同步",
                ))
            continue

        # --- 4b. Markdown-only analysis -------------------------------------
        print(f"Analyzing: {rel_path}", file=sys.stderr)

        if src_path.exists() and not tgt_path.exists():
            file_reports.append(FileReport(
                rel_path=rel_path,
                status="missing",
                summary="目标文件缺失",
            ))
            continue

        if not src_path.exists() and tgt_path.exists():
            file_reports.append(FileReport(
                rel_path=rel_path,
                status="outdated",
                summary="源文件已删除",
            ))
            continue

        # Both exist – read and parse.
        src_text = _read_text(src_path)
        tgt_text = _read_text(tgt_path)

        if src_text is None or tgt_text is None:
            overall_warnings.append(f"Skipping '{rel_path}' due to read error.")
            continue

        src_blocks: List[Block] = parse_markdown(src_text)
        tgt_blocks: List[Block] = parse_markdown(tgt_text)

        # Git blame for both files.
        try:
            src_line_commits = get_line_commits(str(src_path), git_cwd=git_cwd)
        except Exception as exc:
            warnings.warn(f"Git blame failed for source '{src_path}': {exc}")
            src_line_commits = {}

        try:
            tgt_line_commits = get_line_commits(str(tgt_path), git_cwd=git_cwd)
        except Exception as exc:
            warnings.warn(f"Git blame failed for target '{tgt_path}': {exc}")
            tgt_line_commits = {}

        # Compare.
        try:
            block_diffs: List[BlockDiff] = compare_file(
                src_blocks, tgt_blocks,
                src_line_commits=src_line_commits,
                tgt_line_commits=tgt_line_commits,
                stop_tag_commit=stop_commit,
            )
        except Exception as exc:
            warnings.warn(f"Comparison failed for '{rel_path}': {exc}")
            overall_warnings.append(f"Comparison failed for '{rel_path}': {exc}")
            block_diffs = []

        # Attach commit info to each diff (best-effort).
        for diff in block_diffs:
            if diff.src_block is not None:
                diff.src_commit = get_block_commit(diff.src_block, src_line_commits)
            if diff.tgt_block is not None:
                diff.tgt_commit = get_block_commit(diff.tgt_block, tgt_line_commits)

        # Determine overall status.
        status, summary = _classify_status(block_diffs)

        # Apply structural threshold for whole-file summary.
        total_blocks = len(block_diffs)
        if total_blocks > 0 and status not in ("normal", "warning"):
            non_unchanged = sum(1 for d in block_diffs if d.status != "unchanged")
            fraction = non_unchanged / total_blocks
            if fraction > structural_threshold:
                status = "structural_diff"
                summary = f"整文件需重新翻译 ({non_unchanged}/{total_blocks} 块变动)"
                # We keep block_diffs empty (or optionally clear them) to
                # signal the caller that a high-level summary is preferred.
                block_diffs = []

        # Collect per-file warnings.
        file_warnings: List[str] = []
        for diff in block_diffs:
            file_warnings.extend(diff.notes)

        file_reports.append(FileReport(
            rel_path=rel_path,
            status=status,
            summary=summary,
            block_diffs=block_diffs,
            warnings=file_warnings,
        ))

    # ------------------------------------------------------------------
    # 5. Build and return report
    # ------------------------------------------------------------------
    return AnalysisReport(
        source_dir=source_dir,
        target_dir=target_dir,
        stop_tag_prefix=stop_tag_prefix,
        file_reports=file_reports,
        overall_warnings=overall_warnings,
    )
