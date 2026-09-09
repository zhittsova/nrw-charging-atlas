from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
import refresh_nrw_database as refresh  # noqa: E402


class RefreshCoordinatorTest(unittest.TestCase):
    @patch("refresh_nrw_database.run_refresh")
    @patch("refresh_nrw_database.build_seed_imports", return_value=("BEGIN;\nSELECT 1;\nCOMMIT;\n",))
    def test_coordinator_has_one_publish_boundary(self, build_imports, run_refresh) -> None:
        refresh.refresh_database("postgresql://example.invalid/nrw")

        build_imports.assert_called_once_with()
        run_refresh.assert_called_once()
        kwargs = run_refresh.call_args.kwargs
        self.assertEqual(run_refresh.call_args.args, ("postgresql://example.invalid/nrw",))
        self.assertEqual(kwargs["import_sql"], ("BEGIN;\nSELECT 1;\nCOMMIT;\n",))
        self.assertIn("BEGIN;", kwargs["schema_sql"])
        self.assertIn("BEGIN;", kwargs["analytics_sql"])

    def test_seed_and_image_do_not_depend_on_frontend_snapshots(self) -> None:
        stack = (ROOT / "scripts" / "project_stack.py").read_text(encoding="utf-8")
        image = (ROOT / "docker" / "etl.Dockerfile").read_text(encoding="utf-8")

        self.assertIn("scripts/refresh_nrw_database.py", stack)
        self.assertNotIn("scripts/load_nrw_postgis.py", stack)
        self.assertNotIn("COPY frontend/data", image)


if __name__ == "__main__":
    unittest.main()
