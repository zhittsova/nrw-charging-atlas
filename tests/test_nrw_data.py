from __future__ import annotations

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

    def test_frontend_uses_generated_runtime_artifacts(self) -> None:
        config = read_simple_region_config(ROOT / "config" / "regions" / "nrw.yml")
        self.assertEqual(config["frontend_region_file"], "data/runtime/current/nrw_regions_sample.geojson")
        self.assertEqual(config["frontend_charger_file"], "data/runtime/current/nrw_charging_stations_sample.geojson")

    def test_old_synthetic_frontend_files_are_not_defaults(self) -> None:
        self.assertFalse((ROOT / "frontend" / "data" / "germany_regions_sample.geojson").exists())
        self.assertFalse((ROOT / "frontend" / "data" / "ev_charging_stations_sample.geojson").exists())


if __name__ == "__main__":
    unittest.main()
