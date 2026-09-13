from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
import verify_project_e2e as verifier  # noqa: E402


class ProjectEndToEndVerifierTest(unittest.TestCase):
    @staticmethod
    def _scenario_metrics() -> tuple[dict, dict]:
        """A rounded, non-clamped scenario calculated from the canonical model."""
        before = {
            "scenario_chargers_total": 10,
            "scenario_charging_points_total": 24,
            "baseline_charger_density_score": 40.0,
            "baseline_charger_accessibility_score": 60.0,
            "baseline_population_adjusted_coverage_score": 80.0,
            "baseline_ev_readiness_score": 58.0,
            "baseline_charger_deficit_score": 42.0,
            "baseline_investment_priority_score": 45.2,
            "scenario_charger_density_score": 40.0,
            "scenario_charger_accessibility_score": 60.0,
            "scenario_population_adjusted_coverage_score": 80.0,
            "scenario_ev_readiness_score": 58.0,
            "scenario_charger_deficit_score": 42.0,
            "scenario_investment_priority_score": 45.2,
            "scenario_priority_rank": 3,
            "data_quality_flag": "complete_proxy_inputs",
            "baseline_charging_points_per_km2": 40.0,
            "scenario_charging_points_per_km2": 40.0,
            "baseline_distance_to_nearest_charger_m": 40.0,
            "scenario_distance_to_nearest_charger_m": 40.0,
            "baseline_charging_points_per_100k_population": 80.0,
            "scenario_charging_points_per_100k_population": 80.0,
            "charging_point_density_lower_bound": 0.0,
            "charging_point_density_upper_bound": 100.0,
            "charger_distance_lower_bound": 0.0,
            "charger_distance_upper_bound": 100.0,
            "population_coverage_lower_bound": 0.0,
            "population_coverage_upper_bound": 100.0,
        }
        after = {
            **before,
            "scenario_chargers_total": 11,
            "scenario_charging_points_total": 28,
            "scenario_charging_points_per_km2": 41.0,
            "scenario_charger_density_score": 41.0,
            "scenario_ev_readiness_score": 58.4,
            "scenario_charger_deficit_score": 41.6,
            "scenario_investment_priority_score": 45.0,
            "scenario_priority_rank": 4,
            "charger_density_score_delta": 1.0,
            "charger_accessibility_score_delta": 0.0,
            "population_adjusted_coverage_score_delta": 0.0,
            "ev_readiness_score_delta": 0.4,
            "charger_deficit_score_delta": -0.4,
            "investment_priority_score_delta": -0.2,
            "infrastructure_opportunity_score": 50.0,
        }
        return before, after

    def test_catalog_verification_requires_both_road_layers(self) -> None:
        self.assertIn("nrw_ev_baseline_metrics", verifier.REQUIRED_CATALOG_LAYERS)
        self.assertIn("nrw_chargers", verifier.REQUIRED_CATALOG_LAYERS)
        self.assertIn("nrw_autobahns", verifier.REQUIRED_CATALOG_LAYERS)
        self.assertIn("nrw_regional_roads", verifier.REQUIRED_CATALOG_LAYERS)
        self.assertIn("nrw_renewable_potential", verifier.REQUIRED_CATALOG_LAYERS)

    def test_large_wfs_layers_use_selective_sample_envelopes(self) -> None:
        self.assertEqual(
            verifier.WFS_SAMPLE_BBOXES["nrw_renewable_potential"],
            "6.27,51.82,6.29,51.84,EPSG:4326",
        )
        self.assertTrue({"nrw_chargers", "nrw_autobahns", "nrw_regional_roads"} <= verifier.WFS_SAMPLE_BBOXES.keys())

    def test_insert_transaction_escapes_name_and_contains_gml_point(self) -> None:
        document = verifier.insert_xml(
            "Research & Development <NRW>", "123e4567-e89b-12d3-a456-426614174000"
        )

        self.assertIn("Research &amp; Development &lt;NRW&gt;", document)
        self.assertIn("<nrw:proposed_chargers>", document)
        self.assertIn("<nrw:max_point_power_kw>150</nrw:max_point_power_kw>", document)
        self.assertIn("<nrw:request_id>123e4567-e89b-12d3-a456-426614174000</nrw:request_id>", document)
        self.assertIn('<gml:coordinates decimal="." cs="," ts=" ">6.7735,51.2277</gml:coordinates>', document)
        self.assertIn('version="1.0.0"', document)

    def test_delete_targets_only_the_created_uuid(self) -> None:
        document = verifier.delete_xml("123e4567-e89b-12d3-a456-426614174000")

        self.assertIn('typeName="nrw:proposed_chargers"', document)
        self.assertIn("<ogc:PropertyName>id</ogc:PropertyName>", document)
        self.assertIn("123e4567-e89b-12d3-a456-426614174000", document)

    def test_official_write_probe_cannot_match_a_real_station(self) -> None:
        document = verifier.harmless_official_write_xml()

        self.assertIn("nrw:nrw_chargers", document)
        self.assertIn("__e2e_verifier_never_matches__", document)

    def test_read_only_write_probes_cover_official_and_analytical_layers(self) -> None:
        self.assertEqual(
            verifier.READ_ONLY_TRANSACTION_LAYERS,
            ("nrw_chargers", "nrw_ev_scenario_metrics"),
        )
        analytical_probe = verifier.harmless_read_only_write_xml("nrw_ev_scenario_metrics")
        self.assertIn(
            'typeName="nrw:nrw_ev_scenario_metrics"',
            analytical_probe,
        )
        self.assertIn("<ogc:PropertyName>nuts_code</ogc:PropertyName>", analytical_probe)
        self.assertIn(
            "<ogc:PropertyName>source_id</ogc:PropertyName>",
            verifier.harmless_official_write_xml(),
        )

    def test_metric_increment_checks_station_and_connector_counts(self) -> None:
        before = {"scenario_chargers_total": 10, "scenario_charging_points_total": 24}
        after = {"scenario_chargers_total": 11, "scenario_charging_points_total": 28}

        verifier.assert_metric_increment(before, after)

        with self.assertRaisesRegex(RuntimeError, "charger total"):
            verifier.assert_metric_increment(before, {**after, "scenario_chargers_total": 12})

    def test_scenario_formula_check_requires_published_delta_consistency_and_weights(self) -> None:
        before, after = self._scenario_metrics()

        verifier.assert_scenario_formula_change(before, after)

        with self.assertRaisesRegex(RuntimeError, "delta"):
            verifier.assert_scenario_formula_change(before, {**after, "ev_readiness_score_delta": 0})
        with self.assertRaisesRegex(RuntimeError, "weights"):
            verifier.assert_scenario_formula_change(before, {**after, "scenario_ev_readiness_score": 59.0, "ev_readiness_score_delta": 1.0})
        with self.assertRaisesRegex(RuntimeError, "priority"):
            verifier.assert_scenario_formula_change(before, {**after, "scenario_investment_priority_score": 999.0, "investment_priority_score_delta": 953.8})
        frozen = {
            **after,
            "scenario_charger_density_score": before["scenario_charger_density_score"],
            "scenario_charger_accessibility_score": before["scenario_charger_accessibility_score"],
            "scenario_population_adjusted_coverage_score": before["scenario_population_adjusted_coverage_score"],
            "scenario_ev_readiness_score": before["scenario_ev_readiness_score"],
            "scenario_charger_deficit_score": before["scenario_charger_deficit_score"],
            "scenario_investment_priority_score": before["scenario_investment_priority_score"],
            "charger_density_score_delta": 0.0,
            "charger_accessibility_score_delta": 0.0,
            "population_adjusted_coverage_score_delta": 0.0,
            "ev_readiness_score_delta": 0.0,
            "charger_deficit_score_delta": 0.0,
            "investment_priority_score_delta": 0.0,
        }
        with self.assertRaisesRegex(RuntimeError, "fixed baseline bounds"):
            verifier.assert_scenario_formula_change(before, frozen)

    def test_scenario_formula_check_allows_clamped_scores_and_existing_proposals(self) -> None:
        before, after = self._scenario_metrics()
        clamped = {
            **after,
            "scenario_charger_density_score": 100.0,
            "scenario_charger_accessibility_score": 100.0,
            "scenario_population_adjusted_coverage_score": 100.0,
            "scenario_ev_readiness_score": 100.0,
            "scenario_charger_deficit_score": 0.0,
            "scenario_investment_priority_score": 20.0,
            "charger_density_score_delta": 60.0,
            "charger_accessibility_score_delta": 40.0,
            "population_adjusted_coverage_score_delta": 20.0,
            "ev_readiness_score_delta": 42.0,
            "charger_deficit_score_delta": -42.0,
            "investment_priority_score_delta": -25.2,
            "scenario_chargers_total": 37,
            "scenario_charging_points_total": 132,
            "scenario_charging_points_per_km2": 150.0,
            "scenario_distance_to_nearest_charger_m": -30.0,
            "scenario_charging_points_per_100k_population": 150.0,
        }
        verifier.assert_scenario_formula_change(before, clamped)

    def test_cleanup_restores_every_proposal_sensitive_value(self) -> None:
        before, after = self._scenario_metrics()
        verifier.assert_metric_restoration(before, before.copy())
        with self.assertRaisesRegex(RuntimeError, "did not restore"):
            verifier.assert_metric_restoration(before, {**after, "scenario_chargers_total": 12})

    def test_http_and_ogc_exception_reports_are_failures(self) -> None:
        self.assertTrue(verifier.is_exception_response(403, "Forbidden"))
        self.assertTrue(verifier.is_exception_response(200, "<ows:ExceptionReport/>"))
        self.assertTrue(verifier.is_exception_response(200, "<ServiceExceptionReport/>"))
        self.assertFalse(verifier.is_exception_response(200, "<wfs:TransactionResponse/>"))

    def test_insert_response_yields_uuid_for_reliable_cleanup(self) -> None:
        station_id = "123e4567-e89b-12d3-a456-426614174000"
        body = f'<ogc:FeatureId fid="proposed_chargers.{station_id}"/>'

        self.assertEqual(verifier.inserted_station_id(body), station_id)


if __name__ == "__main__":
    unittest.main()
