from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "provision_geoserver_layers.py"
SPEC = importlib.util.spec_from_file_location("provision_geoserver_layers", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RecordingSession:
    def __init__(self, *, existing: bool = False) -> None:
        self.existing = existing
        self.auth = None
        self.calls: list[tuple[str, str, dict]] = []

    def _response(self, method: str, url: str, kwargs: dict) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if method == "GET" and url.endswith("/rest/services/wfs/settings.json"):
            return FakeResponse(200, {"wfs": {"serviceLevel": "BASIC"}})
        if method == "GET" and url.endswith("/rest/security/acl/layers.json"):
            return FakeResponse(200, {})
        if method == "GET":
            return FakeResponse(200 if self.existing else 404)
        return FakeResponse(200)

    def get(self, url: str, **kwargs) -> FakeResponse:
        return self._response("GET", url, kwargs)

    def post(self, url: str, **kwargs) -> FakeResponse:
        return self._response("POST", url, kwargs)

    def put(self, url: str, **kwargs) -> FakeResponse:
        return self._response("PUT", url, kwargs)


def config() -> object:
    return module.GeoServerConfig(
        base_url="http://localhost:8080/geoserver",
        admin_user="admin",
        admin_password="secret",
        database_host="db",
        database_port=5432,
        database_name="nrw_gis",
        publish_user="nrw_geoserver_read",
        publish_password="read-secret",
        scenario_user="nrw_geoserver_scenario",
        scenario_password="write-secret",
    )


class GeoServerProvisioningTest(unittest.TestCase):
    def test_database_default_honors_environment_configuration(self) -> None:
        values = {
            "GEOSERVER_ADMIN_USER": "admin",
            "GEOSERVER_ADMIN_PASSWORD": "secret",
            "NRW_GEOSERVER_READ_USER": "reader",
            "NRW_GEOSERVER_READ_PASSWORD": "reader-secret",
            "NRW_GEOSERVER_SCENARIO_USER": "writer",
            "NRW_GEOSERVER_SCENARIO_PASSWORD": "writer-secret",
            "NRW_DATABASE_NAME": "nrw_alternate",
        }
        with (
            patch.dict(os.environ, values, clear=True),
            patch.object(module, "read_env", return_value={}),
            patch.object(module, "provision_geoserver") as provision,
            patch.object(sys, "argv", ["provision_geoserver_layers.py"]),
        ):
            module.main()

        self.assertEqual(provision.call_args.args[1].database_name, "nrw_alternate")

    def test_selected_env_file_database_name_reaches_publication(self) -> None:
        values = {
            "GEOSERVER_ADMIN_USER": "admin",
            "GEOSERVER_ADMIN_PASSWORD": "secret",
            "NRW_GEOSERVER_READ_USER": "reader",
            "NRW_GEOSERVER_READ_PASSWORD": "reader-secret",
            "NRW_GEOSERVER_SCENARIO_USER": "writer",
            "NRW_GEOSERVER_SCENARIO_PASSWORD": "writer-secret",
        }
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / "alternate.env"
            env_path.write_text("NRW_DATABASE_NAME=nrw_from_file\n", encoding="utf-8")
            with (
                patch.dict(os.environ, values, clear=True),
                patch.object(module, "provision_geoserver") as provision,
                patch.object(sys, "argv", ["provision_geoserver_layers.py", "--env-file", str(env_path)]),
            ):
                module.main()

        self.assertEqual(provision.call_args.args[1].database_name, "nrw_from_file")

    def test_explicit_database_name_beats_process_and_selected_file_for_publication(self) -> None:
        values = {
            "GEOSERVER_ADMIN_USER": "admin",
            "GEOSERVER_ADMIN_PASSWORD": "secret",
            "NRW_GEOSERVER_READ_USER": "reader",
            "NRW_GEOSERVER_READ_PASSWORD": "reader-secret",
            "NRW_GEOSERVER_SCENARIO_USER": "writer",
            "NRW_GEOSERVER_SCENARIO_PASSWORD": "writer-secret",
            "NRW_DATABASE_NAME": "nrw_from_process",
        }
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / "alternate.env"
            env_path.write_text("NRW_DATABASE_NAME=nrw_from_file\n", encoding="utf-8")
            with (
                patch.dict(os.environ, values, clear=True),
                patch.object(module, "provision_geoserver") as provision,
                patch.object(
                    sys,
                    "argv",
                    [
                        "provision_geoserver_layers.py",
                        "--env-file",
                        str(env_path),
                        "--database-name",
                        "nrw_from_cli",
                    ],
                ),
            ):
                module.main()

        self.assertEqual(provision.call_args.args[1].database_name, "nrw_from_cli")

    def test_empty_explicit_database_name_fails_before_publication(self) -> None:
        values = {
            "GEOSERVER_ADMIN_USER": "admin",
            "GEOSERVER_ADMIN_PASSWORD": "secret",
            "NRW_GEOSERVER_READ_USER": "reader",
            "NRW_GEOSERVER_READ_PASSWORD": "reader-secret",
            "NRW_GEOSERVER_SCENARIO_USER": "writer",
            "NRW_GEOSERVER_SCENARIO_PASSWORD": "writer-secret",
        }
        with (
            patch.dict(os.environ, values, clear=True),
            patch.object(module, "read_env", return_value={}),
            patch.object(module, "provision_geoserver") as provision,
            patch.object(sys, "argv", ["provision_geoserver_layers.py", "--database-name", ""]),
        ):
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                module.main()

        provision.assert_not_called()

    def test_declares_autobahn_and_regional_road_layers(self) -> None:
        self.assertEqual(module.PUBLISH_LAYERS["nrw_autobahns"], "NRW Autobahns")
        self.assertEqual(module.PUBLISH_LAYERS["nrw_regional_roads"], "NRW Federal and State Roads")

    def test_flattens_both_supported_acl_json_shapes(self) -> None:
        direct = {"rules": {"nrw.*.r": "*", "nrw.proposed_chargers.w": "*"}}
        entries = {
            "rules": {
                "rule": [
                    {"@resource": "nrw.*.r", "$": "*"},
                    {"@resource": "nrw.proposed_chargers.w", "$": "*"},
                ]
            }
        }

        expected = {"nrw.*.r": "*", "nrw.proposed_chargers.w": "*"}
        self.assertEqual(module._flatten_rules(direct), expected)
        self.assertEqual(module._flatten_rules(entries), expected)

    def test_creates_schema_isolated_stores_and_every_declared_layer(self) -> None:
        session = RecordingSession()

        module.provision_geoserver(session, config())

        post_payloads = [call[2].get("json") for call in session.calls if call[0] == "POST"]
        stores = [payload["dataStore"] for payload in post_payloads if payload and "dataStore" in payload]
        self.assertEqual({store["name"] for store in stores}, {"nrw_publish", "nrw_scenario"})
        parameters = {
            store["name"]: {
                entry["@key"]: entry["$"]
                for entry in store["connectionParameters"]["entry"]
            }
            for store in stores
        }
        self.assertEqual(parameters["nrw_publish"]["schema"], "publish")
        self.assertEqual(parameters["nrw_publish"]["user"], "nrw_geoserver_read")
        self.assertEqual(parameters["nrw_scenario"]["schema"], "scenario")
        self.assertEqual(parameters["nrw_scenario"]["user"], "nrw_geoserver_scenario")

        feature_types = [
            payload["featureType"]["name"]
            for payload in post_payloads
            if payload and "featureType" in payload
        ]
        self.assertEqual(set(feature_types), set(module.PUBLISH_LAYERS) | {module.SCENARIO_LAYER})
        self.assertEqual(len(feature_types), len(module.PUBLISH_LAYERS) + 1)

    def test_existing_catalog_resources_are_not_created_again(self) -> None:
        session = RecordingSession(existing=True)

        module.provision_geoserver(session, config())

        catalog_posts = [
            call for call in session.calls
            if call[0] == "POST" and "/rest/security/" not in call[1]
        ]
        self.assertEqual(catalog_posts, [])

    def test_enables_transactions_and_limits_anonymous_write_to_scenario_layer(self) -> None:
        session = RecordingSession()

        module.provision_geoserver(session, config())

        wfs_updates = [
            call for call in session.calls
            if call[0] == "PUT" and call[1].endswith("/rest/services/wfs/settings.json")
        ]
        self.assertEqual(wfs_updates[0][2]["json"], {"wfs": {"serviceLevel": "TRANSACTIONAL"}})
        security_posts = [
            call for call in session.calls
            if call[0] == "POST" and call[1].endswith("/rest/security/acl/layers.json")
        ]
        rules = security_posts[0][2]["json"]
        self.assertEqual(rules["nrw.proposed_chargers.w"], "*")
        self.assertEqual(rules["nrw.*.r"], "*")
        self.assertTrue(all(
            rules[f"nrw.{layer}.w"] == "ROLE_ADMINISTRATOR"
            for layer in module.PUBLISH_LAYERS
        ))
        self.assertNotIn("nrw.*.w", rules)

    def test_geonode_sync_commands_apply_read_only_and_scenario_permissions(self) -> None:
        commands = module.geonode_sync_commands(admin_username="admin")

        self.assertEqual(len(commands), 2)
        publish = " ".join(commands[0])
        scenario = " ".join(commands[1])
        self.assertIn("--store nrw_publish", publish)
        self.assertNotIn("change_dataset_data", publish)
        self.assertIn("--store nrw_scenario", scenario)
        self.assertIn("change_dataset_data", scenario)
        self.assertIn("AnonymousUser", publish)
        self.assertIn("AnonymousUser", scenario)


if __name__ == "__main__":
    unittest.main()
