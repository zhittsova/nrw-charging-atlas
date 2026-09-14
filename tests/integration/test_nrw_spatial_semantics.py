"""Spatial semantics regressions for F11 and F12.

Every nearest-feature expectation here is recomputed from coordinates read back
out of the database using an independent pyproj oracle, so a test failure means
the SQL selected or measured the wrong feature rather than that the assertion
restates the query.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests" / "fixtures"))
from run_postgis_tests import psql_connection, require_disposable_database_url  # noqa: E402

from projected_geometry import (  # noqa: E402
    nearest_by_degrees,
    nearest_by_metres,
    projected_distance_m,
    projected_point_to_segment_distance_m,
)

from geometry_cases import GEOMETRY_CASES, WKT_BY_NAME  # noqa: E402

import nrw_charger_quality as quality  # noqa: E402


DATABASE_URL = os.environ.get("SCENARIO_TEST_DATABASE_URL")

# Offsets in projected metres from a district centroid.  "east" is nearer in
# metres; "north" is nearer in raw EPSG:4326 degrees, because at NRW latitudes a
# degree of longitude covers roughly 69 km against 111 km for a degree of
# latitude.  Any query that orders by degrees and then measures in metres picks
# "north" and reports its larger distance.
EAST_OFFSET_M = 2100.0
NORTH_OFFSET_M = 2800.0

DISTRICTS_SQL = """
INSERT INTO raw.admin_regions (nuts_code, ags, district_name, region_name, geom) VALUES
    ('DEA01', '05111', 'West', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((7.0 51.0,7.5 51.0,7.5 51.5,7.0 51.5,7.0 51.0))', 4326))),
    ('DEA02', '05112', 'Centre', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((7.5 51.0,8.0 51.0,8.0 51.5,7.5 51.5,7.5 51.0))', 4326))),
    ('DEA03', '05113', 'East', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((8.0 51.0,8.5 51.0,8.5 51.5,8.0 51.5,8.0 51.0))', 4326)));

INSERT INTO raw.population (district_code, nuts_code, ags, population, reference_year, source) VALUES
    ('05111', 'DEA01', '05111', 100000, 2024, 'fixture'),
    ('05112', 'DEA02', '05112', 200000, 2024, 'fixture'),
    ('05113', 'DEA03', '05113', 300000, 2024, 'fixture');
"""


@unittest.skipUnless(DATABASE_URL, "SCENARIO_TEST_DATABASE_URL is not configured")
class DisposableSpatialFixture(unittest.TestCase):
    """Shared psql plumbing against the runner-owned disposable database."""

    fixture_sql = ""
    load_analytics = True

    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None
        run_id = os.environ.get("SCENARIO_TEST_RUN_ID")
        endpoint = os.environ.get("SCENARIO_TEST_ENDPOINT")
        require_disposable_database_url(DATABASE_URL, run_id=run_id, endpoint=endpoint)
        cls.psql_base, cls.psql_environment = psql_connection(
            DATABASE_URL, run_id=run_id, endpoint=endpoint
        )
        cls.psql(
            "DROP SCHEMA IF EXISTS publish CASCADE;"
            "DROP SCHEMA IF EXISTS analytics CASCADE;"
            "DROP SCHEMA IF EXISTS staging CASCADE;"
            "DROP SCHEMA IF EXISTS scenario CASCADE;"
            "DROP SCHEMA IF EXISTS raw CASCADE;"
        )
        cls.psql_file(ROOT / "db/nrw_schema.sql")
        if cls.fixture_sql:
            cls.psql(cls.fixture_sql)
        cls.psql("REFRESH MATERIALIZED VIEW analytics.nrw_district_metrics;")
        if cls.load_analytics:
            cls.psql_file(ROOT / "db/nrw_analytics.sql")

    @classmethod
    def psql(cls, sql: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            cls.psql_base,
            input=sql,
            text=True,
            capture_output=True,
            check=False,
            env=cls.psql_environment,
        )
        if check and result.returncode:
            raise RuntimeError(result.stderr or result.stdout or "psql failed")
        return result

    @classmethod
    def psql_file(cls, path: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [*cls.psql_base, "-f", str(path)],
            text=True,
            capture_output=True,
            check=False,
            env=cls.psql_environment,
        )
        if check and result.returncode:
            raise RuntimeError(result.stderr or result.stdout or "psql file failed")
        return result

    def rows(self, sql: str) -> list[list[str]]:
        result = self.psql(sql, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line.split("|") for line in result.stdout.strip().splitlines() if line]

    def value(self, sql: str) -> str:
        rows = self.rows(sql)
        self.assertEqual(len(rows), 1, rows)
        return rows[0][0]

    def lonlat(self, sql: str) -> dict[str, tuple[float, float]]:
        """Return {id: (lon, lat)} for a query yielding id, longitude, latitude."""
        return {row[0]: (float(row[1]), float(row[2])) for row in self.rows(sql)}


class BufferedContextTests(DisposableSpatialFixture):
    """The ten-kilometre context margin keeps its semantics without per-row buffers."""

    load_analytics = False
    fixture_sql = DISTRICTS_SQL + """
    INSERT INTO raw.renewable_assets (source_id, geom) VALUES
        ('inside', ST_SetSRID(ST_Point(7.2,51.2),4326)),
        ('nearby', ST_SetSRID(ST_Point(6.95,51.2),4326)),
        ('far', ST_SetSRID(ST_Point(6.5,51.2),4326));
    INSERT INTO raw.grid_infrastructure (source_id, geom)
        SELECT source_id, geom FROM raw.renewable_assets;
    INSERT INTO raw.roads (osm_id, road_class, geom)
        SELECT source_id, 'B', ST_MakeLine(geom, ST_Translate(geom,0,0.001))
        FROM raw.renewable_assets;
    """

    def test_context_includes_nearby_features_but_excludes_distant_features(self):
        for view, key in (("nrw_renewables", "source_id"), ("nrw_grid", "source_id"), ("nrw_roads", "osm_id")):
            with self.subTest(view=view):
                self.assertEqual(
                    self.rows(f"SELECT {key} FROM staging.{view} ORDER BY {key}"),
                    [["inside"], ["nearby"]],
                )

    def test_buffer_executes_once_per_query_for_all_context_layers(self):
        def walk(plan):
            yield plan
            for child in plan.get("Plans", []):
                yield from walk(child)

        for view in ("nrw_renewables", "nrw_grid", "nrw_roads"):
            with self.subTest(view=view):
                plan = json.loads(self.psql(
                    f"EXPLAIN (ANALYZE, VERBOSE, FORMAT JSON) SELECT * FROM staging.{view}"
                ).stdout)[0]["Plan"]
                buffer_nodes = [node for node in walk(plan)
                                if "st_buffer(" in str(node.get("Output", "")).lower()
                                and node["Node Type"] == "Aggregate"]
                self.assertEqual(len(buffer_nodes), 1, plan)
                self.assertEqual(buffer_nodes[0]["Actual Loops"], 1, plan)


# Chargers, roads and substations are positioned by translating the district's
# own projected centroid, so the counterexample holds for the centroid PostGIS
# actually computes rather than for one assumed by the test.
POINT_FIXTURE_SQL = DISTRICTS_SQL + f"""
INSERT INTO raw.chargers (
    source_id, operator, status, charger_type, charging_points,
    power_kw, max_point_power_kw, bundesland, geom
)
SELECT 'east', 'Fixture', 'active', 'fast', 2, 150, 150, 'Nordrhein-Westfalen',
       ST_Transform(ST_Translate(d.centroid_25832, {EAST_OFFSET_M}, 0), 4326)
FROM raw.admin_regions d WHERE d.nuts_code = 'DEA01'
UNION ALL
SELECT 'north', 'Fixture', 'active', 'normal', 1, 22, 22, 'Nordrhein-Westfalen',
       ST_Transform(ST_Translate(d.centroid_25832, 0, {NORTH_OFFSET_M}), 4326)
FROM raw.admin_regions d WHERE d.nuts_code = 'DEA01'
UNION ALL
SELECT 'centre-station', 'Fixture', 'active', 'fast', 2, 75, 50, 'Nordrhein-Westfalen',
       ST_Transform(ST_Translate(d.centroid_25832, 500, 500), 4326)
FROM raw.admin_regions d WHERE d.nuts_code = 'DEA02'
UNION ALL
SELECT 'east-station', 'Fixture', 'active', 'unknown', 1, 50, NULL, 'Nordrhein-Westfalen',
       ST_Transform(ST_Translate(d.centroid_25832, 400, -400), 4326)
FROM raw.admin_regions d WHERE d.nuts_code = 'DEA03';

INSERT INTO raw.grid_infrastructure (source_id, asset_type, voltage, name, geom)
SELECT 'sub-east', 'substation', '110 kV', 'East substation',
       ST_Transform(ST_Translate(d.centroid_25832, {EAST_OFFSET_M}, 0), 4326)
FROM raw.admin_regions d WHERE d.nuts_code = 'DEA01'
UNION ALL
SELECT 'sub-north', 'substation', '220 kV', 'North substation',
       ST_Transform(ST_Translate(d.centroid_25832, 0, {NORTH_OFFSET_M}), 4326)
FROM raw.admin_regions d WHERE d.nuts_code = 'DEA01'
UNION ALL
SELECT 'sub-far', 'substation', '380 kV', 'Far substation',
       ST_Transform(ST_Translate(d.centroid_25832, 600, 600), 4326)
FROM raw.admin_regions d WHERE d.nuts_code = 'DEA03';

INSERT INTO raw.roads (osm_id, road_class, name, traffic_total, source, geom)
SELECT 'road-east', 'B', 'East road', 12000, 'fixture',
       ST_Transform(
           ST_MakeLine(
               ST_Translate(d.centroid_25832, {EAST_OFFSET_M}, -3000),
               ST_Translate(d.centroid_25832, {EAST_OFFSET_M}, 3000)
           ),
           4326
       )
FROM raw.admin_regions d WHERE d.nuts_code = 'DEA01'
UNION ALL
SELECT 'road-north', 'L', 'North road', 8000, 'fixture',
       ST_Transform(
           ST_MakeLine(
               ST_Translate(d.centroid_25832, -3000, {NORTH_OFFSET_M}),
               ST_Translate(d.centroid_25832, 3000, {NORTH_OFFSET_M})
           ),
           4326
       )
FROM raw.admin_regions d WHERE d.nuts_code = 'DEA01';
"""


class ProjectedNearestSelectionTest(DisposableSpatialFixture):
    fixture_sql = POINT_FIXTURE_SQL

    def centroid(self, nuts_code: str) -> tuple[float, float]:
        row = self.rows(
            "SELECT ST_X(ST_Transform(centroid_25832, 4326)),"
            "       ST_Y(ST_Transform(centroid_25832, 4326))"
            f" FROM raw.admin_regions WHERE nuts_code = '{nuts_code}';"
        )[0]
        return float(row[0]), float(row[1])

    def test_degree_ordering_selects_a_different_station_than_metre_ordering(self) -> None:
        """The counterexample: the two orderings disagree, and metres must win."""
        row = self.rows(
            """
            SELECT
                (SELECT cn.source_id FROM raw.chargers cn
                 ORDER BY ST_Centroid(d.geom) <-> cn.geom LIMIT 1),
                (SELECT cn.source_id FROM raw.chargers cn
                 ORDER BY d.centroid_25832 <-> cn.geom_25832 LIMIT 1),
                (SELECT ST_Distance(d.centroid_25832, cn.geom_25832) FROM raw.chargers cn
                 ORDER BY ST_Centroid(d.geom) <-> cn.geom LIMIT 1)
            FROM staging.nrw_districts d WHERE d.nuts_code = 'DEA01';
            """
        )[0]
        degree_pick, metre_pick, degree_pick_distance_m = row[0], row[1], float(row[2])

        self.assertEqual(degree_pick, "north")
        self.assertEqual(metre_pick, "east")

        reported = float(
            self.value(
                "SELECT distance_to_nearest_charger_m FROM analytics.nrw_district_metrics"
                " WHERE nuts_code = 'DEA01';"
            )
        )
        self.assertAlmostEqual(reported, EAST_OFFSET_M, delta=1.0)
        # The published distance must no longer be the one belonging to the
        # degree-ordered station, which is several hundred metres farther away.
        self.assertGreater(degree_pick_distance_m - reported, 500.0)

    def test_baseline_nearest_charger_agrees_with_independent_projection(self) -> None:
        chargers = self.lonlat(
            "SELECT source_id, ST_X(geom), ST_Y(geom) FROM raw.chargers;"
        )
        reported = {
            row[0]: float(row[1])
            for row in self.rows(
                "SELECT nuts_code, distance_to_nearest_charger_m"
                " FROM analytics.nrw_district_metrics ORDER BY nuts_code;"
            )
        }
        self.assertEqual(len(reported), 3)

        for nuts_code, distance_m in reported.items():
            centroid = self.centroid(nuts_code)
            expected_id, expected_m = nearest_by_metres(centroid, chargers)
            degree_id, _ = nearest_by_degrees(centroid, chargers)
            with self.subTest(district=nuts_code, expected=expected_id, degrees=degree_id):
                self.assertAlmostEqual(distance_m, expected_m, delta=1.0)

    def test_nearest_substation_agrees_with_independent_projection(self) -> None:
        substations = self.lonlat(
            "SELECT source_id, ST_X(geom), ST_Y(geom) FROM raw.grid_infrastructure"
            " WHERE asset_type IN ('substation', 'transformer');"
        )
        for row in self.rows(
            "SELECT nuts_code, distance_to_nearest_substation_m"
            " FROM analytics.nrw_grid_proxy_metrics ORDER BY nuts_code;"
        ):
            nuts_code, distance_m = row[0], float(row[1])
            expected_id, expected_m = nearest_by_metres(self.centroid(nuts_code), substations)
            with self.subTest(district=nuts_code, expected=expected_id):
                self.assertAlmostEqual(distance_m, expected_m, delta=1.0)
        self.assertEqual(
            self.value(
                "SELECT (SELECT g.source_id FROM raw.grid_infrastructure g"
                " WHERE g.asset_type = 'substation'"
                " ORDER BY ST_Centroid(d.geom) <-> g.geom LIMIT 1)"
                " FROM staging.nrw_districts d WHERE d.nuts_code = 'DEA01';"
            ),
            "sub-north",
            "fixture no longer exercises the degree-versus-metre disagreement",
        )

    def test_nearest_road_agrees_with_independent_projected_segment_distance(self) -> None:
        segments = {
            row[0]: (
                (float(row[1]), float(row[2])),
                (float(row[3]), float(row[4])),
            )
            for row in self.rows(
                "SELECT osm_id, ST_X(ST_StartPoint(geom)), ST_Y(ST_StartPoint(geom)),"
                "       ST_X(ST_EndPoint(geom)), ST_Y(ST_EndPoint(geom))"
                " FROM raw.roads;"
            )
        }
        for row in self.rows(
            "SELECT nuts_code, distance_to_nearest_road_m"
            " FROM analytics.nrw_transport_metrics ORDER BY nuts_code;"
        ):
            nuts_code, distance_m = row[0], float(row[1])
            centroid = self.centroid(nuts_code)
            expected_m = min(
                projected_point_to_segment_distance_m(centroid, start, end)
                for start, end in segments.values()
            )
            with self.subTest(district=nuts_code):
                # A projected straight segment is not exactly straight in
                # EPSG:4326, so allow a few metres of chord deviation.
                self.assertAlmostEqual(distance_m, expected_m, delta=5.0)

    def test_scenario_nearest_charger_uses_the_same_projected_selection(self) -> None:
        self.psql(
            """
            INSERT INTO scenario.proposed_chargers (
                name, charging_points, power_kw, max_point_power_kw, geom
            )
            SELECT 'Nearer proposal', 4, 150, 150,
                   ST_Transform(ST_Translate(d.centroid_25832, 900, 0), 4326)
            FROM raw.admin_regions d WHERE d.nuts_code = 'DEA01';
            """
        )
        self.addCleanup(lambda: self.psql("TRUNCATE scenario.proposed_chargers;"))

        candidates = self.lonlat(
            "SELECT source_id, ST_X(geom), ST_Y(geom) FROM raw.chargers"
            " UNION ALL SELECT id::text, ST_X(geom), ST_Y(geom) FROM scenario.proposed_chargers;"
        )
        row = self.rows(
            "SELECT scenario_distance_to_nearest_charger_m,"
            "       distance_to_nearest_charger_m_delta"
            " FROM publish.nrw_ev_scenario_metrics WHERE nuts_code = 'DEA01';"
        )[0]
        scenario_distance_m, delta_m = float(row[0]), float(row[1])

        _, expected_m = nearest_by_metres(self.centroid("DEA01"), candidates)
        self.assertAlmostEqual(scenario_distance_m, expected_m, delta=1.0)
        self.assertAlmostEqual(scenario_distance_m, 900.0, delta=1.0)
        self.assertAlmostEqual(delta_m, 900.0 - EAST_OFFSET_M, delta=2.0)


class MissingFeatureDistanceTest(DisposableSpatialFixture):
    """Absent features leave an unavailable distance, never zero or infinity."""

    fixture_sql = DISTRICTS_SQL

    def test_absent_features_leave_distances_and_dependent_scores_unavailable(self) -> None:
        row = self.rows(
            "SELECT distance_to_nearest_charger_m IS NULL,"
            "       COALESCE(distance_to_nearest_charger_m::text, 'null')"
            " FROM analytics.nrw_district_metrics WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(row[0], "t")
        self.assertEqual(row[1], "null")

        row = self.rows(
            "SELECT charger_accessibility_score IS NULL, ev_readiness_score IS NULL,"
            "       investment_priority_score IS NULL, data_quality_flag"
            " FROM publish.nrw_ev_baseline_metrics WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(row[:3], ["t", "t", "t"])
        self.assertEqual(row[3], "missing_ev_input")

    def test_absent_roads_and_substations_do_not_become_zero_distances(self) -> None:
        road = self.rows(
            "SELECT distance_to_nearest_road_m IS NULL, road_proximity_score IS NULL,"
            "       transport_load_score IS NULL"
            " FROM analytics.nrw_transport_metrics WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(road, ["t", "t", "t"])

        grid = self.rows(
            "SELECT distance_to_nearest_substation_m IS NULL,"
            "       grid_readiness_proxy_score IS NULL"
            " FROM analytics.nrw_grid_proxy_metrics WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(grid, ["t", "t"])

        self.assertEqual(
            self.value(
                "SELECT COUNT(*) FROM analytics.nrw_district_metrics"
                " WHERE distance_to_nearest_charger_m = 0"
                "    OR distance_to_nearest_charger_m = 'Infinity'::double precision;"
            ),
            "0",
        )

    def test_a_charger_exactly_on_the_centroid_still_reports_a_real_zero(self) -> None:
        """A measured zero distance must stay distinguishable from unavailable."""
        self.psql(
            """
            INSERT INTO raw.chargers (
                source_id, operator, status, charging_points, power_kw,
                max_point_power_kw, bundesland, geom
            )
            SELECT 'on-centroid', 'Fixture', 'active', 1, 22, 22, 'Nordrhein-Westfalen',
                   ST_Transform(d.centroid_25832, 4326)
            FROM raw.admin_regions d WHERE d.nuts_code = 'DEA01';
            REFRESH MATERIALIZED VIEW analytics.nrw_district_metrics;
            """
        )
        self.addCleanup(
            lambda: self.psql(
                "DELETE FROM raw.chargers WHERE source_id = 'on-centroid';"
                "REFRESH MATERIALIZED VIEW analytics.nrw_district_metrics;"
            )
        )
        row = self.rows(
            "SELECT distance_to_nearest_charger_m IS NULL,"
            "       ROUND(distance_to_nearest_charger_m::numeric, 6)"
            " FROM analytics.nrw_district_metrics WHERE nuts_code = 'DEA01';"
        )[0]
        self.assertEqual(row[0], "f")
        self.assertEqual(float(row[1]), 0.0)


ROAD_CLASS_FIXTURE_SQL = DISTRICTS_SQL + """
INSERT INTO raw.roads (osm_id, road_class, name, road_number, traffic_total, source, geom) VALUES
    ('src-a', 'A', 'Autobahn', 'A1', 90000, 'Strassen.NRW Verkehrswerte',
     ST_GeomFromText('LINESTRING(7.1 51.1,7.4 51.1)', 4326)),
    ('src-b', 'B', 'Bundesstrasse', 'B7', 30000, 'Strassen.NRW Verkehrswerte',
     ST_GeomFromText('LINESTRING(7.1 51.2,7.4 51.2)', 4326)),
    ('src-l', 'L', 'Landesstrasse', 'L663', 9000, 'Strassen.NRW Verkehrswerte',
     ST_GeomFromText('LINESTRING(7.1 51.3,7.4 51.3)', 4326)),
    ('src-k', 'K', 'Kreisstrasse', 'K12', 2000, 'Strassen.NRW Verkehrswerte',
     ST_GeomFromText('LINESTRING(7.1 51.4,7.4 51.4)', 4326)),
    ('osm-motorway', 'motorway', 'OSM motorway', NULL, 50000, 'openstreetmap',
     ST_GeomFromText('LINESTRING(7.6 51.1,7.9 51.1)', 4326)),
    ('osm-secondary', 'secondary', 'OSM secondary', NULL, 5000, 'openstreetmap',
     ST_GeomFromText('LINESTRING(7.6 51.2,7.9 51.2)', 4326)),
    ('osm-residential', 'residential', 'OSM residential', NULL, 500, 'openstreetmap',
     ST_GeomFromText('LINESTRING(7.6 51.3,7.9 51.3)', 4326));
"""


class RoadClassificationTest(DisposableSpatialFixture):
    fixture_sql = ROAD_CLASS_FIXTURE_SQL

    def test_source_classes_are_mapped_explicitly(self) -> None:
        mapping = {
            row[0]: row[1]
            for row in self.rows(
                "SELECT source_class, COALESCE(staging.normalize_road_class(source_class), 'null')"
                " FROM (VALUES ('A'), ('B'), ('L'), ('K'), (' b '), ('motorway'),"
                "              ('residential'), (''), (NULL)) AS v(source_class);"
            )
        }
        self.assertEqual(mapping["A"], "motorway")
        self.assertEqual(mapping["B"], "primary")
        self.assertEqual(mapping["L"], "secondary")
        self.assertEqual(mapping["K"], "tertiary")
        self.assertEqual(mapping[" b "], "primary")
        self.assertEqual(mapping["motorway"], "motorway")
        self.assertEqual(mapping["residential"], "residential")
        self.assertEqual(mapping[""], "null")

    def test_published_accessibility_is_not_empty_for_source_classes(self) -> None:
        """F12: Strassen.NRW A/B/L/K used to match no OSM class at all."""
        selected = sorted(
            row[0] for row in self.rows("SELECT osm_id FROM publish.nrw_accessibility;")
        )
        self.assertEqual(
            selected, ["osm-motorway", "osm-secondary", "src-a", "src-b", "src-l"]
        )
        self.assertNotIn("src-k", selected, "Kreisstrassen are not a major road class")
        self.assertNotIn("osm-residential", selected)

    def test_accessibility_exposes_both_source_and_normalised_class(self) -> None:
        rows = {
            row[0]: (row[1], row[2])
            for row in self.rows(
                "SELECT osm_id, road_class, road_class_normalized"
                " FROM publish.nrw_accessibility ORDER BY osm_id;"
            )
        }
        self.assertEqual(rows["src-a"], ("A", "motorway"))
        self.assertEqual(rows["src-b"], ("B", "primary"))
        self.assertEqual(rows["src-l"], ("L", "secondary"))

    def test_regional_roads_keep_the_bundes_and_landesstrasse_selection(self) -> None:
        self.assertEqual(
            sorted(row[0] for row in self.rows("SELECT source_id FROM publish.nrw_regional_roads;")),
            ["osm-secondary", "src-b", "src-l"],
        )

    def test_transport_load_still_uses_the_whole_counted_traffic_network(self) -> None:
        """Traffic intensity is a property of the counted network, not of the
        accessibility class filter, so Kreisstrassen still contribute."""
        self.assertEqual(
            self.value(
                "SELECT COUNT(*) FROM raw.roads r JOIN staging.nrw_districts d"
                " ON ST_Intersects(d.geom, r.geom) WHERE d.nuts_code = 'DEA01';"
            ),
            "4",
        )
        self.assertGreater(
            float(
                self.value(
                    "SELECT traffic_road_length_km FROM analytics.nrw_transport_metrics"
                    " WHERE nuts_code = 'DEA01';"
                )
            ),
            0.0,
        )


# DEA01 carries a hole; DEA01 and DEA02 share the 7.5 meridian.
CONTAINMENT_FIXTURE_SQL = """
INSERT INTO raw.admin_regions (nuts_code, ags, district_name, region_name, geom) VALUES
    ('DEA01', '05111', 'West', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText(
        'POLYGON((7.0 51.0,7.5 51.0,7.5 51.5,7.0 51.5,7.0 51.0),'
        '(7.2 51.2,7.3 51.2,7.3 51.3,7.2 51.3,7.2 51.2))', 4326))),
    ('DEA02', '05112', 'Centre', 'Nordrhein-Westfalen',
     ST_Multi(ST_GeomFromText('POLYGON((7.5 51.0,8.0 51.0,8.0 51.5,7.5 51.5,7.5 51.0))', 4326)));

INSERT INTO raw.population (district_code, nuts_code, ags, population, reference_year, source) VALUES
    ('05111', 'DEA01', '05111', 100000, 2024, 'fixture'),
    ('05112', 'DEA02', '05112', 200000, 2024, 'fixture');

INSERT INTO raw.chargers (
    source_id, operator, status, charging_points, power_kw,
    max_point_power_kw, bundesland, geom
) VALUES
    ('interior', 'Fixture', 'active', 1, 22, 22, 'Nordrhein-Westfalen',
     ST_SetSRID(ST_Point(7.1, 51.1), 4326)),
    ('in-hole', 'Fixture', 'active', 1, 22, 22, 'Nordrhein-Westfalen',
     ST_SetSRID(ST_Point(7.25, 51.25), 4326)),
    ('on-hole-edge', 'Fixture', 'active', 1, 22, 22, 'Nordrhein-Westfalen',
     ST_SetSRID(ST_Point(7.2, 51.25), 4326)),
    ('on-shared-boundary', 'Fixture', 'active', 1, 22, 22, 'Nordrhein-Westfalen',
     ST_SetSRID(ST_Point(7.5, 51.25), 4326)),
    ('outside-nrw', 'Fixture', 'active', 1, 22, 22, 'Nordrhein-Westfalen',
     ST_SetSRID(ST_Point(6.0, 50.0), 4326));
"""

CONTAINMENT_REGIONS = [
    {
        "type": "Feature",
        "properties": {"nuts_code": "DEA01", "district_name": "West"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[7.0, 51.0], [7.5, 51.0], [7.5, 51.5], [7.0, 51.5], [7.0, 51.0]],
                [[7.2, 51.2], [7.3, 51.2], [7.3, 51.3], [7.2, 51.3], [7.2, 51.2]],
            ],
        },
    },
    {
        "type": "Feature",
        "properties": {"nuts_code": "DEA02", "district_name": "Centre"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[7.5, 51.0], [8.0, 51.0], [8.0, 51.5], [7.5, 51.5], [7.5, 51.0]]
            ],
        },
    },
]

CONTAINMENT_POINTS = {
    "interior": (7.1, 51.1),
    "in-hole": (7.25, 51.25),
    "on-hole-edge": (7.2, 51.25),
    "on-shared-boundary": (7.5, 51.25),
    "outside-nrw": (6.0, 50.0),
}


class ContainmentSemanticsTest(DisposableSpatialFixture):
    fixture_sql = CONTAINMENT_FIXTURE_SQL
    load_analytics = False

    def test_district_assignment_follows_covers_semantics(self) -> None:
        assignment = {
            row[0]: row[1]
            for row in self.rows(
                "SELECT source_id, nuts_code FROM staging.nrw_charger_districts ORDER BY source_id;"
            )
        }
        self.assertEqual(assignment.get("interior"), "DEA01")
        self.assertEqual(assignment.get("on-hole-edge"), "DEA01", "a hole edge is covered")
        self.assertNotIn("in-hole", assignment, "a point inside a hole is outside the district")
        self.assertNotIn("outside-nrw", assignment)

    def test_a_station_on_a_shared_boundary_is_counted_exactly_once(self) -> None:
        self.assertEqual(
            self.value(
                "SELECT COUNT(*) FROM raw.chargers c JOIN staging.nrw_districts d"
                " ON ST_Covers(d.geom, c.geom) WHERE c.source_id = 'on-shared-boundary';"
            ),
            "2",
            "the fixture must actually be boundary-ambiguous",
        )
        self.assertEqual(
            self.value(
                "SELECT nuts_code FROM staging.nrw_charger_districts"
                " WHERE source_id = 'on-shared-boundary';"
            ),
            "DEA01",
        )
        self.assertEqual(
            self.value(
                "SELECT SUM(chargers_total)::text FROM analytics.nrw_district_metrics;"
            ),
            "3",
        )

    def test_ingestion_and_sql_assign_the_same_districts(self) -> None:
        """The ingestion validator and the database must not disagree."""
        candidates = [
            {
                "type": "Feature",
                "properties": {"id": station_id, "name": station_id, "operator": "Fixture"},
                "geometry": {"type": "Point", "coordinates": list(coordinates)},
            }
            for station_id, coordinates in CONTAINMENT_POINTS.items()
        ]
        accepted, rejected = quality.classify_chargers(candidates, CONTAINMENT_REGIONS)
        ingestion = {
            item["properties"]["id"]: item["properties"]["nuts_code"] for item in accepted
        }
        database = {
            row[0]: row[1]
            for row in self.rows("SELECT source_id, nuts_code FROM staging.nrw_charger_districts;")
        }

        self.assertEqual(ingestion, database)
        self.assertEqual(
            sorted(item["reason"] for item in rejected), ["outside_nrw", "outside_nrw"]
        )

    def test_a_proposal_inside_a_hole_or_outside_nrw_is_refused(self) -> None:
        for name, longitude, latitude in (
            ("hole proposal", 7.25, 51.25),
            ("outside proposal", 6.0, 50.0),
        ):
            with self.subTest(case=name):
                result = self.psql(
                    "INSERT INTO scenario.proposed_chargers ("
                    " name, charging_points, power_kw, max_point_power_kw, geom) VALUES ("
                    f" '{name}', 2, 100, 50, ST_SetSRID(ST_Point({longitude}, {latitude}), 4326));",
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("exactly one NRW district", result.stderr)


class GeometryValidityAgreementTest(DisposableSpatialFixture):
    """PostGIS and the ingestion validator must share one validity verdict.

    Both use GEOS, but only an actual round trip shows that the catalogue's
    expectations hold in the database the analytics run against, rather than
    only in the Python process that writes the fixtures.
    """

    fixture_sql = ""
    load_analytics = False

    def test_postgis_agrees_with_the_declared_geos_verdicts(self) -> None:
        for geometry_case in GEOMETRY_CASES:
            wkt = WKT_BY_NAME[geometry_case.name]
            with self.subTest(case=geometry_case.name):
                row = self.rows(
                    f"SELECT ST_IsValid(ST_GeomFromText('{wkt}', 4326))::text,"
                    f"       ST_IsValidReason(ST_GeomFromText('{wkt}', 4326));"
                )[0]
                self.assertEqual(
                    row[0],
                    "true" if geometry_case.valid else "false",
                    f"PostGIS says {row[1]}",
                )

    def test_the_ingestion_validator_matches_postgis_case_by_case(self) -> None:
        for geometry_case in GEOMETRY_CASES:
            wkt = WKT_BY_NAME[geometry_case.name]
            postgis_valid = self.value(
                f"SELECT ST_IsValid(ST_GeomFromText('{wkt}', 4326))::text;"
            ) == "true"
            district = {
                "type": "Feature",
                "properties": {"nuts_code": "DEA01", "district_name": "Fixture"},
                "geometry": geometry_case.geometry,
            }
            candidate = {
                "type": "Feature",
                "properties": {"id": "probe", "name": "probe", "operator": "Fixture"},
                "geometry": {"type": "Point", "coordinates": [0.5, 0.5]},
            }
            with self.subTest(case=geometry_case.name, postgis_valid=postgis_valid):
                if postgis_valid:
                    quality.classify_chargers([candidate], [district])
                else:
                    with self.assertRaises(ValueError):
                        quality.classify_chargers([candidate], [district])

    def test_the_wkt_and_geojson_forms_describe_the_same_shape(self) -> None:
        """Otherwise the PostGIS cross-check would test a different geometry."""
        for geometry_case in GEOMETRY_CASES:
            wkt = WKT_BY_NAME[geometry_case.name]
            with self.subTest(case=geometry_case.name):
                self.assertEqual(
                    self.value(
                        "SELECT ST_OrderingEquals("
                        f"  ST_GeomFromText('{wkt}', 4326),"
                        f"  ST_GeomFromGeoJSON('{json.dumps(geometry_case.geometry)}')"
                        ")::text;"
                    ),
                    "true",
                )


if __name__ == "__main__":
    unittest.main()
