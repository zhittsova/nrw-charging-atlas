CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS publish;

CREATE TABLE IF NOT EXISTS raw.chargers (
    source_id text NOT NULL,
    operator text,
    status text,
    charger_type text,
    charging_points integer,
    power_kw numeric,
    street text,
    postcode text,
    city text,
    district_text text,
    bundesland text,
    geom geometry(Point, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.admin_regions (
    nuts_code text NOT NULL,
    ags text,
    district_name text,
    region_name text,
    geom geometry(MultiPolygon, 4326) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS raw_chargers_source_id_uq
    ON raw.chargers (source_id);
CREATE INDEX IF NOT EXISTS raw_chargers_geom_gix
    ON raw.chargers USING gist (geom);
CREATE UNIQUE INDEX IF NOT EXISTS raw_admin_regions_nuts_code_uq
    ON raw.admin_regions (nuts_code);
CREATE INDEX IF NOT EXISTS raw_admin_regions_geom_gix
    ON raw.admin_regions USING gist (geom);

CREATE TABLE IF NOT EXISTS raw.population (
    district_code text,
    nuts_code text,
    ags text,
    population integer,
    reference_year integer,
    source text
);

CREATE TABLE IF NOT EXISTS raw.roads (
    osm_id text,
    road_class text,
    name text,
    road_number text,
    traffic_total numeric,
    traffic_light numeric,
    traffic_heavy numeric,
    source text,
    geom geometry(LineString, 4326)
);

ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS road_number text;
ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS traffic_total numeric;
ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS traffic_light numeric;
ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS traffic_heavy numeric;
ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS source text;

CREATE TABLE IF NOT EXISTS raw.grid_infrastructure (
    source_id text,
    asset_type text,
    voltage text,
    name text,
    geom geometry(Geometry, 4326)
);

CREATE UNIQUE INDEX IF NOT EXISTS raw_grid_source_id_uq
    ON raw.grid_infrastructure (source_id);
CREATE INDEX IF NOT EXISTS raw_grid_geom_gix
    ON raw.grid_infrastructure USING gist (geom);

CREATE TABLE IF NOT EXISTS raw.renewable_assets (
    source_id text,
    asset_type text,
    technology text,
    capacity_mw numeric,
    status text,
    geom geometry(Point, 4326)
);

CREATE TABLE IF NOT EXISTS raw.power_plants (
    source_id text PRIMARY KEY,
    name text,
    operator text,
    energy_source text,
    technology text,
    capacity_mw numeric,
    status text,
    voltage text,
    network_operator text,
    geom geometry(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS raw_roads_geom_gix ON raw.roads USING gist (geom);
CREATE UNIQUE INDEX IF NOT EXISTS raw_roads_source_id_uq ON raw.roads (osm_id);
CREATE INDEX IF NOT EXISTS raw_renewable_assets_geom_gix ON raw.renewable_assets USING gist (geom);
CREATE UNIQUE INDEX IF NOT EXISTS raw_renewable_assets_source_id_uq ON raw.renewable_assets (source_id);
CREATE INDEX IF NOT EXISTS raw_power_plants_geom_gix ON raw.power_plants USING gist (geom);

CREATE OR REPLACE VIEW staging.nrw_boundary AS
SELECT ST_Union(geom)::geometry(MultiPolygon, 4326) AS geom
FROM raw.admin_regions
WHERE nuts_code LIKE 'DEA%';

CREATE OR REPLACE VIEW staging.nrw_districts AS
SELECT *
FROM raw.admin_regions
WHERE nuts_code LIKE 'DEA%';

CREATE OR REPLACE VIEW staging.nrw_chargers AS
SELECT c.*
FROM raw.chargers c
JOIN staging.nrw_boundary b
  ON ST_Intersects(c.geom, b.geom);

CREATE OR REPLACE VIEW staging.nrw_roads AS
SELECT r.*
FROM raw.roads r
JOIN staging.nrw_boundary b
  ON ST_Intersects(r.geom, ST_Buffer(b.geom::geography, 10000)::geometry)
WHERE r.road_class IN ('motorway', 'trunk', 'primary', 'secondary');

CREATE OR REPLACE VIEW staging.nrw_grid AS
SELECT g.*
FROM raw.grid_infrastructure g
JOIN staging.nrw_boundary b
  ON ST_Intersects(g.geom, ST_Buffer(b.geom::geography, 10000)::geometry);

CREATE OR REPLACE VIEW staging.nrw_renewables AS
SELECT a.*
FROM raw.renewable_assets a
JOIN staging.nrw_boundary b
  ON ST_Intersects(a.geom, ST_Buffer(b.geom::geography, 10000)::geometry);

DROP MATERIALIZED VIEW IF EXISTS analytics.nrw_district_metrics CASCADE;

CREATE MATERIALIZED VIEW analytics.nrw_district_metrics AS
SELECT
    d.nuts_code,
    d.ags,
    d.district_name,
    ST_Area(ST_Transform(d.geom, 25832)) / 1000000.0 AS area_km2,
    p.population,
    COUNT(c.source_id) AS chargers_total,
    COALESCE(
        SUM(COALESCE(c.charging_points, 1)) FILTER (WHERE c.source_id IS NOT NULL),
        0
    ) AS charging_points_total,
    COUNT(c.source_id) FILTER (WHERE COALESCE(c.power_kw, 0) >= 50) AS fast_chargers_total,
    COUNT(c.source_id) FILTER (WHERE COALESCE(c.power_kw, 0) < 50) AS normal_chargers_total,
    nearest.distance_to_nearest_charger_m,
    d.geom
FROM staging.nrw_districts d
LEFT JOIN raw.chargers c
  ON ST_Intersects(c.geom, d.geom)
LEFT JOIN LATERAL (
    SELECT ST_Distance(
        ST_Centroid(ST_Transform(d.geom, 25832)),
        ST_Transform(cn.geom, 25832)
    ) AS distance_to_nearest_charger_m
    FROM raw.chargers cn
    ORDER BY ST_Centroid(d.geom) <-> cn.geom
    LIMIT 1
) nearest ON true
LEFT JOIN raw.population p
  ON p.nuts_code = d.nuts_code OR p.ags = d.ags
GROUP BY d.nuts_code, d.ags, d.district_name, p.population, nearest.distance_to_nearest_charger_m, d.geom;

CREATE OR REPLACE VIEW analytics.nrw_priority_scores AS
SELECT *
FROM analytics.nrw_district_metrics;

CREATE OR REPLACE VIEW publish.nrw_district_priority AS
SELECT *
FROM analytics.nrw_priority_scores;

CREATE OR REPLACE VIEW publish.nrw_chargers AS
SELECT *
FROM staging.nrw_chargers;

CREATE OR REPLACE VIEW publish.nrw_grid_readiness AS
SELECT *
FROM staging.nrw_grid;

CREATE OR REPLACE VIEW publish.nrw_renewable_potential AS
SELECT *
FROM staging.nrw_renewables;

CREATE OR REPLACE VIEW publish.nrw_accessibility AS
SELECT *
FROM staging.nrw_roads;
