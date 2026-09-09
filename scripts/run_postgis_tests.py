"""Run the required SQL integration tests against a disposable PostGIS container.

The project database is never a test target. This command creates a uniquely
named container, database, and temporary volume, runs only the PostGIS suites,
and removes every owned resource even when pytest fails.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_PREFIX = "nrw_test_"
DEFAULT_POSTGIS_IMAGE = "geonode/postgis:15-3.5-latest"
INTEGRATION_SUITES = (
    "tests/integration/test_nrw_scenario_database.py",
    "tests/integration/test_nrw_scenario_analytics.py",
    "tests/integration/test_nrw_spatial_semantics.py",
    "tests/integration/test_nrw_schema_migration.py",
    "tests/integration/test_nrw_missing_data_semantics.py",
    "tests/integration/test_nrw_verification_modules.py",
    "tests/integration/test_nrw_score_model.py",
)


def test_database_name(run_id: str) -> str:
    if not run_id or any(character not in "0123456789abcdef" for character in run_id):
        raise ValueError("PostGIS test run identifier must be lowercase hexadecimal")
    return f"{TEST_DATABASE_PREFIX}{run_id}"


def test_volume_name(run_id: str) -> str:
    return f"nrw-postgis-test-data-{run_id}"


def disposable_endpoint(url: str) -> str:
    """Return the exact loopback endpoint used by a disposable test target."""
    parsed = urlsplit(url)
    if parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise ValueError("SCENARIO_TEST_DATABASE_URL must use an explicit 127.0.0.1 endpoint")
    return f"{parsed.hostname}:{parsed.port}"


def require_disposable_database_url(
    url: str, *, run_id: str | None, endpoint: str | None
) -> str:
    """Reject any URL that could reset a project or otherwise unintended database."""
    parsed = urlsplit(url)
    database_name = unquote(parsed.path.lstrip("/"))
    if parsed.scheme not in {"postgres", "postgresql"} or not database_name:
        raise ValueError("SCENARIO_TEST_DATABASE_URL must be a PostgreSQL database URL")
    if not run_id:
        raise ValueError("SCENARIO_TEST_RUN_ID must identify the current PostGIS test run")
    if database_name != test_database_name(run_id):
        raise ValueError(
            "Refusing non-disposable SCENARIO_TEST_DATABASE_URL; "
            "database name must match the current test run"
        )
    if not endpoint or disposable_endpoint(url) != endpoint:
        raise ValueError("SCENARIO_TEST_DATABASE_URL does not match the current disposable endpoint")
    return url


def psql_connection(
    url: str,
    *,
    database_name: str | None = None,
    run_id: str | None,
    endpoint: str | None,
) -> tuple[list[str], dict[str, str]]:
    """Return password-safe psql arguments for a previously validated test URL."""
    parsed = urlsplit(require_disposable_database_url(url, run_id=run_id, endpoint=endpoint))
    target_database = database_name or unquote(parsed.path.lstrip("/"))
    if not parsed.hostname or not parsed.username or parsed.password is None:
        raise ValueError("SCENARIO_TEST_DATABASE_URL must include host, user, and password")
    command = [
        "psql",
        "--host",
        parsed.hostname,
        "--port",
        str(parsed.port or 5432),
        "--username",
        unquote(parsed.username),
        "--dbname",
        target_database,
        "-v",
        "ON_ERROR_STOP=1",
        "-qAt",
    ]
    return command, os.environ | {"PGPASSWORD": unquote(parsed.password)}


def available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def run_command(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False, **kwargs)


def require_command(name: str) -> None:
    if not shutil.which(name):
        raise RuntimeError(f"Required command not found: {name}")


def wait_for_database(database_url: str, *, run_id: str, endpoint: str) -> None:
    command, environment = psql_connection(
        database_url, database_name="postgres", run_id=run_id, endpoint=endpoint
    )
    for _ in range(60):
        result = run_command([*command, "-c", "SELECT 1"], env=environment)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("Disposable PostGIS container did not become ready for TCP connections")


def create_database(container_name: str, database_name: str) -> None:
    result = run_command(
        [
            "docker",
            "exec",
            container_name,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f"CREATE DATABASE {database_name}",
        ]
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "CREATE DATABASE failed").strip())


def start_disposable_postgis(*, image: str, run_id: str) -> tuple[str, str, str]:
    database_name = test_database_name(run_id)
    container_name = f"nrw-postgis-test-{run_id}"
    volume_name = test_volume_name(run_id)
    password = f"test{run_id}{secrets.token_hex(12)}"
    port = available_loopback_port()
    result = run_command(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container_name,
            "--label",
            "nrw.disposable-test=true",
            "--env",
            "POSTGRES_USER=postgres",
            "--env",
            f"POSTGRES_PASSWORD={password}",
            "--mount",
            f"type=volume,source={volume_name},target=/var/lib/postgresql/data",
            "--publish",
            f"127.0.0.1:{port}:5432",
            image,
        ]
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "docker run failed").strip())
    return (
        container_name,
        f"postgresql://postgres:{password}@127.0.0.1:{port}/{database_name}",
        volume_name,
    )


def run_postgis_tests(*, image: str) -> int:
    require_command("docker")
    require_command("psql")
    run_id = secrets.token_hex(8)
    # These names are ours before Docker is asked to create either resource, so
    # partial docker-run failures and KeyboardInterrupt still have cleanup ownership.
    container_name = f"nrw-postgis-test-{run_id}"
    volume_name = test_volume_name(run_id)
    primary_failed = False
    try:
        started_container, database_url, started_volume = start_disposable_postgis(
            image=image, run_id=run_id
        )
        if (started_container, started_volume) != (container_name, volume_name):
            raise RuntimeError("Disposable PostGIS startup returned unexpected resource names")
        endpoint = disposable_endpoint(database_url)
        wait_for_database(database_url, run_id=run_id, endpoint=endpoint)
        require_disposable_database_url(database_url, run_id=run_id, endpoint=endpoint)
        create_database(container_name, test_database_name(run_id))
        environment = os.environ | {
            "SCENARIO_TEST_DATABASE_URL": database_url,
            "SCENARIO_TEST_RUN_ID": run_id,
            "SCENARIO_TEST_ENDPOINT": endpoint,
        }
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *INTEGRATION_SUITES],
            cwd=ROOT,
            env=environment,
            check=False,
        )
        if result.returncode:
            primary_failed = True
            logs = run_command(["docker", "logs", container_name])
            diagnostics = (logs.stdout or logs.stderr).strip()
            if diagnostics:
                print("Disposable PostGIS logs before cleanup:", file=sys.stderr)
                print(diagnostics, file=sys.stderr)
        return result.returncode
    except BaseException:
        primary_failed = True
        raise
    finally:
        cleanup_errors: list[str] = []
        cleanup = run_command(["docker", "rm", "--force", container_name])
        if cleanup.returncode and "No such container" not in (cleanup.stderr or cleanup.stdout):
            cleanup_errors.append((cleanup.stderr or cleanup.stdout or "docker cleanup failed").strip())
        cleanup = run_command(["docker", "volume", "rm", volume_name])
        if cleanup.returncode and "No such volume" not in (cleanup.stderr or cleanup.stdout):
            cleanup_errors.append((cleanup.stderr or cleanup.stdout or "volume cleanup failed").strip())
        if cleanup_errors:
            message = "; ".join(cleanup_errors)
            if primary_failed:
                print(f"Disposable PostGIS cleanup warning: {message}", file=sys.stderr)
            else:
                raise RuntimeError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run required SQL integration tests in a disposable PostGIS container"
    )
    parser.add_argument(
        "--image",
        default=os.environ.get("POSTGIS_TEST_IMAGE", DEFAULT_POSTGIS_IMAGE),
        help="PostGIS image for the disposable test container",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(run_postgis_tests(image=args.image))


if __name__ == "__main__":
    main()
