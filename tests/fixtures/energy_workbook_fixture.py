"""Small synthetic Energieatlas workbook fixture for parser tests.

This deliberately contains one fictional district and only the columns needed
to exercise reporting-year selection, power aggregation, and storage removal.
It must never be treated as dashboard or production input.

Capacity cells are written out explicitly, including zeros, because the real
Energieatlas workbook never leaves a ``Leistung (MW)`` cell empty: an absent
technology is recorded as 0.  Generation cells are left empty where the
matching capacity is zero, which is the source's own dominant pattern for
"no installation, so no yield to report".
"""

from __future__ import annotations

import pandas as pd


LABEL = "synthetic parser fixture; not NRW source data"


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
                # No landfill-gas plant, so the empty yield cell is a real zero
                # rather than an unpublished reading.
                "Deponiegas: Leistung (MW)": 0,
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
                "PV: Bauliche Anlagen Leistung (MW)": 0,
                "Deponiegas: Leistung (MW)": 0,
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
                "PV: Bauliche Anlagen Leistung (MW)": 0,
                "Biomasse: Leistung (MW)": 0,
                "Speicher: Batteriespeicher Leistung (MW)": 0,
            },
            {
                "Jahr": 2023,
                "Gemeinde": "Teststadt",
                "Kreis": "Teststadt",
                "AGS": "05111000",
                "Wind: Leistung (MW)": 0,
                "PV: Bauliche Anlagen Leistung (MW)": 2,
                "Biomasse: Leistung (MW)": 0,
                "Speicher: Batteriespeicher Leistung (MW)": 0,
            },
            {
                "Jahr": 2024,
                "Gemeinde": "Teststadt",
                "Kreis": "Teststadt",
                "AGS": "05111000",
                "Wind: Leistung (MW)": 0,
                "PV: Bauliche Anlagen Leistung (MW)": 0,
                "Biomasse: Leistung (MW)": 3,
                "Speicher: Batteriespeicher Leistung (MW)": 100,
            },
        ]
    )


def workbook_sheets() -> dict[str, pd.DataFrame]:
    return {
        "Stromverbrauch": consumption_frame(),
        "Bestand Gemeinden EE": renewable_stock_frame(),
        "Nottozubau Gemeinden EE": renewable_growth_frame(),
    }
