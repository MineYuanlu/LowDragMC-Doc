"""
Core data types for translate-tracker.
All modules must import from here to ensure type consistency.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any


@dataclass
class CommitInfo:
    """Git commit information for a line or block."""
    hash: str
    author: str
    timestamp: int  # Unix timestamp
    message: str


@dataclass
class Block:
    """
    A semantic block extracted from a Markdown file.
    This is the atomic unit for translation tracking.
    """
    block_type: str  # 'HEADING', 'PARAGRAPH', 'TABLE', 'CODE_BLOCK',
                     # 'ADMONITION', 'LIST', 'HORIZONTAL_RULE', 'OTHER'
    start_line: int  # 1-based, inclusive
    end_line: int    # 1-based, inclusive
    raw_lines: List[str]
    signature: Tuple[Any, ...]  # Language-agnostic signature for weak matching
    content_hash: str  # SHA256 of normalized content for exact comparison

    def line_range_str(self) -> str:
        if self.start_line == self.end_line:
            return f"line {self.start_line}"
        return f"lines {self.start_line}-{self.end_line}"


@dataclass
class BlockDiff:
    """Difference between a source block and its corresponding target block."""
    src_block: Optional[Block]
    tgt_block: Optional[Block]
    status: str  # 'added', 'removed', 'modified', 'unchanged', 'moved'
    src_commit: Optional[CommitInfo] = None
    tgt_commit: Optional[CommitInfo] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class FileReport:
    """Translation status report for a single file."""
    rel_path: str  # Relative path from source/target root
    status: str    # 'missing', 'outdated', 'structural_diff', 'normal', 'warning'
    summary: str
    block_diffs: List[BlockDiff] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class AnalysisReport:
    """Top-level report for the entire analysis run."""
    source_dir: str
    target_dir: str
    stop_tag_prefix: Optional[str]
    file_reports: List[FileReport] = field(default_factory=list)
    overall_warnings: List[str] = field(default_factory=list)

    def get_files_by_status(self, status: str) -> List[FileReport]:
        return [r for r in self.file_reports if r.status == status]
