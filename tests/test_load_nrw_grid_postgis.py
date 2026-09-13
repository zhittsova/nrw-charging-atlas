from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
MODULE_PATH = ROOT / "scripts" / "load_nrw_grid_postgis.py"
SPEC = importlib.util.spec_from_file_location("load_nrw_grid_postgis", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class GridPreparationTest(unittest.TestCase):
    def test_reads_power_lines_substations_and_transformers(self) -> None:
        document = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "way/1",
                    "properties": {"power": "line", "voltage": "380000", "name": "Line"},
                    "geometry": {"type": "LineString", "coordinates": [[7, 51], [7.1, 51.1]]},
                },
                {
                    "type": "Feature",
                    "properties": {"@id": "node/2", "power": "substation", "voltage": "110000"},
                    "geometry": {"type": "Point", "coordinates": [7.2, 51.2]},
                },
                {
                    "type": "Feature",
                    "id": "way/3",
                    "properties": {"power": "transformer"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[7.3, 51.3], [7.4, 51.3], [7.4, 51.4], [7.3, 51.3]]],
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grid.geojson"
            path.write_text(json.dumps(document), encoding="utf-8")

            rows = module.read_grid_geojson(path)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["source_id"], "way/1")
        self.assertEqual(rows[0]["asset_type"], "line")
        self.assertEqual(rows[1]["source_id"], "node/2")
        self.assertEqual(rows[2]["asset_type"], "transformer")

    def test_rejects_duplicate_ids_and_unexpected_power_types(self) -> None:
        duplicate = [
            {"type": "Feature", "id": "way/1", "properties": {"power": "line"},
             "geometry": {"type": "LineString", "coordinates": [[7, 51], [8, 52]]}},
            {"type": "Feature", "id": "way/1", "properties": {"power": "generator"},
             "geometry": {"type": "Point", "coordinates": [7, 51]}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grid.geojson"
            path.write_text(json.dumps({"type": "FeatureCollection", "features": duplicate}), encoding="utf-8")

            rows = module.read_grid_geojson(path)

        self.assertEqual(len(rows), 1)

    def test_import_is_snapshot_based_and_refreshes_analytics(self) -> None:
        rows = [{
            "source_id": "way/1",
            "asset_type": "line",
            "voltage": "380000",
            "name": "Line",
            "geom_json": '{"type":"LineString","coordinates":[[7,51],[8,52]]}',
        }]

        sql = module.build_import_script(rows)

        self.assertIn("TRUNCATE raw.grid_infrastructure", sql)
        self.assertIn("ST_GeomFromGeoJSON", sql)
        self.assertIn("COMMIT;", sql)

    def test_osmium_filter_selects_only_required_grid_assets(self) -> None:
        command = module.osmium_filter_command(Path("source.pbf"), Path("filtered.pbf"))

        self.assertIn("w/power=line,cable,minor_line", command)
        self.assertIn("nwr/power=substation,transformer", command)


if __name__ == "__main__":
    unittest.main()
