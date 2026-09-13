"""The independent expectation catalogue must be internally consistent.

`tests/fixtures/score_model_cases.py` deliberately states every expected value
twice: once as a literal worked out by hand from the written contract, and once
as something its own helpers can compute.  This module holds the two apart and
fails if they ever disagree, so the catalogue that the SQL is measured against
cannot quietly drift into agreeing with a defect.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "tests" / "fixtures"))

from score_model_cases import (  # noqa: E402
    COMPOSITE_CASES,
    EDGE_NOTES,
    INDICATOR_WEIGHTS,
    NORMALIZATION_CASES,
    PERCENTILE_CASES,
    RANK_CASES,
    normalize_5_95,
    percentile_cont,
    rank_desc_nulls_last,
    round_half_up,
    weighted_composite,
)

ANALYTICS_SQL = (ROOT / "db/nrw_analytics.sql").read_text(encoding="utf-8")


class RoundingTest(unittest.TestCase):
    def test_rounds_half_away_from_zero_like_postgresql_numeric(self) -> None:
        # Python's built-in round() answers 0.0, 2.2 and -0.0 here.
        self.assertEqual(round_half_up(0.05), 0.1)
        self.assertEqual(round_half_up(2.25), 2.3)
        self.assertEqual(round_half_up(-0.05), -0.1)
        self.assertIsNone(round_half_up(None))


class CatalogueConsistencyTest(unittest.TestCase):
    def test_normalization_literals_match_the_helper(self) -> None:
        self.assertGreaterEqual(len(NORMALIZATION_CASES), 20)
        for case in NORMALIZATION_CASES:
            with self.subTest(case.label):
                self.assertEqual(
                    normalize_5_95(
                        case.metric, case.lower_bound, case.upper_bound, case.inverse
                    ),
                    case.expected,
                    case.note,
                )

    def test_percentile_literals_match_the_helper(self) -> None:
        self.assertGreaterEqual(len(PERCENTILE_CASES), 7)
        for case in PERCENTILE_CASES:
            with self.subTest(case.label):
                self.assertEqual(
                    percentile_cont(case.values, case.fraction), case.expected, case.note
                )

    def test_composite_literals_match_the_helper(self) -> None:
        self.assertGreaterEqual(len(COMPOSITE_CASES), 10)
        for case in COMPOSITE_CASES:
            with self.subTest(case.label):
                self.assertEqual(
                    weighted_composite(
                        case.components, INDICATOR_WEIGHTS[case.composite_name]
                    ),
                    case.expected,
                    case.note,
                )

    def test_rank_literals_match_the_helper(self) -> None:
        self.assertGreaterEqual(len(RANK_CASES), 5)
        for case in RANK_CASES:
            with self.subTest(case.label):
                self.assertEqual(
                    tuple(rank_desc_nulls_last(list(case.scores))), case.expected, case.note
                )

    def test_edge_rules_are_stated_for_a_reviewer(self) -> None:
        self.assertGreaterEqual(len(EDGE_NOTES), 4)
        for note in EDGE_NOTES:
            self.assertTrue(note.strip())


class WeightsMatchTheSqlTest(unittest.TestCase):
    """The catalogue's weights must be the ones the model publishes.

    `db/verify_nrw_analytics.sql` recomputes every district's composites from
    `publish.nrw_score_model`, so a weight the SQL applies but does not publish
    fails there.  This cheaper check reads the published model straight out of
    the SQL document, so a weight that disagrees with the independently derived
    catalogue fails without needing a database at all.
    """

    def _model_rows(self) -> list[tuple[str, str, float]]:
        model = ANALYTICS_SQL.split("CREATE VIEW analytics.nrw_score_model AS", 1)[1]
        model = model.split(") model;", 1)[0]
        pattern = re.compile(
            r"'(?P<indicator_score>[a-z_]+_score)'(?:::text)?\s*(?:AS\s+indicator_score)?\s*,\s*"
            r"'(?P<component>[a-z_]+_score)'(?:::text)?\s*(?:AS\s+component_score)?\s*,\s*"
            r"(?P<weight>-?[0-9.]+)",
        )
        return [
            (match.group("indicator_score"), match.group("component"), float(match.group("weight")))
            for match in pattern.finditer(model)
        ]

    def test_every_published_indicator_is_modelled(self) -> None:
        modelled = {row[0] for row in self._model_rows()}
        self.assertEqual(modelled, set(INDICATOR_WEIGHTS))

    def test_model_weights_match_the_independent_catalogue(self) -> None:
        for indicator, component, weight in self._model_rows():
            with self.subTest(f"{indicator}:{component}"):
                expected = INDICATOR_WEIGHTS[indicator]
                key = component if component in expected else f"inverse:{component}"
                self.assertIn(key, expected, f"{component} is not a component of {indicator}")
                self.assertAlmostEqual(abs(weight), expected[key], places=6)


if __name__ == "__main__":
    unittest.main()
