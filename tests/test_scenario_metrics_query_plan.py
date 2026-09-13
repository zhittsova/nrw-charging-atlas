"""Keep the bounded scenario-read query plan reproducible in source."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYTICS_SQL = (ROOT / "db/nrw_analytics.sql").read_text(encoding="utf-8")


class ScenarioMetricsQueryPlanTest(unittest.TestCase):
    def test_effective_chargers_is_inlined_for_the_scenario_view(self) -> None:
        """District and nearest-station consumers need source spatial indexes."""
        scenario_view = ANALYTICS_SQL.split(
            "CREATE VIEW analytics.nrw_ev_scenario_metrics AS", 1
        )[1].split("CREATE VIEW publish.nrw_ev_scenario_metrics AS", 1)[0]
        self.assertIn("WITH effective_chargers AS NOT MATERIALIZED (", scenario_view)


if __name__ == "__main__":
    unittest.main()
