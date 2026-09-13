from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

import load_nrw_energy_balance_postgis as loader  # noqa: E402


def consumption_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Jahr": 2023,
                "Gemeinde": "Teststadt",
                "Kreis": "Teststadt",
                "AGS": "05111000",
                "Stromverbrauch (GWh)": 90,
            },
            {
                "Jahr": 2024,
                "Gemeinde": "Teststadt",
                "Kreis": "Teststadt",
                "AGS": "05111000",
                "Stromverbrauch (GWh)": 100,
                "Stromverbrauch Industrie (GWh)": 40,
                "Stromverbrauch GHD (GWh)": 30,
                "Stromverbrauch Haushalte (GWh)": 30,
            },
        ]
    )


def renewable_stock_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Jahr": 2024,
                "Gemeinde": "Teststadt",
                "Kreis": "Teststadt",
                "AGS": "05111000",
                "Biomasse: Leistung (MW)": 1,
                "Biomasse: Stromertrag (MWh)": 10,
                "PV: Bauliche Anlagen Leistung (MW)": 2,
                "PV: Bauliche Anlagen Stromertrag (MWh)": 20,
                "Speicher: Batteriespeicher Leistung (MW)": 9,
                "Wind: Leistung (MW)": 5,
            },
            {
                "Jahr": 2025,
                "Gemeinde": "Teststadt",
                "Kreis": "Teststadt",
                "AGS": "05111000",
                "Biomasse: Leistung (MW)": 2,
                "Biomasse: Stromertrag (MWh)": 15,
                "Wind: Leistung (MW)": 6,
            },
        ]
    )


def renewable_growth_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Jahr": 2022,
                "Gemeinde": "Teststadt",
                "Kreis": "Teststadt",
                "AGS": "05111000",
                "Wind: Leistung (MW)": 1,
            },
            {
                "Jahr": 2023,
                "Gemeinde": "Teststadt",
                "Kreis": "Teststadt",
                "AGS": "05111000",
                "PV: Bauliche Anlagen Leistung (MW)": 2,
            },
            {
                "Jahr": 2024,
                "Gemeinde": "Teststadt",
                "Kreis": "Teststadt",
                "AGS": "05111000",
                "Biomasse: Leistung (MW)": 3,
                "Speicher: Batteriespeicher Leistung (MW)": 100,
            },
        ]
    )


class EnergyWorkbookPreparationTest(unittest.TestCase):
    def test_uses_latest_common_year_and_excludes_storage_and_conventional_energy(self) -> None:
        consumption, renewables, reporting_year = loader.prepare_energy_snapshots(
            consumption_frame(),
            renewable_stock_frame(),
            renewable_growth_frame(),
            {"teststadt": "DEA11"},
        )

        self.assertEqual(reporting_year, 2024)
        self.assertEqual(len(consumption), 2)
        row = next(item for item in renewables if item["year"] == 2024)
        self.assertEqual(row["nuts_code"], "DEA11")
        self.assertEqual(row["published_generation_mwh"], 30)
        self.assertEqual(row["wind_capacity_mw"], 5)
        self.assertEqual(row["renewable_capacity_mw"], 8)
        self.assertEqual(row["renewable_net_addition_mw"], 3)

    def test_rejects_duplicate_year_and_municipality_keys(self) -> None:
        duplicate = pd.concat([consumption_frame(), consumption_frame().iloc[[1]]], ignore_index=True)

        with self.assertRaisesRegex(ValueError, "duplicate"):
            loader.prepare_energy_snapshots(
                duplicate,
                renewable_stock_frame(),
                renewable_growth_frame(),
                {"teststadt": "DEA11"},
            )

    def test_rejects_unmapped_districts(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unmapped NRW district"):
            loader.prepare_energy_snapshots(
                consumption_frame(),
                renewable_stock_frame(),
                renewable_growth_frame(),
                {},
            )

    def test_reads_real_energieatlas_snapshot_for_all_nrw_districts(self) -> None:
        admin_rows = loader.read_admin_regions(
            ROOT / "frontend" / "data" / "nrw_regions_sample.geojson"
        )

        consumption, renewables, reporting_year = loader.read_energy_workbook(
            loader.ENERGY_WORKBOOK,
            loader.build_district_lookup(admin_rows),
        )

        self.assertEqual(reporting_year, 2024)
        self.assertEqual(len({row["nuts_code"] for row in consumption if row["year"] == 2024}), 53)
        self.assertEqual(len({row["nuts_code"] for row in renewables if row["year"] == 2024}), 53)


class EnergyImportScriptTest(unittest.TestCase):
    def test_snapshot_sync_is_transactional_and_refreshes_analytics(self) -> None:
        consumption, renewables, reporting_year = loader.prepare_energy_snapshots(
            consumption_frame(),
            renewable_stock_frame(),
            renewable_growth_frame(),
            {"teststadt": "DEA11"},
        )

        sql = loader.build_import_script(consumption, renewables, reporting_year)

        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertIn("DELETE FROM raw.energy_consumption_municipal", sql)
        self.assertIn("DELETE FROM raw.renewable_balance_municipal", sql)
        self.assertIn("REFRESH MATERIALIZED VIEW analytics.nrw_local_energy_balance", sql)
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))


if __name__ == "__main__":
    unittest.main()
