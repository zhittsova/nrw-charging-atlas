"""End-to-end transaction boundaries for the three-document charger refresh."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from run_postgis_tests import psql_connection, require_disposable_database_url  # noqa: E402

import load_nrw_postgis as loader  # noqa: E402


DATABASE_URL = os.environ.get("SCENARIO_TEST_DATABASE_URL")


def region() -> dict:
    return {
        "type": "Feature",
        "properties": {
            "nuts_code": "DEA01",
            "ags": "05111000",
            "district_name": "Fixture district",
            "region": "Nordrhein-Westfalen",
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[6.7, 51.1], [6.9, 51.1], [6.9, 51.3], [6.7, 51.3], [6.7, 51.1]]
            ],
        },
    }


def charger(source_id: str) -> dict:
    return {
        "type": "Feature",
        "properties": {"id": source_id, "charging_points": 2, "power_kw": 22.0},
        "geometry": {"type": "Point", "coordinates": [6.78, 51.23]},
    }


@unittest.skipUnless(DATABASE_URL, "SCENARIO_TEST_DATABASE_URL is not configured")
class RefreshLifecycleTest(unittest.TestCase):
    """A failed candidate leaves the prior coherent publication untouched."""

    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None
        run_id = os.environ.get("SCENARIO_TEST_RUN_ID")
        endpoint = os.environ.get("SCENARIO_TEST_ENDPOINT")
        require_disposable_database_url(DATABASE_URL, run_id=run_id, endpoint=endpoint)
        cls.psql_base, cls.psql_environment = psql_connection(
            DATABASE_URL, run_id=run_id, endpoint=endpoint
        )
        cls.schema_sql = (ROOT / "db/nrw_schema.sql").read_text(encoding="utf-8")
        cls.analytics_sql = (ROOT / "db/nrw_analytics.sql").read_text(encoding="utf-8")
        cls.reader_role = f"nrw_refresh_reader_{run_id}"

    @classmethod
    def psql(cls, sql: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            cls.psql_base,
            input=sql,
            text=True,
            capture_output=True,
            check=False,
            env=cls.psql_environment,
        )
        if check and result.returncode:
            raise RuntimeError(result.stderr or result.stdout or "psql failed")
        return result

    @staticmethod
    def import_sql(*source_ids: str) -> str:
        return loader.build_import_script(
            [loader.admin_row(region())],
            [loader.charger_row(charger(source_id)) for source_id in source_ids],
        )

    @staticmethod
    def population_import() -> str:
        return """BEGIN;
CREATE TEMP TABLE import_population (
  district_code text, nuts_code text, ags text, population integer,
  reference_year integer, source text
) ON COMMIT DROP;
INSERT INTO import_population VALUES ('DEA01', 'DEA01', '05111000', 100000, 2024, 'fixture');
TRUNCATE raw.population;
INSERT INTO raw.population
SELECT district_code, nuts_code, ags, population, reference_year, source
FROM import_population;
COMMIT;
"""

    def setUp(self) -> None:
        self.psql(
            "DROP SCHEMA IF EXISTS publish CASCADE;"
            "DROP SCHEMA IF EXISTS analytics CASCADE;"
            "DROP SCHEMA IF EXISTS staging CASCADE;"
            "DROP SCHEMA IF EXISTS scenario CASCADE;"
            "DROP SCHEMA IF EXISTS raw CASCADE;"
        )
        assert DATABASE_URL is not None
        loader.run_refresh(
            DATABASE_URL,
            schema_sql=self.schema_sql,
            import_sql=(self.import_sql("before-refresh"), self.population_import()),
            analytics_sql=self.analytics_sql,
        )
        self.psql(
            "INSERT INTO scenario.proposed_chargers "
            "(name, charging_points, power_kw, max_point_power_kw, geom) VALUES "
            "('preserved proposal', 2, 22, 22, "
            "ST_SetSRID(ST_MakePoint(6.85, 51.15), 4326));"
        )
        self.assertEqual(
            self.value(
                "SELECT chargers_total || '|' || population "
                "FROM analytics.nrw_district_metrics WHERE nuts_code = 'DEA01';"
            ),
            "1|100000",
        )
        self.psql(
            f"CREATE ROLE {self.reader_role}; "
            f"GRANT USAGE ON SCHEMA publish TO {self.reader_role}; "
            f"GRANT SELECT ON publish.nrw_district_priority TO {self.reader_role};"
        )

    def tearDown(self) -> None:
        self.psql(f"DROP OWNED BY {self.reader_role}; DROP ROLE {self.reader_role};")

    def value(self, sql: str) -> str:
        result = self.psql(sql, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def reader_value(self, sql: str) -> str:
        return self.value(f"SET ROLE {self.reader_role}; {sql} RESET ROLE;")

    @staticmethod
    def fail_before_commit(sql: str, mutation: str = "") -> str:
        return sql.replace(
            "\nCOMMIT;",
            f"\n{mutation}\nSELECT 1 / 0;\nCOMMIT;",
            1,
        )

    def test_schema_failure_stops_before_import_and_preserves_the_prior_refresh(self) -> None:
        assert DATABASE_URL is not None
        with self.assertRaisesRegex(RuntimeError, "division by zero"):
            loader.run_refresh(
                DATABASE_URL,
                schema_sql=self.fail_before_commit(
                    self.schema_sql,
                    "COMMENT ON SCHEMA raw IS 'failed schema marker';",
                ),
                import_sql=self.import_sql("after-refresh"),
                analytics_sql=self.analytics_sql,
            )

        self.assertEqual(self.value("SELECT source_id FROM raw.chargers;"), "before-refresh")
        self.assertEqual(
            self.value("SELECT COALESCE(obj_description('raw'::regnamespace, 'pg_namespace'), '');"),
            "",
        )
        self.assertEqual(
            self.value("SELECT to_regclass('publish.nrw_district_priority') IS NOT NULL;"), "t"
        )
        self.assertEqual(self.value("SELECT count(*) FROM scenario.proposed_chargers;"), "1")

    def test_import_failure_rolls_back_its_changes_and_skips_analytics(self) -> None:
        assert DATABASE_URL is not None
        with self.assertRaisesRegex(RuntimeError, "division by zero"):
            loader.run_refresh(
                DATABASE_URL,
                schema_sql=self.schema_sql,
                import_sql=self.fail_before_commit(self.import_sql("after-refresh")),
                analytics_sql=self.analytics_sql,
            )

        self.assertEqual(self.value("SELECT source_id FROM raw.chargers;"), "before-refresh")
        self.assertEqual(
            self.value("SELECT to_regclass('publish.nrw_district_priority') IS NOT NULL;"), "t"
        )
        self.assertEqual(self.value("SELECT count(*) FROM scenario.proposed_chargers;"), "1")
        self.assertEqual(
            self.reader_value("SELECT count(*) FROM publish.nrw_district_priority;"), "1"
        )

    def test_analytics_failure_rolls_back_every_candidate_change(self) -> None:
        assert DATABASE_URL is not None

        with self.assertRaisesRegex(RuntimeError, "division by zero"):
            loader.run_refresh(
                DATABASE_URL,
                schema_sql=self.schema_sql,
                import_sql=self.import_sql("after-refresh"),
                analytics_sql=self.fail_before_commit(
                    self.analytics_sql,
                    "COMMENT ON VIEW publish.nrw_district_priority IS 'failed analytics marker';",
                ),
            )

        self.assertEqual(self.value("SELECT source_id FROM raw.chargers;"), "before-refresh")
        self.assertEqual(
            self.value(
                "SELECT COALESCE("
                "obj_description('publish.nrw_district_priority'::regclass, 'pg_class'), '');"
            ),
            "",
        )
        self.assertEqual(self.value("SELECT count(*) FROM scenario.proposed_chargers;"), "1")
        self.assertEqual(
            self.reader_value("SELECT count(*) FROM publish.nrw_district_priority;"), "1"
        )

    def test_repeated_success_is_idempotent_and_preserves_proposals(self) -> None:
        assert DATABASE_URL is not None
        loader.run_refresh(
            DATABASE_URL,
            schema_sql=self.schema_sql,
            import_sql=(
                self.import_sql("before-refresh", "after-refresh"),
                self.population_import(),
            ),
            analytics_sql=self.analytics_sql,
        )

        self.assertEqual(self.value("SELECT count(*) FROM raw.chargers;"), "2")
        self.assertEqual(
            self.value(
                "SELECT chargers_total || '|' || population "
                "FROM analytics.nrw_district_metrics WHERE nuts_code = 'DEA01';"
            ),
            "2|100000",
        )
        self.assertEqual(self.value("SELECT count(*) FROM scenario.proposed_chargers;"), "1")

        loader.run_refresh(
            DATABASE_URL,
            schema_sql=self.schema_sql,
            import_sql=(
                self.import_sql("before-refresh", "after-refresh"),
                self.population_import(),
            ),
            analytics_sql=self.analytics_sql,
        )
        self.assertEqual(
            self.value(
                "SELECT chargers_total || '|' || population "
                "FROM analytics.nrw_district_metrics WHERE nuts_code = 'DEA01';"
            ),
            "2|100000",
        )
        self.assertEqual(self.value("SELECT count(*) FROM scenario.proposed_chargers;"), "1")


if __name__ == "__main__":
    unittest.main()
