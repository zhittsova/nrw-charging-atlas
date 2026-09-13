from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
import verify_nrw_database as verifier  # noqa: E402


class DatabaseVerificationTest(unittest.TestCase):
    def test_runs_both_modules_with_stop_on_error(self) -> None:
        with patch("verify_nrw_database.subprocess.run") as run:
            verifier.verify_database("postgresql://owner:secret@db/nrw_gis")

        self.assertEqual(run.call_count, 2)
        modules = [call.args[0][-1] for call in run.call_args_list]
        self.assertEqual(modules, [str(path) for path in verifier.VERIFICATION_MODULES])
        self.assertTrue(all(call.args[0][3:7] == ["--no-psqlrc", "--set", "ON_ERROR_STOP=1", "--file"] for call in run.call_args_list))

    def test_refuses_missing_database_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            verifier.verify_database("")


if __name__ == "__main__":
    unittest.main()
