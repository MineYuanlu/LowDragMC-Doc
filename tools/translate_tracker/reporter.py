"""
Report generators for translation analysis.

Produces three output formats:
* **Text** – human-readable, grouped by status.
* **Markdown** – suitable for posting as a CI comment.
* **JSON** – machine-readable for further automation.

All generators are defensive: missing or empty fields are handled gracefully
so that the report is always produced even when the analysis data is partial.
"""

import json
from typing import List, Dict, Any

from tools.translate_tracker.core_types import Block, BlockDiff, FileReport, AnalysisReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_display_name(status: str) -> str:
    """Map internal status to user-facing Chinese labels."""
    mapping = {
        "missing": "整体缺失 - 需全文翻译",
        "outdated": "整体过时 - 需删除",
        "structural_diff": "结构缺失/过时",
        "normal": "翻译正常",
        "warning": "警告 - 状态无法确认",
    }
    return mapping.get(status, status)


def _block_type_display(block: Block) -> str:
    """Short human-readable description of a block."""
    if block.block_type == "HEADING":
        level = block.signature[1] if len(block.signature) > 1 else "?"
        return f'HEADING level {level}'
    if block.block_type == "TABLE":
        cols = len(block.signature[1]) if len(block.signature) > 1 else 0
        return f'TABLE ({cols} cols)'
    if block.block_type == "CODE_BLOCK":
        lang = block.signature[1] if len(block.signature) > 1 else ""
        return f'CODE_BLOCK ({lang or "no lang"})'
    if block.block_type == "ADMONITION":
        ad_type = block.signature[1] if len(block.signature) > 1 else ""
        return f'ADMONITION ({ad_type})'
    if block.block_type == "LIST":
        ltype = block.signature[1] if len(block.signature) > 1 else ""
        cnt = block.signature[3] if len(block.signature) > 3 else 0
        return f'LIST ({ltype}, {cnt} items)'
    return block.block_type


def _collect_structural_messages(diff: BlockDiff) -> List[str]:
    """Return per-block human-readable messages for structural diffs."""
    msgs: List[str] = []
    if diff.src_block is not None and diff.tgt_block is None:
        src = diff.src_block
        msgs.append(
            f"源文件第 {src.line_range_str()} 行（块类型 \"{_block_type_display(src)}\"）"
            f"在目标文件中缺失"
        )
    elif diff.src_block is None and diff.tgt_block is not None:
        tgt = diff.tgt_block
        msgs.append(
            f"目标文件第 {tgt.line_range_str()} 行（块类型 \"{_block_type_display(tgt)}\"）"
            f"在源文件中不存在，可能已过时"
        )
    elif diff.src_block is not None and diff.tgt_block is not None:
        if diff.status == "modified":
            src = diff.src_block
            tgt = diff.tgt_block
            msgs.append(
                f"源文件第 {src.line_range_str()} 行与目标文件对应块内容不同，"
                f"且源在目标之后更新"
            )
    return msgs


def _file_has_structural_detail(report: FileReport) -> bool:
    """Return ``True`` if the file report carries per-block diff details."""
    return any(
        d.status in ("added", "removed", "modified") for d in report.block_diffs
    )


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

def generate_text_report(report: AnalysisReport, full_details: bool = True) -> str:
    """
    Generate a human-readable text report.

    Files are grouped by status and, when *full_details* is ``True``,
    individual block-level messages are printed for structural-diff files.

    Parameters
    ----------
    report:
        The top-level analysis report.
    full_details:
        If ``True``, include per-block details for structural-diff files.

    Returns
    -------
    Multi-line string containing the full report.
    """
    lines: List[str] = []
    sep = "=" * 40

    lines.append(sep)
    lines.append("翻译分析报告")
    lines.append(f"源目录: {report.source_dir or 'N/A'}    "
                 f"目标目录: {report.target_dir or 'N/A'}")
    tag = report.stop_tag_prefix or "N/A"
    lines.append(f"停止Tag前缀: {tag}")
    lines.append(sep)
    lines.append("")

    # Order matters – most severe first.
    statuses = ["missing", "outdated", "structural_diff", "normal", "warning"]

    for status in statuses:
        files = report.get_files_by_status(status)
        if not files:
            continue

        label = _status_display_name(status)
        lines.append(f"[{label}] ({len(files)} files)")

        for f in files:
            lines.append(f"  {f.rel_path}")

            if status == "missing":
                lines.append("    → 源文件存在但目标文件缺失")
            elif status == "outdated":
                lines.append("    → 源文件已删除，目标文件仍保留")
            elif status == "normal":
                # No extra detail for normal files.
                pass
            elif status == "warning":
                if f.warnings:
                    for w in f.warnings:
                        lines.append(f"    → {w}")
                else:
                    lines.append("    → 状态无法确认")
            elif status == "structural_diff":
                if full_details and f.block_diffs:
                    printed = False
                    for diff in f.block_diffs:
                        msgs = _collect_structural_messages(diff)
                        for msg in msgs:
                            lines.append(f"    → {msg}")
                            printed = True
                        if diff.notes:
                            for note in diff.notes:
                                lines.append(f"    ⚠ {note}")
                                printed = True
                    if not printed and f.summary:
                        lines.append(f"    → {f.summary}")
                elif f.summary:
                    lines.append(f"    → {f.summary}")

        lines.append("")

    # Overall warnings.
    if report.overall_warnings:
        lines.append("[全局警告]")
        for w in report.overall_warnings:
            lines.append(f"  ! {w}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def generate_markdown_report(report: AnalysisReport) -> str:
    """
    Generate a Markdown-formatted report suitable for CI comments.

    Uses collapsible sections so that long lists do not overwhelm the reader.

    Parameters
    ----------
    report:
        The top-level analysis report.

    Returns
    -------
    Multi-line Markdown string.
    """
    lines: List[str] = []
    lines.append("## Translation Analysis Report")
    lines.append("")
    lines.append(f"- **Source:** `{report.source_dir or 'N/A'}`")
    lines.append(f"- **Target:** `{report.target_dir or 'N/A'}`")
    lines.append(f"- **Stop tag prefix:** `{report.stop_tag_prefix or 'N/A'}`")
    lines.append("")

    statuses = ["missing", "outdated", "structural_diff", "normal", "warning"]

    for status in statuses:
        files = report.get_files_by_status(status)
        if not files:
            continue

        label = _status_display_name(status)
        emoji = {
            "missing": "🚨",
            "outdated": "🗑️",
            "structural_diff": "⚠️",
            "normal": "✅",
            "warning": "🔍",
        }.get(status, "")
        lines.append(f"### {emoji} {label} ({len(files)} files)")
        lines.append("")

        for f in files:
            lines.append(f"**`{f.rel_path}`**")
            if status == "missing":
                lines.append("- Source exists but target is missing.")
            elif status == "outdated":
                lines.append("- Source deleted, target still present.")
            elif status == "normal":
                lines.append("- Translation is up to date.")
            elif status == "warning":
                if f.warnings:
                    for w in f.warnings:
                        lines.append(f"- ⚠ {w}")
                else:
                    lines.append("- Status cannot be determined.")
            elif status == "structural_diff":
                details: List[str] = []
                for diff in f.block_diffs:
                    details.extend(_collect_structural_messages(diff))
                    if diff.notes:
                        for note in diff.notes:
                            details.append(f"⚠ {note}")
                if details:
                    lines.append("<details>")
                    lines.append("<summary>Block-level details</summary>")
                    lines.append("")
                    for d in details:
                        lines.append(f"- {d}")
                    lines.append("</details>")
                elif f.summary:
                    lines.append(f"- {f.summary}")
            lines.append("")

    if report.overall_warnings:
        lines.append("### Global Warnings")
        for w in report.overall_warnings:
            lines.append(f"- ⚠ {w}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------

def _commit_to_dict(commit: Any) -> Dict[str, Any]:
    """Serialize a CommitInfo (or None) to a dictionary."""
    if commit is None:
        return None  # type: ignore[return-value]
    return {
        "hash": commit.hash,
        "author": commit.author,
        "timestamp": commit.timestamp,
        "message": commit.message,
    }


def _block_to_dict(block: Any) -> Dict[str, Any]:
    """Serialize a Block (or None) to a dictionary."""
    if block is None:
        return None  # type: ignore[return-value]
    return {
        "block_type": block.block_type,
        "start_line": block.start_line,
        "end_line": block.end_line,
        "signature": block.signature,
        "content_hash": block.content_hash,
    }


def _diff_to_dict(diff: BlockDiff) -> Dict[str, Any]:
    """Serialize a BlockDiff to a dictionary."""
    return {
        "src_block": _block_to_dict(diff.src_block),
        "tgt_block": _block_to_dict(diff.tgt_block),
        "status": diff.status,
        "src_commit": _commit_to_dict(diff.src_commit),
        "tgt_commit": _commit_to_dict(diff.tgt_commit),
        "notes": diff.notes,
    }


def _file_report_to_dict(report: FileReport) -> Dict[str, Any]:
    """Serialize a FileReport to a dictionary."""
    return {
        "rel_path": report.rel_path,
        "status": report.status,
        "summary": report.summary,
        "block_diffs": [_diff_to_dict(d) for d in report.block_diffs],
        "warnings": report.warnings,
    }


def generate_json_report(report: AnalysisReport) -> str:
    """
    Generate a JSON report for programmatic consumption.

    Parameters
    ----------
    report:
        The top-level analysis report.

    Returns
    -------
    Pretty-printed JSON string.
    """
    data = {
        "source_dir": report.source_dir,
        "target_dir": report.target_dir,
        "stop_tag_prefix": report.stop_tag_prefix,
        "overall_warnings": report.overall_warnings,
        "files": [_file_report_to_dict(f) for f in report.file_reports],
        "summary": {
            "total_files": len(report.file_reports),
            "by_status": {
                status: len(report.get_files_by_status(status))
                for status in ("missing", "outdated", "structural_diff", "normal", "warning")
            },
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2)
