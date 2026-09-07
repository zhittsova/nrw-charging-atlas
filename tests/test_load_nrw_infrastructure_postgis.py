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


if __name__ == "__main__":
    unittest.main()
