from __future__ import annotations

import argparse
import math
import os
import re
import tempfile
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd

from config_utils import ROOT
from load_nrw_postgis import _copy_block, read_raw_seed_snapshot


ENERGY_WORKBOOK = (
    ROOT
    / "data"
    / "raw"
    / "nrw_infrastructure"
    / "renewables"
    / "NW-Strom-VWE-Aggregiert_EPSG25832_Excel.xlsx"
)
CONSUMPTION_SHEET = "Stromverbrauch"
RENEWABLE_STOCK_SHEET = "Bestand Gemeinden EE"
RENEWABLE_GROWTH_SHEET = "Nottozubau Gemeinden EE"

CONSUMPTION_COLUMNS = (
    "year",
    "municipality_name",
    "district_name",
    "nuts_code",
    "ags",
    "consumption_gwh",
    "industry_gwh",
    "commerce_services_gwh",
    "households_gwh",
    "source",
)
RENEWABLE_COLUMNS = (
    "year",
    "municipality_name",
    "district_name",
    "nuts_code",
    "ags",
    "published_generation_mwh",
    "generation_components_unknown",
    "wind_capacity_mw",
    "renewable_capacity_mw",
    "renewable_net_addition_mw",
    "source",
)

BASE_REQUIRED_COLUMNS = {"Jahr", "Gemeinde", "Kreis", "AGS"}
STORAGE_PREFIX = "Speicher:"
SOURCE_NAME = "LANUK NRW Energieatlas"


def normalize_district_name(value: object) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("a.d.", "an der")
    text = re.sub(r",?\s*kreisfreie\s+stadt$", "", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", "", text)


def build_district_lookup(admin_rows: list[dict]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in admin_rows:
        key = normalize_district_name(row["district_name"])
        nuts_code = str(row["nuts_code"])
        if not key:
            raise ValueError(f"NRW district {nuts_code} is missing a name")
        if key in lookup and lookup[key] != nuts_code:
            raise ValueError(f"Ambiguous normalized NRW district name: {row['district_name']}")
        lookup[key] = nuts_code
    return lookup


def _number(value: object, *, field: str, allow_missing: bool = True) -> float | None:
    """Return a finite reading, an explicit unknown, or raise.

    ``pd.notna`` is true for both infinities, so it cannot decide finiteness;
    a non-finite reading is rejected outright rather than being carried into an
    aggregate (contract C03).
    """
    if value is None or (isinstance(value, float) and math.isnan(value)) or (
        not isinstance(value, (str, bytes)) and pd.isna(value)
    ):
        if allow_missing:
            return None
        raise ValueError(f"{field} is missing")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite, not {value!r}")
    return result


def _required_columns(frame: pd.DataFrame, label: str, extra: set[str] | None = None) -> None:
    required = BASE_REQUIRED_COLUMNS | (extra or set())
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(missing)}")


def _validate_keys(frame: pd.DataFrame, label: str) -> None:
    if frame.empty:
        raise ValueError(f"{label} snapshot is empty")
    if frame[["Jahr", "AGS"]].isna().any(axis=None):
        raise ValueError(f"{label} contains a missing year or AGS")
    duplicate = frame.duplicated(subset=["Jahr", "AGS"], keep=False)
    if duplicate.any():
        example = frame.loc[duplicate, ["Jahr", "AGS"]].iloc[0]
        raise ValueError(f"{label} contains duplicate key ({int(example['Jahr'])}, {example['AGS']})")


def _nuts_code(district_name: object, lookup: dict[str, str]) -> str:
    key = normalize_district_name(district_name)
    try:
        return lookup[key]
    except KeyError as error:
        raise ValueError(f"Unmapped NRW district: {district_name}") from error


def _sum_columns(row: pd.Series, columns: list[str]) -> float | None:
    """Sum a technology group, keeping an absent reading distinct from zero.

    A technology column that is simply not present in the sheet is not part of
    this source's reporting, so it is skipped.  A column that is present but
    empty for this municipality is an unknown reading: the group total is then
    unavailable rather than silently smaller (contract C03).
    """
    total = 0.0
    for column in columns:
        if column not in row.index:
            continue
        value = _number(row.get(column), field=column)
        if value is None:
            return None
        total += value
    return total


def _sum_generation(row: pd.Series, columns: list[str]) -> tuple[float | None, int]:
    """Sum published yields, separating "no plant" from "yield not published".

    The source writes an explicit 0 in every ``Leistung (MW)`` cell but leaves
    ``Stromertrag (MWh)`` empty in two different situations.  Where the matching
    capacity is zero there is no installation and therefore no yield, which is a
    real zero contribution.  Where capacity is positive the yield exists but was
    not published, so the municipal total is incomplete and must not be passed
    off as a measured sum (contract C03).  Returns the total, or ``None`` when
    any component is unknown, together with the number of unknown components.
    """
    total = 0.0
    unknown = 0
    for column in columns:
        if column not in row.index:
            continue
        value = _number(row.get(column), field=column)
        if value is not None:
            total += value
            continue
        capacity_column = column.replace("Stromertrag (MWh)", "Leistung (MW)")
        capacity = (
            _number(row.get(capacity_column), field=capacity_column)
            if capacity_column in row.index
            else None
        )
        if capacity == 0:
            continue
        unknown += 1
    return (None if unknown else total), unknown


def prepare_energy_snapshots(
    consumption_frame: pd.DataFrame,
    renewable_stock_frame: pd.DataFrame,
    renewable_growth_frame: pd.DataFrame,
    district_lookup: dict[str, str],
) -> tuple[list[dict], list[dict], int]:
    _required_columns(
        consumption_frame,
        CONSUMPTION_SHEET,
        {"Stromverbrauch (GWh)"},
    )
    _required_columns(renewable_stock_frame, RENEWABLE_STOCK_SHEET)
    _required_columns(renewable_growth_frame, RENEWABLE_GROWTH_SHEET)
    _validate_keys(consumption_frame, CONSUMPTION_SHEET)
    _validate_keys(renewable_stock_frame, RENEWABLE_STOCK_SHEET)
    _validate_keys(renewable_growth_frame, RENEWABLE_GROWTH_SHEET)

    consumption_years = set(pd.to_numeric(consumption_frame["Jahr"], errors="raise").astype(int))
    stock_years = set(pd.to_numeric(renewable_stock_frame["Jahr"], errors="raise").astype(int))
    common_years = consumption_years & stock_years
    if not common_years:
        raise ValueError("Consumption and renewable stock have no common reporting year")
    reporting_year = max(common_years)

    consumption_rows: list[dict] = []
    for _, row in consumption_frame.iterrows():
        consumption = _number(
            row["Stromverbrauch (GWh)"],
            field="Stromverbrauch (GWh)",
            allow_missing=False,
        )
        if consumption is not None and consumption < 0:
            raise ValueError("Stromverbrauch (GWh) cannot be negative")
        consumption_rows.append(
            {
                "year": int(row["Jahr"]),
                "municipality_name": str(row["Gemeinde"]),
                "district_name": str(row["Kreis"]),
                "nuts_code": _nuts_code(row["Kreis"], district_lookup),
                "ags": str(row["AGS"]).zfill(8),
                "consumption_gwh": consumption,
                "industry_gwh": _number(row.get("Stromverbrauch Industrie (GWh)"), field="industry_gwh"),
                "commerce_services_gwh": _number(row.get("Stromverbrauch GHD (GWh)"), field="commerce_services_gwh"),
                "households_gwh": _number(row.get("Stromverbrauch Haushalte (GWh)"), field="households_gwh"),
                "source": SOURCE_NAME,
            }
        )

    generation_columns = [
        str(column)
        for column in renewable_stock_frame.columns
        if str(column).endswith("Stromertrag (MWh)") and not str(column).startswith(STORAGE_PREFIX)
    ]
    capacity_columns = [
        str(column)
        for column in renewable_stock_frame.columns
        if str(column).endswith("Leistung (MW)") and not str(column).startswith(STORAGE_PREFIX)
    ]
    growth_columns = [
        str(column)
        for column in renewable_growth_frame.columns
        if str(column).endswith("Leistung (MW)") and not str(column).startswith(STORAGE_PREFIX)
    ]
    if not generation_columns or "Wind: Leistung (MW)" not in capacity_columns:
        raise ValueError("Renewable stock does not contain generation fields and wind capacity")
    if not growth_columns:
        raise ValueError("Renewable growth does not contain renewable capacity fields")

    stock_by_key = {
        (int(row["Jahr"]), str(row["AGS"]).zfill(8)): row
        for _, row in renewable_stock_frame.iterrows()
    }
    growth_by_key = {
        (int(row["Jahr"]), str(row["AGS"]).zfill(8)): row
        for _, row in renewable_growth_frame.iterrows()
    }
    renewable_rows: list[dict] = []
    for key in sorted(set(stock_by_key) | set(growth_by_key)):
        stock = stock_by_key.get(key)
        growth = growth_by_key.get(key)
        identity = stock if stock is not None else growth
        assert identity is not None
        generation_total, generation_unknown = (
            _sum_generation(stock, generation_columns) if stock is not None else (None, 0)
        )
        renewable_rows.append(
            {
                "year": key[0],
                "municipality_name": str(identity["Gemeinde"]),
                "district_name": str(identity["Kreis"]),
                "nuts_code": _nuts_code(identity["Kreis"], district_lookup),
                "ags": key[1],
                "published_generation_mwh": generation_total,
                "generation_components_unknown": generation_unknown,
                "wind_capacity_mw": (
                    _number(stock.get("Wind: Leistung (MW)"), field="Wind: Leistung (MW)")
                    if stock is not None
                    else None
                ),
                "renewable_capacity_mw": (
                    _sum_columns(stock, capacity_columns) if stock is not None else None
                ),
                "renewable_net_addition_mw": (
                    _sum_columns(growth, growth_columns) if growth is not None else None
                ),
                "source": SOURCE_NAME,
            }
        )

    return consumption_rows, renewable_rows, reporting_year


def read_energy_workbook(
    path: Path,
    district_lookup: dict[str, str],
) -> tuple[list[dict], list[dict], int]:
    sheet_names = [CONSUMPTION_SHEET, RENEWABLE_STOCK_SHEET, RENEWABLE_GROWTH_SHEET]
    try:
        sheets = pd.read_excel(path, sheet_name=sheet_names, dtype={"AGS": str})
    except ImportError as error:
        raise RuntimeError("Reading Energieatlas XLSX requires the openpyxl package") from error
    except ValueError as error:
        if "could not read stylesheet" not in str(error):
            raise
        with tempfile.TemporaryDirectory() as directory:
            sanitized_path = Path(directory) / path.name
            replacement_count = 0
            with zipfile.ZipFile(path) as source, zipfile.ZipFile(
                sanitized_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as target:
                for member in source.infolist():
                    payload = source.read(member.filename)
                    if member.filename == "xl/styles.xml":
                        payload, replacement_count = re.subn(
                            rb'rgb="#([0-9A-Fa-f]{6})"',
                            rb'rgb="FF\1"',
                            payload,
                        )
                    target.writestr(member, payload)
            if replacement_count == 0:
                raise
            sheets = pd.read_excel(
                sanitized_path,
                sheet_name=sheet_names,
                dtype={"AGS": str},
            )
    return prepare_energy_snapshots(
        sheets[CONSUMPTION_SHEET],
        sheets[RENEWABLE_STOCK_SHEET],
        sheets[RENEWABLE_GROWTH_SHEET],
        district_lookup,
    )


def build_import_script(
    consumption_rows: list[dict],
    renewable_rows: list[dict],
    reporting_year: int,
) -> str:
    if not consumption_rows or not renewable_rows:
        raise ValueError("Energy snapshots must not be empty")
    if reporting_year <= 0:
        raise ValueError("Reporting year must be positive")
    consumption_copy = _copy_block(
        "import_energy_consumption",
        CONSUMPTION_COLUMNS,
        consumption_rows,
    )
    renewable_copy = _copy_block(
        "import_renewable_balance",
        RENEWABLE_COLUMNS,
        renewable_rows,
    )
    return f"""BEGIN;
CREATE TEMP TABLE import_energy_consumption (
    year integer, municipality_name text, district_name text, nuts_code text, ags text,
    consumption_gwh numeric, industry_gwh numeric, commerce_services_gwh numeric,
    households_gwh numeric, source text
) ON COMMIT DROP;
CREATE TEMP TABLE import_renewable_balance (
    year integer, municipality_name text, district_name text, nuts_code text, ags text,
    published_generation_mwh numeric, generation_components_unknown integer,
    wind_capacity_mw numeric, renewable_capacity_mw numeric,
    renewable_net_addition_mw numeric, source text
) ON COMMIT DROP;
{consumption_copy}{renewable_copy}
INSERT INTO raw.energy_consumption_municipal
SELECT * FROM import_energy_consumption
ON CONFLICT (year, ags) DO UPDATE SET
    municipality_name = EXCLUDED.municipality_name,
    district_name = EXCLUDED.district_name,
    nuts_code = EXCLUDED.nuts_code,
    consumption_gwh = EXCLUDED.consumption_gwh,
    industry_gwh = EXCLUDED.industry_gwh,
    commerce_services_gwh = EXCLUDED.commerce_services_gwh,
    households_gwh = EXCLUDED.households_gwh,
    source = EXCLUDED.source;
DELETE FROM raw.energy_consumption_municipal AS existing
WHERE NOT EXISTS (
    SELECT 1 FROM import_energy_consumption incoming
    WHERE incoming.year = existing.year AND incoming.ags = existing.ags
);

INSERT INTO raw.renewable_balance_municipal
SELECT * FROM import_renewable_balance
ON CONFLICT (year, ags) DO UPDATE SET
    municipality_name = EXCLUDED.municipality_name,
    district_name = EXCLUDED.district_name,
    nuts_code = EXCLUDED.nuts_code,
    published_generation_mwh = EXCLUDED.published_generation_mwh,
    generation_components_unknown = EXCLUDED.generation_components_unknown,
    wind_capacity_mw = EXCLUDED.wind_capacity_mw,
    renewable_capacity_mw = EXCLUDED.renewable_capacity_mw,
    renewable_net_addition_mw = EXCLUDED.renewable_net_addition_mw,
    source = EXCLUDED.source;
DELETE FROM raw.renewable_balance_municipal AS existing
WHERE NOT EXISTS (
    SELECT 1 FROM import_renewable_balance incoming
    WHERE incoming.year = existing.year AND incoming.ags = existing.ags
);

COMMIT;
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load NRW municipal energy balance into PostGIS")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--workbook", type=Path, default=ENERGY_WORKBOOK)
    parser.add_argument(
        "--regions",
        type=Path,
        default=ROOT / "data" / "raw" / "nuts3_regions_gisco_2024.geojson",
    )
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.regions == ROOT / "data" / "raw" / "nuts3_regions_gisco_2024.geojson":
        admin_regions, _, _ = read_raw_seed_snapshot()
    else:
        from load_nrw_postgis import read_admin_regions

        admin_regions = read_admin_regions(args.regions)
    lookup = build_district_lookup(admin_regions)
    consumption, renewables, reporting_year = read_energy_workbook(args.workbook, lookup)
    print(
        f"validated energy snapshot: consumption={len(consumption)}, "
        f"renewables={len(renewables)}, reporting_year={reporting_year}"
    )
    if args.check_only:
        return
    if not args.database_url:
        raise ValueError("DATABASE_URL or --database-url is required unless --check-only is used")
    raise ValueError(
        "Individual loaders only validate inputs. Use scripts/refresh_nrw_database.py "
        "to publish a complete atomic seed."
    )


if __name__ == "__main__":
    main()
