"""Compatibility wrapper; use scan_app_strings.py for other applications."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.scan_app_strings import *  # noqa: F403
if __name__ == "__main__":
    if "--app" not in sys.argv:
        sys.argv.extend(["--app", "cursor"])
    raise SystemExit(main())
