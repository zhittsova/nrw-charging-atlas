"""Historical upgrade regressions for S06-R01.

The published views fix an explicit public field order. A database upgraded
from before ``max_point_power_kw`` existed carries that column wherever
``ALTER TABLE ADD COLUMN`` appended it, and ``CREATE OR REPLACE VIEW`` cannot
reorder an existing view's columns. These tests therefore start from a faithful
reconstruction of the older published layout rather than from a fresh schema,
which is the coverage the S06 suite was missing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from run_postgis_tests import psql_connection, require_disposable_database_url  # noqa: E402


DATABASE_URL = os.environ.get("SCENARIO_TEST_DATABASE_URL")

# The public field contract the current schema publishes.
PUBLISHED_CHARGER_COLUMNS = [
    "source_id",
    "operator",
    "status",
    "charger_type",
    "charging_points",
    "power_kw",
    "max_point_power_kw",
    "street",
    "postcode",
    "city",
    "district_text",
    "bundesland",
    "geom",
]

# raw.chargers as it stood before the maximum-point-power contract, followed by
# the additive ALTER that S05 applied. The resulting publish view lists
# max_point_power_kw last, exactly like an upgraded installation.
PRE_S05_LAYOUT_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA raw;
CREATE SCHEMA staging;
CREATE SCHEMA analytics;
CREATE SCHEMA publish;
CREATE SCHEMA scenario;

CREATE TABLE raw.chargers (
    source_id text NOT NULL,
    operator text,
    status text,
    charger_type text,
    charging_points integer,
    power_kw numeric,
    street text,
    postcode text,
    city text,
    district_text text,
    bundesland text,
    geom geometry(Point, 4326) NOT NULL
);
CREATE UNIQUE INDEX raw_chargers_source_id_uq ON raw.chargers (source_id);

CREATE TABLE raw.admin_regions (
    nuts_code text NOT NULL,
    ags text,
    district_name text,
    region_name text,
    geom geometry(MultiPolygon, 4326) NOT NULL
);
CREATE UNIQUE INDEX raw_admin_regions_nuts_code_uq ON raw.admin_regions (nuts_code);

CREATE TABLE scenario.proposed_chargers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    charging_points integer NOT NULL,
    power_kw numeric NOT NULL,
    nuts_code text NOT NULL,
    status text NOT NULL DEFAULT 'proposed',
    created_at timestamptz NOT NULL DEFAULT now(),
    geom geometry(Point, 4326) NOT NULL
);

CREATE TABLE raw.roads (
    osm_id text, road_class text, name text, road_number text,
    traffic_total numeric, traffic_light numeric, traffic_heavy numeric,
    source text, geom geometry(LineString, 4326)
);
CREATE TABLE raw.grid_infrastructure (
    source_id text, asset_type text, voltage text, name text,
    geom geometry(Geometry, 4326)
);
-- name and operator arrived through ALTER here too, so the older published
-- renewable layer lists them after geom.
CREATE TABLE raw.renewable_assets (
    source_id text, asset_type text, technology text, capacity_mw numeric,
    status text, geom geometry(Point, 4326)
);
ALTER TABLE raw.renewable_assets ADD COLUMN name text;
ALTER TABLE raw.renewable_assets ADD COLUMN operator text;

CREATE TABLE raw.population (
    district_code text, nuts_code text, ags text, population integer,
    reference_year integer, source text
);

-- The S05 upgrade appended the measured connector maximum.
ALTER TABLE raw.chargers ADD COLUMN max_point_power_kw numeric;
ALTER TABLE scenario.proposed_chargers ADD COLUMN max_point_power_kw numeric;

CREATE VIEW staging.nrw_boundary AS
SELECT ST_Union(geom)::geometry(MultiPolygon, 4326) AS geom
FROM raw.admin_regions WHERE nuts_code LIKE 'DEA%';
CREATE VIEW staging.nrw_chargers AS
SELECT c.* FROM raw.chargers c JOIN staging.nrw_boundary b ON ST_Intersects(c.geom, b.geom);
CREATE VIEW staging.nrw_renewables AS
SELECT a.* FROM raw.renewable_assets a;

CREATE VIEW publish.nrw_chargers AS SELECT * FROM staging.nrw_chargers;
CREATE VIEW publish.nrw_renewable_potential AS SELECT * FROM staging.nrw_renewables;
"""

FIXTURE_DATA_SQL = """
INSERT INTO raw.admin_regions (nuts_code, ags, district_name, region_name, geom) VALUES
    ('DEA01', '05111', 'Fixture', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((6 50,7 50,7 51,6 51,6 50))', 4326)));
INSERT INTO raw.population (district_code, nuts_code, ags, population, reference_year, source)
    VALUES ('05111', 'DEA01', '05111', 100000, 2024, 'fixture');
INSERT INTO raw.chargers (
    source_id, operator, status, charger_type, charging_points, power_kw,
    street, postcode, city, district_text, bundesland, geom
) VALUES (
    'legacy-official', 'Legacy operator', 'active', 'normal', 2, 44,
    'Teststrasse 1', '40000', 'Testort', 'Fixture', 'Nordrhein-Westfalen',
    ST_SetSRID(ST_Point(6.5, 50.5), 4326)
);
INSERT INTO scenario.proposed_chargers (id, name, charging_points, power_kw, nuts_code, geom)
VALUES (
    '123e4567-e89b-12d3-a456-426614174000', 'Legacy proposal', 2, 44, 'DEA01',
    ST_SetSRID(ST_Point(6.6, 50.6), 4326)
);
"""


@unittest.skipUnless(DATABASE_URL, "SCENARIO_TEST_DATABASE_URL is not configured")
class HistoricalPublishedViewUpgradeTest(unittest.TestCase):
    """Upgrading an older installation must not need a reset to succeed."""

    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None
        cls.run_id = os.environ.get("SCENARIO_TEST_RUN_ID") or ""
        endpoint = os.environ.get("SCENARIO_TEST_ENDPOINT")
        require_disposable_database_url(DATABASE_URL, run_id=cls.run_id, endpoint=endpoint)
        cls.psql_base, cls.psql_environment = psql_connection(
            DATABASE_URL, run_id=cls.run_id, endpoint=endpoint
        )
        cls.reader_role = f"nrw_publish_reader_{cls.run_id}"

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

    @classmethod
    def psql_file(cls, path: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return cls.psql(path.read_text(encoding="utf-8"), check=check)

    def setUp(self) -> None:
        self.psql(
            "DROP SCHEMA IF EXISTS publish CASCADE;"
            "DROP SCHEMA IF EXISTS analytics CASCADE;"
            "DROP SCHEMA IF EXISTS staging CASCADE;"
            "DROP SCHEMA IF EXISTS scenario CASCADE;"
            "DROP SCHEMA IF EXISTS raw CASCADE;"
            f"DROP ROLE IF EXISTS {self.reader_role};"
        )
        self.psql(PRE_S05_LAYOUT_SQL)
        self.psql(FIXTURE_DATA_SQL)
        self.addCleanup(
            lambda: self.psql(f"DROP ROLE IF EXISTS {self.reader_role};", check=False)
        )

    def rows(self, sql: str) -> list[list[str]]:
        result = self.psql(sql, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line.split("|") for line in result.stdout.strip().splitlines() if line]

    def value(self, sql: str) -> str:
        rows = self.rows(sql)
        self.assertEqual(len(rows), 1, rows)
        return rows[0][0]

    def published_columns(self, view: str) -> list[str]:
        return [
            row[0]
            for row in self.rows(
                "SELECT column_name FROM information_schema.columns"
                f" WHERE table_schema = 'publish' AND table_name = '{view}'"
                " ORDER BY ordinal_position;"
            )
        ]

    def apply_current_schema(self) -> None:
        self.psql_file(ROOT / "db/nrw_schema.sql")
        self.psql_file(ROOT / "db/nrw_analytics.sql")

    def test_the_fixture_reproduces_the_older_published_field_order(self) -> None:
        """Without this the rest of the class would silently test a fresh schema."""
        columns = self.published_columns("nrw_chargers")
        self.assertEqual(columns[-1], "max_point_power_kw")
        self.assertNotEqual(columns, PUBLISHED_CHARGER_COLUMNS)
        self.assertEqual(self.published_columns("nrw_renewable_potential")[-1], "operator")

    def test_upgrading_an_older_installation_succeeds_and_repeats(self) -> None:
        self.apply_current_schema()
        self.assertEqual(self.published_columns("nrw_chargers"), PUBLISHED_CHARGER_COLUMNS)

        # A second application must be a no-op rather than another replacement.
        self.apply_current_schema()
        self.assertEqual(self.published_columns("nrw_chargers"), PUBLISHED_CHARGER_COLUMNS)
        self.assertEqual(
            self.published_columns("nrw_renewable_potential"),
            [
                "source_id",
                "name",
                "operator",
                "asset_type",
                "technology",
                "capacity_mw",
                "status",
                "geom",
            ],
        )

    def test_the_upgrade_preserves_raw_and_proposal_rows(self) -> None:
        self.apply_current_schema()

        official = self.rows(
            "SELECT source_id, operator, power_kw, max_point_power_kw IS NULL,"
            "       street, charging_points"
            " FROM publish.nrw_chargers WHERE source_id = 'legacy-official';"
        )[0]
        self.assertEqual(
            official,
            ["legacy-official", "Legacy operator", "44", "t", "Teststrasse 1", "2"],
        )

        proposal = self.rows(
            "SELECT id, name, power_kw, max_point_power_kw IS NULL, request_id IS NULL, nuts_code"
            " FROM scenario.proposed_chargers;"
        )[0]
        self.assertEqual(
            proposal,
            ["123e4567-e89b-12d3-a456-426614174000", "Legacy proposal", "44", "t", "t", "DEA01"],
        )

    def test_the_upgraded_published_layer_exposes_one_epsg_4326_geometry(self) -> None:
        self.apply_current_schema()
        for view in ("nrw_chargers", "nrw_grid_readiness", "nrw_accessibility",
                     "nrw_renewable_potential"):
            with self.subTest(view=view):
                geometry_columns = self.rows(
                    "SELECT column_name FROM information_schema.columns"
                    f" WHERE table_schema = 'publish' AND table_name = '{view}'"
                    "   AND udt_name = 'geometry' ORDER BY ordinal_position;"
                )
                self.assertEqual([row[0] for row in geometry_columns], ["geom"])
        self.assertEqual(
            self.value("SELECT DISTINCT ST_SRID(geom)::text FROM publish.nrw_chargers;"),
            "4326",
        )

    def test_the_upgrade_preserves_published_read_privileges(self) -> None:
        self.psql(
            f"CREATE ROLE {self.reader_role} NOLOGIN;"
            f"GRANT USAGE ON SCHEMA publish TO {self.reader_role};"
            f"GRANT SELECT ON publish.nrw_chargers TO {self.reader_role};"
            f"GRANT SELECT ON publish.nrw_renewable_potential TO {self.reader_role};"
        )
        self.assertEqual(
            self.value(
                "SELECT has_table_privilege("
                f"'{self.reader_role}', 'publish.nrw_chargers', 'SELECT')::text;"
            ),
            "true",
        )

        self.apply_current_schema()

        for view in ("nrw_chargers", "nrw_renewable_potential"):
            with self.subTest(view=view):
                self.assertEqual(
                    self.value(
                        "SELECT has_table_privilege("
                        f"'{self.reader_role}', 'publish.{view}', 'SELECT')::text;"
                    ),
                    "true",
                )

    def test_a_dependent_object_blocks_the_replacement_instead_of_being_dropped(self) -> None:
        """The replacement must never CASCADE away something it did not create."""
        self.psql("CREATE VIEW publish.local_charger_extract AS"
                  " SELECT source_id, power_kw FROM publish.nrw_chargers;")

        result = self.psql_file(ROOT / "db/nrw_schema.sql", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("depends on", result.stderr)
        self.assertEqual(
            self.value(
                "SELECT COUNT(*)::text FROM information_schema.views"
                " WHERE table_schema = 'publish' AND table_name = 'local_charger_extract';"
            ),
            "1",
        )

    def test_a_failing_upgrade_leaves_the_previous_analytics_in_place(self) -> None:
        """The analytics drop is CASCADE, so a partial run must roll back."""
        self.apply_current_schema()
        before = self.value(
            "SELECT COUNT(*)::text FROM publish.nrw_ev_baseline_metrics;"
        )
        self.assertEqual(before, "1")

        schema = (ROOT / "db/nrw_schema.sql").read_text(encoding="utf-8")
        self.assertTrue(
            schema.lstrip().startswith("--") and "\nBEGIN;\n" in schema
            and schema.rstrip().endswith("COMMIT;"),
            "db/nrw_schema.sql must apply as one transaction for this guarantee",
        )
        broken = schema.replace("\nCOMMIT;", "\nSELECT 1 / 0;\n\nCOMMIT;")
        result = self.psql(broken, check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("division by zero", result.stderr)
        self.assertEqual(
            self.value("SELECT COUNT(*)::text FROM publish.nrw_ev_baseline_metrics;"),
            before,
            "a failed upgrade destroyed the published analytics",
        )
        self.assertEqual(
            self.value(
                "SELECT COUNT(*)::text FROM publish.nrw_chargers"
                " WHERE source_id = 'legacy-official';"
            ),
            "1",
        )


if __name__ == "__main__":
    unittest.main()
