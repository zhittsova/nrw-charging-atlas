from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
MODULE_PATH = ROOT / "scripts" / "load_nrw_road_network_postgis.py"
SPEC = importlib.util.spec_from_file_location("load_nrw_road_network_postgis", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class RoadNetworkPreparationTest(unittest.TestCase):
    def test_osmium_filter_selects_motorway_ways(self) -> None:
        command = module.osmium_filter_command(Path("source.pbf"), Path("filtered.pbf"))

        self.assertIn("w/highway=motorway", command)
        self.assertNotIn("n/highway=motorway", command)

    def test_reads_only_unique_motorway_lines(self) -> None:
        document = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "way/1",
                    "properties": {"highway": "motorway", "ref": "A 3", "name": "A3"},
                    "geometry": {"type": "LineString", "coordinates": [[7, 51], [7.1, 51.1]]},
                },
                {
                    "type": "Feature",
                    "id": "way/2",
                    "properties": {"highway": "primary", "ref": "B 1"},
                    "geometry": {"type": "LineString", "coordinates": [[7, 51], [7.2, 51.2]]},
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roads.geojson"
            path.write_text(json.dumps(document), encoding="utf-8")

            rows = module.read_road_geojson(path)

        self.assertEqual(
            rows,
            [{
                "source_id": "way/1",
                "highway": "motorway",
                "ref": "A 3",
                "name": "A3",
                "geom_json": '{"type":"LineString","coordinates":[[7,51],[7.1,51.1]]}',
            }],
        )

    def test_reads_real_osmium_type_id_feature_shape(self) -> None:
        # `osmium export --add-unique-id type_id` uses compact feature IDs
        # (for example w123), rather than an @id property.
        document = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "id": "w123",
                "properties": {"highway": "motorway", "ref": "A 3"},
                "geometry": {"type": "LineString", "coordinates": [[7, 51], [7.1, 51.1]]},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roads.geojson"
            path.write_text(json.dumps(document), encoding="utf-8")
            rows = module.read_road_geojson(path)

        self.assertEqual(rows[0]["source_id"], "w123")

    @patch("load_nrw_road_network_postgis.subprocess.run")
    def test_extraction_requests_type_scoped_ids_for_real_osmium_features(self, run) -> None:
        module.extract_road_geojson(Path("source.pbf"), Path("roads.geojson"))

        export_command = run.call_args_list[1].args[0]
        self.assertEqual(export_command[:2], ["osmium", "export"])
        identity_option = export_command.index("--add-unique-id")
        self.assertEqual(export_command[identity_option + 1], "type_id")

    def test_rejects_duplicate_ids_and_non_line_motorways(self) -> None:
        duplicate = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "way/1",
                    "properties": {"highway": "motorway"},
                    "geometry": {"type": "LineString", "coordinates": [[7, 51], [8, 52]]},
                },
                {
                    "type": "Feature",
                    "id": "way/1",
                    "properties": {"highway": "motorway"},
                    "geometry": {"type": "MultiLineString", "coordinates": [[[7, 51], [8, 52]]]},
                },
            ],
        }
        point = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "id": "node/1",
                "properties": {"highway": "motorway"},
                "geometry": {"type": "Point", "coordinates": [7, 51]},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roads.geojson"
            path.write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate id"):
                module.read_road_geojson(path)

            path.write_text(json.dumps(point), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line geometry"):
                module.read_road_geojson(path)

    def test_import_is_transactional_snapshot_replacement(self) -> None:
        rows = [{
            "source_id": "way/1",
            "highway": "motorway",
            "ref": "A 3",
            "name": "A3",
            "geom_json": '{"type":"LineString","coordinates":[[7,51],[8,52]]}',
        }]

        sql = module.build_import_script(rows)

        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertIn("TRUNCATE raw.osm_roads", sql)
        self.assertIn("ST_GeomFromGeoJSON", sql)
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))


if __name__ == "__main__":
    unittest.main()
