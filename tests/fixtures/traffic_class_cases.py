"""Shared catalogue of Strassen.NRW traffic-count rows.

This is a deliberately synthetic fixture catalogue of Strassen.NRW traffic
count rows, used to regression-test how the ingestion pipeline decides
whether a station published anything and how it stores the vehicle-class
split. Background: ``DTVKFZA`` is the average daily traffic volume for all
motor vehicles on all days of the year, ``DTVLVA`` and ``DTVSVA`` are the
same day-type's light- and heavy-vehicle shares, and ``DTVKFZW`` /
``DTVKFZU`` / ``DTVKFZS`` are the working-day, holiday-working-day and
Sunday/public-holiday all-vehicle totals. The publisher defines no no-data
code for this feed.

The project infers non-publication at the level of the whole row: a
station is treated as having published nothing only when all four of the
all-day/day-type totals (``DTVKFZA``, ``DTVKFZW``, ``DTVKFZU``,
``DTVKFZS``) are zero at once, because in the real snapshot every zero row
looks exactly like that and such rows only ever occur at manual (SVZ, not
automatic-counter) stations. That inference is deliberately never applied
to a single field in isolation: a zero heavy-vehicle count on a road that
otherwise carries positive total and light traffic is a real, measured
zero and must be kept, not converted to unknown.

This module is test-support code, not production code. It intentionally has
no dependency on anything beyond the standard library -- and, in
particular, no import from ``scripts/`` -- so that it states what a correct
loader must do without being able to agree with a broken loader by
construction. It stays trivially importable from a non-Python (SQL) test
suite as well: it is pure data plus a couple of tiny lookup helpers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrafficRow:
    """One named Strassen.NRW traffic row with its expected stored values."""

    name: str
    total_all_days: float | None
    total_working: float | None
    total_holiday: float | None
    total_sunday: float | None
    light: float | None
    heavy: float | None
    station_type: str
    expected_total: float | None
    expected_light: float | None
    expected_heavy: float | None
    description: str


TRAFFIC_ROWS: tuple[TrafficRow, ...] = (
    TrafficRow(
        name="measured_with_class_split",
        total_all_days=12000.0,
        total_working=12500.0,
        total_holiday=11000.0,
        total_sunday=8000.0,
        light=11400.0,
        heavy=600.0,
        station_type="automatic",
        expected_total=12000.0,
        expected_light=11400.0,
        expected_heavy=600.0,
        description="A fully reported automatic station: everything is kept as-is.",
    ),
    TrafficRow(
        name="zero_heavy_on_a_busy_road",
        total_all_days=100.0,
        total_working=105.0,
        total_holiday=95.0,
        total_sunday=70.0,
        light=100.0,
        heavy=0.0,
        station_type="automatic",
        expected_total=100.0,
        expected_light=100.0,
        expected_heavy=0.0,
        description=(
            "Traffic class-zero counterexample: total and light traffic are positive, so "
            "the row is clearly published, and a heavy-vehicle count of zero "
            "is a real measurement that must not be erased to unknown."
        ),
    ),
    TrafficRow(
        name="zero_light_with_heavy_traffic",
        total_all_days=600.0,
        total_working=620.0,
        total_holiday=580.0,
        total_sunday=400.0,
        light=0.0,
        heavy=600.0,
        station_type="automatic",
        expected_total=600.0,
        expected_light=0.0,
        expected_heavy=600.0,
        description="The mirror case: a measured zero light-vehicle count must also survive.",
    ),
    TrafficRow(
        name="all_day_types_zero_manual_station",
        total_all_days=0.0,
        total_working=0.0,
        total_holiday=0.0,
        total_sunday=0.0,
        light=0.0,
        heavy=0.0,
        station_type="manual",
        expected_total=None,
        expected_light=None,
        expected_heavy=None,
        description=(
            "All four all-day/day-type totals are zero at once at a manual SVZ "
            "station: this is the station-did-not-publish pattern, not a real "
            "reading, so everything is unknown."
        ),
    ),
    TrafficRow(
        name="class_split_absent",
        total_all_days=11454.0,
        total_working=11800.0,
        total_holiday=10900.0,
        total_sunday=7600.0,
        light=0.0,
        heavy=0.0,
        station_type="automatic",
        expected_total=11454.0,
        expected_light=None,
        expected_heavy=None,
        description=(
            "The day-type totals are positive, so the total is a genuine "
            "measurement and is kept. But a positive total cannot consist of "
            "zero light and zero heavy vehicles, so the class split itself is "
            "unpublished, not a real zero/zero split."
        ),
    ),
    TrafficRow(
        name="absent_readings",
        total_all_days=None,
        total_working=None,
        total_holiday=None,
        total_sunday=None,
        light=None,
        heavy=None,
        station_type="automatic",
        expected_total=None,
        expected_light=None,
        expected_heavy=None,
        description="No readings at all: everything stays unknown.",
    ),
)


def row(name: str) -> TrafficRow:
    """Look up a :class:`TrafficRow` by name, or raise ``KeyError(name)``."""
    for entry in TRAFFIC_ROWS:
        if entry.name == name:
            return entry
    raise KeyError(name)


def unpublished_rows() -> tuple[TrafficRow, ...]:
    """Every row expected to store no total (``expected_total is None``)."""
    return tuple(entry for entry in TRAFFIC_ROWS if entry.expected_total is None)


def published_rows() -> tuple[TrafficRow, ...]:
    """Every row expected to store a total (``expected_total is not None``)."""
    return tuple(entry for entry in TRAFFIC_ROWS if entry.expected_total is not None)
