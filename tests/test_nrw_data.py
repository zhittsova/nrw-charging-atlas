from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

from config_utils import read_simple_region_config  # noqa: E402


class NRWDataTest(unittest.TestCase):
    def test_region_config_is_nrw(self) -> None:
        config = read_simple_region_config(ROOT / "config" / "regions" / "nrw.yml")

        self.assertEqual(config["region_name"], "Nordrhein-Westfalen")
        self.assertEqual(config["region_abbr"], "NRW")
        self.assertEqual(config["nuts1"], "DEA")
        self.assertEqual(config["ags_prefix"], "05")
        self.assertEqual(config["projected_crs"], "EPSG:25832")

    def test_frontend_regions_are_real_nrw_nuts3(self) -> None:
        path = ROOT / "frontend" / "data" / "nrw_regions_sample.geojson"
        regions = json.loads(path.read_text(encoding="utf-8"))
        features = regions["features"]

        self.assertEqual(len(features), 53)
        self.assertTrue(all(feature["properties"]["region_abbr"] == "NRW" for feature in features))
        self.assertTrue(all(feature["properties"]["nuts_code"].startswith("DEA") for feature in features))

    def test_frontend_chargers_are_nrw_records(self) -> None:
        path = ROOT / "frontend" / "data" / "nrw_charging_stations_sample.geojson"
        chargers = json.loads(path.read_text(encoding="utf-8"))
        features = chargers["features"]

        self.assertEqual(len(features), 256)
        self.assertTrue(all(feature["properties"]["region"] == "Nordrhein-Westfalen" for feature in features))
        self.assertTrue(all(feature["properties"]["state"] == "Nordrhein-Westfalen" for feature in features))

    def test_old_synthetic_frontend_files_are_not_defaults(self) -> None:
        self.assertFalse((ROOT / "frontend" / "data" / "germany_regions_sample.geojson").exists())
        self.assertFalse((ROOT / "frontend" / "data" / "ev_charging_stations_sample.geojson").exists())


if __name__ == "__main__":
    unittest.main()
