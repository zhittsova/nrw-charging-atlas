from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ.get("SCENARIO_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "SCENARIO_TEST_DATABASE_URL is not configured")
class ProposedChargerDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
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
        return subprocess.run(
            ["psql", DATABASE_URL, "-v", "ON_ERROR_STOP=1", "-qAt"],
            input=sql,
            text=True,
            capture_output=True,
            check=check,
        )

    @classmethod
    def psql_file(cls, path: Path) -> None:
        subprocess.run(
            ["psql", DATABASE_URL, "-v", "ON_ERROR_STOP=1", "-f", str(path)],
            text=True,
            capture_output=True,
            check=True,
        )

    def test_valid_insert_normalizes_name_and_assigns_covering_district(self) -> None:
        result = self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, geom
            ) VALUES (
                '  Demo Station  ', 4, 150,
                ST_SetSRID(ST_Point(6.5, 50.5), 4326)
            )
            RETURNING name, charging_points, power_kw, nuts_code,
                      status, id IS NOT NULL, created_at IS NOT NULL;
            """,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "Demo Station|4|150|DEA01|proposed|t|t",
        )

    def test_insert_outside_nrw_is_rejected_without_persisting_a_row(self) -> None:
        result = self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, geom
            ) VALUES (
                'Outside', 2, 22,
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
                name, charging_points, power_kw, geom
            ) VALUES (
                'Boundary', 2, 22,
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
            ("'   '", "2", "22", "proposed charger name"),
            ("'Bad points'", "0", "22", "proposed_chargers_charging_points_check"),
            ("'Bad power'", "2", "1001", "proposed_chargers_power_kw_check"),
        )
        for name, points, power, expected_error in invalid_rows:
            with self.subTest(expected_error=expected_error):
                result = self.psql(
                    f"""
                    INSERT INTO scenario.proposed_chargers (
                        name, charging_points, power_kw, geom
                    ) VALUES (
                        {name}, {points}, {power},
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
                name, charging_points, power_kw, geom
            ) VALUES (
                '{'x' * 121}', 2, 22,
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
                name, charging_points, power_kw, geom
            ) VALUES (
                'Invalid coordinate', 2, 22,
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
                name, charging_points, power_kw, geom
            ) VALUES (
                'Move me', 2, 50,
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
