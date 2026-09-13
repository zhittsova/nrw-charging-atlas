from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from publish_geonode_seed import (  # noqa: E402
    ENV_PATH,
    GEONODE_URL,
    GEOSERVER_URL,
    find_resource,
    read_geonode_credentials,
    verify_wfs,
)

import requests  # noqa: E402


@unittest.skipUnless(
    os.getenv("GEONODE_INTEGRATION") == "1",
    "set GEONODE_INTEGRATION=1 to test the running Docker stack",
)
class GeoNodeFoundationIntegrationTest(unittest.TestCase):
    def test_geonode_is_reachable(self) -> None:
        with urlopen("http://localhost:8000/", timeout=10) as response:
            self.assertLess(response.status, 400)

    def test_geoserver_wms_capabilities_are_reachable(self) -> None:
        url = (
            "http://localhost:8080/geoserver/ows"
            "?service=WMS&version=1.3.0&request=GetCapabilities"
        )
        with urlopen(url, timeout=10) as response:
            body = response.read()
        self.assertIn(b"WMS_Capabilities", body)

    def test_nrw_seed_is_catalogued_and_publicly_readable_through_wfs(self) -> None:
        """Catches a published layer that is missing from GeoNode or needs administrator access."""
        catalog_session = requests.Session()
        catalog_session.auth = read_geonode_credentials(ENV_PATH)

        self.assertIsNotNone(find_resource(catalog_session, GEONODE_URL))
        self.assertEqual(verify_wfs(requests.Session(), GEOSERVER_URL), 53)


if __name__ == "__main__":
    unittest.main()
