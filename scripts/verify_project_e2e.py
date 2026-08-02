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


def insert_xml(name: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<wfs:Transaction service="WFS" version="1.1.0"
  xmlns:wfs="http://www.opengis.net/wfs"
  xmlns:gml="http://www.opengis.net/gml"
  xmlns:nrw="https://nrw.local/scenario">
  <wfs:Insert>
    <nrw:proposed_chargers>
      <nrw:name>{escape(name)}</nrw:name>
      <nrw:charging_points>{CHARGING_POINTS}</nrw:charging_points>
      <nrw:power_kw>{POWER_KW}</nrw:power_kw>
      <nrw:geom><gml:Point srsName="EPSG:4326"><gml:pos>{LONGITUDE} {LATITUDE}</gml:pos></gml:Point></nrw:geom>
    </nrw:proposed_chargers>
  </wfs:Insert>
</wfs:Transaction>'''


def delete_xml(station_id: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<wfs:Transaction service="WFS" version="1.1.0"
  xmlns:wfs="http://www.opengis.net/wfs"
  xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:nrw="https://nrw.local/scenario">
  <wfs:Delete typeName="nrw:proposed_chargers">
    <ogc:Filter><ogc:PropertyIsEqualTo>
      <ogc:PropertyName>id</ogc:PropertyName><ogc:Literal>{escape(station_id)}</ogc:Literal>
    </ogc:PropertyIsEqualTo></ogc:Filter>
  </wfs:Delete>
</wfs:Transaction>'''


def harmless_official_write_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?>
<wfs:Transaction service="WFS" version="1.1.0"
  xmlns:wfs="http://www.opengis.net/wfs"
  xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:nrw="https://nrw.local/scenario">
  <wfs:Delete typeName="nrw:nrw_chargers">
    <ogc:Filter><ogc:PropertyIsEqualTo>
      <ogc:PropertyName>source_id</ogc:PropertyName>
      <ogc:Literal>__e2e_verifier_never_matches__</ogc:Literal>
    </ogc:PropertyIsEqualTo></ogc:Filter>
  </wfs:Delete>
</wfs:Transaction>'''


def is_exception_response(status_code: int, body: str) -> bool:
    normalized = body.lower()
    return status_code >= 400 or "exceptionreport" in normalized or "<ows:exception" in normalized


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
) -> list[dict]:
    params = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typeName": f"nrw:{layer}",
        "outputFormat": "application/json",
    }
    if cql_filter:
        params["CQL_FILTER"] = cql_filter
    response = session.get(wfs_url, params=params, timeout=60)
    response.raise_for_status()
    document = response.json()
    features = document.get("features")
    if not isinstance(features, list):
        raise RuntimeError(f"GeoServer returned invalid GeoJSON for {layer}")
    return features


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
    missing = [name for name in ("nrw_ev_scenario_metrics", "proposed_chargers") if name not in body]
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
    wfs_url = f"{frontend_url.rstrip('/')}/geoserver/nrw/ows"
    before = district_metrics(session, wfs_url)
    name = f"E2E verifier {uuid4()}"
    station_id: str | None = None
    try:
        station_id = inserted_station_id(post_transaction(session, wfs_url, insert_xml(name)))
        queried_id = find_station_id(get_features(session, wfs_url, "proposed_chargers"), name)
        if queried_id != station_id:
            raise RuntimeError("Inserted and queried proposed-station UUIDs differ")
        assert_metric_increment(before, district_metrics(session, wfs_url))

        official_response = session.post(
            wfs_url,
            data=harmless_official_write_xml().encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=UTF-8"},
            timeout=60,
        )
        if not is_exception_response(official_response.status_code, official_response.text):
            raise RuntimeError("Official charger layer unexpectedly accepted a WFS transaction")
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
