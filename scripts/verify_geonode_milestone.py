from __future__ import annotations

import subprocess

import requests

from geonode_stack import assert_core_services, compose_command, stack_status, start_stack
from publish_geonode_seed import (
    ENV_PATH,
    read_geonode_credentials,
    verify_published_seed,
)


def restart_without_volume_removal() -> None:
    """Recreate containers while preserving all named Docker volumes."""
    subprocess.run(compose_command("down"), check=True)
    start_stack()


def verify_seed() -> int:
    """Verify the exact GeoNode catalogue entry and public WFS layer."""
    catalogue_session = requests.Session()
    catalogue_session.auth = read_geonode_credentials(ENV_PATH)
    return verify_published_seed(catalogue_session, requests.Session())


def main() -> None:
    baseline_count = verify_seed()
    restart_without_volume_removal()
    assert_core_services(stack_status())
    persisted_count = verify_seed()
    if persisted_count != baseline_count:
        raise RuntimeError(
            "GeoNode seed count changed across restart: "
            f"before={baseline_count}, after={persisted_count}"
        )
    print(f"GeoNode milestone verified: catalog and {persisted_count} NRW districts persisted")


if __name__ == "__main__":
    main()
