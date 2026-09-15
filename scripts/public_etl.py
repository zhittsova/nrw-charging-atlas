"""Refresh and verify the public export inside the disposable ETL Compose stack."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-download", action="store_true", help="Use a complete existing validated raw cache")
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        parser.error("DATABASE_URL is required")
    scripts = []
    if not args.skip_download:
        scripts += [("fetch_real_data.py", "--refresh"), ("fetch_nrw_infrastructure.py", "--allow-large", "--refresh")]
    scripts += [
        ("refresh_nrw_database.py",),
        ("verify_nrw_database.py",),
        ("export_nrw_runtime.py",),
    ]
    for script, *arguments in scripts:
        subprocess.run([sys.executable, str(ROOT / "scripts" / script), *arguments], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
