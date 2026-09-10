from __future__ import annotations

import sys
import tempfile
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

    def test_malformed_consumed_provenance_fails_before_seed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            source.write_text("canonical bytes\n", encoding="utf-8")
            provenance = root / "provenance"
            provenance.mkdir()
            (provenance / "fixture.json").write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Consumed provenance is malformed"):
                refresh.consumed_input_records({"fixture": source}, provenance_dir=provenance)

    def test_ingest_provenance_is_transaction_bound(self) -> None:
        sql = refresh.ingest_provenance_import([
            {"source_key": "fixture", "source_path": "data/raw/fixture", "bytes": 3, "sha256": "a" * 64, "provenance": {"sha256": "old"}, "provenance_sha256": "b" * 64},
        ], ingest_run_id="00000000-0000-0000-0000-000000000001")
        self.assertIn("BEGIN;", sql)
        self.assertIn("UPDATE raw.ingest_runs SET is_current = false", sql)
        self.assertIn("INSERT INTO raw.ingest_source_inputs", sql)
        self.assertIn("old", sql)

    def test_failed_seed_cannot_publish_a_replacement_ingest_run(self) -> None:
        sql = refresh.ingest_provenance_import([
            {"source_key": "fixture", "source_path": "data/raw/fixture", "bytes": 3, "sha256": "a" * 64, "provenance": None, "provenance_sha256": None},
        ], ingest_run_id="00000000-0000-0000-0000-000000000002")
        self.assertLess(sql.index("BEGIN;"), sql.index("UPDATE raw.ingest_runs SET is_current = false"))
        self.assertLess(sql.index("UPDATE raw.ingest_runs SET is_current = false"), sql.index("INSERT INTO raw.ingest_runs"))
        self.assertLess(sql.index("INSERT INTO raw.ingest_source_inputs"), sql.index("COMMIT;"))


if __name__ == "__main__":
    unittest.main()
