from __future__ import annotations

import argparse
import json
import re
from uuid import uuid4
from xml.sax.saxutils import escape

import requests


DISTRICT = "DEA11"
LONGITUDE = 6.7735
LATITUDE = 51.2277
CHARGING_POINTS = 4
POWER_KW = 150
MAX_POINT_POWER_KW = 150
REQUIRED_CATALOG_LAYERS = (
    "nrw_ev_baseline_metrics",
    "nrw_chargers",
    "nrw_ev_scenario_metrics",
    "proposed_chargers",
    "nrw_autobahns",
    "nrw_regional_roads",
    "nrw_renewable_potential",
)
REQUIRED_WFS_PROPERTIES = {
    "nrw_ev_baseline_metrics": {"nuts_code", "district_name", "ev_readiness_score"},
    "nrw_chargers": {"source_id", "operator", "power_kw", "max_point_power_kw"},
    "nrw_ev_scenario_metrics": {"nuts_code", "scenario_chargers_total"},
    "proposed_chargers": {"id", "name", "charging_points", "power_kw", "max_point_power_kw", "request_id", "geom"},
    "nrw_autobahns": {"source_id", "highway"},
    "nrw_regional_roads": {"source_id", "road_class", "traffic_total"},
    "nrw_renewable_potential": {"source_id", "technology", "status", "asset_type"},
}
READ_ONLY_TRANSACTION_LAYERS = ("nrw_chargers", "nrw_ev_scenario_metrics")
READ_ONLY_TRANSACTION_PROPERTIES = {
    "nrw_chargers": "source_id",
    "nrw_ev_scenario_metrics": "nuts_code",
}
# Representative local envelopes keep read checks selective on the statewide
# source views.  They are not display filters: each request still exercises the
# frontend WFS path and a real feature from its declared layer.
WFS_SAMPLE_BBOXES = {
    "nrw_chargers": "6.76,51.22,6.79,51.24,EPSG:4326",
    "nrw_autobahns": "7.32,50.64,7.34,50.66,EPSG:4326",
    "nrw_regional_roads": "7.63,52.39,7.65,52.41,EPSG:4326",
    "nrw_renewable_potential": "6.27,51.82,6.29,51.84,EPSG:4326",
}


def insert_xml(name: str, request_id: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:Transaction service="WFS" version="1.0.0"
  xmlns:wfs="http://www.opengis.net/wfs"
  xmlns:gml="http://www.opengis.net/gml"
  xmlns:nrw="https://nrw.local/scenario">
  <wfs:Insert>
    <nrw:proposed_chargers>
      <nrw:name>{escape(name)}</nrw:name>
      <nrw:charging_points>{CHARGING_POINTS}</nrw:charging_points>
      <nrw:power_kw>{POWER_KW}</nrw:power_kw>
      <nrw:max_point_power_kw>{MAX_POINT_POWER_KW}</nrw:max_point_power_kw>
      <nrw:request_id>{escape(request_id)}</nrw:request_id>
      <nrw:geom><gml:Point srsName="EPSG:4326"><gml:coordinates decimal="." cs="," ts=" ">{LONGITUDE},{LATITUDE}</gml:coordinates></gml:Point></nrw:geom>
    </nrw:proposed_chargers>
  </wfs:Insert>
</wfs:Transaction>"""


def delete_xml(station_id: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:Transaction service="WFS" version="1.0.0"
  xmlns:wfs="http://www.opengis.net/wfs"
  xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:nrw="https://nrw.local/scenario">
  <wfs:Delete typeName="nrw:proposed_chargers">
    <ogc:Filter><ogc:PropertyIsEqualTo>
      <ogc:PropertyName>id</ogc:PropertyName><ogc:Literal>{escape(station_id)}</ogc:Literal>
    </ogc:PropertyIsEqualTo></ogc:Filter>
  </wfs:Delete>
</wfs:Transaction>"""


def harmless_read_only_write_xml(layer: str) -> str:
    property_name = READ_ONLY_TRANSACTION_PROPERTIES[layer]
    return (
        """<?xml version="1.0" encoding="UTF-8"?>
<wfs:Transaction service="WFS" version="1.0.0"
  xmlns:wfs="http://www.opengis.net/wfs"
  xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:nrw="https://nrw.local/scenario">
  <wfs:Delete typeName="nrw:"""
        + escape(layer)
        + """">
    <ogc:Filter><ogc:PropertyIsEqualTo>
      <ogc:PropertyName>"""
        + escape(property_name)
        + """</ogc:PropertyName>
      <ogc:Literal>__e2e_verifier_never_matches__</ogc:Literal>
    </ogc:PropertyIsEqualTo></ogc:Filter>
  </wfs:Delete>
</wfs:Transaction>"""
    )


def harmless_official_write_xml() -> str:
    """Backward-compatible official-layer probe used by focused tests."""
    return harmless_read_only_write_xml("nrw_chargers")


def is_exception_response(status_code: int, body: str) -> bool:
    normalized = body.lower()
    return (
        status_code >= 400
        or "exceptionreport" in normalized
        or "serviceexceptionreport" in normalized
        or "<ows:exception" in normalized
        or "<serviceexception" in normalized
    )


def post_transaction(session: requests.Session, wfs_url: str, document: str) -> str:
    response = session.post(
        wfs_url,
        data=document.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=UTF-8"},
        timeout=60,
    )
    if is_exception_response(response.status_code, response.text):
        raise RuntimeError(f"WFS transaction failed ({response.status_code}): {response.text[:500]}")
    return response.text


def inserted_station_id(body: str) -> str:
    match = re.search(
        r'(?:fid|rid)=["\'][^"\']*\.([0-9a-fA-F-]{36})["\']',
        body,
    )
    if not match:
        raise RuntimeError("WFS insert response did not contain the proposed-station UUID")
    return match.group(1)


def get_features(
    session: requests.Session,
    wfs_url: str,
    layer: str,
    *,
    cql_filter: str | None = None,
    count: int | None = None,
    bbox: str | None = None,
) -> list[dict]:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": f"nrw:{layer}",
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
    }
    if cql_filter:
        params["CQL_FILTER"] = cql_filter
    if count is not None:
        params["count"] = count
    if bbox:
        params["bbox"] = bbox
    response = session.get(wfs_url, params=params, timeout=60)
    response.raise_for_status()
    document = response.json()
    features = document.get("features")
    if not isinstance(features, list):
        raise RuntimeError(f"GeoServer returned invalid GeoJSON for {layer}")
    return features


def describe_feature_type(session: requests.Session, wfs_url: str, layer: str) -> str:
    response = session.get(
        wfs_url,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "DescribeFeatureType",
            "typeNames": f"nrw:{layer}",
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.text


def verify_wfs_contract(session: requests.Session, wfs_url: str) -> None:
    for layer, required_properties in REQUIRED_WFS_PROPERTIES.items():
        schema = describe_feature_type(session, wfs_url, layer)
        missing = [property_name for property_name in required_properties if property_name not in schema]
        if missing:
            raise RuntimeError(f"DescribeFeatureType for {layer} is missing: {', '.join(sorted(missing))}")
        # Contract evidence needs a real feature, not an unbounded download of
        # a statewide source layer.
        features = get_features(
            session,
            wfs_url,
            layer,
            count=1,
            bbox=WFS_SAMPLE_BBOXES.get(layer),
        )
        if layer != "proposed_chargers" and not features:
            raise RuntimeError(f"WFS sample read returned no feature for {layer}")


def district_metrics(session: requests.Session, wfs_url: str) -> dict:
    features = get_features(
        session,
        wfs_url,
        "nrw_ev_scenario_metrics",
        cql_filter=f"nuts_code='{DISTRICT}'",
    )
    if len(features) != 1:
        raise RuntimeError(f"Expected one metrics row for {DISTRICT}, received {len(features)}")
    return features[0]["properties"]


def assert_metric_increment(before: dict, after: dict) -> None:
    if int(after["scenario_chargers_total"]) != int(before["scenario_chargers_total"]) + 1:
        raise RuntimeError("Scenario charger total did not increase by one")
    if int(after["scenario_charging_points_total"]) != int(before["scenario_charging_points_total"]) + CHARGING_POINTS:
        raise RuntimeError("Scenario charging-point total did not increase by four")


def find_station_id(features: list[dict], name: str) -> str:
    matches = [feature for feature in features if feature.get("properties", {}).get("name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one verifier station named {name}, received {len(matches)}")
    station_id = matches[0].get("properties", {}).get("id")
    if not station_id:
        raise RuntimeError("Verifier station has no UUID")
    return str(station_id)


def verify_catalog(session: requests.Session, geonode_url: str) -> None:
    response = session.get(f"{geonode_url.rstrip('/')}/api/v2/datasets/", params={"page_size": 100}, timeout=60)
    response.raise_for_status()
    body = json.dumps(response.json())
    missing = [name for name in REQUIRED_CATALOG_LAYERS if name not in body]
    if missing:
        raise RuntimeError(f"GeoNode catalog is missing project layers: {', '.join(missing)}")


def verify(
    session: requests.Session,
    *,
    frontend_url: str,
    geonode_url: str,
) -> None:
    frontend = session.get(frontend_url, timeout=30)
    frontend.raise_for_status()
    # The frontend proxies GeoServer's global OWS endpoint.  A workspace is
    # selected by the qualified type name, not by placing it in the OWS path.
    wfs_url = f"{frontend_url.rstrip('/')}/geoserver/ows"
    verify_wfs_contract(session, wfs_url)
    before = district_metrics(session, wfs_url)
    request_id = str(uuid4())
    name = f"E2E verifier {request_id}"
    station_id: str | None = None
    try:
        station_id = inserted_station_id(post_transaction(session, wfs_url, insert_xml(name, request_id)))
        queried_id = find_station_id(
            get_features(
                session,
                wfs_url,
                "proposed_chargers",
                cql_filter=f"request_id='{request_id}'",
                count=1,
            ),
            name,
        )
        if queried_id != station_id:
            raise RuntimeError("Inserted and queried proposed-station UUIDs differ")
        assert_metric_increment(before, district_metrics(session, wfs_url))

        for layer in READ_ONLY_TRANSACTION_LAYERS:
            response = session.post(
                wfs_url,
                data=harmless_read_only_write_xml(layer).encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=UTF-8"},
                timeout=60,
            )
            if not is_exception_response(response.status_code, response.text):
                raise RuntimeError(f"Read-only layer unexpectedly accepted a WFS transaction: {layer}")
        verify_catalog(session, geonode_url)
    finally:
        if station_id is not None:
            post_transaction(session, wfs_url, delete_xml(station_id))

    restored = district_metrics(session, wfs_url)
    if int(restored["scenario_chargers_total"]) != int(before["scenario_chargers_total"]):
        raise RuntimeError("Verifier cleanup did not restore the scenario charger total")
    if int(restored["scenario_charging_points_total"]) != int(before["scenario_charging_points_total"]):
        raise RuntimeError("Verifier cleanup did not restore the scenario charging-point total")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the local GeoNode scenario workflow end to end")
    parser.add_argument("--frontend-url", default="http://localhost:8081")
    parser.add_argument("--geonode-url", default="http://localhost:8000")
    args = parser.parse_args()
    verify(
        requests.Session(),
        frontend_url=args.frontend_url,
        geonode_url=args.geonode_url,
    )
    print("End-to-end verification passed; the temporary proposed station was removed")


if __name__ == "__main__":
    main()
