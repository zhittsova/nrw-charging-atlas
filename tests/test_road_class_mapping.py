"""Guard against the F12 drift between the region config and the SQL filter.

The accessibility layer was empty because ``config/regions/nrw.yml`` listed
OpenStreetMap highway values while the loader wrote Strassen.NRW STRKL source
classes and the SQL compared the two directly. These checks fail if the
declared mapping and the SQL implementation stop agreeing.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

from config_utils import read_simple_region_config  # noqa: E402


SCHEMA_SQL = (ROOT / "db/nrw_schema.sql").read_text(encoding="utf-8")
ANALYTICS_SQL = (ROOT / "db/nrw_analytics.sql").read_text(encoding="utf-8")


def declared_mapping() -> dict[str, str]:
    config = read_simple_region_config()
    entries = config["road_class_source_mapping"]
    assert isinstance(entries, list)
    return dict(entry.split("=", 1) for entry in entries)


def sql_mapping() -> dict[str, str]:
    body = SCHEMA_SQL.split("CREATE OR REPLACE FUNCTION staging.normalize_road_class", 1)[1]
    body = body.split("$$;", 1)[0]
    return dict(re.findall(r"WHEN '([^']+)' THEN '([^']+)'", body))


class RoadClassMappingTest(unittest.TestCase):
    def test_the_declared_source_mapping_matches_the_sql_implementation(self) -> None:
        self.assertEqual(declared_mapping(), sql_mapping())

    def test_every_strassen_nrw_source_class_is_mapped(self) -> None:
        """The loader writes STRKL values A, B, L and K; none may fall through."""
        self.assertEqual(set(declared_mapping()), {"A", "B", "L", "K"})

    def test_the_mapping_targets_are_openstreetmap_highway_values(self) -> None:
        self.assertEqual(
            declared_mapping(),
            {"A": "motorway", "B": "primary", "L": "secondary", "K": "tertiary"},
        )

    def test_the_accessibility_filter_uses_the_configured_major_road_classes(self) -> None:
        config = read_simple_region_config()
        major = config["major_road_classes"]
        self.assertIsInstance(major, list)

        filter_clause = re.search(
            r"WHERE staging\.normalize_road_class\(r\.road_class\)\s*\n?\s*IN \(([^)]*)\)",
            SCHEMA_SQL,
        )
        self.assertIsNotNone(filter_clause, "staging.nrw_roads no longer filters on the mapping")
        assert filter_clause is not None
        filtered = re.findall(r"'([^']+)'", filter_clause.group(1))
        self.assertEqual(sorted(filtered), sorted(major))

    def test_at_least_one_source_class_reaches_the_accessibility_layer(self) -> None:
        """The exact defect: no STRKL value satisfied the old OSM-only filter."""
        config = read_simple_region_config()
        major = set(config["major_road_classes"])
        reachable = {
            source for source, target in declared_mapping().items() if target in major
        }
        self.assertEqual(reachable, {"A", "B", "L"})

    def test_regional_roads_no_longer_hardcode_the_source_letters(self) -> None:
        self.assertNotIn("WHERE road_class IN ('B', 'L')", ANALYTICS_SQL)
        self.assertIn(
            "WHERE staging.normalize_road_class(road_class) IN ('primary', 'secondary')",
            ANALYTICS_SQL,
        )


if __name__ == "__main__":
    unittest.main()
