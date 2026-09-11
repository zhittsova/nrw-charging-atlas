from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote
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
        if method == "GET" and url.endswith("/rest/security/acl/services.json"):
            return FakeResponse(200, {"wfs.Transaction": "ROLE_AUTHENTICATED"})
        if method == "GET" and url.endswith("/rest/geofence/rules.json"):
            return FakeResponse(200, {"rules": []})
        if method == "GET":
            return FakeResponse(200 if self.existing else 404)
        return FakeResponse(200)

    def get(self, url: str, **kwargs) -> FakeResponse:
        return self._response("GET", url, kwargs)

    def post(self, url: str, **kwargs) -> FakeResponse:
        return self._response("POST", url, kwargs)

    def put(self, url: str, **kwargs) -> FakeResponse:
        return self._response("PUT", url, kwargs)

    def delete(self, url: str, **kwargs) -> FakeResponse:
        return self._response("DELETE", url, kwargs)


class StatefulGeoServerSession:
    """Small REST state model, including GeoServer's workspace namespace side effect."""

    def __init__(self) -> None:
        self.auth = None
        self.calls: list[tuple[str, str, dict]] = []
        self.workspace = True
        self.namespace_uri = "http://nrw"
        self.stores = {
            "nrw_publish": {"schema": "wrong", "database": "wrong"},
            "nrw_scenario": {"schema": "wrong", "database": "wrong"},
        }
        self.feature_types = {
            ("nrw_publish", "nrw_chargers"): {"title": "stale", "srs": "EPSG:3857"},
        }
        self.rules = {"nrw.*.r": "*", "nrw.*.w": "*", "other.*.w": "*"}
        self.service_rules = {"*.*": "*", "wfs.Transaction": "ROLE_AUTHENTICATED"}
        self.wfs_service_level = "BASIC"
        self.geofence_rules: list[dict] = []

    @staticmethod
    def _entries(payload: dict) -> dict:
        return {entry["@key"]: entry["$"] for entry in payload["dataStore"]["connectionParameters"]["entry"]}

    @staticmethod
    def _store_from_url(url: str) -> str:
        return url.removesuffix(".json").rsplit("/", 1)[-1]

    @staticmethod
    def _feature_type_from_url(url: str) -> tuple[str, str]:
        parts = url.removesuffix(".json").split("/")
        return parts[-3], parts[-1]

    def get(self, url: str, **kwargs) -> FakeResponse:
        if url.endswith("/rest/services/wfs/settings.json"):
            return FakeResponse(200, {"wfs": {"serviceLevel": self.wfs_service_level}})
        if url.endswith("/rest/security/acl/layers.json"):
            return FakeResponse(200, {"rules": dict(self.rules)})
        if url.endswith("/rest/security/acl/services.json"):
            return FakeResponse(200, {"rules": dict(self.service_rules)})
        if url.endswith("/rest/geofence/rules.json"):
            return FakeResponse(200, {"rules": list(self.geofence_rules)})
        if "/namespaces/nrw.json" in url:
            return FakeResponse(200 if self.namespace_uri else 404)
        if "/workspaces/nrw.json" in url:
            return FakeResponse(200 if self.workspace else 404)
        if "/datastores/" in url and "/featuretypes/" not in url:
            return FakeResponse(200 if self._store_from_url(url) in self.stores else 404)
        if "/featuretypes/" in url:
            return FakeResponse(200 if self._feature_type_from_url(url) in self.feature_types else 404)
        raise AssertionError(f"Unexpected GET {url}")

    def post(self, url: str, **kwargs) -> FakeResponse:
        if url.endswith("/rest/reload"):
            return FakeResponse(200)
        payload = kwargs["json"]
        if url.endswith("/rest/geofence/rules"):
            self.geofence_rules.append(payload["Rule"])
            return FakeResponse(200)
        elif url.endswith("/rest/security/acl/layers.json"):
            self.rules.update(payload)
            return FakeResponse(200)
        if url.endswith("/rest/workspaces"):
            self.workspace = True
            # GeoServer creates the namespace with its default URI at workspace creation.
            self.namespace_uri = "http://nrw"
        elif url.endswith("/rest/namespaces"):
            self.namespace_uri = payload["namespace"]["uri"]
        elif url.endswith("/datastores"):
            self.stores[payload["dataStore"]["name"]] = self._entries(payload)
        elif url.endswith("/featuretypes"):
            store = url.rsplit("/", 2)[-2]
            self.feature_types[(store, payload["featureType"]["name"])] = payload["featureType"]
        elif url.endswith("/rest/security/acl/services.json"):
            self.service_rules.update(payload)
        else:
            raise AssertionError(f"Unexpected POST {url}")
        return FakeResponse(200)

    def put(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("PUT", url, kwargs))
        if url.endswith("/rest/geofence/ruleCache/invalidate"):
            return FakeResponse(200)
        payload = kwargs["json"]
        if "/namespaces/nrw.json" in url:
            self.namespace_uri = payload["namespace"]["uri"]
        elif url.endswith("/rest/services/wfs/settings.json"):
            self.wfs_service_level = payload["wfs"]["serviceLevel"]
        elif url.endswith("/rest/security/acl/layers.json"):
            self.rules.update(payload)
        elif url.endswith("/rest/security/acl/services.json"):
            self.service_rules.update(payload)
        elif "/datastores/" in url and "/featuretypes/" not in url:
            self.stores[self._store_from_url(url)] = self._entries(payload)
        elif "/featuretypes/" in url:
            self.feature_types[self._feature_type_from_url(url)] = payload["featureType"]
        else:
            raise AssertionError(f"Unexpected PUT {url}")
        return FakeResponse(200)

    def delete(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("DELETE", url, kwargs))
        expected_prefix = "/rest/security/acl/layers/"
        if expected_prefix not in url or ".json/" in url:
            raise AssertionError(f"Unexpected layer ACL deletion route: {url}")
        self.rules.pop(unquote(url.rsplit("/", 1)[-1]), None)
        return FakeResponse(200)


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
    def test_only_scenario_metrics_skip_count_on_create_and_reconcile(self) -> None:
        for existing in (False, True):
            with self.subTest(existing=existing):
                session = RecordingSession(existing=existing)
                for layer in ("nrw_ev_scenario_metrics", "nrw_chargers"):
                    module.ensure_feature_type(
                        session, config(), store=module.PUBLISH_STORE,
                        layer=layer, title=layer,
                    )
                writes = [kwargs["json"]["featureType"]
                          for method, _, kwargs in session.calls
                          if method in {"POST", "PUT"}]
                self.assertEqual(len(writes), 2 if existing else 4)
                for feature_type in writes:
                    self.assertEqual(feature_type["skipNumberMatched"],
                                     feature_type["name"] == "nrw_ev_scenario_metrics")

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
            store["name"]: {entry["@key"]: entry["$"] for entry in store["connectionParameters"]["entry"]}
            for store in stores
        }
        self.assertEqual(parameters["nrw_publish"]["schema"], "publish")
        self.assertEqual(parameters["nrw_publish"]["user"], "nrw_geoserver_read")
        self.assertEqual(parameters["nrw_scenario"]["schema"], "scenario")
        self.assertEqual(parameters["nrw_scenario"]["user"], "nrw_geoserver_scenario")

        feature_types = [
            payload["featureType"]["name"] for payload in post_payloads if payload and "featureType" in payload
        ]
        self.assertEqual(set(feature_types), set(module.PUBLISH_LAYERS) | {module.SCENARIO_LAYER})
        self.assertEqual(len(feature_types), len(module.PUBLISH_LAYERS) + 1)
        feature_type_payloads = [
            payload["featureType"] for payload in post_payloads if payload and "featureType" in payload
        ]
        self.assertTrue(
            all(
                feature_type["nativeBoundingBox"] == module.NRW_BOUNDS
                and feature_type["latLonBoundingBox"] == module.NRW_BOUNDS
                for feature_type in feature_type_payloads
            )
        )
        self.assertIn(
            ("POST", "http://localhost:8080/geoserver/rest/reload", {"timeout": 30}),
            session.calls,
        )

        geofence_rules = [
            payload["Rule"]
            for method, url, kwargs in session.calls
            if method == "POST" and url.endswith("/rest/geofence/rules") and (payload := kwargs.get("json"))
        ]
        self.assertEqual({rule["layer"] for rule in geofence_rules}, set(module.PUBLISH_LAYERS) | {module.SCENARIO_LAYER})
        self.assertTrue(all(rule["service"] == "WFS" and rule["access"] == "ALLOW" for rule in geofence_rules))
        self.assertIn(
            ("PUT", "http://localhost:8080/geoserver/rest/geofence/ruleCache/invalidate", {"timeout": 30}),
            session.calls,
        )

    def test_existing_catalog_resources_are_not_created_again(self) -> None:
        session = RecordingSession(existing=True)

        module.provision_geoserver(session, config())

        catalog_posts = [
            call
            for call in session.calls
            if call[0] == "POST"
            and "/rest/security/" not in call[1]
            and "/rest/geofence/" not in call[1]
            and not call[1].endswith("/rest/reload")
        ]
        self.assertEqual(catalog_posts, [])

    def test_reconciles_workspace_side_effects_existing_drift_and_stale_write_rules(self) -> None:
        session = StatefulGeoServerSession()

        module.provision_geoserver(session, config())

        self.assertEqual(session.namespace_uri, module.NAMESPACE_URI)
        self.assertEqual(session.stores["nrw_publish"]["schema"], "publish")
        self.assertEqual(session.stores["nrw_publish"]["database"], "nrw_gis")
        self.assertEqual(session.stores["nrw_scenario"]["schema"], "scenario")
        self.assertEqual(
            session.feature_types[("nrw_publish", "nrw_chargers")]["title"],
            module.PUBLISH_LAYERS["nrw_chargers"],
        )
        self.assertEqual(
            set(session.feature_types),
            {(module.PUBLISH_STORE, layer) for layer in module.PUBLISH_LAYERS}
            | {(module.SCENARIO_STORE, module.SCENARIO_LAYER)},
        )
        feature_type_updates = [call for call in session.calls if call[0] == "PUT" and "/featuretypes/" in call[1]]
        self.assertTrue(feature_type_updates)
        self.assertTrue(all(call[2]["params"] == {"recalculate": ""} for call in feature_type_updates))
        self.assertEqual(session.wfs_service_level, "TRANSACTIONAL")
        self.assertEqual(session.service_rules["wfs.Transaction"], "*")
        self.assertEqual(
            {rule["layer"] for rule in session.geofence_rules},
            set(module.PUBLISH_LAYERS) | {module.SCENARIO_LAYER},
        )
        self.assertNotIn("nrw.*.w", session.rules)
        deletion_calls = [call for call in session.calls if call[0] == "DELETE"]
        self.assertEqual(
            deletion_calls[0][1],
            "http://localhost:8080/geoserver/rest/security/acl/layers/nrw.%2A.w",
        )
        self.assertEqual(session.rules["other.*.w"], "*")
        self.assertEqual(session.rules["nrw.proposed_chargers.w"], "*")
        self.assertTrue(all(session.rules[f"nrw.{layer}.w"] == "ROLE_ADMINISTRATOR" for layer in module.PUBLISH_LAYERS))

        state_after_first_run = (
            session.namespace_uri,
            dict(session.stores),
            dict(session.feature_types),
            dict(session.rules),
            dict(session.service_rules),
            session.wfs_service_level,
            tuple(sorted(rule["layer"] for rule in session.geofence_rules)),
        )
        module.provision_geoserver(session, config())
        self.assertEqual(
            state_after_first_run,
            (
                session.namespace_uri,
                dict(session.stores),
                dict(session.feature_types),
                dict(session.rules),
                dict(session.service_rules),
                session.wfs_service_level,
                tuple(sorted(rule["layer"] for rule in session.geofence_rules)),
            ),
        )

    def test_enables_transactions_and_limits_anonymous_write_to_scenario_layer(self) -> None:
        session = RecordingSession()

        module.provision_geoserver(session, config())

        wfs_updates = [
            call for call in session.calls if call[0] == "PUT" and call[1].endswith("/rest/services/wfs/settings.json")
        ]
        self.assertEqual(wfs_updates[0][2]["json"], {"wfs": {"serviceLevel": "TRANSACTIONAL"}})
        service_updates = [
            call for call in session.calls if call[0] == "PUT" and call[1].endswith("/rest/security/acl/services.json")
        ]
        self.assertEqual(service_updates[0][2]["json"], {"wfs.Transaction": "*"})
        security_posts = [
            call for call in session.calls if call[0] == "POST" and call[1].endswith("/rest/security/acl/layers.json")
        ]
        rules = security_posts[0][2]["json"]
        self.assertEqual(rules["nrw.proposed_chargers.w"], "*")
        self.assertEqual(rules["nrw.*.r"], "*")
        self.assertTrue(all(rules[f"nrw.{layer}.w"] == "ROLE_ADMINISTRATOR" for layer in module.PUBLISH_LAYERS))
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
