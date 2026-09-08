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
class ScenarioAnalyticsDatabaseTest(unittest.TestCase):
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
                ('DEA01', '05111', 'West', 'Nordrhein-Westfalen',
                 ST_Multi(ST_GeomFromText('POLYGON((6 50,7 50,7 51,6 51,6 50))', 4326))),
                ('DEA02', '05112', 'Centre', 'Nordrhein-Westfalen',
                 ST_Multi(ST_GeomFromText('POLYGON((7 50,8 50,8 51,7 51,7 50))', 4326))),
                ('DEA03', '05113', 'East', 'Nordrhein-Westfalen',
                 ST_Multi(ST_GeomFromText('POLYGON((8 50,9 50,9 51,8 51,8 50))', 4326)));

            INSERT INTO raw.population (
                district_code, nuts_code, ags, population, reference_year, source
            ) VALUES
                ('05111', 'DEA01', '05111', 100000, 2024, 'fixture'),
                ('05112', 'DEA02', '05112', 200000, 2024, 'fixture'),
                ('05113', 'DEA03', '05113', 300000, 2024, 'fixture');

            INSERT INTO raw.chargers (
                source_id, operator, status, charger_type, charging_points,
                power_kw, bundesland, geom
            ) VALUES
                ('c-1', 'Fixture', 'active', 'normal', 1, 22, 'Nordrhein-Westfalen',
                 ST_SetSRID(ST_Point(6.2, 50.2), 4326)),
                ('c-2', 'Fixture', 'active', 'fast', 2, 75, 'Nordrhein-Westfalen',
                 ST_SetSRID(ST_Point(7.2, 50.2), 4326)),
                ('c-3', 'Fixture', 'active', 'fast', 3, 150, 'Nordrhein-Westfalen',
                 ST_SetSRID(ST_Point(8.2, 50.2), 4326)),
                ('c-4', 'Fixture', 'active', 'normal', 2, 22, 'Nordrhein-Westfalen',
                 ST_SetSRID(ST_Point(8.7, 50.7), 4326));

            INSERT INTO raw.roads (
                osm_id, road_class, traffic_total, source, geom
            ) VALUES
                ('r-1', 'primary', 10000, 'fixture',
                 ST_GeomFromText('LINESTRING(6.1 50.5,6.9 50.5)', 4326)),
                ('r-2', 'primary', 20000, 'fixture',
                 ST_GeomFromText('LINESTRING(7.1 50.5,7.9 50.5)', 4326)),
                ('r-3', 'primary', 30000, 'fixture',
                 ST_GeomFromText('LINESTRING(8.1 50.5,8.9 50.5)', 4326));

            INSERT INTO raw.grid_infrastructure (
                source_id, asset_type, voltage, name, geom
            ) VALUES
                ('g-l1', 'line', '110 kV', 'Line 1',
                 ST_GeomFromText('LINESTRING(6.1 50.4,6.9 50.4)', 4326)),
                ('g-l2', 'line', '220 kV', 'Line 2',
                 ST_GeomFromText('LINESTRING(7.1 50.4,7.9 50.4)', 4326)),
                ('g-l3', 'line', '380 kV', 'Line 3',
                 ST_GeomFromText('LINESTRING(8.1 50.4,8.9 50.4)', 4326)),
                ('g-s1', 'substation', '110 kV', 'Substation 1',
                 ST_SetSRID(ST_Point(6.3, 50.5), 4326)),
                ('g-s2', 'substation', '220 kV', 'Substation 2',
                 ST_SetSRID(ST_Point(7.5, 50.5), 4326)),
                ('g-s3', 'substation', '380 kV', 'Substation 3',
                 ST_SetSRID(ST_Point(8.7, 50.5), 4326));

            INSERT INTO raw.renewable_assets (
                source_id, asset_type, technology, capacity_mw, status, geom
            ) VALUES
                ('re-1', 'generator', 'solar', 10, 'In Betrieb',
                 ST_SetSRID(ST_Point(6.4, 50.4), 4326)),
                ('re-2', 'generator', 'wind', 20, 'In Betrieb',
                 ST_SetSRID(ST_Point(7.4, 50.4), 4326)),
                ('re-3', 'generator', 'wind', 40, 'In Betrieb',
                 ST_SetSRID(ST_Point(8.4, 50.4), 4326));

            REFRESH MATERIALIZED VIEW analytics.nrw_district_metrics;
            """
        )
        cls.psql_file(ROOT / "db/nrw_analytics.sql")

    def setUp(self) -> None:
        self.psql("TRUNCATE scenario.proposed_chargers;")

    @classmethod
    def psql(
        cls, sql: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
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
        result = subprocess.run(
            [*cls.psql_base, "-f", str(path)],
            text=True,
            capture_output=True,
            check=False,
            env=cls.psql_environment,
        )
        if check and result.returncode:
            raise RuntimeError(result.stderr or result.stdout or "psql file failed")
        return result

    def test_baseline_and_empty_scenario_are_identical(self) -> None:
        result = self.psql(
            """
            SELECT COUNT(*),
                   bool_and(b.ev_readiness_score = s.scenario_ev_readiness_score),
                   bool_and(s.ev_readiness_score_delta = 0),
                   bool_and(s.chargers_total_delta = 0)
            FROM publish.nrw_ev_baseline_metrics b
            JOIN publish.nrw_ev_scenario_metrics s USING (nuts_code);
            """,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "3|t|t|t")

    def test_proposed_charger_changes_only_its_district_charger_metrics(self) -> None:
        self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, geom
            ) VALUES (
                'Professor scenario', 4, 150,
                ST_SetSRID(ST_Point(6.6, 50.6), 4326)
            );
            """
        )
        result = self.psql(
            """
            SELECT nuts_code, chargers_total_delta, charging_points_total_delta,
                   fast_chargers_total_delta,
                   scenario_ev_readiness_score >= baseline_ev_readiness_score
            FROM publish.nrw_ev_scenario_metrics
            ORDER BY nuts_code;
            """,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip().splitlines(),
            ["DEA01|1|4|1|t", "DEA02|0|0|0|t", "DEA03|0|0|0|t"],
        )

    def test_scenario_uses_unchanged_baseline_normalization_bounds(self) -> None:
        before = self.psql(
            "SELECT row_to_json(b)::text FROM analytics.nrw_ev_baseline_bounds b;"
        ).stdout.strip()
        self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, geom
            ) VALUES (
                'Large scenario', 100, 1000,
                ST_SetSRID(ST_Point(6.6, 50.6), 4326)
            );
            """
        )
        after = self.psql(
            "SELECT row_to_json(b)::text FROM analytics.nrw_ev_baseline_bounds b;"
        ).stdout.strip()

        self.assertEqual(before, after)

    def test_investment_priority_uses_agreed_formula_and_descending_rank(self) -> None:
        result = self.psql(
            """
            SELECT bool_and(
                       investment_priority_score = ROUND(
                           0.60 * charger_deficit_score
                           + 0.40 * infrastructure_opportunity_score,
                           1
                       )
                   ),
                   MIN(priority_rank), MAX(priority_rank),
                   bool_and(data_quality_flag = 'complete_proxy_inputs')
            FROM publish.nrw_ev_baseline_metrics;
            """,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "t|1|3|t")


if __name__ == "__main__":
    unittest.main()
