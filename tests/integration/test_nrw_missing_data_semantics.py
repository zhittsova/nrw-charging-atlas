"""Missing-data and coverage regressions for F13, F14 and F15.

Every expectation about the energy roll-up comes from the hand-derived
catalogue in `tests/fixtures/energy_coverage_cases.py`, which a separate unit
module proves is arithmetically self-consistent. These tests therefore check
that the SQL agrees with an independently derived answer rather than restating
the query.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests" / "fixtures"))
from run_postgis_tests import psql_connection, require_disposable_database_url  # noqa: E402

from energy_coverage_cases import (  # noqa: E402
    COVERAGE_CASES,
    case,
    consumption_values_sql,
    renewable_values_sql,
)
from voltage_tag_cases import VOLTAGE_CASES, sql_literal  # noqa: E402


DATABASE_URL = os.environ.get("SCENARIO_TEST_DATABASE_URL")

DISTRICTS_SQL = """
INSERT INTO raw.admin_regions (nuts_code, ags, district_name, region_name, geom) VALUES
    ('DEA01', '05111', 'West', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((7.0 51.0,7.5 51.0,7.5 51.5,7.0 51.5,7.0 51.0))', 4326))),
    ('DEA02', '05112', 'Centre', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((7.5 51.0,8.0 51.0,8.0 51.5,7.5 51.5,7.5 51.0))', 4326)));

INSERT INTO raw.population (district_code, nuts_code, ags, population, reference_year, source) VALUES
    ('05111', 'DEA01', '05111', 100000, 2024, 'fixture'),
    ('05112', 'DEA02', '05112', 200000, 2024, 'fixture');
"""


@unittest.skipUnless(DATABASE_URL, "SCENARIO_TEST_DATABASE_URL is not configured")
class DisposableSemanticsFixture(unittest.TestCase):
    fixture_sql = ""

    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None
        run_id = os.environ.get("SCENARIO_TEST_RUN_ID")
        endpoint = os.environ.get("SCENARIO_TEST_ENDPOINT")
        require_disposable_database_url(DATABASE_URL, run_id=run_id, endpoint=endpoint)
        cls.psql_base, cls.psql_environment = psql_connection(
            DATABASE_URL, run_id=run_id, endpoint=endpoint
        )
        cls.reset()

    @classmethod
    def reset(cls, extra_sql: str = "") -> None:
        cls.psql(
            "DROP SCHEMA IF EXISTS publish CASCADE;"
            "DROP SCHEMA IF EXISTS analytics CASCADE;"
            "DROP SCHEMA IF EXISTS staging CASCADE;"
            "DROP SCHEMA IF EXISTS scenario CASCADE;"
            "DROP SCHEMA IF EXISTS raw CASCADE;"
        )
        cls.psql_file(ROOT / "db/nrw_schema.sql")
        cls.psql(DISTRICTS_SQL + (cls.fixture_sql or "") + (extra_sql or ""))
        cls.psql("REFRESH MATERIALIZED VIEW analytics.nrw_district_metrics;")
        cls.psql_file(ROOT / "db/nrw_analytics.sql")

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

    def rows(self, sql: str) -> list[list[str]]:
        result = self.psql(sql, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line.split("|") for line in result.stdout.strip().splitlines() if line]

    def value(self, sql: str) -> str:
        rows = self.rows(sql)
        self.assertEqual(len(rows), 1, rows)
        return rows[0][0]

    @staticmethod
    def optional_float(text: str) -> float | None:
        return None if text == "" else float(text)


# The energy composite also needs the grid-readiness proxy, so these cases give
# each district a voltage-tagged line and a substation.  Without them every
# district would report the grid proxy as its unavailable reason and the energy
# coverage question under test would be invisible.
ENERGY_GRID_BASELINE_SQL = """
INSERT INTO raw.grid_infrastructure (source_id, asset_type, voltage, name, geom) VALUES
    ('e-line-a', 'line', '110000', 'Line A',
     ST_GeomFromText('LINESTRING(7.1 51.1,7.4 51.1)', 4326)),
    ('e-sub-a', 'substation', '110000', 'Substation A',
     ST_SetSRID(ST_Point(7.2, 51.25), 4326)),
    ('e-line-b', 'line', '220000', 'Line B',
     ST_GeomFromText('LINESTRING(7.6 51.1,7.9 51.1)', 4326)),
    ('e-sub-b', 'substation', '220000', 'Substation B',
     ST_SetSRID(ST_Point(7.7, 51.25), 4326));
"""


class EnergyCoverageTest(DisposableSemanticsFixture):
    """The SQL roll-up must match the independently derived expectations."""

    def load_case(self, name: str) -> None:
        coverage_case = case(name)
        self.reset(
            ENERGY_GRID_BASELINE_SQL +
            "INSERT INTO raw.energy_consumption_municipal"
            " (year, nuts_code, ags, consumption_gwh, municipality_name, district_name, source)"
            " SELECT v.year, v.nuts_code, v.ags, v.consumption_gwh, 'M' || v.ags,"
            "        'D' || v.nuts_code, 'fixture'"
            f" FROM (VALUES\n{consumption_values_sql(coverage_case)}\n"
            " ) AS v(year, nuts_code, ags, consumption_gwh);\n"
            "INSERT INTO raw.renewable_balance_municipal"
            " (year, nuts_code, ags, published_generation_mwh, wind_capacity_mw,"
            "  renewable_net_addition_mw, renewable_capacity_mw, municipality_name,"
            "  district_name, source)"
            " SELECT v.year, v.nuts_code, v.ags, v.published_generation_mwh, v.wind_capacity_mw,"
            "        v.renewable_net_addition_mw, v.wind_capacity_mw, 'M' || v.ags,"
            "        'D' || v.nuts_code, 'fixture'"
            f" FROM (VALUES\n{renewable_values_sql(coverage_case)}\n"
            " ) AS v(year, nuts_code, ags, published_generation_mwh, wind_capacity_mw,"
            "        renewable_net_addition_mw);"
        )

    def test_district_rollups_match_the_independent_expectations(self) -> None:
        for coverage_case in COVERAGE_CASES:
            self.load_case(coverage_case.name)
            actual = {
                row[0]: row
                for row in self.rows(
                    "SELECT nuts_code, reporting_year, expected_municipalities,"
                    "       consumption_municipalities_reported,"
                    "       COALESCE(consumption_mwh::text, ''),"
                    "       renewable_municipalities_reported, growth_years_reported,"
                    "       COALESCE(renewable_net_addition_3y_mw::text, ''),"
                    "       COALESCE(energy_unavailable_reason, '')"
                    " FROM publish.nrw_local_energy_balance ORDER BY nuts_code;"
                )
            }
            for expected in coverage_case.expected:
                with self.subTest(case=coverage_case.name, district=expected.nuts_code):
                    row = actual[expected.nuts_code]
                    self.assertEqual(int(row[1]), expected.reporting_year)
                    self.assertEqual(int(row[2]), expected.expected_municipalities)
                    self.assertEqual(
                        int(row[3]), expected.consumption_municipalities_reported
                    )
                    self.assertEqual(
                        self.optional_float(row[4]), expected.consumption_mwh
                    )
                    self.assertEqual(
                        int(row[5]), expected.renewable_municipalities_reported
                    )
                    self.assertEqual(int(row[6]), expected.growth_years_reported)
                    self.assertEqual(
                        self.optional_float(row[7]), expected.renewable_net_addition_3y_mw
                    )
                    self.assertEqual(row[8] or None, expected.unavailable_reason)

    def test_a_measured_zero_is_not_treated_as_a_missing_municipality(self) -> None:
        """The control that separates C03's two kinds of nothing."""
        self.load_case("municipality_reports_a_true_zero")
        row = self.rows(
            "SELECT consumption_municipalities_reported, expected_municipalities,"
            "       consumption_mwh, COALESCE(energy_unavailable_reason, 'none')"
            " FROM publish.nrw_local_energy_balance WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(row[0], row[1])
        self.assertEqual(row[3], "none")

        self.load_case("municipality_missing_in_reporting_year")
        row = self.rows(
            "SELECT consumption_mwh IS NULL, energy_unavailable_reason,"
            "       energy_data_quality_flag"
            " FROM publish.nrw_local_energy_balance WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(row[0], "t")
        self.assertEqual(row[1], "incomplete_consumption_coverage")
        self.assertEqual(row[2], "missing_required_input")

    def test_unknown_capacity_does_not_publish_a_short_district_total(self) -> None:
        """Capacity was gated by generation/wind completeness.

        A municipality can report its yield and wind capacity while its total
        technology capacity is unknown; summing over that gap published a short
        total marked complete.
        """
        self.load_case("complete_two_municipalities")
        self.psql(
            "UPDATE raw.renewable_balance_municipal SET renewable_capacity_mw = NULL"
            " WHERE ags = (SELECT MIN(ags) FROM raw.renewable_balance_municipal);"
        )
        self.psql_file(ROOT / "db/nrw_analytics.sql")

        row = self.rows(
            "SELECT COALESCE(renewable_capacity_mw::text, ''),"
            "       renewable_capacity_municipalities_reported,"
            "       renewable_capacity_coverage::text,"
            "       COALESCE(renewable_capacity_unavailable_reason, ''),"
            "       renewable_municipal_coverage::text,"
            "       COALESCE(published_generation_mwh::text, ''),"
            "       COALESCE(energy_unavailable_reason, '')"
            " FROM publish.nrw_local_energy_balance WHERE nuts_code = 'DEA01';"
        )[0]

        self.assertEqual(row[0], "", "a partial capacity sum was published as a total")
        self.assertEqual(int(row[1]), 1)
        self.assertEqual(float(row[2]), 0.5)
        self.assertEqual(row[3], "incomplete_capacity_coverage")
        # Independently known generation and wind must not be invalidated.
        self.assertEqual(float(row[4]), 1.0)
        self.assertNotEqual(row[5], "", "known generation was thrown away with the capacity")
        self.assertEqual(row[6], "", "the composite was invalidated by context data")

    def test_complete_zero_capacity_is_published_as_a_measured_zero(self) -> None:
        self.load_case("complete_two_municipalities")
        self.psql("UPDATE raw.renewable_balance_municipal SET renewable_capacity_mw = 0;")
        self.psql_file(ROOT / "db/nrw_analytics.sql")

        row = self.rows(
            "SELECT renewable_capacity_mw::text, renewable_capacity_coverage::text,"
            "       COALESCE(renewable_capacity_unavailable_reason, '')"
            " FROM publish.nrw_local_energy_balance WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(float(row[0]), 0.0)
        self.assertEqual(float(row[1]), 1.0)
        self.assertEqual(row[2], "")

    def test_missing_generation_leaves_independently_known_capacity_available(self) -> None:
        self.load_case("complete_two_municipalities")
        self.psql(
            "UPDATE raw.renewable_balance_municipal SET published_generation_mwh = NULL"
            " WHERE ags = (SELECT MIN(ags) FROM raw.renewable_balance_municipal);"
        )
        self.psql_file(ROOT / "db/nrw_analytics.sql")

        row = self.rows(
            "SELECT COALESCE(published_generation_mwh::text, ''),"
            "       COALESCE(renewable_capacity_mw::text, ''),"
            "       renewable_capacity_coverage::text,"
            "       COALESCE(energy_unavailable_reason, '')"
            " FROM publish.nrw_local_energy_balance WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(row[0], "")
        self.assertNotEqual(row[1], "", "known capacity was invalidated by missing generation")
        self.assertEqual(float(row[2]), 1.0)
        self.assertEqual(row[3], "incomplete_renewable_coverage")

    def test_an_incomplete_district_does_not_affect_its_neighbour(self) -> None:
        self.load_case("two_districts_independent")
        rows = {
            row[0]: (row[1], row[2])
            for row in self.rows(
                "SELECT nuts_code, consumption_mwh IS NULL,"
                "       COALESCE(energy_unavailable_reason, 'none')"
                " FROM publish.nrw_local_energy_balance ORDER BY nuts_code;"
            )
        }
        self.assertEqual(rows["DEA01"], ("f", "none"))
        self.assertEqual(rows["DEA02"][0], "t")

    def test_the_energy_verifier_accepts_the_published_dataset(self) -> None:
        """F32: the module must run against whatever year was actually selected."""
        self.load_case("complete_two_municipalities")
        # The verifier requires the full 53 districts, so only its year and
        # coverage assertions are exercised here.
        result = self.psql(
            """
            SELECT DISTINCT reporting_year FROM analytics.nrw_local_energy_balance;
            SELECT wind_full_load_hours FROM analytics.nrw_energy_assumptions;
            """,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("2024", (ROOT / "db/verify_nrw_energy_balance.sql").read_text(
            encoding="utf-8"
        ), "the verifier must not pin a hardcoded reporting year")

    def test_non_finite_municipal_values_are_refused(self) -> None:
        self.load_case("complete_two_municipalities")
        for literal in ("'NaN'", "'Infinity'", "'-Infinity'"):
            with self.subTest(value=literal):
                result = self.psql(
                    "INSERT INTO raw.energy_consumption_municipal"
                    " (year, municipality_name, district_name, nuts_code, ags,"
                    "  consumption_gwh, source)"
                    f" VALUES (2019, 'M', 'D', 'DEA01', '05111999', {literal}::numeric, 'fixture');",
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("energy_consumption_finite_chk", result.stderr)


GRID_FIXTURE_SQL = """
INSERT INTO raw.grid_infrastructure (source_id, asset_type, voltage, name, geom) VALUES
    ('line-known', 'line', '110000', 'Tagged line',
     ST_GeomFromText('LINESTRING(7.1 51.1,7.4 51.1)', 4326)),
    ('line-unknown', 'line', NULL, 'Untagged line',
     ST_GeomFromText('LINESTRING(7.1 51.2,7.4 51.2)', 4326)),
    ('line-zero-volt', 'line', '0', 'Zero-volt tag',
     ST_GeomFromText('LINESTRING(7.1 51.3,7.4 51.3)', 4326)),
    ('sub-a', 'substation', '110 kV', 'Substation',
     ST_SetSRID(ST_Point(7.2, 51.25), 4326)),
    ('line-b-unknown', 'line', 'unknown', 'Unparseable tag',
     ST_GeomFromText('LINESTRING(7.6 51.1,7.9 51.1)', 4326)),
    ('sub-b', 'substation', NULL, 'Substation without voltage',
     ST_SetSRID(ST_Point(7.7, 51.25), 4326));
"""


class GridVoltageSemanticsTest(DisposableSemanticsFixture):
    fixture_sql = GRID_FIXTURE_SQL

    def test_unknown_and_zero_voltage_tags_are_not_measured_voltages(self) -> None:
        rows = {
            row[0]: row[1:]
            for row in self.rows(
                "SELECT nuts_code, line_voltage_coverage::text,"
                "       COALESCE(voltage_weighted_line_density::text, ''),"
                "       COALESCE(maximum_mapped_voltage_kv::text, ''),"
                "       grid_data_quality_flag,"
                "       COALESCE(grid_readiness_proxy_score::text, '')"
                " FROM analytics.nrw_grid_proxy_metrics ORDER BY nuts_code;"
            )
        }
        # DEA01 has one tagged line out of three: partial, and the 0 V tag must
        # not count as a reading.
        self.assertEqual(float(rows["DEA01"][0]), 1 / 3)
        self.assertEqual(rows["DEA01"][3], "partial_line_voltage")
        self.assertNotEqual(rows["DEA01"][1], "")

        # DEA02's only line has an unparseable tag: no voltage evidence at all,
        # so the density is unavailable rather than zero and the proxy with it.
        self.assertEqual(float(rows["DEA02"][0]), 0.0)
        self.assertEqual(rows["DEA02"][1], "", "unknown voltage became a zero density")
        self.assertEqual(rows["DEA02"][2], "", "unknown voltage became a measured maximum")
        self.assertEqual(rows["DEA02"][3], "unknown_line_voltage")
        self.assertEqual(rows["DEA02"][4], "", "an unmeasured component produced a score")

    def test_a_zero_voltage_tag_never_becomes_the_district_maximum(self) -> None:
        self.assertEqual(
            self.value(
                "SELECT COALESCE(MAX(voltage_kv)::text, 'none') FROM ("
                "  SELECT (SELECT MAX(p.v) FROM ("
                "    SELECT CASE WHEN t ~* 'kv'"
                "      THEN NULLIF(substring(t FROM '([0-9]+(?:\\.[0-9]+)?)'), '')::numeric"
                "      ELSE NULLIF(substring(t FROM '([0-9]+(?:\\.[0-9]+)?)'), '')::numeric / 1000.0"
                "    END AS v FROM regexp_split_to_table(COALESCE(g.voltage, ''), ';') AS t"
                "  ) p WHERE p.v > 0) AS voltage_kv"
                "  FROM raw.grid_infrastructure g WHERE g.source_id = 'line-zero-volt'"
                ") parsed;"
            ),
            "none",
        )


TRAFFIC_FIXTURE_SQL = """
INSERT INTO raw.roads (
    osm_id, road_class, name, traffic_total, traffic_light, traffic_heavy,
    counting_station_type, source, geom
) VALUES
    ('measured', 'B', 'Counted road', 12000, 11400, 600,
     'automatische Dauerzaehlstelle', 'Strassen.NRW Verkehrswerte',
     ST_GeomFromText('LINESTRING(7.1 51.1,7.4 51.1)', 4326)),
    ('unmeasured', 'L', 'Uncounted road', NULL, NULL, NULL,
     'manuelle Zaehlstelle (SVZ)', 'Strassen.NRW Verkehrswerte',
     ST_GeomFromText('LINESTRING(7.1 51.2,7.4 51.2)', 4326)),
    ('all-unmeasured', 'B', 'Uncounted road', NULL, NULL, NULL,
     'manuelle Zaehlstelle (SVZ)', 'Strassen.NRW Verkehrswerte',
     ST_GeomFromText('LINESTRING(7.6 51.1,7.9 51.1)', 4326));
"""


class TrafficCoverageTest(DisposableSemanticsFixture):
    fixture_sql = TRAFFIC_FIXTURE_SQL

    def test_unpublished_traffic_does_not_lower_the_measured_intensity(self) -> None:
        rows = {
            row[0]: row[1:]
            for row in self.rows(
                "SELECT nuts_code, ROUND(traffic_road_length_km::numeric, 3)::text,"
                "       ROUND(traffic_measured_length_km::numeric, 3)::text,"
                "       COALESCE(ROUND(traffic_length_coverage::numeric, 4)::text, ''),"
                "       COALESCE(ROUND(traffic_intensity_dtv::numeric, 2)::text, ''),"
                "       transport_data_quality_flag,"
                "       COALESCE(transport_load_score::text, '')"
                " FROM analytics.nrw_transport_metrics ORDER BY nuts_code;"
            )
        }
        # DEA01 has one counted and one uncounted road of the same length: the
        # intensity is the counted road's value, not half of it.
        self.assertAlmostEqual(float(rows["DEA01"][3]), 12000.0, delta=0.01)
        # The two fixture roads differ slightly in projected length, so the
        # measured share is about a half rather than exactly a half.
        self.assertAlmostEqual(float(rows["DEA01"][2]), 0.5, delta=0.01)
        self.assertEqual(rows["DEA01"][4], "partial_traffic_coverage")
        self.assertLess(float(rows["DEA01"][1]), float(rows["DEA01"][0]))

        # DEA02's only road published nothing: unavailable, not zero traffic.
        self.assertEqual(rows["DEA02"][3], "")
        self.assertEqual(float(rows["DEA02"][1]), 0.0)
        self.assertEqual(rows["DEA02"][4], "no_published_traffic_value")
        self.assertEqual(rows["DEA02"][5], "", "an unmeasured district scored anyway")

    def test_the_traffic_measure_and_network_scope_are_published(self) -> None:
        row = self.rows(
            "SELECT traffic_measure_field, traffic_measure_unit, traffic_network_scope,"
            "       traffic_source FROM publish.nrw_traffic_assumptions;"
        )[0]
        self.assertEqual(row[0], "DTVKFZA")
        self.assertEqual(row[1], "vehicles per day")
        self.assertIn("Autobahn", row[2])
        self.assertIn("Verkehrswerte", row[3])


class VoltageTagParsingTest(DisposableSemanticsFixture):
    """An unsigned substring search read "-110000" as 110 kV."""

    fixture_sql = ""

    def test_every_catalogued_tag_parses_to_the_expected_kilovolts(self) -> None:
        values = ",\n".join(
            f"('{voltage_case.name}', {sql_literal(voltage_case.tag)})"
            for voltage_case in VOLTAGE_CASES
        )
        self.psql(
            "TRUNCATE raw.grid_infrastructure;"
            " INSERT INTO raw.grid_infrastructure (source_id, asset_type, voltage, geom)"
            " SELECT v.name, 'line', v.tag,"
            "        ST_GeomFromText('LINESTRING(7.1 51.1,7.2 51.1)', 4326)"
            f" FROM (VALUES\n{values}\n ) AS v(name, tag);"
        )
        parsed = {
            parsed_row[0]: (None if parsed_row[1] == "" else float(parsed_row[1]))
            for parsed_row in self.rows(
                "SELECT source_id, COALESCE(voltage_kv::text, '') FROM ("
                "  SELECT g.source_id, ("
                "    SELECT MAX(p.voltage_kv) FROM ("
                "      SELECT CASE"
                "        WHEN btrim(t) ~ '^-?[0-9]+(\\.[0-9]+)?$'"
                "          THEN btrim(t)::numeric / 1000.0"
                "        WHEN btrim(t) ~* '^-?[0-9]+(\\.[0-9]+)?[[:space:]]*kv$'"
                "          THEN substring(btrim(t) FROM '^(-?[0-9]+(?:\\.[0-9]+)?)')::numeric"
                "      END AS voltage_kv"
                "      FROM regexp_split_to_table(COALESCE(g.voltage, ''), ';') AS t"
                "    ) p WHERE p.voltage_kv > 0"
                "  ) AS voltage_kv"
                "  FROM raw.grid_infrastructure g"
                ") parsed ORDER BY source_id;"
            )
        }
        for voltage_case in VOLTAGE_CASES:
            with self.subTest(case=voltage_case.name, tag=voltage_case.tag):
                self.assertEqual(
                    parsed[voltage_case.name],
                    voltage_case.expected_kv,
                    voltage_case.description,
                )

    def test_a_negative_tag_is_not_positive_measured_evidence(self) -> None:
        self.psql(
            "TRUNCATE raw.grid_infrastructure;"
            " INSERT INTO raw.grid_infrastructure (source_id, asset_type, voltage, geom) VALUES"
            " ('neg', 'line', '-110000',"
            "  ST_GeomFromText('LINESTRING(7.1 51.1,7.4 51.1)', 4326)),"
            " ('sub', 'substation', '110000', ST_SetSRID(ST_Point(7.2, 51.2), 4326));"
        )
        self.psql_file(ROOT / "db/nrw_analytics.sql")
        row = self.rows(
            "SELECT COALESCE(voltage_weighted_line_density::text, ''),"
            "       line_voltage_coverage::text, grid_data_quality_flag,"
            "       COALESCE(grid_readiness_proxy_score::text, '')"
            " FROM analytics.nrw_grid_proxy_metrics WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(row[0], "", "a negative tag produced a positive density")
        self.assertEqual(float(row[1]), 0.0)
        self.assertEqual(row[2], "unknown_line_voltage")
        self.assertEqual(row[3], "")

    def test_a_mixed_tag_keeps_its_valid_half(self) -> None:
        self.psql(
            "TRUNCATE raw.grid_infrastructure;"
            " INSERT INTO raw.grid_infrastructure (source_id, asset_type, voltage, geom) VALUES"
            " ('mixed', 'line', '-110000;220000',"
            "  ST_GeomFromText('LINESTRING(7.1 51.1,7.4 51.1)', 4326)),"
            " ('sub', 'substation', '110000', ST_SetSRID(ST_Point(7.2, 51.2), 4326));"
        )
        self.psql_file(ROOT / "db/nrw_analytics.sql")
        row = self.rows(
            "SELECT line_voltage_coverage::text, maximum_mapped_voltage_kv::text,"
            "       grid_data_quality_flag"
            " FROM analytics.nrw_grid_proxy_metrics WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(float(row[0]), 1.0)
        self.assertEqual(row[2], "complete_grid_inputs")


class GridComponentAvailabilityTest(DisposableSemanticsFixture):
    """Substation proximity carries 45% of the proxy."""

    fixture_sql = """
INSERT INTO raw.grid_infrastructure (source_id, asset_type, voltage, name, geom) VALUES
    ('line-a', 'line', '110000', 'Tagged line',
     ST_GeomFromText('LINESTRING(7.1 51.1,7.4 51.1)', 4326)),
    ('line-b', 'line', '220000', 'Tagged line',
     ST_GeomFromText('LINESTRING(7.6 51.1,7.9 51.1)', 4326));
"""

    def test_a_missing_substation_is_named_rather_than_called_complete(self) -> None:
        for nuts_code in ("DEA01", "DEA02"):
            with self.subTest(district=nuts_code):
                row = self.rows(
                    "SELECT COALESCE(grid_readiness_proxy_score::text, ''),"
                    "       grid_data_quality_flag, line_voltage_coverage::text,"
                    "       COALESCE(distance_to_nearest_substation_m::text, '')"
                    f" FROM analytics.nrw_grid_proxy_metrics WHERE nuts_code = '{nuts_code}';"
                )[0]
                self.assertEqual(row[0], "")
                self.assertEqual(row[1], "no_mapped_substation")
                self.assertEqual(float(row[2]), 1.0, "line voltage really is complete")
                self.assertEqual(row[3], "")


if __name__ == "__main__":
    unittest.main()
