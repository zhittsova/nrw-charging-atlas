from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

import load_nrw_infrastructure_postgis as loader  # noqa: E402


class RenewableImportTest(unittest.TestCase):
    def test_preserves_optional_renewable_name_and_operator(self) -> None:
        renewable = {
            "source_id": "Windenergie|unit-1",
            "name": "Wind turbine 1",
            "operator": "Example Wind GmbH",
            "asset_type": "Windenergie",
            "technology": "Windenergie",
            "capacity_mw": 4.2,
            "status": "In Betrieb",
            "geom_json": '{"type":"Point","coordinates":[7.0,51.0]}',
        }

        sql = loader.build_import_script([], [], [renewable])

        self.assertIn("source_id, name, operator, asset_type, technology", sql)
        self.assertIn("Example Wind GmbH", sql)


class TrafficImportTest(unittest.TestCase):
    def test_the_counting_station_type_reaches_the_import(self) -> None:
        road = {
            "source_id": "abs|zst|0|100",
            "road_class": "B",
            "name": "B7",
            "road_number": 7,
            "traffic_total": 12000,
            "traffic_light": 11400,
            "traffic_heavy": 600,
            "counting_station_type": "automatische Dauerzählstelle",
            "source": "Straßen.NRW Verkehrswerte",
            "geom_json": '{"type":"LineString","coordinates":[[7.0,51.0],[7.1,51.0]]}',
        }

        sql = loader.build_import_script([], [road], [])

        self.assertIn("counting_station_type", sql)
        self.assertIn("automatische Dauerzählstelle", sql)


if __name__ == "__main__":
    unittest.main()
