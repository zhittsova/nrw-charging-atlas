from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
GEONODE_DIR = ROOT / "geonode"
ENV_PATH = GEONODE_DIR / ".env"
COMPOSE_PATH = GEONODE_DIR / "docker-compose.yml"
APPLE_SILICON_COMPOSE_PATH = ROOT / "config" / "geonode" / "docker-compose.apple-silicon.yml"
UPSTREAM_PIN_PATH = ROOT / "config" / "geonode-upstream.json"

CORE_SERVICES = {
    "django",
    "celery",
    "memcached",
    "geonode",
    "geoserver",
    "data-dir-conf",
    "db",
    "redis",
}

LOCAL_ENV = {
    "SITEURL": "http://localhost:8000/",
    "NGINX_BASE_URL": "http://localhost:8000",
    "HTTP_HOST": "localhost",
    "HTTP_PORT": "8000",
    "HTTPS_HOST": "",
    "HTTPS_PORT": "8443",
    "GEOSERVER_WEB_UI_LOCATION": "http://localhost:8080/geoserver/",
    "GEOSERVER_PUBLIC_LOCATION": "http://localhost:8080/geoserver/",
    "MEMCACHED_LOCATION": "memcached:11211",
    "MEMCACHED_OPTIONS": "",
    "ALLOWED_HOSTS": "\"['django', 'localhost', '127.0.0.1']\"",
    "LETSENCRYPT_MODE": "disabled",
}


def patch_geoserver_java_opts(value: str) -> str:
    value = re.sub(r"-Xms\S+", "-Xms512m", value)
    return re.sub(r"-Xmx\S+", "-Xmx1G", value)


def patch_env_text(text: str) -> str:
    lines = text.splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else ""
        if key in LOCAL_ENV:
            output.append(f"{key}={LOCAL_ENV[key]}")
            seen.add(key)
        elif key == "GEOSERVER_JAVA_OPTS":
            output.append(f"{key}={patch_geoserver_java_opts(line.split('=', 1)[1])}")
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in LOCAL_ENV.items() if key not in seen)
    return "\n".join(output) + "\n"


def assert_resolved_env(text: str) -> None:
    unresolved = sorted(
        set(re.findall(r"(?<!\$)\{[a-zA-Z_][a-zA-Z0-9_]*\}", text))
    )
    if unresolved:
        raise ValueError(f"GeoNode .env has unresolved placeholders: {', '.join(unresolved)}")


def upstream_pin() -> dict[str, str]:
    expected = json.loads(UPSTREAM_PIN_PATH.read_text(encoding="utf-8"))
    repository = expected.get("repository")
    commit = expected.get("commit")
    if not isinstance(repository, str) or not isinstance(commit, str):
        raise RuntimeError("config/geonode-upstream.json must contain repository and commit")
    return {"repository": repository, "commit": commit}


def normalize_repository_url(url: str) -> str:
    return url.removesuffix(".git").rstrip("/")


def validate_upstream_checkout() -> None:
    if not (GEONODE_DIR / ".git").exists():
        raise RuntimeError(
            "Official GeoNode checkout is missing at geonode/. "
            "Run 'python3 scripts/geonode_stack.py provision' first."
        )
    expected = upstream_pin()
    commit = subprocess.run(
        ["git", "-C", str(GEONODE_DIR), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    remote = subprocess.run(
        ["git", "-C", str(GEONODE_DIR), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if (
        commit != expected["commit"]
        or normalize_repository_url(remote)
        != normalize_repository_url(expected["repository"])
    ):
        raise RuntimeError(
            "GeoNode checkout does not match config/geonode-upstream.json"
        )
    status = subprocess.run(
        ["git", "-C", str(GEONODE_DIR), "status", "--porcelain", "--untracked-files=no"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise RuntimeError("GeoNode checkout has tracked local modifications")


def provision_upstream_checkout() -> None:
    """Clone the pinned upstream checkout once, without modifying an existing clone."""
    if GEONODE_DIR.exists():
        validate_upstream_checkout()
        return
    expected = upstream_pin()
    subprocess.run(
        ["git", "clone", "--no-checkout", expected["repository"], str(GEONODE_DIR)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(GEONODE_DIR), "checkout", "--detach", expected["commit"]],
        check=True,
        capture_output=True,
        text=True,
    )
    validate_upstream_checkout()


def compose_command(*args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(GEONODE_DIR),
        "--env-file",
        str(ENV_PATH),
        "-f",
        str(COMPOSE_PATH),
        "-f",
        str(APPLE_SILICON_COMPOSE_PATH),
        *args,
    ]


def docker_is_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def parse_compose_ps(output: str) -> dict[str, str]:
    output = output.strip()
    if not output:
        return {}
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in output.splitlines()]
    else:
        rows = parsed if isinstance(parsed, list) else [parsed]
    return {
        row["Service"]: str(row.get("Health") or row.get("State") or "unknown").lower()
        for row in rows
    }


def stack_status() -> dict[str, str]:
    result = subprocess.run(
        compose_command("ps", "--format", "json"),
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_compose_ps(result.stdout)


def assert_core_services(states: dict[str, str]) -> None:
    missing = sorted(CORE_SERVICES - states.keys())
    bad = sorted(
        name
        for name in CORE_SERVICES & states.keys()
        if states[name] not in {"healthy", "running"}
    )
    problems = missing + bad
    if problems:
        raise RuntimeError(f"GeoNode core services are not healthy: {', '.join(problems)}")


def wait_for_http(url: str, timeout: float = 300.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=5) as response:
                if 200 <= response.status < 400:
                    return
        except (HTTPError, URLError, TimeoutError, ConnectionResetError) as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def wait_for_core_services(timeout: float = 300.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: RuntimeError | None = None
    while time.monotonic() < deadline:
        try:
            assert_core_services(stack_status())
            return
        except RuntimeError as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for GeoNode core services: {last_error}")


def wait_until_healthy() -> None:
    wait_for_http("http://localhost:8000/")
    wait_for_http(
        "http://localhost:8080/geoserver/ows"
        "?service=WMS&version=1.3.0&request=GetCapabilities"
    )
    wait_for_core_services()


def initialize_environment() -> None:
    validate_upstream_checkout()
    if (
        not (GEONODE_DIR / "create-envfile.py").is_file()
        or not COMPOSE_PATH.is_file()
        or not APPLE_SILICON_COMPOSE_PATH.is_file()
    ):
        raise RuntimeError("Official GeoNode checkout is missing or incomplete")
    if not ENV_PATH.exists():
        try:
            subprocess.run(
                [
                    sys.executable,
                    "create-envfile.py",
                    "--hostname",
                    "localhost",
                    "--env_type",
                    "dev",
                    "--noinput",
                ],
                cwd=GEONODE_DIR,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            for diagnostic in (error.stdout, error.stderr):
                if diagnostic:
                    print(diagnostic, file=sys.stderr, end="" if diagnostic.endswith("\n") else "\n")
            raise
    text = patch_env_text(ENV_PATH.read_text(encoding="utf-8"))
    assert_resolved_env(text)
    ENV_PATH.write_text(text, encoding="utf-8")
    os.chmod(ENV_PATH, 0o600)
    subprocess.run(compose_command("config", "--quiet"), check=True)


def start_stack() -> None:
    if not docker_is_ready():
        raise RuntimeError("Docker Desktop is not running or Docker is unavailable")
    initialize_environment()
    subprocess.run(compose_command("up", "-d"), check=True)
    wait_until_healthy()


def stop_stack() -> None:
    subprocess.run(compose_command("stop"), check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("provision")
    subparsers.add_parser("init")
    subparsers.add_parser("start")
    subparsers.add_parser("stop")
    subparsers.add_parser("status")
    args = parser.parse_args()

    if args.command == "provision":
        provision_upstream_checkout()
        print("Pinned GeoNode 5.1.0 checkout is ready at geonode/")
    elif args.command == "init":
        initialize_environment()
        print("GeoNode local environment is ready at geonode/.env")
    elif args.command == "start":
        start_stack()
    elif args.command == "stop":
        stop_stack()
    elif args.command == "status":
        states = stack_status()
        for service, state in states.items():
            print(f"{service}: {state}")
        assert_core_services(states)


if __name__ == "__main__":
    main()
