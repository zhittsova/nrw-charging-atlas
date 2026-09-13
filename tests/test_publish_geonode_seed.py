from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "publish_geonode_seed.py"


def seed_payload() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"nuts_code": f"DEA{index:02d}", "box": [6.3, 50.3, 6.9, 50.8]},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[[[6.3, 50.3], [6.9, 50.3], [6.9, 50.8], [6.3, 50.3]]]],
                },
            }
            for index in range(1, 54)
        ],
    }


def write_seed(directory: str) -> Path:
    path = Path(directory) / "nrw_regions_sample.geojson"
    path.write_text(json.dumps(seed_payload()), encoding="utf-8")
    return path


def load_module():
    spec = importlib.util.spec_from_file_location("publish_geonode_seed", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class SeedValidationTest(unittest.TestCase):
    def test_seed_contract_requires_53_nrw_multipolygons(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            data = module.validate_seed(write_seed(directory))

        self.assertEqual(len(data["features"]), 53)

    def test_seed_contract_requires_each_nrw_nuts3_code_once(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            data = module.validate_seed(write_seed(directory))
        codes = [feature["properties"]["nuts_code"] for feature in data["features"]]

        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(code.startswith("DEA") for code in codes))

    def test_seed_rejects_non_nrw_feature(self) -> None:
        """Catches accidentally publishing a district outside NRW."""
        module = load_module()
        data = seed_payload()
        data["features"][0]["properties"]["nuts_code"] = "DE999"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.geojson"
            path.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "DEA"):
                module.validate_seed(path)

    def test_credentials_are_read_without_exporting_the_env_file(self) -> None:
        """Catches a parser that loses quoted administrator credentials."""
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("ADMIN_USERNAME=admin\nADMIN_PASSWORD='safe value'\n", encoding="utf-8")

            self.assertEqual(module.read_geonode_credentials(path), ("admin", "safe value"))

    def test_upload_copy_removes_unsupported_presentation_properties(self) -> None:
        """Catches passing list-valued frontend metadata to GeoNode's tabular importer."""
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            upload_path = Path(directory) / "nrw_nuts3_districts.geojson"
            source_path = write_seed(directory)

            module.prepare_upload_seed(source_path, upload_path)

            original = json.loads(source_path.read_text(encoding="utf-8"))
            uploaded = json.loads(upload_path.read_text(encoding="utf-8"))
        self.assertEqual(
            original["features"][0]["properties"]["box"],
            [6.3, 50.3, 6.9, 50.8],
        )
        self.assertEqual(uploaded["name"], "nrw_nuts3_districts")
        self.assertNotIn("box", uploaded["features"][0]["properties"])
        self.assertNotIn("accessibilityScore", uploaded["features"][0]["properties"])


class UploadExecutionTest(unittest.TestCase):
    def test_catalog_lookup_accepts_geonode_5_1_alternate_identifier(self) -> None:
        """Catches treating GeoNode 5.1's catalogue ``alternate`` as an absent dataset."""
        module = load_module()

        class GeoNode51CatalogSession:
            def get(self, *_args, **_kwargs):
                return Response(
                    {
                        "resources": [
                            {
                                "pk": "1",
                                "title": "nrw_nuts3_districts",
                                "alternate": "geonode:nrw_nuts3_districts",
                                "resource_type": "dataset",
                            }
                        ]
                    }
                )

        resource = module.find_resource(GeoNode51CatalogSession(), "http://localhost:8000")

        self.assertEqual(resource["pk"], "1")

    def test_catalog_lookup_rejects_name_when_alternate_is_for_another_layer(self) -> None:
        """Catches selecting a same-named dataset from a different GeoNode workspace."""
        module = load_module()

        class WrongWorkspaceSession:
            def get(self, *_args, **_kwargs):
                return Response(
                    {
                        "resources": [
                            {
                                "pk": "2",
                                "name": "nrw_nuts3_districts",
                                "alternate": "other:nrw_nuts3_districts",
                                "resource_type": "dataset",
                            }
                        ]
                    }
                )

        self.assertIsNone(module.find_resource(WrongWorkspaceSession(), "http://localhost:8000"))

    def test_failed_execution_raises_with_server_log(self) -> None:
        """Catches an upload poller that hides the GeoNode failure diagnosis."""
        module = load_module()

        class FailedExecutionSession:
            def get(self, *_args, **_kwargs):
                return Response({"status": "failed", "log": "ogr2ogr failed"})

        with self.assertRaisesRegex(RuntimeError, "ogr2ogr failed"):
            module.wait_for_execution(
                FailedExecutionSession(), "http://localhost:8000", "abc", timeout=0.1
            )

    def test_existing_dataset_reapplies_public_download_without_uploading(self) -> None:
        """Catches a rerun that uploads a duplicate instead of repairing public access."""
        module = load_module()

        class ExistingResourceSession:
            def __init__(self) -> None:
                self.patch_payload: dict | None = None

            def get(self, *_args, **_kwargs):
                return Response(
                    {
                        "resources": [
                            {
                                "pk": 17,
                                "name": "nrw_nuts3_districts",
                                "resource_type": "dataset",
                            }
                        ]
                    }
                )

            def patch(self, _url, **kwargs):
                self.patch_payload = kwargs["json"]
                return Response({})

            def post(self, *_args, **_kwargs):
                raise AssertionError("an existing dataset must not be uploaded again")

        session = ExistingResourceSession()
        with tempfile.TemporaryDirectory() as directory:
            result = module.publish_seed(session, "http://localhost:8000", write_seed(directory))

        self.assertEqual(result, "already-present")
        self.assertEqual(
            session.patch_payload,
            {
                "groups": [],
                "organizations": [],
                "users": [{"id": -1, "permissions": "download"}],
            },
        )

    def test_permission_execution_failure_is_reported_before_wfs_verification(self) -> None:
        """Catches treating GeoNode's asynchronous permission update as immediately complete."""
        module = load_module()

        class FailedPermissionSession:
            def patch(self, *_args, **_kwargs):
                return Response({"execution_id": "permission-1"})

            def get(self, *_args, **_kwargs):
                return Response({"status": "failed", "log": "anonymous download denied"})

        with self.assertRaisesRegex(RuntimeError, "anonymous download denied"):
            module.set_public_download(FailedPermissionSession(), "http://localhost:8000", 17)

    def test_synchronous_permission_response_without_json_does_not_poll(self) -> None:
        """Catches requiring an execution id from a synchronous no-content permission response."""
        module = load_module()

        class NoContentResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                raise ValueError("no JSON body")

        class SynchronousPermissionSession:
            def patch(self, *_args, **_kwargs):
                return NoContentResponse()

            def get(self, *_args, **_kwargs):
                raise AssertionError("a synchronous response must not be polled")

        module.set_public_download(SynchronousPermissionSession(), "http://localhost:8000", 17)

    def test_wfs_verification_requires_exact_feature_count(self) -> None:
        """Catches a successful but incomplete public WFS response."""
        module = load_module()

        class EmptyWfsSession:
            def get(self, *_args, **_kwargs):
                return Response({"type": "FeatureCollection", "features": []})

        with self.assertRaisesRegex(RuntimeError, "expected 53"):
            module.verify_wfs(EmptyWfsSession(), "http://localhost:8080/geoserver")


if __name__ == "__main__":
    unittest.main()
