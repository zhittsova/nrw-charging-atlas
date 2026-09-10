from __future__ import annotations

import csv
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class SourceRegistryTest(unittest.TestCase):
    def test_every_consumed_general_source_has_a_registry_row(self) -> None:
        registry = {row["dataset_id"]: row for row in rows(ROOT / "catalog" / "data_sources.csv")}

        # These are the files read by the supported refresh boundary.  The
        # remaining general-catalogue rows are either authenticated or
        # explicitly contextual/manual sources.
        expected = {
            "nuts3_regions_gisco_nrw": "data/raw/nuts3_regions_gisco_2024.geojson",
            "bnetza_charging_register_nrw": "data/raw/bnetza_ladesaeulenregister.csv",
            "eurostat_population_nrw": "data/raw/eurostat_population_nrw.json",
            "opsd_conventional_power_plants_nrw": "data/raw/opsd_conventional_power_plants_de.csv",
        }
        for dataset_id, target_path in expected.items():
            with self.subTest(dataset_id=dataset_id):
                self.assertIn(dataset_id, registry)
                self.assertEqual(registry[dataset_id]["target_path"], target_path)
                self.assertTrue(registry[dataset_id]["source_url"])
                self.assertTrue(registry[dataset_id]["license_note"])

    def test_infrastructure_manifest_is_explicit_about_consumed_roles(self) -> None:
        registry = rows(ROOT / "catalog" / "nrw_infrastructure_sources.csv")
        by_id = {row["dataset_id"]: row for row in registry}
        expected = {
            "strassen_nrw_traffic_values",
            "strassen_nrw_counting_stations",
            "strassen_nrw_road_sections",
            "energieatlas_nrw_renewable_sites",
            "energieatlas_nrw_admin_electricity",
            "energieatlas_nrw_renewable_potential",
            "geofabrik_nrw_osm_power",
        }
        self.assertEqual(set(by_id), expected)
        for dataset_id, row in by_id.items():
            with self.subTest(dataset_id=dataset_id):
                self.assertTrue(row["url"].startswith(("https://", "http://")))
                self.assertTrue(row["target_path"].startswith("data/raw/"))
                self.assertTrue(row["licence"])
                self.assertGreater(int(row["max_bytes"]), 0)

    def test_registry_does_not_promote_contextual_downloads_to_scored_inputs(self) -> None:
        docs = (ROOT / "docs" / "data_sources.md").read_text(encoding="utf-8")
        catalog_ids = {row["dataset_id"] for row in rows(ROOT / "catalog" / "data_sources.csv")}
        infra_ids = {row["dataset_id"] for row in rows(ROOT / "catalog" / "nrw_infrastructure_sources.csv")}
        for dataset_id in (
            "strassen_nrw_counting_stations",
            "strassen_nrw_road_sections",
            "energieatlas_nrw_renewable_potential",
            "bast_traffic_counts_nrw",
        ):
            with self.subTest(dataset_id=dataset_id):
                self.assertIn(dataset_id, catalog_ids | infra_ids)
                self.assertIn(dataset_id, docs)
        self.assertIn("Downloaded but not scored", docs)


if __name__ == "__main__":
    unittest.main()
