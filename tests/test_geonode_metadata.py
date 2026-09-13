from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_FIXTURE = ROOT / "tests" / "fixtures" / "geonode_metadata_manifest.json"
MODULE_PATH = ROOT / "scripts" / "geonode_metadata.py"
SPEC = importlib.util.spec_from_file_location("geonode_metadata", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class MetadataSession:
    def __init__(self) -> None:
        self.instances = {
            index: {
                "abstract": "NRW energy infrastructure intelligence project layer",
                "hkeywords": ["user-retained keyword"],
                "license": {"id": 1, "label": "Not Specified"},
                "supplemental_information": "",
            }
            for index in range(1, len(module.LAYER_METADATA) + 1)
        }
        self.patches: list[tuple[str, dict]] = []
        self.sparse: list[tuple[str, dict]] = []
        self.sparse_values: dict[str, str] = {}

    def get(self, url: str, **kwargs) -> Response:
        if url.endswith("/api/v2/resources/"):
            if kwargs.get("params", {}).get("page", 1) > 1:
                return Response({"resources": [], "total": len(module.LAYER_METADATA)})
            return Response(
                {
                    "resources": [
                        {
                            "pk": index,
                            "name": layer,
                            "alternate": f"nrw:{layer}",
                            "resource_type": "dataset",
                        }
                        for index, layer in enumerate(module.LAYER_METADATA, start=1)
                    ]
                }
            )
        if "/autocomplete/licenses" in url:
            name = kwargs["params"]["q"]
            return Response({"results": [{"id": 2, "label": name}]})
        if "/metadata/sparse/" in url:
            if url not in self.sparse_values:
                return Response({}, 404)
            return Response({"value": self.sparse_values[url]})
        pk = int(url.rstrip("/").split("/")[-1])
        return Response(self.instances[pk])

    def patch(self, url: str, **kwargs) -> Response:
        self.patches.append((url, kwargs["json"]))
        pk = int(url.rstrip("/").split("/")[-1])
        self.instances[pk].update(kwargs["json"])
        return Response({"extraErrors": {}})

    def put(self, url: str, **kwargs) -> Response:
        self.sparse.append((url, kwargs["json"]))
        self.sparse_values[url] = kwargs["json"]["value"]
        return Response({})


class GeoNodeMetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest_patch = patch.object(module, "RUNTIME_MANIFEST", MANIFEST_FIXTURE)
        self.manifest_patch.start()

    def tearDown(self) -> None:
        self.manifest_patch.stop()
    def test_layer_metadata_covers_every_published_layer_and_scenario(self) -> None:
        provision_spec = importlib.util.spec_from_file_location(
            "provision_geoserver_layers", ROOT / "scripts" / "provision_geoserver_layers.py"
        )
        provision = importlib.util.module_from_spec(provision_spec)
        assert provision_spec.loader is not None
        sys.modules[provision_spec.name] = provision
        provision_spec.loader.exec_module(provision)

        self.assertEqual(set(module.LAYER_METADATA), set(provision.PUBLISH_LAYERS) | {provision.SCENARIO_LAYER})

    def test_single_source_layers_use_their_actual_upstream_terms(self) -> None:
        self.assertEqual(module.LAYER_METADATA["nrw_chargers"].licence, module.CC_BY_4)
        self.assertEqual(module.LAYER_METADATA["nrw_regional_roads"].licence, module.DL_DE_BY_2)
        self.assertEqual(module.LAYER_METADATA["nrw_renewable_potential"].licence, module.DL_DE_ZERO_2)
        self.assertEqual(module.LAYER_METADATA["nrw_accessibility"].licence, module.DL_DE_BY_2)
        self.assertEqual(module.LAYER_METADATA["nrw_grid_proxy"].licence, "Varied / Derived")

    def test_every_scoring_source_is_traceable_from_published_metadata(self) -> None:
        referenced = {source_id for metadata in module.LAYER_METADATA.values() for source_id in metadata.source_ids}
        self.assertTrue(set(module.ALL_SCORING_SOURCES).issubset(referenced))

    def test_exact_workspace_identity_rejects_foreign_and_ambiguous_resources(self) -> None:
        foreign = {"pk": 8, "name": "nrw_chargers", "alternate": "other:nrw_chargers", "resource_type": "dataset"}
        local = {**foreign, "pk": 9, "alternate": "nrw:nrw_chargers"}
        self.assertEqual(module.resource_for_layer([foreign, local], "nrw_chargers")["pk"], 9)
        self.assertIsNone(module.resource_for_layer([foreign], "nrw_chargers"))
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            module.resource_for_layer([local, {**local, "pk": 10}], "nrw_chargers")

    def test_sync_replaces_generic_fields_and_preserves_user_keyword(self) -> None:
        session = MetadataSession()

        result = module.sync_metadata(session, "http://metadata.test")

        self.assertEqual(result, {"datasets": len(module.LAYER_METADATA), "changed": len(module.LAYER_METADATA)})
        self.assertGreaterEqual(len(session.sparse), len(module.LAYER_METADATA))
        first_patch = session.patches[0][1]
        self.assertIn("user-retained keyword", first_patch["hkeywords"])
        self.assertNotIn("Not Specified", first_patch["license"]["label"])
        self.assertTrue(first_patch["supplemental_information"].startswith(module.MANAGED_SUPPLEMENTAL_PREFIX))
        provenance = json.loads(next(value["value"] for url, value in session.sparse if url.endswith(module.PROVENANCE_FIELD)))
        self.assertEqual(provenance["field_owner"], "NRW project")
        self.assertEqual(module.sync_metadata(session, "http://metadata.test")["changed"], 0)

    def test_meaningful_user_fields_are_not_overwritten(self) -> None:
        catalog = module.source_catalog()
        current = {
            "abstract": "User-authored field notes",
            "hkeywords": ["community review"],
            "license": {"id": 7, "label": "User selected licence"},
            "supplemental_information": "User-authored caveat",
        }

        update = module.managed_update(current, module.LAYER_METADATA["nrw_chargers"], catalog)

        self.assertNotIn("abstract", update)
        self.assertNotIn("license_name", update)
        self.assertNotIn("supplemental_information", update)
        self.assertIn("community review", update["hkeywords"])

    def test_managed_licence_is_migrated_but_user_selected_licence_is_not(self) -> None:
        catalog = module.source_catalog()
        managed = {
            "license": {"label": "Open Data Commons Open Database License / OSM"},
            "supplemental_information": f"{module.MANAGED_SUPPLEMENTAL_PREFIX} prior run",
        }
        user_owned = {**managed, "supplemental_information": "User-authored ownership note"}
        self.assertEqual(
            module.managed_update(managed, module.LAYER_METADATA["nrw_grid_proxy"], catalog, {"license": "Open Data Commons Open Database License / OSM"})["license_name"],
            "Varied / Derived",
        )
        self.assertNotIn("license_name", module.managed_update(user_owned, module.LAYER_METADATA["nrw_grid_proxy"], catalog))

    def test_two_pass_and_later_user_edit_preserve_user_licence(self) -> None:
        session = MetadataSession()
        session.instances[1]["license"] = {"id": 77, "label": "User selected licence"}
        module.sync_metadata(session, "http://metadata.test")
        self.assertEqual(session.instances[1]["license"]["label"], "User selected licence")
        module.sync_metadata(session, "http://metadata.test")
        self.assertEqual(session.instances[1]["license"]["label"], "User selected licence")
        session.instances[2]["license"] = {"id": 88, "label": "User edited licence"}
        module.sync_metadata(session, "http://metadata.test")
        self.assertEqual(session.instances[2]["license"]["label"], "User edited licence")

    def test_worker_count_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            module.sync_metadata(MetadataSession(), "http://metadata.test", workers=0)

    def test_transient_metadata_transport_failure_is_retried(self) -> None:
        class Flaky:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, _url: str, **_kwargs) -> Response:
                self.calls += 1
                if self.calls == 1:
                    raise module.requests.ConnectionError("temporary")
                return Response({})

        session = Flaky()
        with patch.object(module.time, "sleep"):
            self.assertEqual(module.api_call(session, "get", "http://metadata.test").json(), {})
        self.assertEqual(session.calls, 2)

    def test_provenance_uses_successful_ingest_and_retains_unavailable_dates(self) -> None:
        catalog = module.source_catalog()
        values = module.provenance_values(module.LAYER_METADATA["nrw_chargers"], catalog)
        value = json.loads(values[module.PROVENANCE_FIELD])

        source = value["sources"][0]
        self.assertEqual(source["id"], "bnetza_charging_register_nrw")
        self.assertIn("terms", source)
        self.assertIn("source_date", source)
        self.assertEqual(source["source_date"], "2026-04-22")
        self.assertTrue(source["terms"].startswith(module.CC_BY_4))
        self.assertEqual(source["ingest_status"], "recorded without upstream sidecar")
        self.assertEqual(value["formula_version"], "test-formula-v1")


if __name__ == "__main__":
    unittest.main()
