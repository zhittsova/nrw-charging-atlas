from __future__ import annotations

import os
import unittest
from urllib.request import urlopen


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


if __name__ == "__main__":
    unittest.main()
