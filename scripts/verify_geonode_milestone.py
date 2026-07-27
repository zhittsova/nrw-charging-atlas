from __future__ import annotations

import subprocess

import requests

from geonode_stack import assert_core_services, compose_command, stack_status, start_stack
from publish_geonode_seed import (
    ENV_PATH,
    GEONODE_URL,
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
    verify_seed()
    restart_without_volume_removal()
    assert_core_services(stack_status())
    verify_seed()
    print("GeoNode milestone verified: catalog and 53 NRW districts persisted")


if __name__ == "__main__":
    main()
