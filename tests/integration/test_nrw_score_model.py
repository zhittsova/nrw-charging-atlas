"""The canonical SQL score model, checked against an independent catalogue.

Every expectation here comes from `tests/fixtures/score_model_cases.py`, which
was derived from the written contract rather than from `db/nrw_analytics.sql`.
The SQL and the catalogue therefore have to agree by accident of both being
right, not by one having been copied from the other.

The fixture is deliberately compact and each district exists to make one rule
visible:

* `DEA01`, `DEA02` -- ordinary districts that populate the percentile bounds.
* `DEA03`, `DEA04` -- mirror images about the UTM zone 32 central meridian
  (9 degrees east) carrying identical content, so their measurements, scores and
  investment-priority rank are genuinely tied.
* `DEA05` -- no mapped grid line at all, so the voltage component is
  unavailable, the grid proxy is unavailable with it, and every composite built
  on the proxy is unavailable rather than re-weighted.
* `DEA06` -- no charging station at all, so its charging-point density is a
  measured zero and still scores, unlike an absent measurement.

Every district has exactly one operating renewable technology, so the
technology-diversity bounds are equal and the equal-bound rule has to return 50.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests" / "fixtures"))
from run_postgis_tests import psql_connection, require_disposable_database_url  # noqa: E402

from score_model_cases import (  # noqa: E402
    INDICATOR_WEIGHTS,
    bounds_5_95,
    normalize_5_95,
    rank_desc_nulls_last,
    round_half_up,
    weighted_composite,
)


DATABASE_URL = os.environ.get("SCENARIO_TEST_DATABASE_URL")

FIXTURE_SQL = """
INSERT INTO raw.admin_regions (nuts_code, ags, district_name, region_name, geom) VALUES
    ('DEA01', '05111', 'West', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((6 50,7 50,7 51,6 51,6 50))', 4326))),
    ('DEA02', '05112', 'Centre', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((7 50,8 50,8 51,7 51,7 50))', 4326))),
    ('DEA03', '05113', 'Mirror West', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((8 50,9 50,9 51,8 51,8 50))', 4326))),
    ('DEA04', '05114', 'Mirror East', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((9 50,10 50,10 51,9 51,9 50))', 4326))),
    ('DEA05', '05115', 'No Grid Lines', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((10 50,11 50,11 51,10 51,10 50))', 4326))),
    ('DEA06', '05116', 'No Chargers', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((11 50,12 50,12 51,11 51,11 50))', 4326)));

INSERT INTO raw.population (district_code, nuts_code, ags, population, reference_year, source) VALUES
    ('05111', 'DEA01', '05111', 100000, 2024, 'fixture'),
    ('05112', 'DEA02', '05112', 150000, 2024, 'fixture'),
    ('05113', 'DEA03', '05113', 200000, 2024, 'fixture'),
    ('05114', 'DEA04', '05114', 200000, 2024, 'fixture'),
    ('05115', 'DEA05', '05115', 250000, 2024, 'fixture'),
    ('05116', 'DEA06', '05116', 300000, 2024, 'fixture');

-- DEA03 and DEA04 carry mirrored stations with identical point counts, so the
-- readiness density, the station density and the accessibility distance are the
-- same on both sides of the central meridian.
INSERT INTO raw.chargers (
    source_id, operator, status, charger_type, charging_points,
    power_kw, max_point_power_kw, bundesland, geom
) VALUES
    ('c-1', 'Fixture', 'active', 'normal', 2, 44, 22, 'Nordrhein-Westfalen',
     ST_SetSRID(ST_Point(6.5, 50.5), 4326)),
    ('c-2', 'Fixture', 'active', 'fast', 3, 150, 150, 'Nordrhein-Westfalen',
     ST_SetSRID(ST_Point(7.5, 50.5), 4326)),
    ('c-3', 'Fixture', 'active', 'fast', 4, 300, 150, 'Nordrhein-Westfalen',
     ST_SetSRID(ST_Point(8.4, 50.4), 4326)),
    ('c-4', 'Fixture', 'active', 'fast', 4, 300, 150, 'Nordrhein-Westfalen',
     ST_SetSRID(ST_Point(9.6, 50.4), 4326)),
    ('c-5', 'Fixture', 'active', 'unknown', 1, 150, NULL, 'Nordrhein-Westfalen',
     ST_SetSRID(ST_Point(10.5, 50.5), 4326));

INSERT INTO raw.roads (osm_id, road_class, traffic_total, source, geom) VALUES
    ('r-1', 'B', 10000, 'fixture', ST_GeomFromText('LINESTRING(6.1 50.5,6.9 50.5)', 4326)),
    ('r-2', 'B', 20000, 'fixture', ST_GeomFromText('LINESTRING(7.1 50.5,7.9 50.5)', 4326)),
    ('r-3', 'B', 30000, 'fixture', ST_GeomFromText('LINESTRING(8.1 50.5,8.9 50.5)', 4326)),
    ('r-4', 'B', 30000, 'fixture', ST_GeomFromText('LINESTRING(9.1 50.5,9.9 50.5)', 4326)),
    ('r-5', 'B', 15000, 'fixture', ST_GeomFromText('LINESTRING(10.1 50.5,10.9 50.5)', 4326)),
    ('r-6', 'B', 5000, 'fixture', ST_GeomFromText('LINESTRING(11.1 50.5,11.9 50.5)', 4326));

-- DEA05 has a substation but no line, so exactly one of the three grid
-- components is unavailable.
INSERT INTO raw.grid_infrastructure (source_id, asset_type, voltage, name, geom) VALUES
    ('g-l1', 'line', '110 kV', 'Line 1', ST_GeomFromText('LINESTRING(6.1 50.4,6.9 50.4)', 4326)),
    ('g-l2', 'line', '220 kV', 'Line 2', ST_GeomFromText('LINESTRING(7.1 50.4,7.9 50.4)', 4326)),
    ('g-l3', 'line', '380 kV', 'Line 3', ST_GeomFromText('LINESTRING(8.1 50.4,8.9 50.4)', 4326)),
    ('g-l4', 'line', '380 kV', 'Line 4', ST_GeomFromText('LINESTRING(9.1 50.4,9.9 50.4)', 4326)),
    ('g-l6', 'line', '110 kV', 'Line 6', ST_GeomFromText('LINESTRING(11.1 50.4,11.9 50.4)', 4326)),
    ('g-s1', 'substation', '110 kV', 'Substation 1', ST_SetSRID(ST_Point(6.5, 50.6), 4326)),
    ('g-s2', 'substation', '220 kV', 'Substation 2', ST_SetSRID(ST_Point(7.5, 50.6), 4326)),
    ('g-s3', 'substation', '380 kV', 'Substation 3', ST_SetSRID(ST_Point(8.4, 50.6), 4326)),
    ('g-s4', 'substation', '380 kV', 'Substation 4', ST_SetSRID(ST_Point(9.6, 50.6), 4326)),
    ('g-s5', 'substation', '110 kV', 'Substation 5', ST_SetSRID(ST_Point(10.5, 50.6), 4326)),
    ('g-s6', 'substation', '110 kV', 'Substation 6', ST_SetSRID(ST_Point(11.5, 50.6), 4326));

-- Exactly one operating technology per district, so the diversity bounds are
-- equal and the equal-bound rule has to answer 50 for every district.
INSERT INTO raw.renewable_assets (source_id, asset_type, technology, capacity_mw, status, geom) VALUES
    ('re-1', 'generator', 'wind', 10, 'In Betrieb', ST_SetSRID(ST_Point(6.5, 50.3), 4326)),
    ('re-2', 'generator', 'wind', 20, 'In Betrieb', ST_SetSRID(ST_Point(7.5, 50.3), 4326)),
    ('re-3', 'generator', 'wind', 40, 'In Betrieb', ST_SetSRID(ST_Point(8.4, 50.3), 4326)),
    ('re-4', 'generator', 'wind', 40, 'In Betrieb', ST_SetSRID(ST_Point(9.6, 50.3), 4326)),
    ('re-5', 'generator', 'wind', 5, 'In Betrieb', ST_SetSRID(ST_Point(10.5, 50.3), 4326)),
    ('re-6', 'generator', 'wind', 15, 'In Betrieb', ST_SetSRID(ST_Point(11.5, 50.3), 4326));

INSERT INTO raw.energy_consumption_municipal (
    year, municipality_name, district_name, nuts_code, ags, consumption_gwh, source
)
SELECT
    year,
    'M' || d.ags,
    d.district_name,
    d.nuts_code,
    d.ags,
    d.base_consumption,
    'fixture'
FROM (VALUES
    ('DEA01', '05111', 'West', 1000),
    ('DEA02', '05112', 'Centre', 1500),
    ('DEA03', '05113', 'Mirror West', 2000),
    ('DEA04', '05114', 'Mirror East', 2000),
    ('DEA05', '05115', 'No Grid Lines', 2500),
    ('DEA06', '05116', 'No Chargers', 3000)
) AS d(nuts_code, ags, district_name, base_consumption)
CROSS JOIN (VALUES (2022), (2023), (2024)) AS y(year);

INSERT INTO raw.renewable_balance_municipal (
    year, municipality_name, district_name, nuts_code, ags,
    published_generation_mwh, generation_components_unknown,
    wind_capacity_mw, renewable_capacity_mw, renewable_net_addition_mw, source
)
SELECT
    year,
    'M' || d.ags,
    d.district_name,
    d.nuts_code,
    d.ags,
    d.generation,
    0,
    d.wind_capacity,
    d.capacity,
    d.net_addition,
    'fixture'
FROM (VALUES
    ('DEA01', '05111', 'West', 300000, 20, 40, 2),
    ('DEA02', '05112', 'Centre', 500000, 30, 60, 3),
    ('DEA03', '05113', 'Mirror West', 900000, 50, 90, 5),
    ('DEA04', '05114', 'Mirror East', 900000, 50, 90, 5),
    ('DEA05', '05115', 'No Grid Lines', 400000, 10, 20, 1),
    ('DEA06', '05116', 'No Chargers', 700000, 40, 70, 4)
) AS d(nuts_code, ags, district_name, generation, wind_capacity, capacity, net_addition)
CROSS JOIN (VALUES (2022), (2023), (2024)) AS y(year);

REFRESH MATERIALIZED VIEW analytics.nrw_district_metrics;
"""


@unittest.skipUnless(DATABASE_URL, "SCENARIO_TEST_DATABASE_URL is not configured")
class ScoreModelTest(unittest.TestCase):
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
        cls.psql(FIXTURE_SQL)
        cls.psql_file(ROOT / "db/nrw_analytics.sql")

    def setUp(self) -> None:
        self.psql("TRUNCATE scenario.proposed_chargers;")

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

    @classmethod
    def rows(cls, sql: str) -> list[dict]:
        """Read a result set as JSON so a NULL stays distinguishable from ''."""
        statement = f"SELECT COALESCE(json_agg(row_to_json(source)), '[]'::json) FROM ({sql}) source;"
        result = cls.psql(statement)
        return json.loads(result.stdout.strip())

    @classmethod
    def districts(cls) -> list[dict]:
        return cls.rows(
            "SELECT * FROM publish.nrw_ev_baseline_metrics ORDER BY nuts_code"
        )

    @classmethod
    def model(cls) -> list[dict]:
        return cls.rows("SELECT * FROM publish.nrw_score_model ORDER BY sort_order")

    @staticmethod
    def _number(value: object) -> float | None:
        return None if value is None else float(value)

    # -- the model itself ---------------------------------------------------

    def test_published_bounds_are_the_5th_and_95th_percentiles_of_the_measures(self) -> None:
        districts = self.districts()
        measured = [term for term in self.model() if term["measure"]]
        self.assertGreaterEqual(len(measured), 10)
        for term in measured:
            with self.subTest(term["component_score"]):
                values = [self._number(row[term["measure"]]) for row in districts]
                low, high = bounds_5_95(values)
                self.assertAlmostEqual(self._number(term["lower_bound"]), low, places=6)
                self.assertAlmostEqual(self._number(term["upper_bound"]), high, places=6)

    def test_every_component_score_is_the_normalized_published_measure(self) -> None:
        districts = self.districts()
        for term in (term for term in self.model() if term["measure"]):
            for row in districts:
                with self.subTest(f"{row['nuts_code']}:{term['component_score']}"):
                    expected = round_half_up(
                        normalize_5_95(
                            self._number(row[term["measure"]]),
                            self._number(term["lower_bound"]),
                            self._number(term["upper_bound"]),
                            term["inverse_measure"],
                        )
                    )
                    self.assertEqual(self._number(row[term["component_score"]]), expected)

    def test_every_composite_is_the_weighted_sum_of_its_published_components(self) -> None:
        districts = self.districts()
        component_names = {
            name.removeprefix("inverse:")
            for weights in INDICATOR_WEIGHTS.values()
            for name in weights
        }
        for row in districts:
            components = {name: self._number(row[name]) for name in component_names}
            for indicator, weights in INDICATOR_WEIGHTS.items():
                with self.subTest(f"{row['nuts_code']}:{indicator}"):
                    self.assertEqual(
                        self._number(row[indicator]),
                        weighted_composite(components, weights),
                    )

    def test_equal_bounds_score_fifty(self) -> None:
        diversity = next(
            term
            for term in self.model()
            if term["component_score"] == "renewable_technology_diversity_score"
        )
        self.assertEqual(
            self._number(diversity["lower_bound"]), self._number(diversity["upper_bound"])
        )
        for row in self.districts():
            with self.subTest(row["nuts_code"]):
                self.assertEqual(self._number(row["renewable_technology_count"]), 1.0)
                self.assertEqual(
                    self._number(row["renewable_technology_diversity_score"]), 50.0
                )

    def test_asset_and_municipal_workbook_capacity_stay_distinct(self) -> None:
        """S08-R01: capacity quality may only describe the workbook value."""
        def capacity_row() -> dict:
            return self.rows(
                "SELECT operating_asset_renewable_capacity_mw, "
                "       operating_asset_renewable_capacity_mw_per_km2, "
                "       municipal_workbook_renewable_capacity_mw, "
                "       municipal_workbook_capacity_reporting_year, "
                "       municipal_workbook_renewable_capacity_coverage, "
                "       municipal_workbook_renewable_capacity_unavailable_reason "
                "FROM publish.nrw_ev_baseline_metrics WHERE nuts_code = 'DEA01'"
            )[0]

        # The fixture intentionally gives DEA01 10 MW of operating assets and
        # 40 MW in the municipal workbook.  Both are valid, distinct concepts.
        row = capacity_row()
        self.assertEqual(self._number(row["operating_asset_renewable_capacity_mw"]), 10.0)
        self.assertIsNotNone(row["operating_asset_renewable_capacity_mw_per_km2"])
        self.assertEqual(self._number(row["municipal_workbook_renewable_capacity_mw"]), 40.0)
        self.assertEqual(int(row["municipal_workbook_capacity_reporting_year"]), 2024)
        self.assertEqual(self._number(row["municipal_workbook_renewable_capacity_coverage"]), 1.0)
        self.assertIsNone(row["municipal_workbook_renewable_capacity_unavailable_reason"])

        try:
            self.psql(
                "UPDATE raw.renewable_balance_municipal "
                "SET renewable_capacity_mw = NULL "
                "WHERE nuts_code = 'DEA01' AND year = 2024;"
            )
            self.psql_file(ROOT / "db/nrw_analytics.sql")
            row = capacity_row()
            self.assertEqual(
                self._number(row["operating_asset_renewable_capacity_mw"]), 10.0,
                "missing workbook capacity must not erase the known asset inventory",
            )
            self.assertIsNone(row["municipal_workbook_renewable_capacity_mw"])
            self.assertEqual(
                self._number(row["municipal_workbook_renewable_capacity_coverage"]), 0.0
            )
            self.assertEqual(
                row["municipal_workbook_renewable_capacity_unavailable_reason"],
                "incomplete_capacity_coverage",
            )
        finally:
            # The class fixture is shared by the model assertions, so restore
            # it even when an assertion fails.
            self.psql(
                "UPDATE raw.renewable_balance_municipal "
                "SET renewable_capacity_mw = 40 "
                "WHERE nuts_code = 'DEA01' AND year = 2024;"
            )
            self.psql_file(ROOT / "db/nrw_analytics.sql")

    # -- missing data, verified zeros and ranking ---------------------------

    def test_an_unavailable_component_makes_its_composites_unavailable(self) -> None:
        row = next(d for d in self.districts() if d["nuts_code"] == "DEA05")
        self.assertEqual(row["grid_data_quality_flag"], "no_mapped_grid_lines")
        self.assertIsNone(row["voltage_line_density_score"])
        self.assertIsNone(row["grid_readiness_proxy_score"])
        self.assertIsNone(row["infrastructure_opportunity_score"])
        self.assertIsNone(row["investment_priority_score"])
        self.assertIsNone(row["grid_absorption_risk_proxy_score"])
        self.assertEqual(row["data_quality_flag"], "missing_infrastructure_input")
        self.assertEqual(
            row["infrastructure_data_quality_flag"], "missing_required_component"
        )
        self.assertEqual(row["energy_unavailable_reason"], "unavailable_grid_readiness_proxy")
        # The two available grid components are still published; the weights are
        # not redistributed over them.
        self.assertIsNotNone(row["substation_proximity_score"])
        self.assertIsNotNone(row["substation_density_score"])

    def test_a_verified_zero_is_a_value_and_still_scores(self) -> None:
        row = next(d for d in self.districts() if d["nuts_code"] == "DEA06")
        self.assertEqual(self._number(row["chargers_total"]), 0.0)
        self.assertEqual(self._number(row["charging_points_total"]), 0.0)
        self.assertEqual(self._number(row["charging_points_per_km2"]), 0.0)
        self.assertEqual(self._number(row["charging_points_per_100k_population"]), 0.0)
        self.assertIsNotNone(row["charger_density_score"])
        self.assertIsNotNone(row["ev_readiness_score"])
        self.assertEqual(row["data_quality_flag"], "complete_proxy_inputs")

    def test_priority_rank_follows_the_sql_rank_and_keeps_ties_tied(self) -> None:
        districts = self.districts()
        scores = [self._number(row["investment_priority_score"]) for row in districts]
        expected = rank_desc_nulls_last(scores)
        self.assertEqual(
            [int(row["priority_rank"]) for row in districts],
            expected,
        )

        by_code = {row["nuts_code"]: row for row in districts}
        self.assertEqual(
            self._number(by_code["DEA03"]["investment_priority_score"]),
            self._number(by_code["DEA04"]["investment_priority_score"]),
        )
        self.assertEqual(
            by_code["DEA03"]["priority_rank"], by_code["DEA04"]["priority_rank"]
        )
        # The unavailable district is ranked last rather than treated as zero.
        self.assertEqual(
            by_code["DEA05"]["priority_rank"], max(expected)
        )

    # -- contract A01 -------------------------------------------------------

    def test_readiness_density_measures_charging_points_not_stations(self) -> None:
        districts = self.districts()
        model = {term["component_score"]: term for term in self.model()}
        density = model["charger_density_score"]
        self.assertEqual(density["measure"], "charging_points_per_km2")

        station_values = [self._number(row["chargers_per_km2"]) for row in districts]
        station_low, station_high = bounds_5_95(station_values)
        differing = 0
        for row in districts:
            if self._number(row["charging_points_total"]) == self._number(row["chargers_total"]):
                continue
            station_score = round_half_up(
                normalize_5_95(
                    self._number(row["chargers_per_km2"]), station_low, station_high
                )
            )
            if self._number(row["charger_density_score"]) != station_score:
                differing += 1
        self.assertGreater(
            differing,
            0,
            "no district distinguishes point density from station density",
        )

    def test_station_density_is_published_as_context_and_scores_nothing(self) -> None:
        self.assertNotIn(
            "chargers_per_km2",
            [term["measure"] for term in self.model()],
        )
        for row in self.districts():
            with self.subTest(row["nuts_code"]):
                self.assertIsNotNone(row["chargers_per_km2"])

    # -- scenario comparability ---------------------------------------------

    def test_empty_scenario_equals_baseline(self) -> None:
        rows = self.rows(
            """
            SELECT
                s.nuts_code,
                s.chargers_total_delta,
                s.charging_points_total_delta,
                s.charging_points_per_km2_delta,
                s.chargers_per_km2_delta,
                s.ev_readiness_score_delta,
                s.investment_priority_score_delta,
                s.baseline_priority_rank = s.scenario_priority_rank AS rank_unchanged
            FROM publish.nrw_ev_scenario_metrics s
            ORDER BY s.nuts_code
            """
        )
        self.assertEqual(len(rows), 6)
        for row in rows:
            with self.subTest(row["nuts_code"]):
                self.assertEqual(self._number(row["chargers_total_delta"]), 0.0)
                self.assertEqual(self._number(row["charging_points_total_delta"]), 0.0)
                self.assertEqual(self._number(row["charging_points_per_km2_delta"]), 0.0)
                self.assertEqual(self._number(row["chargers_per_km2_delta"]), 0.0)
                self.assertEqual(self._number(row["ev_readiness_score_delta"]), 0.0)
                self.assertTrue(row["rank_unchanged"])
                if row["nuts_code"] == "DEA05":
                    self.assertIsNone(row["investment_priority_score_delta"])
                else:
                    self.assertEqual(
                        self._number(row["investment_priority_score_delta"]), 0.0
                    )

    def test_a_proposal_moves_only_charger_metrics_and_leaves_the_bounds_fixed(self) -> None:
        before_bounds = self.psql(
            "SELECT row_to_json(b)::text FROM analytics.nrw_ev_baseline_bounds b;"
        ).stdout.strip()
        before = {row["nuts_code"]: row for row in self.districts()}

        self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, geom
            ) VALUES ('Proposal', 6, 300, 150, ST_SetSRID(ST_Point(6.2, 50.2), 4326));
            """
        )

        after_bounds = self.psql(
            "SELECT row_to_json(b)::text FROM analytics.nrw_ev_baseline_bounds b;"
        ).stdout.strip()
        self.assertEqual(before_bounds, after_bounds)

        scenario = {
            row["nuts_code"]: row
            for row in self.rows(
                "SELECT * FROM publish.nrw_ev_scenario_metrics ORDER BY nuts_code"
            )
        }
        self.assertEqual(self._number(scenario["DEA01"]["chargers_total_delta"]), 1.0)
        self.assertEqual(
            self._number(scenario["DEA01"]["charging_points_total_delta"]), 6.0
        )
        self.assertGreater(
            self._number(scenario["DEA01"]["charging_points_per_km2_delta"]), 0.0
        )
        for code, row in scenario.items():
            with self.subTest(code):
                # Infrastructure opportunity is baseline-only context: proposing a
                # charger measures no road, line or renewable asset.
                self.assertEqual(
                    self._number(row["infrastructure_opportunity_score"]),
                    self._number(before[code]["infrastructure_opportunity_score"]),
                )
                if code != "DEA01":
                    self.assertEqual(self._number(row["chargers_total_delta"]), 0.0)
                    self.assertEqual(
                        self._number(row["charging_points_per_km2_delta"]), 0.0
                    )

    # -- provenance and the single priority definition ----------------------

    def test_every_district_carries_its_formula_version_and_source_years(self) -> None:
        for row in self.districts():
            with self.subTest(row["nuts_code"]):
                self.assertEqual(row["formula_version"], "nrw-2026.09.1")
                self.assertEqual(int(row["population_source_year"]), 2024)
                self.assertEqual(row["population_source"], "fixture")
                self.assertEqual(int(row["energy_reporting_year"]), 2024)

    def test_an_unrecorded_charger_snapshot_date_says_so(self) -> None:
        for row in self.districts():
            with self.subTest(row["nuts_code"]):
                self.assertIsNone(row["charger_snapshot_date"])
                self.assertEqual(
                    row["charger_snapshot_unavailable_reason"],
                    "charger_snapshot_date_not_recorded",
                )

        self.psql(
            """
            INSERT INTO raw.source_snapshots (source_key, snapshot_date, source_name)
            VALUES ('bnetza_ladesaeulenregister', DATE '2026-04-22', 'Fixture register');
            """
        )
        try:
            row = self.districts()[0]
            self.assertEqual(row["charger_snapshot_date"], "2026-04-22")
            self.assertEqual(row["charger_snapshot_source"], "Fixture register")
            self.assertIsNone(row["charger_snapshot_unavailable_reason"])
        finally:
            self.psql("DELETE FROM raw.source_snapshots;")

    def test_only_one_district_priority_definition_remains(self) -> None:
        result = self.psql(
            "SELECT to_regclass('analytics.nrw_priority_scores') IS NULL;"
        )
        self.assertEqual(result.stdout.strip(), "t")

        columns = self.rows(
            """
            SELECT relname, attname
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'publish'
              AND c.relname IN ('nrw_district_priority', 'nrw_ev_baseline_metrics')
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY relname, attname
            """
        )
        priority = [row["attname"] for row in columns if row["relname"] == "nrw_district_priority"]
        baseline = [
            row["attname"] for row in columns if row["relname"] == "nrw_ev_baseline_metrics"
        ]
        self.assertEqual(priority, baseline)
        self.assertIn("investment_priority_score", priority)

    def test_the_district_projection_carries_the_promised_detail(self) -> None:
        row = self.districts()[0]
        for field in (
            "transport_load_score",
            "traffic_intensity_dtv",
            "traffic_length_coverage",
            "grid_readiness_proxy_score",
            "voltage_weighted_line_density",
            "line_voltage_coverage",
            "substation_voltage_coverage",
            "renewable_context_score",
            "operating_asset_renewable_capacity_mw",
            "operating_asset_renewable_capacity_mw_per_km2",
            "renewable_capacity_mw_per_km2",
            "municipal_workbook_renewable_capacity_mw",
            "municipal_workbook_capacity_reporting_year",
            "municipal_workbook_renewable_capacity_coverage",
            "municipal_workbook_renewable_capacity_unavailable_reason",
            "local_energy_balance_score",
            "renewable_growth_score",
            "grid_absorption_risk_proxy_score",
            "consumption_mwh",
            "total_renewable_generation_mwh",
            "renewable_net_addition_3y_mw",
            "consumption_municipal_coverage",
            "growth_years_required",
            "growth_years_reported",
            "energy_data_quality_flag",
            "infrastructure_data_quality_flag",
            "wind_full_load_hours",
            "wind_estimate_note",
        ):
            with self.subTest(field):
                self.assertIn(field, row)
                if field != "municipal_workbook_renewable_capacity_unavailable_reason":
                    self.assertIsNotNone(row[field])


if __name__ == "__main__":
    unittest.main()
