from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
import verify_project_e2e as verifier  # noqa: E402


class ProjectEndToEndVerifierTest(unittest.TestCase):
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
