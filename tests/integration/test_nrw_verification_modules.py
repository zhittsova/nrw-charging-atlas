"""Execute the SQL verification modules against a full 53-district fixture.

S07 changed what the modules assert but only checked their source text. These
tests run `db/verify_nrw_analytics.sql` and `db/verify_nrw_energy_balance.sql`
for real, including a district whose consumption is a measured zero, and prove
that contradictory quality metadata is actually rejected rather than merely
described. Wiring the modules into the pipeline remains S18's work.
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

DISTRICT_COUNT = 53
GROWTH_YEARS = (2022, 2023, 2024)
# One district reports a fully measured consumption of zero. It keeps that value
# and must still explain why every ratio built on it is unavailable.
ZERO_CONSUMPTION_DISTRICT = "DEA05"


def nuts_code(index: int) -> str:
    return f"DEA{index:02d}"


def district_rows() -> str:
    """53 non-overlapping one-degree cells covering a plausible NRW extent."""
    values = []
    for index in range(1, DISTRICT_COUNT + 1):
        west = 6.0 + ((index - 1) % 9) * 0.3
        south = 50.5 + ((index - 1) // 9) * 0.3
        values.append(
            f"('{nuts_code(index)}', '05{index:03d}', 'District {index}',"
            " 'Nordrhein-Westfalen',"
            f" ST_Multi(ST_MakeEnvelope({west}, {south}, {west + 0.3}, {south + 0.3}, 4326)))"
        )
    return ",\n".join(values)


def population_rows() -> str:
    return ",\n".join(
        f"('05{index:03d}', '{nuts_code(index)}', '05{index:03d}',"
        f" {100000 + index * 1000}, 2024, 'fixture')"
        for index in range(1, DISTRICT_COUNT + 1)
    )


def infrastructure_rows() -> str:
    """A charger, a counted road, a tagged line and a substation per district."""
    chargers, roads, lines, substations = [], [], [], []
    for index in range(1, DISTRICT_COUNT + 1):
        west = 6.0 + ((index - 1) % 9) * 0.3
        south = 50.5 + ((index - 1) // 9) * 0.3
        chargers.append(
            f"('c{index}', 'Fixture', 'active', 'fast', 2, 150, 150, 'Nordrhein-Westfalen',"
            f" ST_SetSRID(ST_Point({west + 0.1}, {south + 0.1}), 4326))"
        )
        roads.append(
            f"('r{index}', 'B', 'Road {index}', {5000 + index * 100}, {4500 + index * 100},"
            f" {500}, 'automatische Dauerzaehlstelle', 'fixture',"
            f" ST_GeomFromText('LINESTRING({west + 0.05} {south + 0.15},"
            f" {west + 0.25} {south + 0.15})', 4326))"
        )
        lines.append(
            f"('l{index}', 'line', '110000', 'Line {index}',"
            f" ST_GeomFromText('LINESTRING({west + 0.05} {south + 0.2},"
            f" {west + 0.25} {south + 0.2})', 4326))"
        )
        substations.append(
            f"('s{index}', 'substation', '110000', 'Substation {index}',"
            f" ST_SetSRID(ST_Point({west + 0.15}, {south + 0.05}), 4326))"
        )
    return (
        "INSERT INTO raw.chargers (source_id, operator, status, charger_type,"
        " charging_points, power_kw, max_point_power_kw, bundesland, geom) VALUES\n"
        + ",\n".join(chargers)
        + ";\nINSERT INTO raw.roads (osm_id, road_class, name, traffic_total,"
        " traffic_light, traffic_heavy, counting_station_type, source, geom) VALUES\n"
        + ",\n".join(roads)
        + ";\nINSERT INTO raw.grid_infrastructure (source_id, asset_type, voltage, name, geom)"
        " VALUES\n"
        + ",\n".join(lines + substations)
        + ";\nINSERT INTO raw.renewable_assets (source_id, asset_type, technology,"
        " capacity_mw, status, geom)\n"
        "SELECT 'ra' || d.nuts_code, 'generator', 'wind', 12, 'In Betrieb',"
        " ST_Centroid(d.geom)::geometry(Point, 4326)\n"
        "FROM raw.admin_regions d;"
    )


def energy_rows() -> str:
    """Two municipalities per district, complete across the growth window."""
    consumption, renewable = [], []
    for index in range(1, DISTRICT_COUNT + 1):
        code = nuts_code(index)
        for municipality in (0, 1):
            ags = f"05{index:03d}{municipality:03d}"
            for year in GROWTH_YEARS:
                gwh = 0 if code == ZERO_CONSUMPTION_DISTRICT else 100 + index + municipality
                consumption.append(
                    f"({year}, 'M{ags}', 'D{code}', '{code}', '{ags}', {gwh}, 'fixture')"
                )
                renewable.append(
                    f"({year}, 'M{ags}', 'D{code}', '{code}', '{ags}',"
                    f" {1000 + index * 10 + municipality}, {2 + municipality},"
                    f" {5 + municipality}, {1 + municipality}, 'fixture')"
                )
    return (
        "INSERT INTO raw.energy_consumption_municipal (year, municipality_name,"
        " district_name, nuts_code, ags, consumption_gwh, source) VALUES\n"
        + ",\n".join(consumption)
        + ";\nINSERT INTO raw.renewable_balance_municipal (year, municipality_name,"
        " district_name, nuts_code, ags, published_generation_mwh, wind_capacity_mw,"
        " renewable_capacity_mw, renewable_net_addition_mw, source) VALUES\n"
        + ",\n".join(renewable)
        + ";"
    )


@unittest.skipUnless(DATABASE_URL, "SCENARIO_TEST_DATABASE_URL is not configured")
class VerificationModuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None
        run_id = os.environ.get("SCENARIO_TEST_RUN_ID")
        endpoint = os.environ.get("SCENARIO_TEST_ENDPOINT")
        require_disposable_database_url(DATABASE_URL, run_id=run_id, endpoint=endpoint)
        cls.psql_base, cls.psql_environment = psql_connection(
            DATABASE_URL, run_id=run_id, endpoint=endpoint
        )
        cls.build()

    @classmethod
    def build(cls) -> None:
        cls.psql(
            "DROP SCHEMA IF EXISTS publish CASCADE;"
            "DROP SCHEMA IF EXISTS analytics CASCADE;"
            "DROP SCHEMA IF EXISTS staging CASCADE;"
            "DROP SCHEMA IF EXISTS scenario CASCADE;"
            "DROP SCHEMA IF EXISTS raw CASCADE;"
        )
        cls.psql_file(ROOT / "db/nrw_schema.sql")
        cls.psql(
            "INSERT INTO raw.admin_regions (nuts_code, ags, district_name, region_name, geom)"
            f" VALUES\n{district_rows()};\n"
            "INSERT INTO raw.population (district_code, nuts_code, ags, population,"
            f" reference_year, source) VALUES\n{population_rows()};\n"
            + infrastructure_rows()
            + "\n"
            + energy_rows()
        )
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

    def test_the_fixture_really_covers_all_53_districts(self) -> None:
        self.assertEqual(
            self.value("SELECT COUNT(*)::text FROM analytics.nrw_local_energy_balance;"),
            str(DISTRICT_COUNT),
        )

    def test_the_analytics_verifier_passes(self) -> None:
        result = self.psql_file(ROOT / "db/verify_nrw_analytics.sql", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_energy_verifier_passes_with_a_measured_zero_district(self) -> None:
        result = self.psql_file(ROOT / "db/verify_nrw_energy_balance.sql", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_measured_zero_district_keeps_its_value_and_states_its_reason(self) -> None:
        row = self.rows(
            "SELECT consumption_mwh::text, consumption_municipal_coverage::text,"
            "       energy_data_quality_flag, energy_unavailable_reason,"
            "       COALESCE(renewable_coverage_pct::text, ''),"
            "       COALESCE(local_energy_balance_score::text, '')"
            " FROM publish.nrw_local_energy_balance"
            f" WHERE nuts_code = '{ZERO_CONSUMPTION_DISTRICT}';"
        )[0]
        self.assertEqual(float(row[0]), 0.0, "the measured zero was discarded")
        self.assertEqual(float(row[1]), 1.0)
        self.assertEqual(row[2], "missing_required_input")
        self.assertEqual(row[3], "zero_consumption_denominator")
        self.assertEqual(row[4], "", "a ratio was computed against a zero denominator")
        self.assertEqual(row[5], "")

        # The risk score lives on its own published projection; both views must
        # agree that this district has no composite.
        risk = self.rows(
            "SELECT COALESCE(grid_absorption_risk_proxy_score::text, ''),"
            "       energy_unavailable_reason"
            " FROM publish.nrw_grid_absorption_risk"
            f" WHERE nuts_code = '{ZERO_CONSUMPTION_DISTRICT}';"
        )[0]
        self.assertEqual(risk[0], "")
        self.assertEqual(risk[1], "zero_consumption_denominator")

    def test_every_other_district_publishes_a_complete_energy_row(self) -> None:
        self.assertEqual(
            self.value(
                "SELECT COUNT(*)::text FROM publish.nrw_local_energy_balance"
                " WHERE energy_unavailable_reason IS NULL;"
            ),
            str(DISTRICT_COUNT - 1),
        )

    def test_the_energy_verifier_rejects_a_flag_that_contradicts_its_reason(self) -> None:
        """Prove the module fails, not just that it passes on good data."""
        self.addCleanup(self.build)
        self.psql(
            "ALTER MATERIALIZED VIEW analytics.nrw_local_energy_balance"
            " RENAME TO nrw_local_energy_balance_real;"
            " CREATE VIEW analytics.nrw_local_energy_balance AS"
            " SELECT nuts_code, ags, district_name, reporting_year, area_km2,"
            "        expected_municipalities, consumption_municipalities_reported,"
            "        consumption_municipal_coverage, renewable_municipalities_reported,"
            "        renewable_municipal_coverage, renewable_capacity_municipalities_reported,"
            "        renewable_capacity_coverage, renewable_capacity_unavailable_reason,"
            "        growth_years_required, growth_years_reported,"
            "        generation_components_unknown, energy_source, consumption_mwh,"
            "        published_generation_mwh, estimated_wind_generation_mwh,"
            "        total_renewable_generation_mwh, renewable_capacity_mw,"
            "        renewable_net_addition_3y_mw, renewable_growth_density_mw_per_km2,"
            "        renewable_balance_ratio, renewable_coverage_pct,"
            "        local_energy_balance_score, renewable_growth_score,"
            "        grid_readiness_proxy_score, grid_absorption_risk_proxy_score,"
            "        'missing_required_input'::text AS energy_data_quality_flag,"
            "        NULL::text AS energy_unavailable_reason,"
            "        wind_full_load_hours, geom"
            " FROM analytics.nrw_local_energy_balance_real;"
        )

        result = self.psql_file(ROOT / "db/verify_nrw_energy_balance.sql", check=False)

        self.assertNotEqual(result.returncode, 0, "a contradictory row was accepted")
        self.assertIn("why an input is unavailable", result.stderr)

    def test_the_analytics_verifier_rejects_an_unexplained_missing_score(self) -> None:
        self.addCleanup(self.build)
        self.psql("DELETE FROM raw.grid_infrastructure WHERE asset_type = 'substation';")
        self.psql_file(ROOT / "db/nrw_analytics.sql")

        # The grid proxy is now genuinely unavailable and correctly explained.
        passing = self.psql_file(ROOT / "db/verify_nrw_analytics.sql", check=False)
        self.assertEqual(passing.returncode, 0, passing.stderr)
        self.assertEqual(
            self.value(
                "SELECT DISTINCT grid_data_quality_flag FROM analytics.nrw_grid_proxy_metrics;"
            ),
            "no_mapped_substation",
        )

        # Claiming the inputs are complete while the score is missing must fail.
        self.psql(
            "ALTER MATERIALIZED VIEW analytics.nrw_grid_proxy_metrics"
            " RENAME TO nrw_grid_proxy_metrics_real;"
            " CREATE VIEW analytics.nrw_grid_proxy_metrics AS"
            " SELECT nuts_code, grid_line_length_km, grid_line_length_km_known_voltage,"
            "        grid_line_segments, substation_count, maximum_mapped_voltage_kv,"
            "        line_voltage_coverage, substation_voltage_coverage,"
            "        voltage_weighted_line_density, substation_density,"
            "        distance_to_nearest_substation_m, grid_readiness_proxy_score,"
            "        'complete_grid_inputs'::text AS grid_data_quality_flag"
            " FROM analytics.nrw_grid_proxy_metrics_real;"
        )

        result = self.psql_file(ROOT / "db/verify_nrw_analytics.sql", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("why the grid proxy is unavailable", result.stderr)


if __name__ == "__main__":
    unittest.main()
