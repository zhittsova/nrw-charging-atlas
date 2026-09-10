"""Validated, restart-safe raw-source cache shared by NRW fetch commands."""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from csv import Error as CSVError, reader
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from urllib.request import Request, urlopen
from urllib.error import HTTPError


USER_AGENT = "nrw-energy-infrastructure-intelligence/0.1"
CHUNK_SIZE = 1024 * 1024


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def resolve_target(root: Path, target_path: str) -> Path:
    root = root.resolve()
    target = (root / target_path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"Target path escapes project root: {target_path}")
    return target


def provenance_path(provenance_dir: Path, dataset_id: str) -> Path:
    return provenance_dir / f"{dataset_id}.json"


def partial_metadata_path(partial: Path) -> Path:
    return partial.with_suffix(partial.suffix + ".json")


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    try:
        partial.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def cache_record(row: dict[str, str], target: Path, provenance: Path) -> dict[str, object] | None:
    """Return a cache record only when both bytes and provenance still agree."""
    if not target.is_file() or not provenance.is_file():
        return None
    try:
        record = json.loads(provenance.read_text(encoding="utf-8"))
        size, checksum = _sha256(target)
        if (
            record.get("dataset_id") != row["dataset_id"]
            or record.get("target_path") != row["target_path"]
            or record.get("registry_url") != (row.get("url") or row.get("source_url"))
            or record.get("bytes") != size
            or record.get("sha256") != checksum
            or (row.get("sha256") and row["sha256"].strip() != checksum)
            or size > int(row["max_bytes"])
        ):
            return None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return record


def _response_status(response: BinaryIO) -> int | None:
    status = getattr(response, "status", None)
    if isinstance(status, int):
        return status
    getcode = getattr(response, "getcode", None)
    return getcode() if callable(getcode) else None


_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


def _content_range(response: BinaryIO) -> tuple[int, int, int] | None:
    value = response.headers.get("Content-Range")
    if not value:
        return None
    match = _CONTENT_RANGE.fullmatch(value.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _remote_identity(response: BinaryIO) -> tuple[str, str] | None:
    # Weak ETags do not identify byte-for-byte representations for If-Range.
    value = response.headers.get("ETag")
    if value and not value.startswith("W/"):
        return "ETag", value
    return None


def _load_partial_metadata(path: Path, *, url: str, existing: int) -> dict[str, object] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            record.get("url") != url
            or record.get("bytes") != existing
            or record.get("identity_header") != "ETag"
            or not isinstance(record.get("identity_value"), str)
            or not record["identity_value"]
            or record["identity_value"].startswith("W/")
        ):
            return None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return record


def _discard_partial(partial: Path) -> None:
    partial.unlink(missing_ok=True)
    partial_metadata_path(partial).unlink(missing_ok=True)


def validate_consumed_format(path: Path, *, dataset_id: str = "input") -> None:
    """Reject obvious transport/error documents before replacing raw inputs.

    This is deliberately structural rather than a second ETL implementation;
    the S09 import validators remain responsible for domain semantics.
    """
    format_path = path.with_suffix("") if path.suffix == ".part" else path
    suffixes = "".join(format_path.suffixes).lower()
    if suffixes.endswith((".geojson", ".json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{dataset_id} is not valid JSON") from error
        if suffixes.endswith(".geojson") and (
            not isinstance(document, dict) or document.get("type") != "FeatureCollection" or not isinstance(document.get("features"), list)
        ):
            raise ValueError(f"{dataset_id} is not a GeoJSON FeatureCollection")
        return
    if suffixes.endswith(".csv"):
        bnetza = dataset_id == "bnetza_charging_register_nrw"
        try:
            with path.open(encoding="cp1252" if bnetza else "utf-8-sig", newline="") as source:
                for _ in range(10 if bnetza else 0):
                    next(source)
                rows = reader(source, delimiter=";" if bnetza else ",", strict=True)
                headers = next(rows)
                first_row = next(rows)
        except (OSError, UnicodeError, StopIteration, CSVError) as error:
            raise ValueError(f"{dataset_id} is not readable CSV with a header and data") from error
        required = {"Bundesland", "Ladeeinrichtungs-ID"} if bnetza else set()
        if dataset_id == "opsd_conventional_power_plants_nrw":
            required = {"id", "state", "lat", "lon", "name_bnetza", "company", "energy_source",
                        "technology", "capacity_net_bnetza", "status", "voltage", "network_operator"}
        if len(headers) < 2 or len(first_row) != len(headers) or not required.issubset(headers):
            raise ValueError(f"{dataset_id} has an invalid CSV header or data row")
        return
    if suffixes.endswith((".zip", ".xlsx")):
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if archive.testzip() is not None:
                    raise ValueError(f"{dataset_id} has a corrupt archive member")
        except (OSError, zipfile.BadZipFile) as error:
            raise ValueError(f"{dataset_id} is not a valid ZIP container") from error
        if not names or (suffixes.endswith(".xlsx") and "[Content_Types].xml" not in names):
            raise ValueError(f"{dataset_id} has an invalid archive structure")
        return
    if suffixes.endswith(".pbf"):
        with path.open("rb") as source:
            data = source.read(16)
        if (
            len(data) < 16
            or not 0 < int.from_bytes(data[:4], "big") < 64 * 1024
            or data[4:15] != b"\x0a\x09OSMHeader"
            or path.stat().st_size <= 4 + int.from_bytes(data[:4], "big")
        ):
            raise ValueError(f"{dataset_id} is not an OSM PBF stream")


def reuse_cached_source(
    row: dict[str, str], *, root: Path, provenance_dir: Path,
    validator: Callable[[Path], None] | None = None,
) -> dict[str, object] | None:
    """Return a fully validated cached input without opening a network URL."""
    target = resolve_target(root, row["target_path"])
    provenance = provenance_path(provenance_dir, row["dataset_id"])
    cached = cache_record(row, target, provenance)
    if cached and validator:
        try:
            validator(target)
        except (OSError, ValueError):
            cached = None
    if not cached:
        return None
    reused = dict(cached)
    reused["cache_status"] = "reused"
    reused["checked_at"] = _timestamp()
    _atomic_json(provenance, reused)
    return reused


def fetch_source(
    row: dict[str, str],
    *,
    root: Path,
    provenance_dir: Path,
    refresh: bool = False,
    allow_large: bool = False,
    opener: Callable[..., BinaryIO] = urlopen,
    resolved_url: str | None = None,
    validator: Callable[[Path], None] | None = None,
    source_date_from_file: Callable[[Path], str | None] | None = None,
) -> dict[str, object]:
    """Reuse a checksum-validated cache or atomically replace it after validation.

    A retained ``.part`` is never a cache hit.  If its server honours Range we
    resume it; otherwise we safely restart it while leaving the last final file
    untouched until the replacement has passed all checks.
    """
    if row.get("access_type") == "public_large" and not allow_large:
        raise PermissionError(f"{row['dataset_id']} requires --allow-large")
    target = resolve_target(root, row["target_path"])
    provenance = provenance_path(provenance_dir, row["dataset_id"])
    cached = cache_record(row, target, provenance)
    if not refresh:
        reused = reuse_cached_source(row, root=root, provenance_dir=provenance_dir, validator=validator)
        if reused:
            return reused
        cached = None

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial_metadata = partial_metadata_path(partial)
    max_bytes = int(row["max_bytes"])
    if max_bytes <= 0:
        raise ValueError(f"{row['dataset_id']} max_bytes must be positive")
    url = resolved_url or row["url"]
    existing = partial.stat().st_size if partial.is_file() else 0
    had_partial = bool(existing)
    if existing > max_bytes:
        _discard_partial(partial)
        existing = 0

    metadata = _load_partial_metadata(partial_metadata, url=url, existing=existing) if existing else None
    if existing and metadata is None:
        _discard_partial(partial)
        existing = 0

    headers = {"User-Agent": USER_AGENT}
    if existing:
        headers["Range"] = f"bytes={existing}-"
        headers["If-Range"] = str(metadata["identity_value"])
    retained_complete = False
    try:
        response = opener(Request(url, headers=headers), timeout=300)
    except HTTPError as error:
        if error.code != 416 or not existing or metadata is None:
            raise
        range_value = error.headers.get("Content-Range", "")
        complete = re.fullmatch(r"bytes \*/(\d+)", range_value.strip())
        if (
            not complete
            or int(complete.group(1)) != existing
            or _remote_identity(error) != (metadata["identity_header"], metadata["identity_value"])
        ):
            error.close()
            _discard_partial(partial)
            restarted = fetch_source(
                row, root=root, provenance_dir=provenance_dir, refresh=True,
                allow_large=allow_large, opener=opener, resolved_url=resolved_url,
                validator=validator, source_date_from_file=source_date_from_file,
            )
            restarted["resume_status"] = "restarted"
            _atomic_json(provenance, restarted)
            return restarted
        response = error
        retained_complete = True
    with response:
        declared = response.headers.get("Content-Length")
        status = _response_status(response)
        if status == 206 and not existing:
            raise ValueError(f"{row['dataset_id']} received an unsolicited partial response")
        append = bool(existing and status == 206)
        if existing and not append and not retained_complete:
            # A server without range support starts a fresh candidate safely.
            _discard_partial(partial)
            existing = 0
        if append:
            content_range = _content_range(response)
            identity = _remote_identity(response)
            if (
                content_range is None
                or content_range[0] != existing
                or content_range[1] < content_range[0]
                or content_range[2] <= content_range[1]
                or content_range[2] > max_bytes
                or identity != (metadata["identity_header"], metadata["identity_value"])
            ):
                _discard_partial(partial)
                raise ValueError(f"{row['dataset_id']} resume response does not match the retained remote object")
        # A 416 Content-Length describes its error page, not the source bytes.
        declared_size = int(declared) if declared and not retained_complete else None
        if declared_size is not None and existing + declared_size > max_bytes:
            raise ValueError(
                f"{row['dataset_id']} content length {existing + declared_size} exceeds max_bytes {max_bytes} (configured limit)"
            )
        downloaded = existing
        if not retained_complete:
            identity = _remote_identity(response)
            mode = "ab" if append else "wb"
            with partial.open(mode) as output:
                while chunk := response.read(CHUNK_SIZE):
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise ValueError(
                            f"{row['dataset_id']} streamed content exceeds max_bytes {max_bytes} (configured limit)"
                        )
                    output.write(chunk)
                    if identity:
                        _atomic_json(partial_metadata, {
                            "url": url, "bytes": downloaded, "identity_header": identity[0],
                            "identity_value": identity[1],
                        })

        if declared_size is not None and downloaded - existing != declared_size:
            raise ValueError(
                f"{row['dataset_id']} received {downloaded - existing} bytes but Content-Length declared {declared_size}"
            )
        if append:
            content_range = _content_range(response)
            assert content_range is not None
            if downloaded != content_range[1] + 1 or downloaded != content_range[2]:
                raise ValueError(f"{row['dataset_id']} resumed range is incomplete")
        elif status == 416:
            # A retained completed candidate is safe only after its size has
            # been confirmed by the server's unsatisfied-range total.
            downloaded = existing

    size, checksum = _sha256(partial)
    expected_checksum = row.get("sha256", "").strip()
    if expected_checksum and checksum != expected_checksum:
        raise ValueError(f"{row['dataset_id']} checksum mismatch for downloaded input")
    if size == 0:
        raise ValueError(f"{row['dataset_id']} downloaded an empty input")
    if validator:
        validator(partial)
    partial_metadata.unlink(missing_ok=True)
    source_date = source_date_from_file(partial) if source_date_from_file else row.get("source_date") or None
    # os.replace is the sole point at which a valid prior input can change.
    os.replace(partial, target)
    result: dict[str, object] = {
        "dataset_id": row["dataset_id"],
        "url": url,
        "registry_url": row.get("url") or row.get("source_url"),
        "target_path": row["target_path"],
        "source_role": row.get("source_role", "unspecified"),
        "source_date": source_date,
        "licence": row.get("licence") or row.get("license_note") or None,
        "bytes": size,
        "sha256": checksum,
        "content_type": response.headers.get("Content-Type", ""),
        "fetched_at": _timestamp(),
        "accessed_at": _timestamp(),
        "cache_status": "refreshed" if cached else "downloaded",
        "resume_status": "completed_partial" if retained_complete else ("resumed" if append else ("restarted" if had_partial else "fresh")),
    }
    _atomic_json(provenance, result)
    return result


def store_validated_bytes(
    row: dict[str, str],
    content: bytes,
    *,
    root: Path,
    provenance_dir: Path,
    source_url: str,
    validator: Callable[[Path], None] | None = None,
    source_date: str | None = None,
) -> dict[str, object]:
    """Atomically publish a generated API snapshot after validating its bytes."""
    target = resolve_target(root, row["target_path"])
    max_bytes = int(row["max_bytes"])
    if not content:
        raise ValueError(f"{row['dataset_id']} generated an empty input")
    if len(content) > max_bytes:
        raise ValueError(f"{row['dataset_id']} content exceeds configured max_bytes {max_bytes}")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    try:
        partial.write_bytes(content)
        if validator:
            validator(partial)
        size, checksum = _sha256(partial)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    result: dict[str, object] = {
        "dataset_id": row["dataset_id"], "url": source_url,
        "registry_url": row.get("url") or row.get("source_url"), "target_path": row["target_path"],
        "source_role": row.get("source_role", "unspecified"), "source_date": source_date or row.get("source_date") or None,
        "licence": row.get("licence") or row.get("license_note") or None, "bytes": size,
        "sha256": checksum, "content_type": "application/json", "fetched_at": _timestamp(),
        "accessed_at": _timestamp(),
        "cache_status": "downloaded", "resume_status": "not_applicable",
    }
    _atomic_json(provenance_path(provenance_dir, row["dataset_id"]), result)
    return result
