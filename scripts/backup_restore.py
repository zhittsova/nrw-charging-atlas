"""Create restricted GeoNode backups and restore them only into isolated targets.

The project database and GeoNode databases share the Compose PostGIS cluster, so
the database component is a logical ``pg_dumpall`` archive.  GeoServer's data
directory, uploaded media/static state, shared data, and generated nginx state
are archived as Docker volumes.  The local environment file is retained only
inside the restricted backup directory; it is never printed or committed.

``drill`` is deliberately destructive only to resources it names itself: a
fresh ``nrw-restore-<id>`` Compose project, its volumes, and a private restore
workspace below the backup directory.  It refuses the source project and any
pre-existing target volume before creating anything.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

try:
    import geonode_stack
except ModuleNotFoundError:
    from scripts import geonode_stack


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_ROOT = ROOT / "backups"
BACKUP_FORMAT_VERSION = 1
RESTORE_PROJECT_PREFIX = "nrw-restore-"
HELPER_IMAGE = "geonode/geoserver:2.28.4-latest"
VOLUME_COMPONENTS = {
    "geoserver-data-dir": "geoserver-data-dir.tar.gz",
    "statics": "statics.tar.gz",
    "data": "data.tar.gz",
    "nginx-confd": "nginx-confd.tar.gz",
    "nginx-certificates": "nginx-certificates.tar.gz",
}
FILE_COMPONENTS = {
    "environment": (geonode_stack.ENV_PATH, "config/geonode.env", True),
    "upstream-pin": (ROOT / "config" / "geonode-upstream.json", "manifests/geonode-upstream.json", False),
    "runtime-manifest": (ROOT / "data" / "runtime" / "current" / "manifest.json", "manifests/runtime-manifest.json", False),
}
RUNTIME_DIRECTORY = ROOT / "data" / "runtime" / "current"
RUNTIME_ROOT = ROOT / "data" / "runtime"
SAFE_PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,50}\Z")
RESTORE_ONLY_VOLUMES = ("dbdata", "dbbackups", "backup-restore", "tmp", "redisdata")
TARGET_BOOTSTRAP_CLEANUP_SQL = """
UPDATE pg_database SET datistemplate = FALSE WHERE datname = 'template_postgis';
DROP DATABASE IF EXISTS template_postgis;
DROP EXTENSION IF EXISTS postgis_tiger_geocoder CASCADE;
DROP EXTENSION IF EXISTS postgis_topology CASCADE;
DROP EXTENSION IF EXISTS postgis_raster CASCADE;
DROP EXTENSION IF EXISTS postgis CASCADE;
DROP SCHEMA IF EXISTS tiger_data CASCADE;
DROP SCHEMA IF EXISTS tiger CASCADE;
DROP SCHEMA IF EXISTS topology CASCADE;
"""
REQUIRED_COMPONENTS = frozenset(
    {
        "postgresql-all",
        "runtime-current",
        *FILE_COMPONENTS,
        *(f"volume:{name}" for name in VOLUME_COMPONENTS),
    }
)


def run(command: list[str], *, capture_output: bool = False, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=capture_output, **kwargs)


def compose_command(
    *args: str,
    env_file: Path = geonode_stack.ENV_PATH,
    project_name: str | None = None,
    extra_files: tuple[Path, ...] = (),
) -> list[str]:
    command = ["docker", "compose", "--project-directory", str(geonode_stack.GEONODE_DIR)]
    if project_name:
        command.extend(("--project-name", project_name))
    command.extend(("--env-file", str(env_file)))
    for path in (
        geonode_stack.COMPOSE_PATH,
        geonode_stack.APPLE_SILICON_COMPOSE_PATH,
        geonode_stack.PROJECT_COMPOSE_PATH,
        *extra_files,
    ):
        command.extend(("-f", str(path)))
    return [*command, *args]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def restrict(path: Path, mode: int = 0o700) -> None:
    path.chmod(mode)


def backup_directory(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    restrict(output_root)
    destination = output_root / f"nrw-backup-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}"
    destination.mkdir(mode=0o700)
    return destination


def component_record(path: Path, backup: Path, *, archive_type: str, restricted: bool) -> dict[str, Any]:
    restrict(path, 0o600)
    return {
        "path": str(path.relative_to(backup)),
        "type": archive_type,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "restricted": restricted,
    }


def configured_volume_names() -> dict[str, str]:
    completed = run(compose_command("config", "--format", "json"), capture_output=True)
    payload = json.loads(completed.stdout)
    volumes = payload.get("volumes")
    if not isinstance(volumes, dict):
        raise RuntimeError("Compose configuration did not expose named volumes")
    names: dict[str, str] = {}
    for logical_name in (*VOLUME_COMPONENTS, *RESTORE_ONLY_VOLUMES):
        entry = volumes.get(logical_name)
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise RuntimeError(f"Compose configuration has no concrete volume for {logical_name}")
        names[logical_name] = entry["name"]
    return names


def archive_volume(volume_name: str, destination: Path, archive_name: str) -> None:
    """Archive one existing Docker volume read-only through an installed image."""
    run(
        [
            "docker", "run", "--rm",
            "--mount", f"type=volume,source={volume_name},target=/source,readonly",
            "--mount", f"type=bind,source={destination},target=/backup",
            "--entrypoint", "tar", HELPER_IMAGE,
            "-czf", f"/backup/components/{archive_name}", "-C", "/source", ".",
        ]
    )


def restore_volume(volume_name: str, backup: Path, archive_name: str) -> None:
    run(["docker", "volume", "create", volume_name])
    run(
        [
            "docker", "run", "--rm",
            "--mount", f"type=volume,source={volume_name},target=/target",
            "--mount", f"type=bind,source={backup},target=/backup,readonly",
            "--entrypoint", "tar", HELPER_IMAGE,
            "-xzf", f"/backup/components/{archive_name}", "-C", "/target",
        ]
    )


def dump_databases(destination: Path) -> Path:
    target = destination / "components" / "postgresql-all.sql.gz"
    command = compose_command("exec", "-T", "db", "sh", "-lc", 'exec pg_dumpall -U "$POSTGRES_USER"')
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    with gzip.open(target, "wb") as compressed:
        shutil.copyfileobj(process.stdout, compressed)
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    if process.wait() != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"PostGIS logical backup failed: {stderr.strip()[:300] or 'pg_dumpall failed'}")
    return target


def copy_file_component(source: Path, destination: Path) -> Path:
    if not source.is_file():
        raise RuntimeError(f"Required backup dependency is missing: {source.relative_to(ROOT)}")
    target = destination / "components" / destination.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def archive_runtime(destination: Path) -> Path:
    if not RUNTIME_DIRECTORY.is_dir() or not RUNTIME_ROOT.is_dir():
        raise RuntimeError("Required runtime artifact directory data/runtime/current is missing")
    target = destination / "components" / "runtime-current.tar.gz"
    with tarfile.open(target, "w:gz", dereference=True) as archive:
        archive.add(RUNTIME_ROOT, arcname="runtime", recursive=True)
    return target


def create_backup(output_root: Path = DEFAULT_BACKUP_ROOT) -> Path:
    """Create a restricted complete backup without changing source volumes."""
    if not geonode_stack.docker_is_ready():
        raise RuntimeError("Docker Desktop is not running or Docker is unavailable")
    source_volumes = configured_volume_names()
    destination = backup_directory(output_root)
    components = destination / "components"
    components.mkdir(mode=0o700)
    records: dict[str, dict[str, Any]] = {}
    try:
        database = dump_databases(destination)
        records["postgresql-all"] = component_record(database, destination, archive_type="sql.gz", restricted=True)
        for logical_name, archive_name in VOLUME_COMPONENTS.items():
            archive_volume(source_volumes[logical_name], destination, archive_name)
            records[f"volume:{logical_name}"] = component_record(
                components / archive_name, destination, archive_type="tar.gz", restricted=True
            )
        for name, (source, relative_path, secret) in FILE_COMPONENTS.items():
            target = destination / "components" / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if not source.is_file():
                raise RuntimeError(f"Required backup dependency is missing: {source.relative_to(ROOT)}")
            shutil.copy2(source, target)
            records[name] = component_record(target, destination, archive_type="file", restricted=secret)
        runtime = archive_runtime(destination)
        records["runtime-current"] = component_record(runtime, destination, archive_type="tar.gz", restricted=True)
        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "scope": {
                "databases": "all databases and roles in the integrated PostGIS cluster",
                "volumes": sorted(VOLUME_COMPONENTS),
                "runtime": "canonical frontend fallback snapshot and its manifest",
                "configuration": "restricted local environment plus source/artifact manifests",
            },
            "components": records,
        }
        manifest_path = destination / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        restrict(manifest_path, 0o600)
        return destination
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def load_and_verify_manifest(backup: Path) -> dict[str, Any]:
    manifest_path = backup / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Backup manifest is missing or invalid") from error
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION or not isinstance(manifest.get("components"), dict):
        raise RuntimeError("Backup manifest format is unsupported")
    missing = REQUIRED_COMPONENTS - manifest["components"].keys()
    if missing:
        raise RuntimeError(f"Backup manifest is incomplete: {', '.join(sorted(missing))}")
    for name, record in manifest["components"].items():
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise RuntimeError(f"Backup manifest has invalid component record: {name}")
        component = backup / record["path"]
        if not component.is_file() or sha256(component) != record.get("sha256"):
            raise RuntimeError(f"Backup integrity check failed for {name}")
        if component.stat().st_size != record.get("bytes"):
            raise RuntimeError(f"Backup size check failed for {name}")
    return manifest


def validate_restore_target(target: str, source_volumes: dict[str, str]) -> None:
    if not SAFE_PROJECT_NAME.fullmatch(target) or not target.startswith(RESTORE_PROJECT_PREFIX):
        raise ValueError(f"Restore target must begin with {RESTORE_PROJECT_PREFIX} and use lowercase safe characters")
    for source in source_volumes.values():
        if target in source:
            raise ValueError("Restore target must not overlap a source volume name")
    for logical_name in (*VOLUME_COMPONENTS, *RESTORE_ONLY_VOLUMES):
        candidate = restore_volume_name(target, logical_name)
        inspected = subprocess.run(["docker", "volume", "inspect", candidate], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if inspected.returncode == 0:
            raise RuntimeError(f"Refusing existing restore volume: {candidate}")
    active = run(["docker", "ps", "--filter", f"label=com.docker.compose.project={target}", "--quiet"], capture_output=True)
    if active.stdout.strip():
        raise RuntimeError(f"Refusing active restore project: {target}")


def restore_volume_name(target: str, logical_name: str) -> str:
    suffixes = {
        "geoserver-data-dir": "gsdatadir",
        "statics": "statics",
        "data": "data",
        "nginx-confd": "nginxconfd",
        "nginx-certificates": "nginxcerts",
        "dbdata": "dbdata",
        "dbbackups": "dbbackups",
        "backup-restore": "backup-restore",
        "tmp": "tmp",
        "redisdata": "redisdata",
    }
    return f"{target}-{suffixes[logical_name]}"


def safe_extract(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        resolved_destination = destination.resolve()
        for member in archive.getmembers():
            member_path = (resolved_destination / member.name).resolve()
            if not member_path.is_relative_to(resolved_destination) or member.issym() or member.islnk():
                raise RuntimeError("Backup archive contains an unsafe path")
        archive.extractall(destination)


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def write_target_env(source: Path, destination: Path, *, target: str, ports: dict[str, int]) -> None:
    updates = {
        "COMPOSE_PROJECT_NAME": target,
        "HTTP_PORT": str(ports["geonode"]),
        "HTTPS_PORT": str(ports["https"]),
        "SITEURL": f"http://localhost:{ports['geonode']}/",
        "NGINX_BASE_URL": f"http://localhost:{ports['geonode']}",
        "GEOSERVER_WEB_UI_LOCATION": f"http://localhost:{ports['geoserver']}/geoserver/",
        "GEOSERVER_PUBLIC_LOCATION": f"http://localhost:{ports['geoserver']}/geoserver/",
    }
    output: list[str] = []
    seen: set[str] = set()
    for line in source.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else ""
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)
    destination.write_text("\n".join(output) + "\n", encoding="utf-8")
    restrict(destination, 0o600)


RESTORED_ENV_FILE_SERVICES = ("django", "celery", "geonode", "letsencrypt", "geoserver", "db")
RESTORED_ENVIRONMENT_ASSERTION_SERVICES = ("django", "celery", "geonode", "geoserver", "db")


def write_isolated_override(destination: Path, runtime: Path, env_file: Path, ports: dict[str, int]) -> None:
    quoted_runtime_mount = json.dumps(f"{runtime}:/usr/share/nginx/html/runtime:ro")
    quoted_env_file = json.dumps(str(env_file))
    destination.write_text(
        "services:\n"
        "  django:\n    env_file: !override\n"
        f"      - {quoted_env_file}\n"
        "  celery:\n    env_file: !override\n"
        f"      - {quoted_env_file}\n"
        "  letsencrypt:\n    env_file: !override\n"
        f"      - {quoted_env_file}\n"
        "  db:\n    env_file: !override\n"
        f"      - {quoted_env_file}\n"
        "    environment:\n"
        "      GEONODE_DATABASE: \"\"\n"
        "      GEONODE_GEODATABASE: \"\"\n"
        "    ports: !override\n"
        f"      - \"127.0.0.1:{ports['db']}:5432\"\n"
        "  geonode:\n    env_file: !override\n"
        f"      - {quoted_env_file}\n"
        "    ports: !override\n"
        f"      - \"127.0.0.1:{ports['geonode']}:80\"\n"
        f"      - \"127.0.0.1:{ports['https']}:443\"\n"
        "  geoserver:\n    env_file: !override\n"
        f"      - {quoted_env_file}\n"
        "    ports: !override\n"
        f"      - \"127.0.0.1:{ports['geoserver']}:8080\"\n"
        "  frontend:\n    ports: !override\n"
        f"      - \"127.0.0.1:{ports['frontend']}:80\"\n"
        "    volumes: !override\n"
        f"      - {quoted_runtime_mount}\n",
        encoding="utf-8",
    )
    restrict(destination, 0o600)


def wait_for_target(command: list[str], frontend_url: str, geonode_url: str, *, timeout: int = 600) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = run([*command, "ps", "--format", "json"], capture_output=True)
        states = geonode_stack.parse_compose_ps(status.stdout)
        try:
            geonode_stack.assert_core_services(states)
            for url in (frontend_url, geonode_url):
                response = requests.get(url, timeout=5)
                response.raise_for_status()
            return
        except (RuntimeError, requests.RequestException):
            time.sleep(5)
    raise RuntimeError("Timed out waiting for isolated restore services")


def assert_restored_service_environment(command: list[str], ports: dict[str, int]) -> None:
    """Prove services load the private archived target env file, not live `.env`."""
    expected_siteurl = f"http://localhost:{ports['geonode']}/"
    for service in RESTORED_ENVIRONMENT_ASSERTION_SERVICES:
        actual = run([*command, "exec", "-T", service, "printenv", "SITEURL"], capture_output=True).stdout.strip()
        if actual != expected_siteurl:
            raise RuntimeError(
                f"Isolated {service} service did not load the restored SITEURL "
                f"(expected {expected_siteurl!r}, got {actual!r})"
            )


def run_target_sql(command: list[str], document: str) -> None:
    process = subprocess.run(
        [*command, "exec", "-T", "db", "sh", "-lc", 'exec psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1'],
        input=document.encode("utf-8"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        diagnostic = process.stderr.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Could not prepare isolated database restore: {diagnostic or 'psql failed'}")


def restore_database(command: list[str], backup: Path) -> None:
    # The GeoNode PostGIS image bootstraps its own template_postgis and the
    # immutable postgres role.  Drop only that target-side bootstrap state, so
    # the source's pg_dumpall can recreate its exact cluster objects.
    run_target_sql(
        command,
        TARGET_BOOTSTRAP_CLEANUP_SQL,
    )
    sql_backup = backup / "components" / "postgresql-all.sql.gz"
    process = subprocess.Popen(
        [*command, "exec", "-T", "db", "sh", "-lc", 'exec psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1'],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    broken_pipe = False
    try:
        with gzip.open(sql_backup, "rb") as source:
            for line in source:
                # PostgreSQL always creates this bootstrap superuser before a
                # dump can run; its later ALTER ROLE statements remain safe.
                if line.strip() == b"CREATE ROLE postgres;":
                    continue
                process.stdin.write(line)
    except BrokenPipeError:
        broken_pipe = True
    finally:
        try:
            process.stdin.close()
        except BrokenPipeError:
            broken_pipe = True
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    if process.wait() != 0 or broken_pipe:
        raise RuntimeError(f"PostGIS restore failed: {stderr.strip()[:300] or 'psql failed'}")


def wait_for_database_target(command: list[str], *, timeout: int = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = run([*command, "ps", "--format", "json"], capture_output=True)
        if geonode_stack.parse_compose_ps(status.stdout).get("db") == "healthy":
            return
        time.sleep(2)
    raise RuntimeError("Timed out waiting for isolated restore database")


def cleanup_target(command: list[str], target: str) -> None:
    subprocess.run([*command, "down", "--remove-orphans"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for logical_name in (*VOLUME_COMPONENTS, *RESTORE_ONLY_VOLUMES):
        subprocess.run(["docker", "volume", "rm", restore_volume_name(target, logical_name)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def verify_target(command: list[str], ports: dict[str, int], runtime_root: Path) -> None:
    run([*command, "--profile", "tools", "run", "--rm", "nrw-etl", "python", "scripts/verify_nrw_database.py"])
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "verify_project_e2e.py"),
            "--frontend-url", f"http://localhost:{ports['frontend']}",
            "--geonode-url", f"http://localhost:{ports['geonode']}",
            "--runtime-root", str(runtime_root),
        ]
    )


def create_source_probe(frontend_url: str, request_id: str) -> None:
    """Create a run-owned source proposal so the backup proves persistence, not just schema."""
    try:
        from verify_project_e2e import create_or_reconcile
    except ModuleNotFoundError:
        from scripts.verify_project_e2e import create_or_reconcile
    create_or_reconcile(
        requests.Session(),
        f"{frontend_url.rstrip('/')}/geoserver/ows",
        f"S19 restore persistence probe {request_id}",
        request_id,
    )


def cleanup_source_probe(frontend_url: str, request_id: str) -> None:
    try:
        from verify_project_e2e import cleanup_owned
    except ModuleNotFoundError:
        from scripts.verify_project_e2e import cleanup_owned
    try:
        cleanup_owned(requests.Session(), f"{frontend_url.rstrip('/')}/geoserver/ows", request_id)
    except Exception as error:
        raise RuntimeError(f"Failed to clean run-owned S19 source probe {request_id}") from error


def assert_restored_probe(frontend_url: str, request_id: str) -> None:
    try:
        from verify_project_e2e import find_owned_features
    except ModuleNotFoundError:
        from scripts.verify_project_e2e import find_owned_features
    features = find_owned_features(requests.Session(), f"{frontend_url.rstrip('/')}/geoserver/ows", request_id)
    if len(features) != 1:
        raise RuntimeError("Run-owned source proposal did not survive isolated restore")


def restore_backup(
    backup: Path,
    *,
    target: str,
    verify: bool,
    cleanup: bool,
    expected_request_id: str | None = None,
) -> dict[str, int]:
    """Restore a verified archive into a never-before-used, isolated Compose target."""
    backup = backup.resolve()
    load_and_verify_manifest(backup)
    source_volumes = configured_volume_names()
    validate_restore_target(target, source_volumes)
    workspace = backup / f"restore-{target}"
    workspace.mkdir(mode=0o700)
    ports: dict[str, int] = {}
    for name in ("db", "geonode", "https", "geoserver", "frontend"):
        port = available_port()
        while port in ports.values():
            port = available_port()
        ports[name] = port
    safe_extract(backup / "components" / "runtime-current.tar.gz", workspace)
    runtime = workspace / "runtime"
    env_file = workspace / ".env"
    write_target_env(backup / "components" / "config" / "geonode.env", env_file, target=target, ports=ports)
    override = workspace / "compose-isolated.yml"
    write_isolated_override(override, runtime, env_file, ports)
    command = compose_command(env_file=env_file, project_name=target, extra_files=(override,))
    completed = False
    try:
        for logical_name, archive_name in VOLUME_COMPONENTS.items():
            restore_volume(restore_volume_name(target, logical_name), backup, archive_name)
        run([*command, "up", "-d", "db"])
        wait_for_database_target(command)
        restore_database(command, backup)
        run([*command, "up", "-d"])
        frontend_url = f"http://localhost:{ports['frontend']}"
        geonode_url = f"http://localhost:{ports['geonode']}"
        wait_for_target(command, frontend_url, geonode_url)
        assert_restored_service_environment(command, ports)
        if expected_request_id:
            assert_restored_probe(frontend_url, expected_request_id)
        if verify:
            verify_target(command, ports, runtime)
        completed = True
        return ports
    finally:
        if cleanup:
            cleanup_target(command, target)
            shutil.rmtree(workspace, ignore_errors=True)
        elif not completed:
            # A failed restore remains inspectable, but its resources remain visibly isolated.
            print(f"Isolated restore target retained for inspection: {target}", file=sys.stderr)


def run_drill(output_root: Path, target: str, *, source_frontend_url: str = "http://localhost:8081") -> tuple[Path, dict[str, int]]:
    """Run a complete drill with cleanup ownership established before source mutation."""
    request_id = str(uuid4())
    try:
        create_source_probe(source_frontend_url, request_id)
        backup = create_backup(output_root)
        ports = restore_backup(
            backup,
            target=target,
            verify=True,
            cleanup=True,
            expected_request_id=request_id,
        )
        return backup, ports
    finally:
        cleanup_source_probe(source_frontend_url, request_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Restricted GeoNode backup and isolated restore")
    commands = parser.add_subparsers(dest="command", required=True)
    backup_parser = commands.add_parser("backup", help="create a restricted backup outside Git")
    backup_parser.add_argument("--output-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    restore_parser = commands.add_parser("restore", help="restore only into a fresh nrw-restore-* target")
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument("--target", required=True)
    restore_parser.add_argument("--verify", action="store_true")
    drill_parser = commands.add_parser("drill", help="backup, restore into isolation, verify, and remove only drill resources")
    drill_parser.add_argument("--output-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    drill_parser.add_argument("--target", default=f"{RESTORE_PROJECT_PREFIX}{uuid4().hex[:12]}")
    args = parser.parse_args()
    if args.command == "backup":
        print(create_backup(args.output_root))
    elif args.command == "restore":
        ports = restore_backup(args.backup, target=args.target, verify=args.verify, cleanup=False)
        print(json.dumps(ports, sort_keys=True))
    else:
        backup, ports = run_drill(args.output_root, args.target)
        print(json.dumps({"backup": str(backup), "ports": ports}, sort_keys=True))


if __name__ == "__main__":
    main()
