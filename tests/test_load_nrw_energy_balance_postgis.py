from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests" / "fixtures"))

import energy_workbook_fixture as fixture  # noqa: E402
import load_nrw_energy_balance_postgis as loader  # noqa: E402


class EnergyWorkbookPreparationTest(unittest.TestCase):
    def test_uses_latest_common_year_and_excludes_storage_and_conventional_energy(self) -> None:
        consumption, renewables, reporting_year = loader.prepare_energy_snapshots(
            fixture.consumption_frame(),
            fixture.renewable_stock_frame(),
            fixture.renewable_growth_frame(),
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
        consumption = fixture.consumption_frame()
        duplicate = pd.concat([consumption, consumption.iloc[[1]]], ignore_index=True)

        with self.assertRaisesRegex(ValueError, "duplicate"):
            loader.prepare_energy_snapshots(
                duplicate,
                fixture.renewable_stock_frame(),
                fixture.renewable_growth_frame(),
                {"teststadt": "DEA11"},
            )

    def test_rejects_unmapped_districts(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unmapped NRW district"):
            loader.prepare_energy_snapshots(
                fixture.consumption_frame(),
                fixture.renewable_stock_frame(),
                fixture.renewable_growth_frame(),
                {},
            )

    def test_reads_labelled_synthetic_workbook_fixture(self) -> None:
        with patch.object(loader.pd, "read_excel", return_value=fixture.workbook_sheets()) as read_excel:
            consumption, renewables, reporting_year = loader.read_energy_workbook(
                Path("synthetic-energy-workbook.xlsx"), {"teststadt": "DEA11"}
            )

        self.assertEqual(fixture.LABEL, "synthetic parser fixture; not NRW source data")
        self.assertEqual(reporting_year, 2024)
        self.assertEqual(len(consumption), 2)
        self.assertEqual(len(renewables), 4)
        expected_sheets = [
            loader.CONSUMPTION_SHEET,
            loader.RENEWABLE_STOCK_SHEET,
            loader.RENEWABLE_GROWTH_SHEET,
        ]
        read_excel.assert_called_once_with(
            Path("synthetic-energy-workbook.xlsx"),
            sheet_name=expected_sheets,
            dtype={"AGS": str},
        )


class EnergyImportScriptTest(unittest.TestCase):
    def test_snapshot_sync_is_transactional_and_refreshes_analytics(self) -> None:
        consumption, renewables, reporting_year = loader.prepare_energy_snapshots(
            fixture.consumption_frame(),
            fixture.renewable_stock_frame(),
            fixture.renewable_growth_frame(),
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
