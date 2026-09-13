"""Verify the production NRW WFS contract without touching user scenarios.

The verifier uses the dashboard's WFS 2.0 GeoJSON reads and WFS 1.0 writes.
Every temporary write carries a fresh request UUID and cleanup only ever reads
or deletes features carrying that UUID.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from xml.sax.saxutils import escape

import requests


DISTRICT = "DEA11"
LONGITUDE, LATITUDE = 6.7735, 51.2277
CHARGING_POINTS, POWER_KW, MAX_POINT_POWER_KW = 4, 150, 150
WORKSPACE, NAMESPACE_URI, DISTRICT_COUNT = "nrw", "https://nrw.local/scenario", 53
RUN_PREFIX = "S18 E2E verifier"

# S02 A11: this is deliberately the frontend's canonical district contract,
# not a reduced list of fields convenient to the verifier.
DISTRICT_DETAIL_PROPERTIES = frozenset({
    "nuts_code", "district_name", "chargers_total", "charging_points_total", "fast_chargers_total",
    "normal_chargers_total", "unknown_power_chargers_total", "priority_rank", "ev_readiness_score",
    "charger_deficit_score", "infrastructure_opportunity_score", "investment_priority_score",
    "data_quality_flag", "formula_version", "population_source_year", "transport_data_quality_flag",
    "grid_data_quality_flag", "energy_data_quality_flag", "operating_asset_renewable_capacity_mw",
    "municipal_workbook_renewable_capacity_mw", "transport_load_score", "traffic_intensity_score",
    "traffic_road_density_score", "road_proximity_score", "grid_readiness_proxy_score",
    "substation_proximity_score", "voltage_line_density_score", "substation_density_score",
    "renewable_context_score", "renewable_capacity_density_score", "renewable_technology_diversity_score",
    "local_energy_balance_score", "renewable_growth_score", "grid_absorption_risk_proxy_score",
    "energy_reporting_year", "charger_snapshot_date", "charger_snapshot_unavailable_reason",
    "consumption_municipal_coverage", "renewable_municipal_coverage", "municipal_workbook_renewable_capacity_coverage",
    "energy_unavailable_reason", "traffic_intensity_dtv", "traffic_weighted_road_density",
    "traffic_road_length_km", "traffic_measured_length_km", "traffic_length_coverage",
    "distance_to_nearest_road_m", "voltage_weighted_line_density", "substation_density",
    "distance_to_nearest_substation_m", "grid_line_length_km", "substation_count",
    "maximum_mapped_voltage_kv", "line_voltage_coverage", "substation_voltage_coverage",
    "operating_asset_renewable_capacity_mw_per_km2", "renewable_installation_count",
    "renewable_technology_count", "consumption_mwh", "published_generation_mwh",
    "estimated_wind_generation_mwh", "total_renewable_generation_mwh", "renewable_balance_ratio",
    "renewable_coverage_pct", "renewable_net_addition_3y_mw", "renewable_growth_density_mw_per_km2",
    "expected_municipalities", "growth_years_required", "growth_years_reported", "wind_full_load_hours",
    "renewable_growth_window_years", "wind_estimate_note", "municipal_workbook_capacity_reporting_year",
    "municipal_workbook_renewable_capacity_unavailable_reason", "charger_snapshot_source",
    "formula_version_date", "energy_source",
})


@dataclass(frozen=True)
class LayerContract:
    properties: frozenset[str]
    geometry: str
    required_feature: bool = True


LAYER_CONTRACTS = {
    "nrw_ev_baseline_metrics": LayerContract(DISTRICT_DETAIL_PROPERTIES, "MultiPolygon"),
    "nrw_chargers": LayerContract(frozenset({"source_id", "operator", "power_kw", "max_point_power_kw", "charging_points", "charger_type", "status"}), "Point"),
    "nrw_ev_scenario_metrics": LayerContract(frozenset({
        "nuts_code", "baseline_chargers_total", "scenario_chargers_total", "chargers_total_delta",
        "baseline_charging_points_total", "scenario_charging_points_total", "charging_points_total_delta",
        "baseline_charging_points_per_km2", "scenario_charging_points_per_km2", "charging_points_per_km2_delta",
        "baseline_distance_to_nearest_charger_m", "scenario_distance_to_nearest_charger_m", "distance_to_nearest_charger_m_delta",
        "baseline_charging_points_per_100k_population", "scenario_charging_points_per_100k_population", "charging_points_per_100k_population_delta",
        "charging_point_density_lower_bound", "charging_point_density_upper_bound",
        "charger_distance_lower_bound", "charger_distance_upper_bound",
        "population_coverage_lower_bound", "population_coverage_upper_bound",
        "baseline_charger_density_score", "scenario_charger_density_score", "charger_density_score_delta",
        "baseline_charger_accessibility_score", "scenario_charger_accessibility_score", "charger_accessibility_score_delta",
        "baseline_population_adjusted_coverage_score", "scenario_population_adjusted_coverage_score", "population_adjusted_coverage_score_delta",
        "baseline_ev_readiness_score", "scenario_ev_readiness_score", "ev_readiness_score_delta",
        "baseline_charger_deficit_score", "scenario_charger_deficit_score", "charger_deficit_score_delta",
        "infrastructure_opportunity_score", "baseline_investment_priority_score", "scenario_investment_priority_score",
        "investment_priority_score_delta", "baseline_priority_rank", "scenario_priority_rank",
    }), "MultiPolygon"),
    "proposed_chargers": LayerContract(frozenset({"id", "name", "charging_points", "power_kw", "max_point_power_kw", "request_id", "status"}), "Point", False),
    "nrw_autobahns": LayerContract(frozenset({"source_id", "highway"}), "LineString"),
    "nrw_regional_roads": LayerContract(frozenset({"source_id", "road_class", "traffic_total"}), "LineString"),
    "nrw_renewable_potential": LayerContract(frozenset({"source_id", "technology", "status", "asset_type", "capacity_mw"}), "Point"),
}
REQUIRED_CATALOG_LAYERS = tuple(LAYER_CONTRACTS)
REQUIRED_WFS_PROPERTIES = {layer: set(contract.properties) for layer, contract in LAYER_CONTRACTS.items()}
READ_ONLY_TRANSACTION_LAYERS = ("nrw_chargers", "nrw_ev_scenario_metrics")
READ_ONLY_TRANSACTION_PROPERTIES = {"nrw_chargers": "source_id", "nrw_ev_scenario_metrics": "nuts_code"}
WFS_SAMPLE_BBOXES = {
    "nrw_chargers": "6.76,51.22,6.79,51.24,EPSG:4326",
    "nrw_autobahns": "7.32,50.64,7.34,50.66,EPSG:4326",
    "nrw_regional_roads": "7.63,52.39,7.65,52.41,EPSG:4326",
    "nrw_renewable_potential": "6.27,51.82,6.29,51.84,EPSG:4326",
}

# Every value advertised as a score is a public 0--100 component, not just the
# four composites used by the proposal flow.  Keeping this derived from the
# A11 contract makes a newly published component fail closed until it gets an
# explicit availability rule below.
DISTRICT_SCORE_FIELDS = frozenset(
    field for field in DISTRICT_DETAIL_PROPERTIES if field.endswith("_score")
)
TRANSPORT_SCORE_FIELDS = frozenset({
    "traffic_intensity_score", "traffic_road_density_score", "road_proximity_score",
    "transport_load_score",
})
GRID_SCORE_FIELDS = frozenset({
    "substation_proximity_score", "voltage_line_density_score", "substation_density_score",
    "grid_readiness_proxy_score",
})
COMPLETE_BASELINE_SCORE_FIELDS = frozenset({
    "ev_readiness_score", "charger_deficit_score", "infrastructure_opportunity_score",
    "investment_priority_score",
})
ENERGY_COMPONENT_SCORE_FIELDS = frozenset({
    "local_energy_balance_score", "renewable_growth_score", "grid_absorption_risk_proxy_score",
})
COMPLETE_BASELINE_FLAG = "complete_proxy_inputs"
UNAVAILABLE_GRID_FLAGS = frozenset({
    "no_mapped_substation", "no_mapped_grid_lines", "unknown_line_voltage",
})
BASELINE_SCORE_FIELDS = (
    "charger_density_score", "charger_accessibility_score",
    "population_adjusted_coverage_score", "ev_readiness_score",
    "charger_deficit_score", "investment_priority_score",
)
SCENARIO_COMPONENTS = (
    ("charger_density_score", "charging_points_per_km2", "charging_point_density", False),
    ("charger_accessibility_score", "distance_to_nearest_charger_m", "charger_distance", True),
    ("population_adjusted_coverage_score", "charging_points_per_100k_population", "population_coverage", False),
)


class WfsTransactionError(RuntimeError):
    """Response proves a WFS transaction was rejected before a write."""


def insert_xml(name: str, request_id: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<wfs:Transaction service="WFS" version="1.0.0" xmlns:wfs="http://www.opengis.net/wfs" xmlns:gml="http://www.opengis.net/gml" xmlns:ogc="http://www.opengis.net/ogc" xmlns:nrw="{NAMESPACE_URI}">
  <wfs:Insert><nrw:proposed_chargers><nrw:name>{escape(name)}</nrw:name><nrw:charging_points>{CHARGING_POINTS}</nrw:charging_points><nrw:power_kw>{POWER_KW}</nrw:power_kw><nrw:max_point_power_kw>{MAX_POINT_POWER_KW}</nrw:max_point_power_kw><nrw:request_id>{escape(request_id)}</nrw:request_id><nrw:geom><gml:Point srsName="http://www.opengis.net/gml/srs/epsg.xml#4326"><gml:coordinates decimal="." cs="," ts=" ">{LONGITUDE},{LATITUDE}</gml:coordinates></gml:Point></nrw:geom></nrw:proposed_chargers></wfs:Insert>
</wfs:Transaction>'''


def delete_xml(station_id: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<wfs:Transaction service="WFS" version="1.0.0" xmlns:wfs="http://www.opengis.net/wfs" xmlns:ogc="http://www.opengis.net/ogc" xmlns:nrw="{NAMESPACE_URI}"><wfs:Delete typeName="nrw:proposed_chargers"><ogc:Filter><ogc:PropertyIsEqualTo><ogc:PropertyName>id</ogc:PropertyName><ogc:Literal>{escape(station_id)}</ogc:Literal></ogc:PropertyIsEqualTo></ogc:Filter></wfs:Delete></wfs:Transaction>'''


def harmless_read_only_write_xml(layer: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<wfs:Transaction service="WFS" version="1.0.0" xmlns:wfs="http://www.opengis.net/wfs" xmlns:ogc="http://www.opengis.net/ogc" xmlns:nrw="{NAMESPACE_URI}"><wfs:Delete typeName="nrw:{escape(layer)}"><ogc:Filter><ogc:PropertyIsEqualTo><ogc:PropertyName>{escape(READ_ONLY_TRANSACTION_PROPERTIES[layer])}</ogc:PropertyName><ogc:Literal>__e2e_verifier_never_matches__</ogc:Literal></ogc:PropertyIsEqualTo></ogc:Filter></wfs:Delete></wfs:Transaction>'''


def harmless_official_write_xml() -> str:
    return harmless_read_only_write_xml("nrw_chargers")


def has_exception_report(body: str) -> bool:
    lowered = body.lower()
    return "exceptionreport" in lowered or "<ows:exception" in lowered or "<serviceexception" in lowered


def is_exception_response(status_code: int, body: str) -> bool:
    return status_code >= 400 or has_exception_report(body)


def post_transaction(session: requests.Session, wfs_url: str, document: str) -> str:
    response = session.post(wfs_url, data=document.encode("utf-8"), headers={"Content-Type": "text/xml; charset=UTF-8"}, timeout=60)
    # 5xx/malformed success are uncertain; the caller must reconcile request_id.
    if 400 <= response.status_code < 500 or has_exception_report(response.text):
        raise WfsTransactionError(f"WFS transaction failed ({response.status_code}): {response.text[:500]}")
    if response.status_code >= 500:
        raise RuntimeError(f"WFS transaction response is uncertain ({response.status_code})")
    return response.text


def inserted_station_id(body: str) -> str:
    match = re.search(r'(?:fid|rid)=["\'][^"\']*\.([0-9a-fA-F-]{36})["\']', body)
    if not match:
        raise RuntimeError("WFS insert response did not contain the proposed-station UUID")
    return match.group(1)


def get_features(session: requests.Session, wfs_url: str, layer: str, *, cql_filter: str | None = None, count: int | None = None, bbox: str | None = None) -> list[dict]:
    params: dict[str, str | int] = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": f"{WORKSPACE}:{layer}", "outputFormat": "application/json", "srsName": "EPSG:4326"}
    if cql_filter:
        params["CQL_FILTER"] = cql_filter
    if count is not None:
        params["count"] = count
    if bbox:
        params["bbox"] = bbox
    response = session.get(wfs_url, params=params, timeout=60)
    response.raise_for_status()
    features = response.json().get("features")
    if not isinstance(features, list):
        raise RuntimeError(f"GeoServer returned invalid GeoJSON for {layer}")
    return features


def describe_feature_type(session: requests.Session, wfs_url: str, layer: str) -> str:
    response = session.get(wfs_url, params={"service": "WFS", "version": "2.0.0", "request": "DescribeFeatureType", "typeNames": f"{WORKSPACE}:{layer}"}, timeout=60)
    response.raise_for_status()
    return response.text


def positions(value: object) -> Iterable[list[float]]:
    if isinstance(value, list) and value and all(isinstance(item, (int, float)) for item in value):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from positions(item)


def validate_feature(layer: str, feature: dict, contract: LayerContract) -> None:
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        raise RuntimeError(f"WFS feature for {layer} has no properties object")
    missing = sorted(contract.properties - properties.keys())
    if missing:
        raise RuntimeError(f"WFS feature for {layer} is missing: {', '.join(missing)}")
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != contract.geometry:
        raise RuntimeError(f"WFS feature for {layer} has wrong geometry type")
    coordinate_positions = list(positions(geometry.get("coordinates")))
    if not coordinate_positions or any(len(point) < 2 or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90 for point in coordinate_positions):
        raise RuntimeError(f"WFS feature for {layer} is not valid EPSG:4326 longitude/latitude geometry")


def numeric(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} is not numeric")
    try:
        result = float(str(value))
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{field} is not numeric") from error
    if result != result or result in (float("inf"), float("-inf")):
        raise RuntimeError(f"{field} is not finite")
    return result


def require_present_scores(properties: dict, fields: Iterable[str], context: str) -> None:
    missing = sorted(field for field in fields if properties.get(field) is None)
    if missing:
        raise RuntimeError(f"{context} has unavailable required scores: {', '.join(missing)}")


def rounded_score(value: float) -> float:
    """Match PostgreSQL numeric ROUND(..., 1), including midpoint behavior."""
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def normalize_component(metric: float, lower: float, upper: float, *, inverse: bool) -> float:
    if upper < lower:
        raise RuntimeError("Scenario normalization bounds are inverted")
    if upper == lower:
        return 50.0
    clamped = min(max(metric, lower), upper)
    score = 100.0 * (clamped - lower) / (upper - lower)
    return rounded_score(100.0 - score if inverse else score)


def validate_charger_feature(feature: dict) -> None:
    """Check public charger values without turning an unknown power into zero."""
    properties = feature["properties"]
    points = properties.get("charging_points")
    if isinstance(points, bool):
        raise RuntimeError("Charger charging_points is not a positive integer")
    try:
        points_number = int(str(points))
    except (TypeError, ValueError) as error:
        raise RuntimeError("Charger charging_points is not a positive integer") from error
    if str(points_number) != str(points).strip() or points_number < 1:
        raise RuntimeError("Charger charging_points is not a positive integer")
    power = properties.get("power_kw")
    maximum = properties.get("max_point_power_kw")
    if power is not None and numeric(power, "Charger power_kw") < 0:
        raise RuntimeError("Charger power_kw is negative")
    if maximum is not None and numeric(maximum, "Charger max_point_power_kw") < 0:
        raise RuntimeError("Charger max_point_power_kw is negative")


def validate_district_contract(features: list[dict]) -> None:
    if len(features) != DISTRICT_COUNT:
        raise RuntimeError(f"District WFS returned {len(features)} rows; expected {DISTRICT_COUNT}")
    codes: set[str] = set()
    for feature in features:
        validate_feature("nrw_ev_baseline_metrics", feature, LAYER_CONTRACTS["nrw_ev_baseline_metrics"])
        properties = feature["properties"]
        code = properties.get("nuts_code")
        if not isinstance(code, str) or not code or code in codes:
            raise RuntimeError("District WFS has missing or duplicate district identities")
        codes.add(code)
        for field in ("district_name", "formula_version", "population_source_year", "energy_reporting_year", "expected_municipalities", "growth_years_required"):
            if properties.get(field) is None:
                raise RuntimeError(f"District {code} is missing required context {field}")
        for field in ("consumption_municipal_coverage", "renewable_municipal_coverage", "municipal_workbook_renewable_capacity_coverage", "traffic_length_coverage", "line_voltage_coverage", "substation_voltage_coverage"):
            if properties.get(field) is not None and not 0 <= numeric(properties[field], field) <= 1:
                raise RuntimeError(f"District {code} has invalid coverage {field}")
        for field in DISTRICT_SCORE_FIELDS:
            if properties.get(field) is not None and not 0 <= numeric(properties[field], field) <= 100:
                raise RuntimeError(f"District {code} has invalid score range {field}")
        data_quality = properties.get("data_quality_flag")
        if data_quality == COMPLETE_BASELINE_FLAG:
            # This flag is owned by EV/infrastructure. Municipal energy has a
            # separate availability/reason contract and can be unavailable
            # without invalidating readiness or investment priority.
            require_present_scores(properties, COMPLETE_BASELINE_SCORE_FIELDS, f"District {code} with complete inputs")
        elif data_quality not in {"missing_ev_input", "missing_infrastructure_input"}:
            raise RuntimeError(f"District {code} has unknown baseline data quality flag {data_quality!r}")

        transport_quality = properties.get("transport_data_quality_flag")
        if transport_quality == "complete_traffic_coverage":
            require_present_scores(properties, TRANSPORT_SCORE_FIELDS, f"District {code} with complete traffic")
        elif transport_quality in {"no_counted_road_network", "no_published_traffic_value"}:
            if properties.get("transport_load_score") is not None:
                raise RuntimeError(f"District {code} publishes transport score despite unavailable traffic")
        elif transport_quality != "partial_traffic_coverage":
            raise RuntimeError(f"District {code} has unknown transport data quality flag {transport_quality!r}")

        grid_quality = properties.get("grid_data_quality_flag")
        if grid_quality == "complete_grid_inputs":
            require_present_scores(properties, GRID_SCORE_FIELDS, f"District {code} with complete grid inputs")
        elif grid_quality in UNAVAILABLE_GRID_FLAGS:
            if properties.get("grid_readiness_proxy_score") is not None:
                raise RuntimeError(f"District {code} publishes grid readiness despite unavailable grid input")
        elif grid_quality != "partial_line_voltage":
            raise RuntimeError(f"District {code} has unknown grid data quality flag {grid_quality!r}")

        energy_quality = properties.get("energy_data_quality_flag")
        if energy_quality == "missing_required_input":
            if not properties.get("energy_unavailable_reason"):
                raise RuntimeError(f"District {code} has unavailable energy without a reason")
            # Local balance and growth have independent source windows. A
            # zero-consumption denominator can leave growth known, and an
            # incomplete growth window can leave local balance known. Only the
            # composite risk must be unavailable when any input is missing.
            if properties.get("grid_absorption_risk_proxy_score") is not None:
                raise RuntimeError(f"District {code} publishes energy risk despite unavailable input")
            if all(properties.get(field) is not None for field in (
                "local_energy_balance_score", "renewable_growth_score", "grid_readiness_proxy_score",
            )):
                raise RuntimeError(f"District {code} reports unavailable energy despite complete components")
        elif energy_quality in {"hybrid_complete", "published_complete_no_wind"}:
            if properties.get("energy_unavailable_reason") is not None:
                raise RuntimeError(f"District {code} reports an energy-unavailable reason for complete energy input")
            require_present_scores(properties, ENERGY_COMPONENT_SCORE_FIELDS, f"District {code} with complete energy input")
        else:
            raise RuntimeError(f"District {code} has unknown energy data quality flag {energy_quality!r}")


def verify_wfs_contract(session: requests.Session, wfs_url: str) -> None:
    district_features = get_features(session, wfs_url, "nrw_ev_baseline_metrics", count=DISTRICT_COUNT + 1)
    validate_district_contract(district_features)
    for layer, contract in LAYER_CONTRACTS.items():
        schema = describe_feature_type(session, wfs_url, layer)
        if NAMESPACE_URI not in schema:
            raise RuntimeError(f"DescribeFeatureType for {layer} has wrong workspace namespace")
        missing = sorted(field for field in contract.properties if field not in schema)
        if missing:
            raise RuntimeError(f"DescribeFeatureType for {layer} is missing: {', '.join(missing)}")
        if layer == "nrw_ev_baseline_metrics":
            continue
        features = get_features(session, wfs_url, layer, count=1, bbox=WFS_SAMPLE_BBOXES.get(layer))
        if contract.required_feature and not features:
            raise RuntimeError(f"WFS sample read returned no feature for {layer}")
        for feature in features:
            validate_feature(layer, feature, contract)
            if layer == "nrw_chargers":
                validate_charger_feature(feature)
            if layer == "nrw_renewable_potential":
                p = feature["properties"]
                if p.get("technology") not in {"Windenergie", "Photovoltaik Freifläche"} or p.get("status") != "In Betrieb":
                    raise RuntimeError("Renewable display sample includes an excluded technology or non-operating asset")


def district_metrics(session: requests.Session, wfs_url: str) -> dict:
    features = get_features(session, wfs_url, "nrw_ev_scenario_metrics", cql_filter=f"nuts_code='{DISTRICT}'", count=2)
    if len(features) != 1:
        raise RuntimeError(f"Expected one metrics row for {DISTRICT}, received {len(features)}")
    validate_feature("nrw_ev_scenario_metrics", features[0], LAYER_CONTRACTS["nrw_ev_scenario_metrics"])
    return features[0]["properties"]


def assert_metric_increment(before: dict, after: dict) -> None:
    if int(after["scenario_chargers_total"]) != int(before["scenario_chargers_total"]) + 1:
        raise RuntimeError("Scenario charger total did not increase by one")
    if int(after["scenario_charging_points_total"]) != int(before["scenario_charging_points_total"]) + CHARGING_POINTS:
        raise RuntimeError("Scenario charging-point total did not increase by four")


def assert_scenario_formula_change(before: dict, after: dict) -> None:
    # Baseline component scores are generated with immutable baseline bounds.
    # A proposal must never re-normalize those bounds while recomputing its own
    # scenario values.
    for name in BASELINE_SCORE_FIELDS:
        if abs(numeric(after[f"baseline_{name}"], f"baseline_{name}") - numeric(before[f"baseline_{name}"], f"baseline_{name}")) > 0.051:
            raise RuntimeError(f"Scenario proposal changed fixed baseline {name}")
    for name in ("charger_density_score", "charger_accessibility_score", "population_adjusted_coverage_score", "ev_readiness_score", "charger_deficit_score", "investment_priority_score"):
        baseline = numeric(after[f"baseline_{name}"], f"baseline_{name}")
        scenario = numeric(after[f"scenario_{name}"], f"scenario_{name}")
        delta = numeric(after[f"{name}_delta"], f"{name}_delta")
        if abs((scenario - baseline) - delta) > 0.051:
            raise RuntimeError(f"Scenario {name} delta does not equal scenario minus baseline")
        if not 0 <= scenario <= 100:
            raise RuntimeError(f"Scenario {name} is outside the score range")
    for phase in ("baseline", "scenario"):
        for score_name, measure_name, bounds_name, inverse in SCENARIO_COMPONENTS:
            expected = normalize_component(
                numeric(after[f"{phase}_{measure_name}"], f"{phase}_{measure_name}"),
                numeric(after[f"{bounds_name}_lower_bound"], f"{bounds_name}_lower_bound"),
                numeric(after[f"{bounds_name}_upper_bound"], f"{bounds_name}_upper_bound"),
                inverse=inverse,
            )
            actual = numeric(after[f"{phase}_{score_name}"], f"{phase}_{score_name}")
            if abs(actual - expected) > 0.051:
                raise RuntimeError(f"Scenario {phase} {score_name} does not match fixed baseline bounds")
    expected_readiness = rounded_score(
        0.40 * numeric(after["scenario_charger_density_score"], "scenario_charger_density_score")
        + 0.30 * numeric(after["scenario_charger_accessibility_score"], "scenario_charger_accessibility_score")
        + 0.30 * numeric(after["scenario_population_adjusted_coverage_score"], "scenario_population_adjusted_coverage_score"),
    )
    if abs(numeric(after["scenario_ev_readiness_score"], "scenario_ev_readiness_score") - expected_readiness) > 0.051:
        raise RuntimeError("Scenario readiness does not follow the canonical component weights")
    if abs(numeric(after["scenario_charger_deficit_score"], "scenario_charger_deficit_score") - (100 - numeric(after["scenario_ev_readiness_score"], "scenario_ev_readiness_score"))) > 0.051:
        raise RuntimeError("Scenario charger deficit does not equal inverse readiness")
    expected_priority = rounded_score(
        0.60 * numeric(after["scenario_charger_deficit_score"], "scenario_charger_deficit_score")
        + 0.40 * numeric(after["infrastructure_opportunity_score"], "infrastructure_opportunity_score"),
    )
    if abs(numeric(after["scenario_investment_priority_score"], "scenario_investment_priority_score") - expected_priority) > 0.051:
        raise RuntimeError("Scenario priority does not follow the canonical component weights")
    rank = numeric(after["scenario_priority_rank"], "scenario_priority_rank")
    if not rank.is_integer() or not 1 <= rank <= DISTRICT_COUNT:
        raise RuntimeError("Scenario priority rank is outside the district range")
    if numeric(after["scenario_chargers_total"], "scenario_chargers_total") <= numeric(before["scenario_chargers_total"], "scenario_chargers_total"):
        raise RuntimeError("Scenario formulas did not receive the owned proposal")


def assert_metric_restoration(before: dict, restored: dict) -> None:
    """Ensure cleanup restores all proposal-sensitive scenario values."""
    fields = (
        "scenario_chargers_total", "scenario_charging_points_total",
        *(f"scenario_{name}" for name in BASELINE_SCORE_FIELDS),
        "scenario_priority_rank", "data_quality_flag",
    )
    for field in fields:
        if field not in before or field not in restored:
            raise RuntimeError(f"Verifier cleanup response is missing {field}")
        if isinstance(before[field], (int, float)) and not isinstance(before[field], bool):
            if abs(numeric(before[field], field) - numeric(restored[field], field)) > 0.051:
                raise RuntimeError(f"Verifier cleanup did not restore {field}")
        elif before[field] != restored[field]:
            raise RuntimeError(f"Verifier cleanup did not restore {field}")


def find_owned_features(session: requests.Session, wfs_url: str, request_id: str) -> list[dict]:
    return get_features(session, wfs_url, "proposed_chargers", cql_filter=f"request_id='{request_id}'", count=10)


def find_station_id(features: list[dict], name: str) -> str:
    matches = [feature for feature in features if feature.get("properties", {}).get("name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one verifier station named {name}, received {len(matches)}")
    station_id = matches[0].get("properties", {}).get("id")
    if not station_id:
        raise RuntimeError("Verifier station has no UUID")
    return str(station_id)


def create_or_reconcile(session: requests.Session, wfs_url: str, name: str, request_id: str) -> str:
    if find_owned_features(session, wfs_url, request_id):
        raise RuntimeError("New verifier request ID already exists")
    response_id: str | None = None
    try:
        response_id = inserted_station_id(post_transaction(session, wfs_url, insert_xml(name, request_id)))
    except WfsTransactionError:
        raise
    except (requests.RequestException, RuntimeError):
        # The frontend's recovery rule: query the persisted request ID before
        # any retry; this verifier never retries an uncertain insert.
        pass
    owned = find_owned_features(session, wfs_url, request_id)
    station_id = find_station_id(owned, name)
    if response_id is not None and response_id != station_id:
        raise RuntimeError("Inserted and queried proposed-station UUIDs differ")
    coordinates = owned[0].get("geometry", {}).get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2 or abs(float(coordinates[0]) - LONGITUDE) > 1e-6 or abs(float(coordinates[1]) - LATITUDE) > 1e-6:
        raise RuntimeError("Owned proposal did not round-trip asymmetric longitude/latitude coordinates")
    return station_id


def cleanup_owned(session: requests.Session, wfs_url: str, request_id: str) -> None:
    for feature in find_owned_features(session, wfs_url, request_id):
        station_id = feature.get("properties", {}).get("id")
        if not station_id:
            raise RuntimeError("Owned proposal has no UUID for safe cleanup")
        post_transaction(session, wfs_url, delete_xml(str(station_id)))
    if find_owned_features(session, wfs_url, request_id):
        raise RuntimeError("Verifier cleanup did not remove every owned proposal")


def catalog_records(session: requests.Session, geonode_url: str) -> list[dict]:
    url, params = f"{geonode_url.rstrip('/')}/api/v2/datasets/", {"page_size": 100}
    records: list[dict] = []
    while url:
        response = session.get(url, params=params, timeout=60)
        response.raise_for_status()
        body = response.json()
        page = body.get("datasets")
        if not isinstance(page, list):
            raise RuntimeError("GeoNode catalog returned no datasets collection")
        records.extend(record for record in page if isinstance(record, dict))
        links = body.get("links") if isinstance(body.get("links"), dict) else {}
        next_url = links.get("next")
        url, params = (next_url, None) if isinstance(next_url, str) and next_url else ("", None)
    return records


def verify_catalog(session: requests.Session, geonode_url: str) -> None:
    records = catalog_records(session, geonode_url)
    published = {record.get("name") for record in records if record.get("workspace") == WORKSPACE and record.get("is_published") is True}
    missing = [name for name in REQUIRED_CATALOG_LAYERS if name not in published]
    if missing:
        raise RuntimeError(f"GeoNode catalog is missing project layers: {', '.join(missing)}")


def verify(session: requests.Session, *, frontend_url: str, geonode_url: str) -> None:
    frontend = session.get(frontend_url, timeout=30)
    frontend.raise_for_status()
    wfs_url = f"{frontend_url.rstrip('/')}/geoserver/ows"
    verify_wfs_contract(session, wfs_url)
    verify_catalog(session, geonode_url)
    before, request_id = district_metrics(session, wfs_url), str(uuid4())
    name = f"{RUN_PREFIX} {request_id}"
    try:
        create_or_reconcile(session, wfs_url, name, request_id)
        after = district_metrics(session, wfs_url)
        assert_metric_increment(before, after)
        assert_scenario_formula_change(before, after)
        for layer in READ_ONLY_TRANSACTION_LAYERS:
            response = session.post(wfs_url, data=harmless_read_only_write_xml(layer).encode("utf-8"), headers={"Content-Type": "text/xml; charset=UTF-8"}, timeout=60)
            if not is_exception_response(response.status_code, response.text):
                raise RuntimeError(f"Read-only layer unexpectedly accepted a WFS transaction: {layer}")
    finally:
        cleanup_owned(session, wfs_url, request_id)
    restored = district_metrics(session, wfs_url)
    assert_metric_restoration(before, restored)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the local GeoNode scenario workflow end to end")
    parser.add_argument("--frontend-url", default="http://localhost:8081")
    parser.add_argument("--geonode-url", default="http://localhost:8000")
    args = parser.parse_args()
    verify(requests.Session(), frontend_url=args.frontend_url, geonode_url=args.geonode_url)
    print("End-to-end verification passed; owned temporary proposals were removed")


if __name__ == "__main__":
    main()
