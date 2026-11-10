"""Run a dbt command on the transform/ project with the settings of the .env file.

dbt does not read .env by itself: this small launcher loads it, then calls the dbt
installed in the project .venv.

Usage:  python -m bank_dw.run_dbt build          (or: seed, run, test, docs generate...)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = ROOT / "transform"


def find_dbt(root: Path = ROOT) -> str:
    """dbt of the project .venv first (Windows or Linux layout), else the one on the PATH."""
    if os.environ.get("DBT_EXECUTABLE"):
        return os.environ["DBT_EXECUTABLE"]
    for candidate in (root / ".venv" / "Scripts" / "dbt.exe", root / ".venv" / "bin" / "dbt"):
        if candidate.exists():
            return str(candidate)
    found = shutil.which("dbt")
    if found is None:
        raise FileNotFoundError("dbt not found: install requirements-dbt.txt in .venv")
    return found


def dbt_command(args: list[str], dbt: str) -> list[str]:
    """The full command: dbt <args> pointed at transform/ for the project and the profile."""
    return [dbt, *args, "--project-dir", str(PROJECT_DIR), "--profiles-dir", str(PROJECT_DIR)]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        return 2
    load_dotenv(ROOT / ".env")
    try:
        dbt = find_dbt()
    except FileNotFoundError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return subprocess.run(dbt_command(args, dbt), cwd=PROJECT_DIR, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
