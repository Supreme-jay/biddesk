"""Run every test suite. Usage: python run_tests.py"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUITES = [
    ("scoring quality", "tests/evaluate.py"),
    ("document readers", "tests/test_docparse.py"),
    ("pdf reader", "tests/test_pdf.py"),
    ("failure paths", "tests/test_robustness.py"),
    ("drafting safety", "tests/test_draft.py"),
    ("folder watch", "tests/test_watch.py"),
    ("grant extraction", "tests/test_grant_extract.py"),
    ("grant scoring", "tests/test_grant_score.py"),
    ("grant drafting", "tests/test_grant_draft.py"),
    ("grant local draft", "tests/test_grant_localdraft.py"),
]


def main() -> int:
    failed = []
    for label, suite in SUITES:
        print(f"\n{'=' * 60}\n{label}  ({suite})\n{'=' * 60}")
        result = subprocess.run([sys.executable, suite], cwd=ROOT)
        if result.returncode != 0:
            failed.append(label)

    print(f"\n{'=' * 60}")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print(f"All {len(SUITES)} suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
