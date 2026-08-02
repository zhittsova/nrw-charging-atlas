from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
import verify_project_e2e as verifier  # noqa: E402


class ProjectEndToEndVerifierTest(unittest.TestCase):
    def test_insert_transaction_escapes_name_and_contains_gml_point(self) -> None:
        document = verifier.insert_xml("Research & Development <NRW>")

        self.assertIn("Research &amp; Development &lt;NRW&gt;", document)
        self.assertIn("<nrw:proposed_chargers>", document)
        self.assertIn("<gml:pos>6.7735 51.2277</gml:pos>", document)

    def test_delete_targets_only_the_created_uuid(self) -> None:
        document = verifier.delete_xml("123e4567-e89b-12d3-a456-426614174000")

        self.assertIn("typeName=\"nrw:proposed_chargers\"", document)
        self.assertIn("<ogc:PropertyName>id</ogc:PropertyName>", document)
        self.assertIn("123e4567-e89b-12d3-a456-426614174000", document)

    def test_official_write_probe_cannot_match_a_real_station(self) -> None:
        document = verifier.harmless_official_write_xml()

        self.assertIn("nrw:nrw_chargers", document)
        self.assertIn("__e2e_verifier_never_matches__", document)

    def test_metric_increment_checks_station_and_connector_counts(self) -> None:
        before = {"scenario_chargers_total": 10, "scenario_charging_points_total": 24}
        after = {"scenario_chargers_total": 11, "scenario_charging_points_total": 28}

        verifier.assert_metric_increment(before, after)

        with self.assertRaisesRegex(RuntimeError, "charger total"):
            verifier.assert_metric_increment(before, {**after, "scenario_chargers_total": 12})

    def test_http_and_ogc_exception_reports_are_failures(self) -> None:
        self.assertTrue(verifier.is_exception_response(403, "Forbidden"))
        self.assertTrue(verifier.is_exception_response(200, "<ows:ExceptionReport/>"))
        self.assertFalse(verifier.is_exception_response(200, "<wfs:TransactionResponse/>"))

    def test_insert_response_yields_uuid_for_reliable_cleanup(self) -> None:
        station_id = "123e4567-e89b-12d3-a456-426614174000"
        body = f'<ogc:FeatureId fid="proposed_chargers.{station_id}"/>'

        self.assertEqual(verifier.inserted_station_id(body), station_id)


if __name__ == "__main__":
    unittest.main()
