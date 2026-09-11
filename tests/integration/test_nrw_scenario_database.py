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


@unittest.skipUnless(DATABASE_URL, "SCENARIO_TEST_DATABASE_URL is not configured")
class LegacyProposedChargerMigrationDatabaseTest(unittest.TestCase):
    """Exercise the S05 upgrade against an isolated pre-contract table."""

    legacy_id = "123e4567-e89b-12d3-a456-426614174000"

    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None
        run_id = os.environ.get("SCENARIO_TEST_RUN_ID")
        endpoint = os.environ.get("SCENARIO_TEST_ENDPOINT")
        require_disposable_database_url(DATABASE_URL, run_id=run_id, endpoint=endpoint)
        cls.psql_base, cls.psql_environment = psql_connection(
            DATABASE_URL, run_id=run_id, endpoint=endpoint
        )
        cls.psql(
            """
            DROP SCHEMA IF EXISTS publish CASCADE;
            DROP SCHEMA IF EXISTS analytics CASCADE;
            DROP SCHEMA IF EXISTS staging CASCADE;
            DROP SCHEMA IF EXISTS scenario CASCADE;
            DROP SCHEMA IF EXISTS raw CASCADE;
            CREATE EXTENSION IF NOT EXISTS postgis;
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            CREATE SCHEMA raw;
            CREATE SCHEMA scenario;
            CREATE TABLE raw.chargers (
                source_id text NOT NULL, operator text, status text,
                charger_type text, charging_points integer, power_kw numeric,
                street text, postcode text, city text, district_text text,
                bundesland text, geom geometry(Point, 4326) NOT NULL
            );
            CREATE TABLE raw.admin_regions (
                nuts_code text NOT NULL, ags text, district_name text,
                region_name text, geom geometry(MultiPolygon, 4326) NOT NULL
            );
            CREATE TABLE scenario.proposed_chargers (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                name text NOT NULL, charging_points integer NOT NULL,
                power_kw numeric NOT NULL, nuts_code text NOT NULL,
                status text NOT NULL DEFAULT 'proposed',
                created_at timestamptz NOT NULL DEFAULT now(),
                geom geometry(Point, 4326) NOT NULL
            );
            INSERT INTO scenario.proposed_chargers (
                id, name, charging_points, power_kw, nuts_code, status, geom
            ) VALUES (
                '123e4567-e89b-12d3-a456-426614174000', 'Legacy station', 2,
                44, 'DEA01', 'proposed', ST_SetSRID(ST_Point(6.5, 50.5), 4326)
            );
            ALTER TABLE scenario.proposed_chargers
                ADD COLUMN geom_25832 geometry(Point, 25832)
                GENERATED ALWAYS AS (ST_Transform(geom, 25832)) STORED;
            """
        )
        cls.psql_file(ROOT / "db/nrw_schema.sql")

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
    def psql_file(cls, path: Path) -> None:
        result = subprocess.run(
            [*cls.psql_base, "-f", str(path)],
            text=True,
            capture_output=True,
            check=False,
            env=cls.psql_environment,
        )
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout or "psql file failed")

    def test_migration_preserves_legacy_identity_aggregate_and_unknown_maximum(self) -> None:
        result = self.psql(
            """
            SELECT id, name, charging_points, power_kw,
                   max_point_power_kw IS NULL, request_id IS NULL, nuts_code, status
            FROM scenario.proposed_chargers;
            """,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            f"{self.legacy_id}|Legacy station|2|44|t|t|DEA01|proposed",
        )

    def test_migration_is_idempotent_without_backfilling_legacy_maximum(self) -> None:
        self.psql_file(ROOT / "db/nrw_schema.sql")
        result = self.psql(
            "SELECT count(*), bool_and(max_point_power_kw IS NULL), bool_and(request_id IS NULL), min(power_kw) "
            "FROM scenario.proposed_chargers;",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1|t|t|44")

    def test_migration_converts_generated_projection_without_losing_proposal(self) -> None:
        result = self.psql(
            "SELECT is_generated || '|' || "
            "ST_Equals(geom_25832, ST_Transform(geom, 25832))::text "
            "FROM information_schema.columns c "
            "JOIN scenario.proposed_chargers p ON true "
            "WHERE c.table_schema = 'scenario' "
            "  AND c.table_name = 'proposed_chargers' "
            "  AND c.column_name = 'geom_25832';",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "NEVER|true")


@unittest.skipUnless(DATABASE_URL, "SCENARIO_TEST_DATABASE_URL is not configured")
class ProposedChargerDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None
        run_id = os.environ.get("SCENARIO_TEST_RUN_ID")
        endpoint = os.environ.get("SCENARIO_TEST_ENDPOINT")
        require_disposable_database_url(DATABASE_URL, run_id=run_id, endpoint=endpoint)
        cls.psql_base, cls.psql_environment = psql_connection(
            DATABASE_URL, run_id=run_id, endpoint=endpoint
        )
        cls.psql(
            "DROP SCHEMA IF EXISTS publish CASCADE;"
            "DROP SCHEMA IF EXISTS analytics CASCADE;"
            "DROP SCHEMA IF EXISTS staging CASCADE;"
            "DROP SCHEMA IF EXISTS scenario CASCADE;"
            "DROP SCHEMA IF EXISTS raw CASCADE;"
        )
        cls.psql_file(ROOT / "db/nrw_schema.sql")
        cls.psql(
            """
            INSERT INTO raw.admin_regions (
                nuts_code, ags, district_name, region_name, geom
            ) VALUES
            (
                'DEA01', '05111', 'West District', 'Nordrhein-Westfalen',
                ST_Multi(ST_GeomFromText(
                    'POLYGON((6 50, 7 50, 7 51, 6 51, 6 50))', 4326
                ))
            ),
            (
                'DEA02', '05112', 'East District', 'Nordrhein-Westfalen',
                ST_Multi(ST_GeomFromText(
                    'POLYGON((7 50, 8 50, 8 51, 7 51, 7 50))', 4326
                ))
            );
            """
        )

    def setUp(self) -> None:
        self.psql(
            """
            DO $$
            BEGIN
                IF to_regclass('scenario.proposed_chargers') IS NOT NULL THEN
                    TRUNCATE scenario.proposed_chargers;
                END IF;
            END
            $$;
            """
        )

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
    def psql_file(cls, path: Path) -> None:
        result = subprocess.run(
            [*cls.psql_base, "-f", str(path)],
            text=True,
            capture_output=True,
            check=False,
            env=cls.psql_environment,
        )
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout or "psql file failed")

    def test_valid_insert_normalizes_name_and_assigns_covering_district(self) -> None:
        result = self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, request_id, geom
            ) VALUES (
                '  Demo Station  ', 4, 150, 75, '123e4567-e89b-12d3-a456-426614174000',
                ST_SetSRID(ST_Point(6.5, 50.5), 4326)
            )
            RETURNING name, charging_points, power_kw, max_point_power_kw, nuts_code,
                      status, id IS NOT NULL, request_id IS NOT NULL, created_at IS NOT NULL, ST_SRID(geom_25832);
            """,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "Demo Station|4|150|75|DEA01|proposed|t|t|t|25832",
        )

    def test_request_identifier_is_persisted_and_unique_for_new_proposals(self) -> None:
        request_id = "123e4567-e89b-12d3-a456-426614174000"
        self.psql(
            f"""
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, request_id, geom
            ) VALUES ('First request', 2, 22, 22, '{request_id}', ST_SetSRID(ST_Point(6.5, 50.5), 4326));
            """
        )
        generated = self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, geom
            ) VALUES ('Generated request', 2, 22, 22, ST_SetSRID(ST_Point(6.5, 50.5), 4326))
            RETURNING request_id IS NOT NULL;
            """
        )
        self.assertEqual(generated.stdout.strip(), "t")
        duplicate = self.psql(
            f"""
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, request_id, geom
            ) VALUES ('Duplicate request', 2, 22, 22, '{request_id}', ST_SetSRID(ST_Point(6.5, 50.5), 4326));
            """,
            check=False,
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("proposed_chargers_request_id_uq", duplicate.stderr)

    def test_insert_outside_nrw_is_rejected_without_persisting_a_row(self) -> None:
        result = self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, geom
            ) VALUES (
                'Outside', 2, 22, 22,
                ST_SetSRID(ST_Point(9, 54), 4326)
            );
            """,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Proposed charger must fall inside exactly one NRW district",
            result.stderr,
        )
        self.assertEqual(self.psql("SELECT COUNT(*) FROM scenario.proposed_chargers;").stdout.strip(), "0")

    def test_insert_on_shared_boundary_is_rejected_as_ambiguous(self) -> None:
        result = self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, geom
            ) VALUES (
                'Boundary', 2, 22, 22,
                ST_SetSRID(ST_Point(7, 50.5), 4326)
            );
            """,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Proposed charger must fall inside exactly one NRW district",
            result.stderr,
        )

    def test_database_rejects_invalid_name_points_and_power(self) -> None:
        invalid_rows = (
            ("'   '", "2", "22", "22", "proposed charger name"),
            ("'Bad points'", "0", "22", "22", "proposed_chargers_charging_points_check"),
            ("'Bad power'", "2", "1001", "22", "proposed_chargers_power_kw_check"),
            ("'No maximum'", "2", "22", "NULL", "requires max_point_power_kw"),
            ("'Too large maximum'", "2", "22", "50", "max_point_power_kw"),
            ("'Infinite maximum'", "2", "22", "'Infinity'", "max_point_power_kw"),
        )
        for name, points, power, maximum, expected_error in invalid_rows:
            with self.subTest(expected_error=expected_error):
                result = self.psql(
                    f"""
                    INSERT INTO scenario.proposed_chargers (
                        name, charging_points, power_kw, max_point_power_kw, geom
                    ) VALUES (
                        {name}, {points}, {power}, {maximum},
                        ST_SetSRID(ST_Point(6.5, 50.5), 4326)
                    );
                    """,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error.lower(), result.stderr.lower())

    def test_database_rejects_names_longer_than_120_characters(self) -> None:
        result = self.psql(
            f"""
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, geom
            ) VALUES (
                '{'x' * 121}', 2, 22, 22,
                ST_SetSRID(ST_Point(6.5, 50.5), 4326)
            );
            """,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("proposed_chargers_name_check", result.stderr)

    def test_database_rejects_out_of_range_coordinates_explicitly(self) -> None:
        result = self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, geom
            ) VALUES (
                'Invalid coordinate', 2, 22, 22,
                ST_SetSRID(ST_Point(181, 50.5), 4326)
            );
            """,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("finite EPSG:4326 coordinates", result.stderr)

    def test_geometry_update_reassigns_the_district(self) -> None:
        insert = self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, geom
            ) VALUES (
                'Move me', 2, 50, 50,
                ST_SetSRID(ST_Point(6.5, 50.5), 4326)
            ) RETURNING id;
            """,
            check=False,
        )
        self.assertEqual(insert.returncode, 0, insert.stderr)
        station_id = insert.stdout.strip()

        result = self.psql(
            f"""
            UPDATE scenario.proposed_chargers
            SET geom = ST_SetSRID(ST_Point(7.5, 50.5), 4326)
            WHERE id = '{station_id}'
            RETURNING nuts_code;
            """
        )

        self.assertEqual(result.stdout.strip(), "DEA02")


if __name__ == "__main__":
    unittest.main()
