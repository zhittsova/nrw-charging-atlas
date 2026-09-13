"""S18 negative contracts for the project end-to-end verifier.

These tests deliberately use a tiny fake WFS service.  They are not a second
implementation of the verifier: each fake response represents one broken
service state that the production verifier must reject (or reconcile safely).
Keeping the cases here avoids mutating the shared project stack merely to
prove that a guard exists.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
import verify_project_e2e as verifier  # noqa: E402


STATION_ID = "123e4567-e89b-12d3-a456-426614174000"
REQUEST_ID = "223e4567-e89b-12d3-a456-426614174000"


class FakeResponse:
    def __init__(self, *, status_code: int = 200, text: str = "", payload: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


def _schema(layer: str, *, namespace: str = "https://nrw.local/scenario", missing: str | None = None) -> str:
    names = sorted(verifier.REQUIRED_WFS_PROPERTIES[layer] - ({missing} if missing else set()))
    fields = "".join(f'<xsd:element name="{name}" type="xsd:string"/>' for name in names)
    return f'<xsd:schema targetNamespace="{namespace}"><xsd:complexType name="{layer}">{fields}</xsd:complexType></xsd:schema>'


def _feature(layer: str, *, null_score: bool = False, invalid_coverage: bool = False) -> dict:
    properties: dict[str, object] = {key: 1 for key in verifier.REQUIRED_WFS_PROPERTIES[layer]}
    properties.update({
        "nuts_code": "DEA11",
        "district_name": "Düsseldorf",
        "ev_readiness_score": None if null_score else 50.0,
        "charger_deficit_score": 50.0,
        "infrastructure_opportunity_score": 50.0,
        "investment_priority_score": 50.0,
        "priority_rank": 1,
        "source_id": "source-1",
        "operator": "Operator",
        "status": "In Betrieb",
        "charger_type": "normal",
        "power_kw": 150.0,
        "max_point_power_kw": 150.0,
        "charging_points": 4,
        "road_class": "regional",
        "traffic_total": 1000.0,
        "traffic_length_coverage": -0.1 if invalid_coverage else 1.0,
        "highway": "A1",
        "technology": "Windenergie",
        "asset_type": "generator",
        "renewable_capacity_mw": 10.0,
        "renewable_installation_count": 1,
        "id": STATION_ID,
        "name": "owned S18 probe",
        "request_id": REQUEST_ID,
    })
    if layer == "nrw_ev_baseline_metrics":
        properties.update({"formula_version": "fixture", "data_quality_flag": "complete_proxy_inputs", "transport_data_quality_flag": "complete_traffic_coverage", "grid_data_quality_flag": "complete_grid_inputs", "energy_data_quality_flag": "hybrid_complete", "energy_unavailable_reason": None, "formula_version_date": "2026-01-01", "energy_source": "fixture", "wind_estimate_note": "fixture", "charger_snapshot_source": "fixture"})
    elif layer == "nrw_ev_scenario_metrics":
        properties.update({"scenario_chargers_total": 10, "scenario_charging_points_total": 24})
    geometry_type = verifier.LAYER_CONTRACTS[layer].geometry
    coordinates: object = [6.7735, 51.2277]
    if geometry_type == "LineString":
        coordinates = [[6.7, 51.2], [6.8, 51.3]]
    if geometry_type == "MultiPolygon":
        coordinates = [[[[6.7, 51.2], [6.8, 51.2], [6.8, 51.3], [6.7, 51.2]]]]
    return {"type": "Feature", "properties": properties, "geometry": {"type": geometry_type, "coordinates": coordinates}}


class ContractSession:
    def __init__(
        self,
        *,
        wrong_namespace: str | None = None,
        missing_field: str | None = None,
        null_score: bool = False,
        invalid_coverage: bool = False,
        property_updates: dict[str, object] | None = None,
        charger_updates: dict[str, object] | None = None,
        renewable_updates: dict[str, object] | None = None,
    ) -> None:
        self.wrong_namespace = wrong_namespace
        self.missing_field = missing_field
        self.null_score = null_score
        self.invalid_coverage = invalid_coverage
        self.property_updates = property_updates or {}
        self.charger_updates = charger_updates or {}
        self.renewable_updates = renewable_updates or {}

    def get(self, _url: str, *, params: dict, timeout: int) -> FakeResponse:
        del timeout
        layer = params.get("typeNames", "").removeprefix("nrw:")
        if params.get("request") == "DescribeFeatureType":
            return FakeResponse(text=_schema(layer, namespace=self.wrong_namespace or "https://nrw.local/scenario", missing=self.missing_field))
        count = 53 if layer == "nrw_ev_baseline_metrics" else 1
        features = [_feature(layer, null_score=self.null_score, invalid_coverage=self.invalid_coverage) for _ in range(count)]
        if layer == "nrw_ev_baseline_metrics":
            for index, feature in enumerate(features, start=1):
                feature["properties"]["nuts_code"] = f"DEA{index:02d}"
                feature["properties"].update(self.property_updates)
        if layer == "nrw_chargers":
            for feature in features:
                feature["properties"].update(self.charger_updates)
        if layer == "nrw_renewable_potential":
            for feature in features:
                feature["properties"].update(self.renewable_updates)
        return FakeResponse(payload={"features": features})


class MutationSession:
    def __init__(self, *, insert_status: int = 200, forbidden_status: int = 403) -> None:
        self.insert_status = insert_status
        self.forbidden_status = forbidden_status
        self.posts: list[str] = []
        self.deleted: list[str] = []
        self.metrics_reads = 0
        self.insert_name = "owned S18 probe"
        self.insert_request_id = REQUEST_ID

    def get(self, _url: str, *, params: dict | None = None, timeout: int = 60) -> FakeResponse:
        del timeout
        if params is None:
            return FakeResponse()
        if params.get("request") == "GetFeature" and params.get("typeNames") == "nrw:proposed_chargers":
            if not self.posts or self.deleted:
                return FakeResponse(payload={"features": []})
            query = params.get("CQL_FILTER", "")
            request_match = re.search(r"request_id='([^']+)'", query)
            return FakeResponse(
                payload={"features": [{**_feature("proposed_chargers"), "properties": {
                    **_feature("proposed_chargers")["properties"],
                    "name": self.insert_name,
                    "request_id": request_match.group(1) if request_match else self.insert_request_id,
                }}]}
            )
        if params.get("request") == "GetFeature" and params.get("typeNames") == "nrw:nrw_ev_scenario_metrics":
            self.metrics_reads += 1
            return FakeResponse(payload={"features": [_feature("nrw_ev_scenario_metrics")]})
        return FakeResponse()

    def post(self, _url: str, *, data: bytes, headers: dict, timeout: int) -> FakeResponse:
        del headers, timeout
        document = data.decode("utf-8")
        self.posts.append(document)
        if "<wfs:Insert>" in document:
            name_match = re.search(r"<nrw:name>([^<]+)</nrw:name>", document)
            request_match = re.search(r"<nrw:request_id>([^<]+)</nrw:request_id>", document)
            if name_match:
                self.insert_name = name_match.group(1)
            if request_match:
                self.insert_request_id = request_match.group(1)
            if self.insert_status != 200:
                return FakeResponse(status_code=self.insert_status, text="upstream response uncertain")
            return FakeResponse(text=f'<ogc:FeatureId fid="proposed_chargers.{STATION_ID}"/>')
        if "<wfs:Delete" in document and "proposed_chargers" in document:
            self.deleted.append(document)
            return FakeResponse()
        return FakeResponse(status_code=self.forbidden_status, text="Forbidden")


class S18NegativeCasesTest(unittest.TestCase):
    def write_runtime_snapshot(
        self,
        root: Path,
        *,
        technology: str = "Windenergie",
        status: str = "In Betrieb",
    ) -> None:
        current = root / "current"
        current.mkdir(parents=True, exist_ok=True)
        collection = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "source_id": "renewable-1",
                    "technology": technology,
                    "status": status,
                },
                "geometry": {"type": "Point", "coordinates": [7.0, 51.0]},
            }],
        }
        content = json.dumps(collection, sort_keys=True).encode("utf-8")
        (current / "nrw_renewable_assets_sample.geojson").write_bytes(content)
        manifest = {
            "renewable_display_filter": {
                "status": "In Betrieb",
                "technologies": ["Photovoltaik Freifläche", "Windenergie"],
            },
            "artifacts": {
                "renewables": {
                    "file": "nrw_renewable_assets_sample.geojson",
                    "count": 1,
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            },
        }
        (current / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_catalogue_wfs_permits_non_display_renewables(self) -> None:
        verifier.verify_wfs_contract(
            ContractSession(renewable_updates={"technology": "Photovoltaik Bauliche", "status": "Außer Betrieb"}),
            "http://wfs/ows",
        )

    def test_runtime_snapshot_enforces_renewable_display_filter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_runtime_snapshot(root)
            verifier.verify_runtime_display_snapshot(root)
            self.write_runtime_snapshot(root, technology="Photovoltaik Bauliche")
            with self.assertRaisesRegex(RuntimeError, "non-display technology"):
                verifier.verify_runtime_display_snapshot(root)

    def test_missing_layer_fails_contract(self) -> None:
        class MissingLayer(ContractSession):
            def get(self, url: str, *, params: dict, timeout: int) -> FakeResponse:
                if params.get("typeNames") == "nrw:nrw_chargers" and params.get("request") == "DescribeFeatureType":
                    return FakeResponse(status_code=404, text="No such feature type")
                return super().get(url, params=params, timeout=timeout)

        with self.assertRaises(Exception):
            verifier.verify_wfs_contract(MissingLayer(), "http://wfs/ows")

    def test_wrong_namespace_fails_contract(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "namespace"):
            verifier.verify_wfs_contract(ContractSession(wrong_namespace="http://foreign.example/scenario"), "http://wfs/ows")

    def test_missing_required_field_fails_contract(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "max_point_power_kw"):
            verifier.verify_wfs_contract(ContractSession(missing_field="max_point_power_kw"), "http://wfs/ows")

    def test_null_required_score_component_fails_contract(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "null|unavailable|required"):
            verifier.verify_wfs_contract(ContractSession(null_score=True), "http://wfs/ows")

    def test_complete_proxy_requires_all_score_components_and_ranges(self) -> None:
        for updates in (
            {"grid_readiness_proxy_score": None},
            {"charger_deficit_score": None},
        ):
            with self.subTest(updates=updates), self.assertRaisesRegex(RuntimeError, "unavailable|required"):
                verifier.verify_wfs_contract(
                    ContractSession(property_updates=updates),
                    "http://wfs/ows",
                )
        with self.assertRaisesRegex(RuntimeError, "range"):
            verifier.verify_wfs_contract(
                ContractSession(property_updates={"traffic_intensity_score": 101}),
                "http://wfs/ows",
            )

    def test_unavailable_energy_is_allowed_only_with_its_quality_reason(self) -> None:
        for updates in (
            {
                "energy_data_quality_flag": "missing_required_input",
                "energy_unavailable_reason": "zero_consumption_denominator",
                "local_energy_balance_score": None,
                "renewable_growth_score": 61.5,
                "grid_absorption_risk_proxy_score": None,
            },
            {
                "energy_data_quality_flag": "missing_required_input",
                "energy_unavailable_reason": "incomplete_growth_window",
                "local_energy_balance_score": 48.2,
                "renewable_growth_score": None,
                "grid_absorption_risk_proxy_score": None,
            },
        ):
            with self.subTest(updates=updates):
                verifier.verify_wfs_contract(
                    ContractSession(property_updates=updates), "http://wfs/ows"
                )
        with self.assertRaisesRegex(RuntimeError, "energy risk"):
            verifier.verify_wfs_contract(
                ContractSession(property_updates={
                    "energy_data_quality_flag": "missing_required_input",
                    "energy_unavailable_reason": "incomplete_growth_window",
                    "renewable_growth_score": None,
                }),
                "http://wfs/ows",
            )

    def test_invalid_score_or_coverage_fails_contract(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "range|coverage|score|invalid"):
            verifier.verify_wfs_contract(ContractSession(invalid_coverage=True), "http://wfs/ows")

    def test_charger_public_semantics_reject_invalid_known_values_but_allow_unknown_power(self) -> None:
        verifier.verify_wfs_contract(
            ContractSession(charger_updates={"power_kw": None, "max_point_power_kw": None}),
            "http://wfs/ows",
        )
        for updates in (
            {"charging_points": None},
            {"max_point_power_kw": "NaN"},
            {"max_point_power_kw": "Infinity"},
            {"power_kw": -1},
        ):
            with self.subTest(updates=updates), self.assertRaisesRegex(RuntimeError, "charging_points|power_kw"):
                verifier.verify_wfs_contract(ContractSession(charger_updates=updates), "http://wfs/ows")

    def test_intended_write_denial_is_a_failure_response(self) -> None:
        response = MutationSession(insert_status=403).post("http://wfs/ows", data=verifier.insert_xml("owned", REQUEST_ID).encode(), headers={}, timeout=60)
        with self.assertRaisesRegex(RuntimeError, "transaction failed"):
            verifier.post_transaction(MutationSession(insert_status=403), "http://wfs/ows", verifier.insert_xml("owned", REQUEST_ID))
        self.assertTrue(verifier.is_exception_response(response.status_code, response.text))

    def test_accepted_forbidden_write_fails_verification(self) -> None:
        session = MutationSession(forbidden_status=200)
        before = {"scenario_chargers_total": 10, "scenario_charging_points_total": 24}
        after = {"scenario_chargers_total": 11, "scenario_charging_points_total": 28}
        with (
            patch.object(verifier, "verify_wfs_contract"),
            patch.object(verifier, "verify_catalog"),
            patch.object(verifier, "district_metrics", side_effect=[before, after]),
            patch.object(verifier, "assert_scenario_formula_change"),
            self.assertRaisesRegex(RuntimeError, "unexpectedly accepted"),
        ):
            verifier.verify(session, frontend_url="http://frontend", geonode_url="http://geonode")
        self.assertEqual(len(session.deleted), 1)
        self.assertIn(STATION_ID, session.deleted[0])

    def test_uncertain_insert_reconciles_by_request_id_and_owned_cleanup(self) -> None:
        """A timeout-after-commit must query request_id before retrying or failing."""
        session = MutationSession(insert_status=504)
        before = {"scenario_chargers_total": 10, "scenario_charging_points_total": 24}
        after = {"scenario_chargers_total": 11, "scenario_charging_points_total": 28}
        with (
            patch.object(verifier, "verify_wfs_contract"),
            patch.object(verifier, "verify_catalog"),
            patch.object(verifier, "district_metrics", side_effect=[before, after, before]),
            patch.object(verifier, "assert_scenario_formula_change"),
            patch.object(verifier, "assert_metric_restoration"),
        ):
            verifier.verify(session, frontend_url="http://frontend", geonode_url="http://geonode")
        inserts = [body for body in session.posts if "<wfs:Insert>" in body]
        self.assertEqual(len(inserts), 1, "reconciliation must not duplicate the proposal")
        self.assertEqual(len(session.deleted), 1)
        self.assertIn(STATION_ID, session.deleted[0])
        self.assertNotIn("pre-existing", session.deleted[0])


if __name__ == "__main__":
    unittest.main()
