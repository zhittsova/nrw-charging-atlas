"""Export the five dashboard fallbacks from one read-only canonical snapshot.

The dashboard may use these files only as a snapshot fallback.  Scores are
never reimplemented here: every district property comes from the canonical
PostGIS publish view.  A complete staged directory is promoted only after all
five collections and their manifest have passed local structural checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from config_utils import ROOT


RUNTIME_ROOT = ROOT / "data" / "runtime"
CURRENT_DIRECTORY = RUNTIME_ROOT / "current"
MANIFEST_NAME = "manifest.json"

ARTIFACTS = {
    "districts": ("nrw_regions_sample.geojson", "publish.nrw_ev_baseline_metrics", "nuts_code"),
    "chargers": ("nrw_charging_stations_sample.geojson", "publish.nrw_chargers", "source_id"),
    "autobahns": ("nrw_autobahns_sample.geojson", "publish.nrw_autobahns", "source_id"),
    "regional_roads": ("nrw_regional_roads_sample.geojson", "publish.nrw_regional_roads", "source_id"),
    "renewables": ("nrw_renewable_assets_sample.geojson", "publish.nrw_renewable_potential", "source_id"),
}

DISTRICT_REQUIRED_FIELDS = {
    "nuts_code", "district_name", "ev_readiness_score", "charger_deficit_score",
    "infrastructure_opportunity_score", "investment_priority_score", "data_quality_flag",
    "formula_version", "population_source_year", "transport_data_quality_flag",
    "grid_data_quality_flag", "energy_data_quality_flag",
    "operating_asset_renewable_capacity_mw", "municipal_workbook_renewable_capacity_mw",
}
DISPLAY_RENEWABLE_TECHNOLOGIES = {"Windenergie", "Photovoltaik Freifläche"}


def _collection_sql(view: str, order_column: str, *, renewable_display_only: bool = False) -> str:
    where = ""
    if renewable_display_only:
        # This is a source classification filter, deliberately separate from
        # analytics.nrw_renewable_metrics and its district totals.
        where = " WHERE technology IN ('Windenergie', 'Photovoltaik Freifläche') AND status = 'In Betrieb'"
    return f"""
SELECT jsonb_build_object(
  'type', 'FeatureCollection',
  'features', COALESCE(jsonb_agg(jsonb_build_object(
      'type', 'Feature',
      'properties', to_jsonb(row) - 'geom',
      'geometry', ST_AsGeoJSON(row.geom)::jsonb
    ) ORDER BY row.{order_column}), '[]'::jsonb)
)
FROM (SELECT * FROM {view}{where}) AS row
"""


def snapshot_sql() -> str:
    statements = ["BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;"]
    for name, (_, view, order_column) in ARTIFACTS.items():
        query = _collection_sql(view, order_column, renewable_display_only=name == "renewables")
        statements.append(f"SELECT '{name}' || E'\\t' || (({query.strip()})::text);")
    statements.append(
        "SELECT 'metadata' || E'\\t' || jsonb_build_object("
        "'formula_version', (SELECT formula_version FROM analytics.nrw_formula_version), "
        "'formula_version_date', (SELECT formula_version_date FROM analytics.nrw_formula_version), "
        "'score_model', COALESCE((SELECT jsonb_agg(to_jsonb(m) ORDER BY sort_order) "
        "FROM publish.nrw_score_model m), '[]'::jsonb), "
        "'source_snapshots', COALESCE((SELECT jsonb_agg(to_jsonb(s) ORDER BY source_key) "
        "FROM raw.source_snapshots s), '[]'::jsonb), "
        "'ingest_run_id', (SELECT ingest_run_id FROM raw.ingest_runs WHERE is_current), "
        "'ingested_source_provenance', COALESCE((SELECT jsonb_agg(jsonb_build_object("
        "'source_key', i.source_key, 'source_path', i.source_path, 'bytes', i.bytes, "
        "'sha256', i.sha256, 'provenance', i.provenance, 'provenance_sha256', i.provenance_sha256) "
        "ORDER BY i.source_key) FROM raw.ingest_source_inputs i JOIN raw.ingest_runs r "
        "ON r.ingest_run_id = i.ingest_run_id WHERE r.is_current), '[]'::jsonb));"
    )
    statements.append("COMMIT;")
    return "\n".join(statements)


def run_snapshot_query(database_url: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["psql", "--no-psqlrc", "--set", "ON_ERROR_STOP=1", "--tuples-only", "--no-align", "--quiet", database_url],
        input=snapshot_sql(), text=True, capture_output=True, check=True,
    )
    payload: dict[str, Any] = {}
    for line in completed.stdout.splitlines():
        name, separator, document = line.partition("\t")
        if not separator:
            continue
        payload[name] = json.loads(document)
    expected = {*ARTIFACTS, "metadata"}
    missing = expected - payload.keys()
    if missing:
        raise RuntimeError(f"Canonical snapshot query omitted: {', '.join(sorted(missing))}")
    validate_ingested_provenance(payload["metadata"])
    return payload


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_collection(name: str, collection: Any) -> None:
    if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection":
        raise ValueError(f"{name} is not a GeoJSON FeatureCollection")
    features = collection.get("features")
    if not isinstance(features, list):
        raise ValueError(f"{name} has no feature list")
    identity = ARTIFACTS[name][2]
    values: list[str] = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError(f"{name} has an invalid feature")
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict) or not geometry.get("type"):
            raise ValueError(f"{name} has an invalid properties/geometry pair")
        if properties.get(identity) is None:
            raise ValueError(f"{name} feature is missing {identity}")
        values.append(str(properties[identity]))
        if name == "districts" and not DISTRICT_REQUIRED_FIELDS <= properties.keys():
            missing = DISTRICT_REQUIRED_FIELDS - properties.keys()
            raise ValueError(f"districts omitted A11 fields: {', '.join(sorted(missing))}")
        if name == "renewables":
            if properties.get("technology") not in DISPLAY_RENEWABLE_TECHNOLOGIES:
                raise ValueError("renewable display export contains a non-display technology")
            if properties.get("status") != "In Betrieb":
                raise ValueError("renewable display export contains a non-operating asset")
    if values != sorted(values):
        raise ValueError(f"{name} is not ordered by {identity}")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} has duplicate {identity} values")
    if name == "districts" and len(features) != 53:
        raise ValueError(f"Expected 53 canonical districts, found {len(features)}")


def validate_ingested_provenance(metadata: Any) -> None:
    if not isinstance(metadata, dict) or not isinstance(metadata.get("ingest_run_id"), str):
        raise RuntimeError("Canonical snapshot has no successful ingest-run association")
    records = metadata.get("ingested_source_provenance")
    if not isinstance(records, list) or not records:
        raise RuntimeError("Canonical snapshot has no ingested source provenance")
    for record in records:
        if not isinstance(record, dict) or not all(isinstance(record.get(field), str) and record[field] for field in ("source_key", "source_path")):
            raise RuntimeError("Canonical snapshot has malformed ingested source provenance")
        if not isinstance(record.get("bytes"), int) or record["bytes"] < 0:
            raise RuntimeError("Canonical snapshot has malformed ingested source provenance")
        if not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64:
            raise RuntimeError("Canonical snapshot has malformed ingested source provenance")


def build_manifest(payload: dict[str, Any], artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    metadata = payload["metadata"]
    payload_hash = sha256_bytes(canonical_json({name: payload[name] for name in ARTIFACTS}))
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "formula_version": metadata.get("formula_version"),
        "formula_version_date": metadata.get("formula_version_date"),
        "score_model": metadata.get("score_model"),
        "snapshot_payload_sha256": payload_hash,
        "database_source_snapshots": metadata.get("source_snapshots"),
        "ingest_run_id": metadata["ingest_run_id"],
        "ingested_source_provenance": metadata["ingested_source_provenance"],
        "renewable_display_filter": {
            "status": "In Betrieb",
            "technologies": sorted(DISPLAY_RENEWABLE_TECHNOLOGIES),
            "excludes": "building-mounted and unknown solar categories; analytical district totals are unchanged",
        },
        "artifacts": artifacts,
    }


def write_snapshot(payload: dict[str, Any], runtime_root: Path = RUNTIME_ROOT) -> dict[str, Any]:
    runtime_root.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".staged-", dir=runtime_root))
    # The nginx worker reads this host-mounted directory.  Do not inherit a
    # restrictive temporary-directory mode into the promoted artifact set.
    staged.chmod(0o755)
    artifact_manifest: dict[str, dict[str, Any]] = {}
    try:
        for name, (filename, _, _) in ARTIFACTS.items():
            collection = payload[name]
            validate_collection(name, collection)
            content = canonical_json(collection)
            artifact_path = staged / filename
            artifact_path.write_bytes(content)
            artifact_path.chmod(0o644)
            artifact_manifest[name] = {"file": filename, "count": len(collection["features"]), "sha256": sha256_bytes(content)}
        manifest = build_manifest(payload, artifact_manifest)
        manifest_path = staged / MANIFEST_NAME
        manifest_path.write_bytes(canonical_json(manifest))
        manifest_path.chmod(0o644)
        generations = runtime_root / ".generations"
        generations.mkdir(exist_ok=True)
        generation = generations / uuid4().hex
        os.replace(staged, generation)
        pointer = runtime_root / f".current-{uuid4().hex}"
        pointer.symlink_to(generation.relative_to(runtime_root), target_is_directory=True)
        current = runtime_root / "current"
        if current.exists() and not current.is_symlink():
            raise RuntimeError("Runtime current must be a serving pointer; migrate the legacy directory before export")
        os.replace(pointer, current)
        # Only generations not named by the atomic serving pointer are removed.
        active = os.readlink(current)
        for candidate in generations.iterdir():
            if candidate.name != Path(active).name:
                shutil.rmtree(candidate)
        return manifest
    except Exception:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        raise


def export_snapshot(database_url: str, runtime_root: Path = RUNTIME_ROOT) -> dict[str, Any]:
    return write_snapshot(run_snapshot_query(database_url), runtime_root)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export canonical NRW dashboard runtime assets")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--runtime-root", type=Path, default=RUNTIME_ROOT)
    args = parser.parse_args()
    if not args.database_url:
        raise ValueError("DATABASE_URL or --database-url is required")
    manifest = export_snapshot(args.database_url, args.runtime_root)
    print(f"exported {len(manifest['artifacts'])} canonical artifacts to {args.runtime_root / 'current'}")


if __name__ == "__main__":
    main()
