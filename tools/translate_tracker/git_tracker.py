"""
Git history tracker for translate-tracker.

Provides utilities to:
- Run git commands safely
- Parse `git blame --porcelain` output into per-line CommitInfo
- Manage stop tags (prefix-based tag discovery and commit resolution)
- Infer the most recent commit for a block of lines
"""

import subprocess
import warnings
from typing import Dict, List, Optional, Tuple

from tools.translate_tracker.core_types import CommitInfo


def run_git_command(args: List[str], git_cwd: Optional[str] = None) -> str:
    """
    Run a git command and return stdout as string. Raise on error.

    Args:
        args: Command-line arguments for git (without 'git' itself).
        git_cwd: Working directory for the git command (repo root).

    Returns:
        stdout string (trailing newlines stripped from the overall output,
        but internal whitespace preserved).

    Raises:
        subprocess.CalledProcessError: If the git command returns non-zero.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=git_cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.rstrip("\n")


def get_line_commits(file_path: str, git_cwd: Optional[str] = None) -> Dict[int, CommitInfo]:
    """
    Get the last commit info for each line of a file.

    Uses `git blame --porcelain` which outputs metadata per commit once,
    then references it for subsequent lines. This parser maintains a cache
    of recently-seen commits to handle that deduplication.

    Args:
        file_path: Path to the file (relative to git repo root or absolute).
        git_cwd: Working directory for git commands (the repo root).

    Returns:
        Dict mapping 1-based line number to CommitInfo.
        Returns an empty dict if the file is not tracked by git or blame fails.
    """
    try:
        stdout = run_git_command(["blame", "--porcelain", file_path], git_cwd=git_cwd)
    except subprocess.CalledProcessError as exc:
        # Common cases: file not tracked, file deleted then recovered from history,
        # or path is outside the repo.
        warnings.warn(
            f"git blame failed for '{file_path}': {exc.stderr.strip() or exc}"
        )
        return {}

    if not stdout.strip():
        return {}

    line_commits: Dict[int, CommitInfo] = {}
    commit_cache: Dict[str, CommitInfo] = {}

    current_hash: Optional[str] = None
    current_author: Optional[str] = None
    current_timestamp: Optional[int] = None
    current_message: Optional[str] = None

    # Track which line number we are on (1-based)
    current_line_number: int = 0

    lines = stdout.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # A porcelain header looks like:
        # <40-hex-hash> <original-line> <final-line> [<lines>]
        # Example: abc123... 10 20 5
        if len(line) >= 40 and line[:40].replace(" ", "").isalnum():
            parts = line.split()
            if len(parts) >= 3 and len(parts[0]) == 40:
                current_hash = parts[0]
                # final line number is parts[2]
                try:
                    current_line_number = int(parts[2])
                except ValueError:
                    i += 1
                    continue

                # If we have seen this commit before, restore cached metadata
                if current_hash in commit_cache:
                    cached = commit_cache[current_hash]
                    current_author = cached.author
                    current_timestamp = cached.timestamp
                    current_message = cached.message
                else:
                    current_author = None
                    current_timestamp = None
                    current_message = None
                i += 1
                continue

        # Attribute lines for the current commit
        if current_hash is not None:
            if line.startswith("author ") and not line.startswith("author-mail "):
                current_author = line[len("author "):]
            elif line.startswith("author-time "):
                try:
                    current_timestamp = int(line[len("author-time "):])
                except ValueError:
                    current_timestamp = None
            elif line.startswith("summary "):
                current_message = line[len("summary "):]
            elif line.startswith("\t"):
                # This is the actual line content; commit metadata is complete.
                if current_author is not None and current_timestamp is not None and current_message is not None:
                    commit_info = CommitInfo(
                        hash=current_hash,
                        author=current_author,
                        timestamp=current_timestamp,
                        message=current_message,
                    )
                    # Cache it so subsequent lines referencing the same commit
                    # don't need to re-parse attributes.
                    commit_cache[current_hash] = commit_info
                    line_commits[current_line_number] = commit_info
                # Move to next line (next header)
                i += 1
                continue

        i += 1

    return line_commits


def get_commit_for_tag(tag_name: str, git_cwd: Optional[str] = None) -> CommitInfo:
    """
    Get the commit info that a tag points to.

    Uses `git rev-list -n 1 <tag>` to resolve the tag to a commit hash,
    then `git show --format=...` to extract metadata.

    Args:
        tag_name: The git tag name.
        git_cwd: Working directory for git commands.

    Returns:
        CommitInfo for the commit the tag references.

    Raises:
        subprocess.CalledProcessError: If the tag cannot be resolved.
    """
    commit_hash = run_git_command(["rev-list", "-n", "1", tag_name], git_cwd=git_cwd).strip()

    # Use a format string that gives us: hash|author|timestamp|message
    fmt = "%H|%an|%at|%s"
    show_output = run_git_command(
        ["show", "-s", "--no-patch", f"--format={fmt}", commit_hash],
        git_cwd=git_cwd,
    ).strip()

    parts = show_output.split("|", 3)
    if len(parts) != 4:
        raise ValueError(f"Unexpected git show output for tag '{tag_name}': {show_output}")

    h, author, ts_str, message = parts
    return CommitInfo(
        hash=h,
        author=author,
        timestamp=int(ts_str),
        message=message,
    )


def find_stop_tags(tag_prefix: str, git_cwd: Optional[str] = None) -> List[Tuple[str, CommitInfo]]:
    """
    Find all tags matching the given prefix, sorted by commit time (newest first).

    Args:
        tag_prefix: Prefix to filter tags (e.g., 'v' matches 'v1.0', 'v2.0').
        git_cwd: Working directory for git commands.

    Returns:
        List of (tag_name, CommitInfo) tuples, sorted by commit timestamp descending.
        Tags that cannot be resolved are skipped with a warning.
    """
    try:
        tag_list_output = run_git_command(["tag", "-l", f"{tag_prefix}*"], git_cwd=git_cwd).strip()
    except subprocess.CalledProcessError:
        return []

    if not tag_list_output:
        return []

    tag_names = [t for t in tag_list_output.splitlines() if t.strip()]
    result: List[Tuple[str, CommitInfo]] = []

    for tag in tag_names:
        try:
            commit_info = get_commit_for_tag(tag, git_cwd=git_cwd)
            result.append((tag, commit_info))
        except (subprocess.CalledProcessError, ValueError) as exc:
            warnings.warn(f"Could not resolve tag '{tag}': {exc}")
            continue

    # Sort by timestamp descending (newest first)
    result.sort(key=lambda item: item[1].timestamp, reverse=True)
    return result


def get_block_commit(block, line_commits: Dict[int, CommitInfo]) -> Optional[CommitInfo]:
    """
    Determine the most recent commit that modified any line in the block.

    Args:
        block: Any object with `.start_line` and `.end_line` attributes (1-based, inclusive).
        line_commits: Output from `get_line_commits()`.

    Returns:
        CommitInfo with the latest timestamp among the block's lines,
        or None if no commit info is available for any line in the range.
    """
    start = getattr(block, "start_line", None)
    end = getattr(block, "end_line", None)

    if start is None or end is None:
        return None

    latest_commit: Optional[CommitInfo] = None

    for line_no in range(start, end + 1):
        commit_info = line_commits.get(line_no)
        if commit_info is None:
            continue
        if latest_commit is None or commit_info.timestamp > latest_commit.timestamp:
            latest_commit = commit_info

    return latest_commit
