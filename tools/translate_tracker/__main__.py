"""Entry point for python -m tools.translate_tracker"""

import sys
from pathlib import Path

# Ensure the parent directory (repo root) is on the path so that
# 'tools.translate_tracker' imports work correctly.
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.translate_tracker.cli import main

if __name__ == "__main__":
    main()
