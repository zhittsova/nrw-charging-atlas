from __future__ import annotations

import argparse
import json
import time
import tempfile
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
# The canonical export is produced after a successful atomic refresh and before
# layer publication.  It replaces the retired tracked frontend snapshot.
SEED_PATH = ROOT / "data" / "runtime" / "current" / "nrw_regions_sample.geojson"
ENV_PATH = ROOT / "geonode" / ".env"
GEONODE_URL = "http://localhost:8000"
GEOSERVER_URL = "http://localhost:8080/geoserver"
LAYER_NAME = "nrw_nuts3_districts"
QUALIFIED_LAYER_NAME = f"geonode:{LAYER_NAME}"
EXPECTED_FEATURE_COUNT = 53


def validate_seed(path: Path) -> dict:
    """Return a validated NRW district FeatureCollection."""
    data = json.loads(path.read_text(encoding="utf-8"))
    features = data.get("features", [])
    if data.get("type") != "FeatureCollection" or len(features) != EXPECTED_FEATURE_COUNT:
        raise ValueError(
            "NRW district seed must be a FeatureCollection with "
            f"{EXPECTED_FEATURE_COUNT} features"
        )
    if any(feature.get("geometry", {}).get("type") != "MultiPolygon" for feature in features):
        raise ValueError("NRW district seed must contain only MultiPolygon geometries")
    if any(
        not str(feature.get("properties", {}).get("nuts_code", "")).startswith("DEA")
        for feature in features
    ):
        raise ValueError("Every NRW district nuts_code must begin with DEA")
    return data


def read_geonode_credentials(path: Path) -> tuple[str, str]:
    """Read the local administrator credentials without exporting environment values."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip("\"'")
    username = values.get("ADMIN_USERNAME")
    password = values.get("ADMIN_PASSWORD")
    if not username or not password:
        raise RuntimeError("ADMIN_USERNAME and ADMIN_PASSWORD are required in geonode/.env")
    return username, password


def prepare_upload_seed(source_path: Path, upload_path: Path) -> None:
    """Write a GeoNode-compatible copy without frontend-only property values.

    GeoNode 5.1 imports GeoJSON properties into a relational dynamic model.
    It cannot infer a field type for list or object values (the source's
    bounding-box ``box`` property is one such presentation field).  The
    original seed stays untouched; only unsupported and entirely-null
    properties are removed from the transient upload copy.
    """
    data = validate_seed(source_path)
    property_names = {
        key for feature in data["features"] for key in feature.get("properties", {})
    }
    supported_names = {
        key
        for key in property_names
        if not all(feature.get("properties", {}).get(key) is None for feature in data["features"])
        and all(
            isinstance(feature.get("properties", {}).get(key), (str, int, float, bool, type(None)))
            for feature in data["features"]
        )
    }
    prepared = {
        **data,
        "name": LAYER_NAME,
        "features": [
            {
                **feature,
                "properties": {
                    key: value
                    for key, value in feature.get("properties", {}).items()
                    if key in supported_names
                },
            }
            for feature in data["features"]
        ],
    }
    upload_path.write_text(json.dumps(prepared), encoding="utf-8")


def wait_for_execution(
    session: requests.Session,
    base_url: str,
    execution_id: str,
    timeout: float = 300.0,
) -> dict:
    """Wait for a GeoNode asynchronous request and surface its server log on failure."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = session.get(
            f"{base_url}/api/v2/executionrequest/{execution_id}",
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if status == "finished":
            return payload
        if status == "failed":
            raise RuntimeError(f"GeoNode upload failed: {payload.get('log', 'no server log')}")
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for GeoNode execution {execution_id}")


def find_resource(session: requests.Session, base_url: str) -> dict | None:
    """Find exactly the published dataset, or return None when it is absent.

    GeoNode 5.1 filters catalogue datasets by ``title`` and identifies the
    published layer by its qualified ``alternate``.  ``name`` is accepted only
    as a fallback for older records that do not expose an alternate at all.
    """
    response = session.get(
        f"{base_url}/api/v2/resources/",
        params={"filter{title}": LAYER_NAME, "page_size": 100},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    resources = payload.get("resources", payload.get("data", []))
    return next(
        (
            resource
            for resource in resources
            if resource.get("resource_type") == "dataset"
            and (
                resource.get("alternate") == QUALIFIED_LAYER_NAME
                or (
                    resource.get("alternate") is None
                    and resource.get("name") == LAYER_NAME
                )
            )
        ),
        None,
    )


def wait_for_resource(
    session: requests.Session,
    base_url: str,
    timeout: float = 60.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resource = find_resource(session, base_url)
        if resource is not None:
            return resource
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for GeoNode catalog resource {LAYER_NAME}")


def set_public_download(
    session: requests.Session,
    base_url: str,
    resource_pk: int,
) -> None:
    """Grant anonymous download access and wait when GeoNode responds asynchronously."""
    response = session.patch(
        f"{base_url}/api/v2/resources/{resource_pk}/permissions",
        json={
            "groups": [],
            "organizations": [],
            "users": [{"id": -1, "permissions": "download"}],
        },
        timeout=15,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        return
    execution_id = str(payload.get("execution_id") or "")
    if execution_id:
        wait_for_execution(session, base_url, execution_id)


def publish_seed(
    session: requests.Session,
    base_url: str,
    path: Path,
) -> str:
    """Upload the seed once, or repair its anonymous-download permission on reruns."""
    validate_seed(path)
    existing = find_resource(session, base_url)
    if existing is not None:
        set_public_download(session, base_url, int(existing["pk"]))
        return "already-present"

    with tempfile.TemporaryDirectory(prefix="geonode-nrw-") as directory:
        upload_path = Path(directory) / f"{LAYER_NAME}.geojson"
        prepare_upload_seed(path, upload_path)
        with upload_path.open("rb") as source:
            response = session.post(
                f"{base_url}/api/v2/uploads/upload/",
                data={"action": "upload", "store_spatial_files": "true"},
                files={
                    "base_file": (
                        upload_path.name,
                        source,
                        "application/json",
                    )
                },
                timeout=60,
            )
    response.raise_for_status()
    if response.status_code != 201:
        raise RuntimeError(f"GeoNode upload returned HTTP {response.status_code}; expected 201")
    execution_id = str(response.json().get("execution_id", ""))
    if not execution_id:
        raise RuntimeError("GeoNode upload response did not contain execution_id")
    wait_for_execution(session, base_url, execution_id)
    resource = wait_for_resource(session, base_url)
    set_public_download(session, base_url, int(resource["pk"]))
    return execution_id


def verify_wfs(session: requests.Session, geoserver_url: str) -> int:
    """Verify the unauthenticated WFS endpoint serves exactly all NRW districts."""
    response = session.get(
        f"{geoserver_url}/ows",
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": QUALIFIED_LAYER_NAME,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    features = payload.get("features", [])
    if payload.get("type") != "FeatureCollection" or len(features) != EXPECTED_FEATURE_COUNT:
        raise RuntimeError(
            f"WFS returned {len(features)} features for {QUALIFIED_LAYER_NAME}; "
            f"expected {EXPECTED_FEATURE_COUNT}"
        )
    return len(features)


def verify_published_seed(session: requests.Session, anonymous_session: requests.Session) -> int:
    """Require both the exact catalogue dataset and its public WFS layer."""
    if find_resource(session, GEONODE_URL) is None:
        raise RuntimeError(f"GeoNode catalog does not contain dataset {LAYER_NAME}")
    return verify_wfs(anonymous_session, GEOSERVER_URL)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish and verify the local GeoNode NRW seed layer.")
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--publish", action="store_true", help="upload only if the exact dataset is absent")
    command.add_argument("--verify-only", action="store_true", help="verify the catalogue and public WFS only")
    args = parser.parse_args()

    session = requests.Session()
    session.auth = read_geonode_credentials(ENV_PATH)
    anonymous_session = requests.Session()

    if args.publish:
        execution_id = publish_seed(session, GEONODE_URL, SEED_PATH)
        print(f"GeoNode dataset: {LAYER_NAME}")
        print(f"Upload execution: {execution_id}")

    count = verify_published_seed(session, anonymous_session)
    print(f"Public WFS feature count: {count}")


if __name__ == "__main__":
    main()
