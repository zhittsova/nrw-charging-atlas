"""Prove the shared geometry-case catalogue's declared verdicts are real.

``tests/fixtures/geometry_cases.py`` declares, for each named GeoJSON
geometry, whether GEOS considers it valid and why. This module does not
trust those declarations -- it recomputes each verdict with Shapely (the
Python GEOS binding) and checks the catalogue against it, so a future
mistake in the fixture (e.g. a case moved to the wrong bucket) fails loudly
here rather than silently poisoning both the ingestion tests and the SQL
cross-check that also reads this fixture.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.validation import explain_validity
from shapely.wkt import loads as wkt_loads

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "tests" / "fixtures"))

import geometry_cases as fixtures  # noqa: E402


class DeclaredValidityMatchesGeosTest(unittest.TestCase):
    """Every case's ``valid`` flag must match Shapely's ``is_valid``."""

    def test_declared_validity_matches_shapely(self) -> None:
        for entry in fixtures.GEOMETRY_CASES:
            with self.subTest(name=entry.name):
                geom = shape(entry.geometry)
                self.assertEqual(
                    geom.is_valid,
                    entry.valid,
                    msg=(
                        f"{entry.name}: declared valid={entry.valid} but "
                        f"GEOS explain_validity says: {explain_validity(geom)!r}"
                    ),
                )


class DeclaredReasonMatchesExplainValidityTest(unittest.TestCase):
    """Every case's ``reason`` phrase must agree with GEOS's own explanation."""

    _REASON_KEYWORDS: dict[str, tuple[str, ...]] = {
        "too few points": ("too few points",),
        "self-intersection": ("self-intersection",),
        "hole outside shell": ("hole", "outside", "shell"),
    }

    def test_invalid_reason_keywords_appear_in_explain_validity(self) -> None:
        for entry in fixtures.invalid_cases():
            with self.subTest(name=entry.name):
                geom = shape(entry.geometry)
                explanation = explain_validity(geom).lower()
                keywords = self._REASON_KEYWORDS[entry.reason]
                for keyword in keywords:
                    self.assertIn(
                        keyword,
                        explanation,
                        msg=(
                            f"{entry.name}: reason {entry.reason!r} expects "
                            f"keyword {keyword!r} in GEOS explanation "
                            f"{explanation!r}"
                        ),
                    )

    def test_valid_reason_is_valid_and_explain_validity_agrees(self) -> None:
        for entry in fixtures.valid_cases():
            with self.subTest(name=entry.name):
                self.assertEqual(entry.reason, "valid")
                geom = shape(entry.geometry)
                explanation = explain_validity(geom).lower()
                self.assertIn("valid", explanation)


class WktMatchesGeojsonTest(unittest.TestCase):
    """Each case's hand-written WKT must be the same geometry as its GeoJSON."""

    def test_wkt_parses_and_matches_geojson_coordinates(self) -> None:
        for entry in fixtures.GEOMETRY_CASES:
            with self.subTest(name=entry.name):
                wkt = fixtures.WKT_BY_NAME[entry.name]
                from_wkt = wkt_loads(wkt)
                from_geojson = shape(entry.geometry)

                # ``.equals()`` uses topological equality, which is only
                # defined (and reliable) for valid geometries -- it is not a
                # trustworthy comparator for the invalid cases in this
                # catalogue. Instead we compare the coordinate structure of
                # both parses directly via ``shapely.geometry.mapping``,
                # which round-trips exactly for valid and invalid geometry
                # alike and does not depend on any validity-sensitive
                # predicate.
                self.assertEqual(
                    mapping(from_wkt)["coordinates"],
                    mapping(from_geojson)["coordinates"],
                    msg=f"{entry.name}: WKT {wkt!r} does not match its GeoJSON",
                )
                self.assertEqual(mapping(from_wkt)["type"], entry.geometry["type"])


class CatalogueSanityTest(unittest.TestCase):
    """Structural checks on the catalogue itself."""

    def test_invalid_and_valid_cases_partition_the_catalogue(self) -> None:
        # GeometryCase holds a dict field, so it is not hashable -- compare
        # by name instead of building sets of the dataclass instances.
        invalid_names = {entry.name for entry in fixtures.invalid_cases()}
        valid_names = {entry.name for entry in fixtures.valid_cases()}
        all_names = {entry.name for entry in fixtures.GEOMETRY_CASES}

        self.assertEqual(invalid_names | valid_names, all_names)
        self.assertEqual(invalid_names & valid_names, set())
        self.assertEqual(
            len(invalid_names) + len(valid_names), len(fixtures.GEOMETRY_CASES)
        )

    def test_every_wkt_entry_has_a_matching_case_and_vice_versa(self) -> None:
        case_names = {entry.name for entry in fixtures.GEOMETRY_CASES}
        self.assertEqual(case_names, set(fixtures.WKT_BY_NAME))

    def test_at_least_one_invalid_case_per_failure_family(self) -> None:
        invalid_names = {entry.name for entry in fixtures.invalid_cases()}

        degeneracy = {"degenerate_point"}
        self_intersection = {
            "bowtie",
            "zero_area_sliver",
            "hole_crossing_shell",
            "overlapping_multipolygon",
            "touching_multipolygon",
        }
        hole_relationship = {"hole_outside_shell", "hole_crossing_shell"}
        multipolygon_relationship = {
            "overlapping_multipolygon",
            "touching_multipolygon",
        }

        for family_name, family in (
            ("degeneracy", degeneracy),
            ("self-intersection", self_intersection),
            ("hole relationship", hole_relationship),
            ("multipolygon relationship", multipolygon_relationship),
        ):
            with self.subTest(family=family_name):
                self.assertTrue(
                    family & invalid_names,
                    msg=f"no invalid case covers the {family_name} family",
                )

    def test_case_lookup_raises_key_error_for_unknown_name(self) -> None:
        with self.assertRaises(KeyError):
            fixtures.case("nope")

    def test_case_lookup_returns_the_matching_entry(self) -> None:
        entry = fixtures.case("valid_square")
        self.assertEqual(entry.name, "valid_square")
        self.assertTrue(entry.valid)


if __name__ == "__main__":
    unittest.main()
