"""Shared catalogue of OpenStreetMap ``voltage`` tag cases.

This is a deliberately synthetic fixture catalogue of OpenStreetMap
``voltage`` tag strings, used to regression-test how the ingestion pipeline
derives a kilovolt reading from that tag. The rules under test are, in
short: a bare number is a value in volts; a number immediately followed by
``kV`` (any case, with or without a space) is already in kilovolts; several
candidate values may be packed into one tag separated by semicolons, in
which case the highest *valid* one wins; any token that is not one of those
two whole-token forms carries no voltage evidence; and a zero or negative
value is never a measured voltage, even though it is syntactically a
number.

This module is test-support code, not production code. It intentionally has
no dependency on anything beyond the standard library so that it stays
trivially importable from a non-Python (SQL) test suite as well: it is pure
data plus a couple of tiny lookup helpers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoltageCase:
    """One named OSM ``voltage`` tag string with its expected kV reading."""

    name: str
    tag: str | None
    expected_kv: float | None
    description: str


VOLTAGE_CASES: tuple[VoltageCase, ...] = (
    # -- plain, single-value tags -------------------------------------------
    VoltageCase(
        name="plain_volts",
        tag="110000",
        expected_kv=110.0,
        description="A bare number is volts; 110000 V is 110 kV.",
    ),
    VoltageCase(
        name="kilovolt_suffix",
        tag="110 kV",
        expected_kv=110.0,
        description="A number followed by ' kV' is already in kilovolts.",
    ),
    VoltageCase(
        name="kilovolt_suffix_no_space",
        tag="380kV",
        expected_kv=380.0,
        description="The kV suffix does not require a separating space.",
    ),
    VoltageCase(
        name="kilovolt_lowercase",
        tag="20kv",
        expected_kv=20.0,
        description="The kV suffix is recognised case-insensitively.",
    ),
    VoltageCase(
        name="decimal_volts",
        tag="20500.5",
        expected_kv=20.5005,
        description="A bare decimal number is still volts: 20500.5 V is 20.5005 kV.",
    ),
    VoltageCase(
        name="whitespace_padded",
        tag=" 110000 ",
        expected_kv=110.0,
        description="Surrounding whitespace around a lone value is not evidence of anything else.",
    ),
    # -- multi-value tags: highest valid value wins -------------------------
    VoltageCase(
        name="multi_value",
        tag="110000;220000",
        expected_kv=220.0,
        description="Several semicolon-separated volt values: the highest wins.",
    ),
    VoltageCase(
        name="multi_value_mixed_units",
        tag="110000;380 kV",
        expected_kv=380.0,
        description="Units may differ per value; each is converted before comparing.",
    ),
    # -- negative and zero values are not measured voltages -----------------
    VoltageCase(
        name="negative_volts",
        tag="-110000",
        expected_kv=None,
        description=(
            "A sign-stripping parser wrongly reads this as 110 kV (S07-R03); "
            "a negative value is not a measured voltage and yields no evidence."
        ),
    ),
    VoltageCase(
        name="negative_kilovolts",
        tag="-110 kV",
        expected_kv=None,
        description="The sign rule applies with the kV suffix too, not only to bare numbers.",
    ),
    VoltageCase(
        name="mixed_negative_and_valid",
        tag="-110000;220000",
        expected_kv=220.0,
        description=(
            "One candidate is negative and discarded, but the other half of "
            "the semicolon list is still valid evidence."
        ),
    ),
    VoltageCase(
        name="zero_volts",
        tag="0",
        expected_kv=None,
        description="Zero is syntactically a number but is not a measured voltage.",
    ),
    VoltageCase(
        name="zero_and_valid",
        tag="0;110000",
        expected_kv=110.0,
        description="A zero candidate is discarded; the remaining valid candidate still counts.",
    ),
    # -- not evidence at all -------------------------------------------------
    VoltageCase(
        name="embedded_number",
        tag="abc123def",
        expected_kv=None,
        description="A number embedded in an otherwise non-numeric token is not a voltage.",
    ),
    VoltageCase(
        name="unknown_word",
        tag="unknown",
        expected_kv=None,
        description="A non-numeric word carries no voltage evidence.",
    ),
    VoltageCase(
        name="empty_string",
        tag="",
        expected_kv=None,
        description="An empty tag value carries no voltage evidence.",
    ),
    VoltageCase(
        name="absent_tag",
        tag=None,
        expected_kv=None,
        description="A missing tag altogether carries no voltage evidence.",
    ),
)


def case(name: str) -> VoltageCase:
    """Look up a :class:`VoltageCase` by name, or raise ``KeyError(name)``."""
    for entry in VOLTAGE_CASES:
        if entry.name == name:
            return entry
    raise KeyError(name)


def sql_literal(tag: str | None) -> str:
    """Render ``tag`` as a SQL literal: ``NULL`` for ``None``, else a quoted string."""
    if tag is None:
        return "NULL"
    return "'" + tag.replace("'", "''") + "'"
