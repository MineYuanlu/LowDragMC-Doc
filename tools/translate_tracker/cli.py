"""Command-line interface for the translation tracker."""

import argparse
import sys
from pathlib import Path

from tools.translate_tracker.analyzer import analyze_translation
from tools.translate_tracker.reporter import generate_text_report, generate_markdown_report, generate_json_report


def main() -> None:
    """
    Entry point for the translation-tracker CLI.

    Parses arguments, runs the analysis, and emits the report in the
    requested format to stdout or a file.
    """
    parser = argparse.ArgumentParser(
        description="Translation tracker: Analyze translation status between source and target directories using git history."
    )
    parser.add_argument(
        "--source", "-s", default="docs/en/",
        help="Source directory (default: docs/en/)"
    )
    parser.add_argument(
        "--target", "-t", default="docs/zh/",
        help="Target directory (default: docs/zh/)"
    )
    parser.add_argument(
        "--stop-tag-prefix", default=None,
        help="Tag prefix to stop scanning at (e.g., translate-reviewed/)"
    )
    parser.add_argument(
        "--format", "-f", choices=["text", "markdown", "json"], default="text",
        help="Output format"
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output file (default: stdout)"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.5,
        help="Structural diff threshold for whole-file summary (default: 0.5)"
    )
    parser.add_argument(
        "--git-cwd", default=None,
        help="Git repository root (auto-detected if not specified)"
    )

    args = parser.parse_args()

    try:
        report = analyze_translation(
            source_dir=args.source,
            target_dir=args.target,
            stop_tag_prefix=args.stop_tag_prefix,
            git_cwd=args.git_cwd,
            structural_threshold=args.threshold,
        )
    except Exception as exc:
        print(f"Error during analysis: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.format == "text":
        output = generate_text_report(report)
    elif args.format == "markdown":
        output = generate_markdown_report(report)
    elif args.format == "json":
        output = generate_json_report(report)
    else:
        output = generate_text_report(report)

    try:
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
        else:
            print(output)
    except OSError as exc:
        print(f"Error writing output: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
