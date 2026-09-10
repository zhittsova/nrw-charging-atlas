from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import geonode_stack
    from geonode_stack import ROOT, compose_command
except ModuleNotFoundError:
    from scripts import geonode_stack
    from scripts.geonode_stack import ROOT, compose_command


POPULATION_SNAPSHOT = ROOT / "data" / "raw" / "eurostat_population_nrw.json"
CONTAINER_POPULATION_SNAPSHOT = "/app/data/raw/eurostat_population_nrw.json"


def run_etl(script: str, *args: str) -> None:
    subprocess.run(
        compose_command(
            "--profile",
            "tools",
            "run",
            "--rm",
            "nrw-etl",
            "python",
            script,
            *args,
        ),
        check=True,
    )


def fetch_data(*, refresh: bool = False) -> None:
    """Download every public source required by the production data mart."""
    refresh_args = ("--refresh",) if refresh else ()
    run_etl("scripts/fetch_real_data.py", *refresh_args)
    run_etl("scripts/fetch_nrw_infrastructure.py", "--allow-large", *refresh_args)


def seed_database(*, population_snapshot: Path | None = POPULATION_SNAPSHOT) -> None:
    """Validate all raw sources, then publish exactly one atomic refresh."""
    run_etl("scripts/initialize_nrw_database.py")
    if population_snapshot is not None and population_snapshot.is_file():
        run_etl(
            "scripts/refresh_nrw_database.py",
            "--population-snapshot",
            CONTAINER_POPULATION_SNAPSHOT,
        )
    else:
        run_etl("scripts/refresh_nrw_database.py")
    run_etl("scripts/initialize_nrw_database.py", "--grant-only")


def provision_layers() -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.provision_geoserver_layers",
            "--database-name",
            geonode_stack.project_database_name(),
            "--sync-geonode",
        ],
        check=True,
    )


def verify_project() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_project_e2e.py")],
        check=True,
    )


def start() -> None:
    geonode_stack.provision_upstream_checkout()
    geonode_stack.start_stack()


def bootstrap(*, download: bool = True) -> None:
    """Build the local platform, seed its data mart, and publish its layers."""
    start()
    if download:
        fetch_data()
    seed_database()
    provision_layers()
    verify_project()


def print_endpoints() -> None:
    print("GeoNode:  http://localhost:8000")
    print("GeoServer: http://localhost:8080/geoserver")
    print("Scenario dashboard: http://localhost:8081")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the complete local NRW GeoNode project")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = commands.add_parser(
        "bootstrap",
        help="start containers, download data, rebuild PostGIS, and publish layers",
    )
    bootstrap_parser.add_argument(
        "--skip-download",
        action="store_true",
        help="reuse files already present in data/raw",
    )
    commands.add_parser("start", help="start the existing local stack")
    commands.add_parser(
        "rebuild",
        help="rebuild local frontend and ETL images without cache, then recreate containers",
    )
    fetch_parser = commands.add_parser("fetch", help="reuse validated public source datasets")
    fetch_parser.add_argument("--refresh", action="store_true", help="explicitly replace validated cached sources")
    commands.add_parser("seed", help="rebuild the project database from source snapshots")
    commands.add_parser("publish", help="provision GeoServer layers and synchronize GeoNode")
    commands.add_parser("verify", help="test WFS-T, metric recalculation, cleanup, and catalog publication")
    commands.add_parser("stop", help="stop containers without deleting volumes")
    commands.add_parser("status", help="show container health")
    args = parser.parse_args()

    if args.command == "bootstrap":
        bootstrap(download=not args.skip_download)
        print_endpoints()
    elif args.command == "start":
        start()
        print_endpoints()
    elif args.command == "rebuild":
        geonode_stack.rebuild_stack()
        print_endpoints()
    elif args.command == "fetch":
        fetch_data(refresh=args.refresh)
    elif args.command == "seed":
        seed_database()
    elif args.command == "publish":
        provision_layers()
    elif args.command == "verify":
        verify_project()
    elif args.command == "stop":
        geonode_stack.stop_stack()
    elif args.command == "status":
        states = geonode_stack.stack_status()
        for service, state in states.items():
            print(f"{service}: {state}")
        geonode_stack.assert_core_services(states)


if __name__ == "__main__":
    main()
