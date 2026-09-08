from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import requests

try:
    from scripts.geonode_stack import ENV_PATH, compose_command, read_env_file, resolve_database_name
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from geonode_stack import ENV_PATH, compose_command, read_env_file, resolve_database_name


WORKSPACE = "nrw"
NAMESPACE_URI = "https://nrw.local/scenario"
PUBLISH_STORE = "nrw_publish"
SCENARIO_STORE = "nrw_scenario"
SCENARIO_LAYER = "proposed_chargers"

PUBLISH_LAYERS = {
    "nrw_accessibility": "NRW Road Accessibility",
    "nrw_autobahns": "NRW Autobahns",
    "nrw_chargers": "Official NRW EV Charging Stations",
    "nrw_district_priority": "NRW District Investment Priority",
    "nrw_ev_baseline_metrics": "NRW EV Readiness Baseline",
    "nrw_ev_scenario_metrics": "NRW EV Readiness Scenario Comparison",
    "nrw_grid_absorption_risk": "NRW Grid Absorption Risk Proxy",
    "nrw_grid_proxy": "NRW Grid Readiness Proxy",
    "nrw_grid_readiness": "NRW Grid Infrastructure",
    "nrw_infrastructure_opportunity": "NRW Infrastructure Opportunity",
    "nrw_local_energy_balance": "NRW Local Renewable Energy Balance",
    "nrw_renewable_context": "NRW Renewable Context",
    "nrw_renewable_potential": "NRW Renewable Assets",
    "nrw_regional_roads": "NRW Federal and State Roads",
    "nrw_transport_load": "NRW Transport Load",
}


class GeoServerConfig(NamedTuple):
    base_url: str
    admin_user: str
    admin_password: str
    database_host: str
    database_port: int
    database_name: str
    publish_user: str
    publish_password: str
    scenario_user: str
    scenario_password: str


def _rest_url(config: GeoServerConfig, path: str) -> str:
    return f"{config.base_url.rstrip('/')}/rest/{path.lstrip('/')}"


def _exists(session: requests.Session, url: str) -> bool:
    response = session.get(url, params={"quietOnNotFound": "true"}, timeout=30)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


def _create_if_missing(
    session: requests.Session,
    lookup_url: str,
    collection_url: str,
    payload: dict,
) -> None:
    if _exists(session, lookup_url):
        return
    response = session.post(collection_url, json=payload, timeout=30)
    response.raise_for_status()


def _connection_parameters(
    config: GeoServerConfig,
    *,
    schema: str,
    user: str,
    password: str,
) -> dict:
    values: list[tuple[str, object]] = [
        ("dbtype", "postgis"),
        ("host", config.database_host),
        ("port", config.database_port),
        ("database", config.database_name),
        ("schema", schema),
        ("user", user),
        ("passwd", password),
        ("Expose primary keys", True),
        ("validate connections", True),
    ]
    return {"entry": [{"@key": key, "$": value} for key, value in values]}


def ensure_workspace(session: requests.Session, config: GeoServerConfig) -> None:
    _create_if_missing(
        session,
        _rest_url(config, f"workspaces/{WORKSPACE}.json"),
        _rest_url(config, "workspaces"),
        {"workspace": {"name": WORKSPACE}},
    )
    _create_if_missing(
        session,
        _rest_url(config, f"namespaces/{WORKSPACE}.json"),
        _rest_url(config, "namespaces"),
        {"namespace": {"prefix": WORKSPACE, "uri": NAMESPACE_URI}},
    )


def ensure_datastore(
    session: requests.Session,
    config: GeoServerConfig,
    *,
    store: str,
    schema: str,
    user: str,
    password: str,
) -> None:
    _create_if_missing(
        session,
        _rest_url(config, f"workspaces/{WORKSPACE}/datastores/{store}.json"),
        _rest_url(config, f"workspaces/{WORKSPACE}/datastores"),
        {
            "dataStore": {
                "name": store,
                "enabled": True,
                "connectionParameters": _connection_parameters(
                    config,
                    schema=schema,
                    user=user,
                    password=password,
                ),
            }
        },
    )


def ensure_feature_type(
    session: requests.Session,
    config: GeoServerConfig,
    *,
    store: str,
    layer: str,
    title: str,
) -> None:
    _create_if_missing(
        session,
        _rest_url(
            config,
            f"workspaces/{WORKSPACE}/datastores/{store}/featuretypes/{layer}.json",
        ),
        _rest_url(config, f"workspaces/{WORKSPACE}/datastores/{store}/featuretypes"),
        {
            "featureType": {
                "name": layer,
                "nativeName": layer,
                "title": title,
                "abstract": "NRW energy infrastructure intelligence project layer",
                "srs": "EPSG:4326",
                "projectionPolicy": "FORCE_DECLARED",
                "enabled": True,
                "advertised": True,
            }
        },
    )


def ensure_transactional_wfs(session: requests.Session, config: GeoServerConfig) -> None:
    url = _rest_url(config, "services/wfs/settings.json")
    response = session.get(url, timeout=30)
    response.raise_for_status()
    current = response.json().get("wfs", {}).get("serviceLevel")
    if current in {"TRANSACTIONAL", "COMPLETE"}:
        return
    response = session.put(
        url,
        json={"wfs": {"serviceLevel": "TRANSACTIONAL"}},
        timeout=30,
    )
    response.raise_for_status()


def _flatten_rules(payload: dict) -> dict[str, str]:
    if not payload:
        return {}
    if "rules" not in payload:
        return {str(key): str(value) for key, value in payload.items()}
    rules_payload = payload.get("rules", {})
    if isinstance(rules_payload, dict) and "rule" not in rules_payload:
        return {str(key): str(value) for key, value in rules_payload.items()}
    rules = rules_payload.get("rule", [])
    if isinstance(rules, dict):
        rules = [rules]
    return {
        str(rule.get("@resource") or rule.get("resource")): str(rule.get("$") or rule.get("value") or "")
        for rule in rules
    }


def ensure_layer_security(session: requests.Session, config: GeoServerConfig) -> None:
    url = _rest_url(config, "security/acl/layers.json")
    response = session.get(url, timeout=30)
    response.raise_for_status()
    current = _flatten_rules(response.json())
    desired = {
        f"{WORKSPACE}.*.r": "*",
        f"{WORKSPACE}.{SCENARIO_LAYER}.w": "*",
        **{
            f"{WORKSPACE}.{layer}.w": "ROLE_ADMINISTRATOR"
            for layer in PUBLISH_LAYERS
        },
    }
    missing = {key: value for key, value in desired.items() if key not in current}
    changed = {
        key: value
        for key, value in desired.items()
        if key in current and current[key] != value
    }
    if missing:
        response = session.post(url, json=missing, timeout=30)
        response.raise_for_status()
    if changed:
        response = session.put(url, json=changed, timeout=30)
        response.raise_for_status()


def provision_geoserver(session: requests.Session, config: GeoServerConfig) -> None:
    session.auth = (config.admin_user, config.admin_password)
    ensure_workspace(session, config)
    ensure_datastore(
        session,
        config,
        store=PUBLISH_STORE,
        schema="publish",
        user=config.publish_user,
        password=config.publish_password,
    )
    ensure_datastore(
        session,
        config,
        store=SCENARIO_STORE,
        schema="scenario",
        user=config.scenario_user,
        password=config.scenario_password,
    )
    for layer, title in PUBLISH_LAYERS.items():
        ensure_feature_type(
            session,
            config,
            store=PUBLISH_STORE,
            layer=layer,
            title=title,
        )
    ensure_feature_type(
        session,
        config,
        store=SCENARIO_STORE,
        layer=SCENARIO_LAYER,
        title="Proposed NRW EV Charging Stations",
    )
    ensure_transactional_wfs(session, config)
    ensure_layer_security(session, config)


def geonode_sync_commands(admin_username: str) -> list[list[str]]:
    read_permissions = {
        "users": {"AnonymousUser": ["view_resourcebase", "download_resourcebase"]},
        "groups": {},
    }
    scenario_permissions = {
        "users": {
            "AnonymousUser": [
                "view_resourcebase",
                "download_resourcebase",
                "change_dataset_data",
            ]
        },
        "groups": {},
    }
    common = [
        "python",
        "manage.py",
        "updatelayers",
        "--workspace",
        WORKSPACE,
        "--user",
        admin_username,
    ]
    return [
        [*common, "--store", PUBLISH_STORE, "--permissions", repr(read_permissions)],
        [*common, "--store", SCENARIO_STORE, "--permissions", repr(scenario_permissions)],
    ]


def sync_geonode_catalog(admin_username: str) -> None:
    for command in geonode_sync_commands(admin_username):
        subprocess.run(
            compose_command("exec", "-T", "django", *command),
            check=True,
        )


def read_env(path: Path) -> dict[str, str]:
    return read_env_file(path)


def _required(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision NRW PostGIS layers in GeoServer and optionally sync them into GeoNode"
    )
    parser.add_argument("--env-file", type=Path, default=ENV_PATH)
    parser.add_argument("--geoserver-url", default="http://localhost:8080/geoserver")
    parser.add_argument("--database-host", default="db")
    parser.add_argument("--database-port", type=int, default=5432)
    parser.add_argument(
        "--database-name",
        default=None,
    )
    parser.add_argument("--sync-geonode", action="store_true")
    args = parser.parse_args()

    values = {**read_env(args.env_file), **os.environ}
    config = GeoServerConfig(
        base_url=args.geoserver_url,
        admin_user=_required(values.get("GEOSERVER_ADMIN_USER"), "GEOSERVER_ADMIN_USER"),
        admin_password=_required(values.get("GEOSERVER_ADMIN_PASSWORD"), "GEOSERVER_ADMIN_PASSWORD"),
        database_host=args.database_host,
        database_port=args.database_port,
        database_name=resolve_database_name(env_path=args.env_file, explicit=args.database_name),
        publish_user=_required(values.get("NRW_GEOSERVER_READ_USER"), "NRW_GEOSERVER_READ_USER"),
        publish_password=_required(
            values.get("NRW_GEOSERVER_READ_PASSWORD"),
            "NRW_GEOSERVER_READ_PASSWORD",
        ),
        scenario_user=_required(
            values.get("NRW_GEOSERVER_SCENARIO_USER"),
            "NRW_GEOSERVER_SCENARIO_USER",
        ),
        scenario_password=_required(
            values.get("NRW_GEOSERVER_SCENARIO_PASSWORD"),
            "NRW_GEOSERVER_SCENARIO_PASSWORD",
        ),
    )
    provision_geoserver(requests.Session(), config)
    print(f"GeoServer workspace {WORKSPACE}: {len(PUBLISH_LAYERS) + 1} layers ready")
    if args.sync_geonode:
        admin_username = _required(values.get("ADMIN_USERNAME"), "ADMIN_USERNAME")
        sync_geonode_catalog(admin_username)
        print("GeoNode catalog and dataset permissions synchronized")


if __name__ == "__main__":
    main()
