"""Execute the installation's SQL verification modules through its ETL runtime.

This deliberately lives inside the tools container: DATABASE_URL is available
there, while neither its password nor a direct database endpoint need to be
copied to the host-side end-to-end verifier.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFICATION_MODULES = (
    ROOT / "db" / "verify_nrw_analytics.sql",
    ROOT / "db" / "verify_nrw_energy_balance.sql",
)


def verify_database(database_url: str) -> None:
    if not database_url:
        raise ValueError("DATABASE_URL is required for SQL verification")
    for module in VERIFICATION_MODULES:
        subprocess.run(
            [
                "psql",
                "--dbname",
                database_url,
                "--no-psqlrc",
                "--set",
                "ON_ERROR_STOP=1",
                "--file",
                str(module),
            ],
            check=True,
        )


def main() -> None:
    verify_database(os.environ.get("DATABASE_URL", ""))
    print("SQL verification modules passed")


if __name__ == "__main__":
    main()
