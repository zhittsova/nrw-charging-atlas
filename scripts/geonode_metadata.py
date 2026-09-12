"""Idempotent, source-aware metadata reconciliation for NRW GeoNode layers.

Only metadata that the project owns is changed: generic abstracts, project
keywords, the GeoNode licence category, and the ``nrw_project_provenance``
sparse field. Meaningful user-entered title, abstract, keywords, licence, and
supplemental information are retained. Source-specific terms live both in the
catalogue metadata and in ``docs/data-attribution.md``.
"""
from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor

import requests


ROOT = Path(__file__).resolve().parents[1]
CATALOGS = (ROOT / "catalog" / "data_sources.csv", ROOT / "catalog" / "nrw_infrastructure_sources.csv")
RUNTIME_MANIFEST = ROOT / "data" / "runtime" / "current" / "manifest.json"
WORKSPACE = "nrw"
PROJECT_KEYWORDS = ("Nordrhein-Westfalen", "NRW energy infrastructure", "project-managed metadata")
GENERIC_ABSTRACTS = {"", "No abstract provided", "NRW energy infrastructure intelligence project layer"}
GENERIC_SUPPLEMENTAL = {"", "No information provided"}
MANAGED_SUPPLEMENTAL_PREFIX = "NRW project provenance:"
LEGACY_MANAGED_ABSTRACTS = {
    "District grid-readiness planning proxy derived from mapped OpenStreetMap power infrastructure. It does not claim distribution-network capacity or connection availability.",
    "NRW renewable-context planning metric derived from operating renewable assets and documented technology diversity. Potential-study data is not treated as observed stock.",
}
PROVENANCE_FIELD = "nrw_project_provenance"
OWNERSHIP_FIELD = "nrw_project_owned_metadata"
SPARSE_VALUE_LIMIT = 1024
HTTP_ATTEMPTS = 3
HTTP_TIMEOUT = 60


@dataclass(frozen=True)
class LayerMetadata:
    abstract: str
    keywords: tuple[str, ...]
    source_ids: tuple[str, ...]
    licence: str = "Varied / Derived"


ALL_SCORING_SOURCES = (
    "nuts3_regions_gisco_nrw",
    "bnetza_charging_register_nrw",
    "eurostat_population_nrw",
    "strassen_nrw_traffic_values",
    "energieatlas_nrw_renewable_sites",
    "energieatlas_nrw_admin_electricity",
    "geofabrik_nrw_osm_power",
)

# These are exact upstream data terms, not a generic classification of the
# project output.  `provision_geoserver_layers.ensure_geonode_licences` makes
# the three non-default entries available before this module requests them.
CC_BY_4 = "Creative Commons Attribution 4.0 International"
DL_DE_BY_2 = "Data Licence Germany Attribution 2.0"
DL_DE_ZERO_2 = "Data Licence Germany Zero 2.0"

LAYER_METADATA: dict[str, LayerMetadata] = {
    "nrw_accessibility": LayerMetadata(
        "Road-accessibility context derived from Straßen.NRW road and traffic records. It is a planning aid, not a statement about charging-station access, siting approval, or road-network completeness.",
        ("Straßen.NRW", "road accessibility", "planning proxy"),
        ("strassen_nrw_traffic_values",),
        DL_DE_BY_2,
    ),
    "nrw_autobahns": LayerMetadata(
        "Autobahn geometry from the OpenStreetMap-derived NRW extract. It is map context only; no Autobahn traffic intensity is claimed.",
        ("OpenStreetMap", "Autobahn", "map context"),
        ("geofabrik_nrw_osm_power",),
        "Open Data Commons Open Database License / OSM",
    ),
    "nrw_chargers": LayerMetadata(
        "Publicly accessible NRW charging stations filtered from the Bundesnetzagentur Ladesäulenregister. Register availability and snapshot dates are carried with publication where supplied.",
        ("EV charging", "Bundesnetzagentur", "CC BY 4.0"),
        ("bnetza_charging_register_nrw",),
        CC_BY_4,
    ),
    "nrw_district_priority": LayerMetadata(
        "NRW district investment-priority planning proxy derived from the published, versioned analytical model and its documented source inputs. It does not establish grid capacity, demand, or approved sites.",
        ("investment priority", "planning proxy", "derived data"),
        ALL_SCORING_SOURCES,
    ),
    "nrw_ev_baseline_metrics": LayerMetadata(
        "Baseline NRW EV-readiness metrics derived from charging-register, population, district geography, and documented infrastructure context. Missing inputs remain unavailable rather than zero.",
        ("EV readiness", "baseline", "derived data"),
        ALL_SCORING_SOURCES,
    ),
    "nrw_ev_scenario_metrics": LayerMetadata(
        "Scenario comparison of the published NRW EV baseline and locally proposed charging stations. It is a local planning scenario, not an official infrastructure record.",
        ("EV readiness", "scenario", "local proposals"),
        ALL_SCORING_SOURCES,
    ),
    "nrw_grid_absorption_risk": LayerMetadata(
        "Grid-absorption-risk planning proxy derived from local energy-balance, renewable-growth, and mapped grid context. It does not measure distribution-network hosting capacity.",
        ("grid proxy", "energy balance", "planning proxy"),
        ALL_SCORING_SOURCES,
    ),
    "nrw_grid_proxy": LayerMetadata(
        "District grid-readiness planning proxy derived from mapped OpenStreetMap power infrastructure and Eurostat GISCO district geometry. It does not claim distribution-network capacity or connection availability.",
        ("OpenStreetMap", "Eurostat GISCO", "grid proxy", "planning proxy"),
        ("nuts3_regions_gisco_nrw", "geofabrik_nrw_osm_power"),
    ),
    "nrw_grid_readiness": LayerMetadata(
        "Mapped NRW power-infrastructure context derived from the OpenStreetMap extract. It is not a statement of grid ownership, capacity, or available connections.",
        ("OpenStreetMap", "power infrastructure", "map context"),
        ("geofabrik_nrw_osm_power",),
        "Open Data Commons Open Database License / OSM",
    ),
    "nrw_infrastructure_opportunity": LayerMetadata(
        "NRW infrastructure-opportunity planning proxy combining published transport, grid-readiness, and renewable-context components under the documented model.",
        ("infrastructure opportunity", "planning proxy", "derived data"),
        ALL_SCORING_SOURCES,
    ),
    "nrw_local_energy_balance": LayerMetadata(
        "NRW district renewable-generation and consumption context derived from Energieatlas NRW source material and Eurostat GISCO district geometry. Coverage and unavailable reasons are published with the values.",
        ("Energieatlas NRW", "Eurostat GISCO", "renewable energy", "energy balance"),
        ("nuts3_regions_gisco_nrw", "energieatlas_nrw_renewable_sites", "energieatlas_nrw_admin_electricity"),
    ),
    "nrw_renewable_context": LayerMetadata(
        "NRW renewable-context planning metric derived from renewable assets, documented technology diversity, and Eurostat GISCO district geometry. Potential-study data is not treated as observed stock.",
        ("Energieatlas NRW", "Eurostat GISCO", "renewable context", "planning proxy"),
        ("nuts3_regions_gisco_nrw", "energieatlas_nrw_renewable_sites"),
    ),
    "nrw_renewable_potential": LayerMetadata(
        "NRW renewable asset inventory from Energieatlas NRW for map context. This published layer represents the staging renewable records; any operating wind and ground-PV filter is a separate runtime-dashboard consumer behavior, not a claim about this catalogue layer.",
        ("Energieatlas NRW", "renewable assets", "map context"),
        ("energieatlas_nrw_renewable_sites",),
        DL_DE_ZERO_2,
    ),
    "nrw_regional_roads": LayerMetadata(
        "Federal and state-road context derived from Straßen.NRW traffic values. The publisher states that Autobahn traffic is not included; the layer is not a complete road-network claim.",
        ("Straßen.NRW", "roads", "traffic context"),
        ("strassen_nrw_traffic_values",),
        DL_DE_BY_2,
    ),
    "nrw_transport_load": LayerMetadata(
        "NRW district transport-load planning metric derived from Straßen.NRW traffic values and Eurostat GISCO district geometry. It is not a demand forecast and excludes Autobahn traffic values.",
        ("Straßen.NRW", "Eurostat GISCO", "transport load", "planning proxy"),
        ("nuts3_regions_gisco_nrw", "strassen_nrw_traffic_values"),
    ),
    "proposed_chargers": LayerMetadata(
        "Locally proposed NRW charging stations submitted through the demonstrator. These records are user scenarios, not official infrastructure data.",
        ("EV charging", "local proposals", "scenario"),
        (),
        "Varied / Derived",
    ),
}


def source_catalog() -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for path in CATALOGS:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                records[row["dataset_id"]] = row
    return records


def resource_for_layer(resources: list[dict[str, Any]], layer: str) -> dict[str, Any] | None:
    alternate = f"{WORKSPACE}:{layer}"
    matches = [resource for resource in resources if resource.get("resource_type") == "dataset" and resource.get("alternate") == alternate]
    if len(matches) > 1:
        raise RuntimeError(f"GeoNode catalogue has ambiguous qualified resource identity: {alternate}")
    return matches[0] if matches else None


def catalog_resources(session: requests.Session, base_url: str) -> list[dict[str, Any]]:
    """Read every catalogue page; never infer project ownership from a name."""
    resources: list[dict[str, Any]] = []
    page = 1
    while True:
        response = api_call(session, "get", f"{base_url.rstrip('/')}/api/v2/resources/", params={"page_size": 100, "page": page}, timeout=HTTP_TIMEOUT)
        payload = response.json()
        batch = payload.get("resources", payload.get("data", []))
        resources.extend(batch)
        total = payload.get("total")
        if not batch or (isinstance(total, int) and len(resources) >= total):
            return resources
        page += 1


def _source_terms(row: dict[str, str]) -> str:
    return row.get("licence") or row.get("license_note") or "Terms not recorded in the source catalogue"


def successful_ingest_provenance() -> dict[str, Any]:
    """Return only provenance bound to the current successful runtime export.

    Raw-download sidecars are mutable fetch records; they are deliberately not
    catalog metadata evidence.  The runtime manifest is emitted from the
    successful database export and binds the actual input paths and checksums.
    """
    try:
        manifest = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise RuntimeError(f"Invalid successful-ingest manifest: {RUNTIME_MANIFEST}") from None
    except FileNotFoundError:
        raise RuntimeError(f"Missing successful-ingest manifest: {RUNTIME_MANIFEST}") from None
    rows = manifest.get("ingested_source_provenance")
    if not isinstance(rows, list):
        raise RuntimeError("Successful-ingest manifest has no ingested_source_provenance list")
    snapshots = manifest.get("database_source_snapshots", [])
    return {
        "ingest_run_id": manifest.get("ingest_run_id"),
        "formula_version": manifest.get("formula_version"),
        "ingest": {row["source_key"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("source_key"), str)},
        "snapshots": {row["source_key"]: row for row in snapshots if isinstance(row, dict) and isinstance(row.get("source_key"), str)},
    }


def manifest_key_for(source_id: str) -> str:
    """Resolve catalogue identities to the successful-ingest source key."""
    return {"bnetza_charging_register_nrw": "bnetza_ladesaeulenregister"}.get(source_id, source_id)


def source_provenance_record(source_id: str, catalog: dict[str, dict[str, str]], context: dict[str, Any]) -> dict[str, Any]:
    row = catalog[source_id]
    manifest_key = manifest_key_for(source_id)
    record = context["ingest"].get(manifest_key)
    snapshot = context["snapshots"].get(manifest_key, {})
    if record is None:
        return {
            "id": source_id,
            "source_url": row.get("source_url") or row.get("url"),
            "terms": _source_terms(row),
            "role": row.get("source_role"),
            "ingest_status": "not recorded in current successful ingest",
        }
    upstream = record.get("provenance") or {}
    return {
        "id": source_id,
        "source_url": upstream.get("url") or row.get("source_url") or row.get("url"),
        "terms": upstream.get("licence") or _source_terms(row),
        "role": row.get("source_role"),
        "ingest_status": "recorded" if upstream else "recorded without upstream sidecar",
        "source_date": upstream.get("source_date") or snapshot.get("snapshot_date"),
        "source_date_evidence": snapshot.get("source_note"),
        "snapshot_recorded_at": snapshot.get("recorded_at"),
        "accessed_at": upstream.get("accessed_at"),
        "sha256": record.get("sha256"),
    }


def _json_value(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if len(encoded) > SPARSE_VALUE_LIMIT:
        raise RuntimeError(f"GeoNode sparse metadata value exceeds {SPARSE_VALUE_LIMIT} characters")
    return encoded


def provenance_values(
    metadata: LayerMetadata, catalog: dict[str, dict[str, str]], context: dict[str, Any] | None = None
) -> dict[str, str]:
    """Produce bounded sparse fields with source links, terms, and DB-bound provenance.

    GeoNode's sparse-field endpoint is relatively expensive.  Keep the record
    complete but pack several sources into each bounded value rather than issue
    one request per source.  Continuation fields are needed only for layers
    whose source set cannot fit in the primary record.
    """
    context = context or successful_ingest_provenance()
    run_id = context["ingest_run_id"]
    records = [source_provenance_record(source_id, catalog, context) for source_id in metadata.source_ids]
    chunks: list[list[dict[str, Any]]] = [[]]
    for record in records:
        candidate = chunks[-1] + [record]
        try:
            _json_value(
                {
                    "field_owner": "NRW project",
                    "ingest_run_id": run_id,
                    "formula_version": context["formula_version"],
                    "terms_notice": "docs/data-attribution.md",
                    "source_fields": [f"{PROVENANCE_FIELD}_99"],
                    "sources": candidate,
                }
            )
        except RuntimeError:
            if not chunks[-1]:
                raise
            chunks.append([record])
        else:
            chunks[-1] = candidate
    fields = [PROVENANCE_FIELD, *[f"{PROVENANCE_FIELD}_{index:02d}" for index in range(2, len(chunks) + 1)]]
    values: dict[str, str] = {}
    for field, sources in zip(fields, chunks, strict=True):
        payload: dict[str, Any] = {
            "field_owner": "NRW project",
            "ingest_run_id": run_id,
            "formula_version": context["formula_version"],
            "sources": sources,
        }
        if field == PROVENANCE_FIELD:
            payload["terms_notice"] = "docs/data-attribution.md"
            payload["source_fields"] = fields[1:]
        else:
            payload["continuation_of"] = PROVENANCE_FIELD
        values[field] = _json_value(payload)
    return values


def _generic(value: object, values: set[str]) -> bool:
    return not isinstance(value, str) or value.strip() in values


def managed_update(current: dict[str, Any], metadata: LayerMetadata, catalog: dict[str, dict[str, str]], owned: dict[str, str] | None = None) -> dict[str, Any]:
    """Return only project-owned fields that need changing, preserving user data."""
    update: dict[str, Any] = {}
    owned = owned or {}
    current_abstract = current.get("abstract")
    if (_generic(current_abstract, GENERIC_ABSTRACTS) or current_abstract in LEGACY_MANAGED_ABSTRACTS or owned.get("abstract") == current_abstract) and current_abstract != metadata.abstract:
        update["abstract"] = metadata.abstract
    current_keywords = {str(keyword).strip() for keyword in current.get("hkeywords", []) if str(keyword).strip()}
    desired_keywords = sorted(current_keywords | set(PROJECT_KEYWORDS) | set(metadata.keywords))
    if set(desired_keywords) != current_keywords:
        update["hkeywords"] = desired_keywords
    current_license = current.get("license") or {}
    current_license_label = current_license.get("label")
    if (current_license_label in {None, "", "Not Specified"} or (
        owned.get("license") == current_license_label and current_license_label != metadata.licence
    )) and current_license_label != metadata.licence:
        update["license_name"] = metadata.licence
    current_supplemental = current.get("supplemental_information")
    source_names = ", ".join(catalog[source_id].get("source_name", source_id) for source_id in metadata.source_ids)
    desired_supplemental = (
        f"{MANAGED_SUPPLEMENTAL_PREFIX} source terms and redistribution notice: "
        f"docs/data-attribution.md. Sources: {source_names or 'local proposed-station scenarios'}."
    )
    if (_generic(current_supplemental, GENERIC_SUPPLEMENTAL) or owned.get("supplemental_information") == current_supplemental) and current_supplemental != desired_supplemental:
        update["supplemental_information"] = desired_supplemental
    return update


def find_license(session: requests.Session, base_url: str, name: str) -> dict[str, Any]:
    response = api_call(
        session,
        "get",
        f"{base_url.rstrip('/')}/api/v2/metadata/autocomplete/licenses",
        params={"q": name},
        timeout=HTTP_TIMEOUT,
    )
    matches = response.json().get("results", [])
    exact = next((item for item in matches if item.get("label") == name), None)
    if exact is None:
        raise RuntimeError(f"GeoNode does not provide the required licence category: {name}")
    return exact


def api_call(session: requests.Session, method: str, url: str, **kwargs: Any) -> Any:
    """Retry only transient HTTP transport failures from a busy GeoNode service."""
    for attempt in range(HTTP_ATTEMPTS):
        try:
            response = getattr(session, method)(url, **kwargs)
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout):
            if attempt + 1 == HTTP_ATTEMPTS:
                raise
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def sparse_value(session: requests.Session, url: str) -> dict[str, str]:
    response = session.get(url, timeout=HTTP_TIMEOUT)
    if getattr(response, "status_code", None) == 404:
        return {}
    response.raise_for_status()
    try:
        value = json.loads(response.json().get("value", "{}"))
    except (AttributeError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sync_one_metadata_record(
    session: requests.Session,
    base_url: str,
    resource: dict[str, Any],
    metadata: LayerMetadata,
    catalog: dict[str, dict[str, str]],
    licences: dict[str, dict[str, Any]],
    context: dict[str, Any],
) -> int:
    """Reconcile one record; callers may run independent HTTP sessions in parallel."""
    pk = resource["pk"]
    instance_url = f"{base_url.rstrip('/')}/api/v2/metadata/instance/{pk}/"
    current_response = api_call(session, "get", instance_url, timeout=HTTP_TIMEOUT)
    ownership_url = f"{base_url.rstrip('/')}/api/v2/metadata/sparse/{pk}/{OWNERSHIP_FIELD}"
    owned = sparse_value(session, ownership_url)
    update = managed_update(current_response.json(), metadata, catalog, owned)
    licence_name = update.pop("license_name", None)
    licence_updated = bool(licence_name)
    if licence_name:
        update["license"] = licences[licence_name]
    if update:
        patch = api_call(session, "patch", instance_url, json=update, timeout=HTTP_TIMEOUT)
        if patch.json().get("extraErrors"):
            raise RuntimeError(f"GeoNode rejected metadata for {resource['name']}: {patch.json()['extraErrors']}")
    for key, value in provenance_values(metadata, catalog, context).items():
        api_call(
            session,
            "put",
            f"{base_url.rstrip('/')}/api/v2/metadata/sparse/{pk}/{key}",
            json={"value": value},
            timeout=HTTP_TIMEOUT,
        )
    next_owned = dict(owned)
    for field in ("abstract", "supplemental_information"):
        if field in update:
            next_owned[field] = update[field]
    if licence_updated:
        next_owned["license"] = metadata.licence
    api_call(session, "put", ownership_url, json={"value": _json_value(next_owned)}, timeout=HTTP_TIMEOUT)
    return int(bool(update))


def sync_metadata(session: requests.Session, base_url: str, *, workers: int = 1) -> dict[str, int]:
    """Synchronize all declared project datasets and verify a repeatable read."""
    catalog = source_catalog()
    context = successful_ingest_provenance()
    resources = catalog_resources(session, base_url)
    missing: list[str] = []
    jobs: list[tuple[dict[str, Any], LayerMetadata]] = []
    for layer, metadata in LAYER_METADATA.items():
        resource = resource_for_layer(resources, layer)
        if resource is None:
            missing.append(layer)
            continue
        jobs.append((resource, metadata))
    if missing:
        raise RuntimeError(f"GeoNode catalogue is missing declared NRW datasets: {', '.join(sorted(missing))}")
    licence_names = {metadata.licence for _, metadata in jobs}
    licences = {name: find_license(session, base_url, name) for name in licence_names}
    if workers < 1:
        raise ValueError("workers must be at least one")
    if workers == 1:
        changed = sum(
            _sync_one_metadata_record(session, base_url, resource, metadata, catalog, licences, context)
            for resource, metadata in jobs
        )
    else:
        def independent_sync(resource: dict[str, Any], metadata: LayerMetadata) -> int:
            independent = requests.Session()
            independent.auth = session.auth
            return _sync_one_metadata_record(independent, base_url, resource, metadata, catalog, licences, context)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            changed = sum(pool.map(lambda job: independent_sync(*job), jobs))
    return {"datasets": len(LAYER_METADATA), "changed": changed}
