-- Applied as one transaction.  The analytics materialized view below is
-- dropped with CASCADE, so a failure part way through a non-transactional run
-- would leave the published dataset without its dependent views.  Wrapping the
-- document means an upgrade either lands completely or leaves the previous
-- usable installation untouched (contract C08).
BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS publish;
CREATE SCHEMA IF NOT EXISTS scenario;

CREATE TABLE IF NOT EXISTS raw.chargers (
    source_id text NOT NULL,
    operator text,
    status text,
    charger_type text,
    charging_points integer,
    -- Aggregate station nominal power as supplied by BNetzA.  This is not a
    -- per-connector classification value.
    power_kw numeric,
    -- Measured maximum of the valid BNetzA connector nominal-power fields.
    -- NULL is an explicit unknown, not a value inferred from power_kw.
    max_point_power_kw numeric,
    street text,
    postcode text,
    city text,
    district_text text,
    bundesland text,
    geom geometry(Point, 4326) NOT NULL,
    -- Projected companion geometry.  Nearest-feature ordering and the final
    -- distance measurement must both run in EPSG:25832 metres (contract C02);
    -- ordering by EPSG:4326 degrees selects a different feature at NRW
    -- latitudes, where one degree of longitude is far shorter than one degree
    -- of latitude.
    geom_25832 geometry(Point, 25832)
        GENERATED ALWAYS AS (ST_Transform(geom, 25832)) STORED
);

-- Keep existing source rows compatible while introducing the measured
-- connector maximum.  In particular, never manufacture this value from the
-- aggregate station power or number of charging points.
ALTER TABLE raw.chargers ADD COLUMN IF NOT EXISTS max_point_power_kw numeric;
ALTER TABLE raw.chargers ADD COLUMN IF NOT EXISTS geom_25832 geometry(Point, 25832)
    GENERATED ALWAYS AS (ST_Transform(geom, 25832)) STORED;

CREATE TABLE IF NOT EXISTS raw.admin_regions (
    nuts_code text NOT NULL,
    ags text,
    district_name text,
    region_name text,
    geom geometry(MultiPolygon, 4326) NOT NULL,
    geom_25832 geometry(MultiPolygon, 25832)
        GENERATED ALWAYS AS (ST_Transform(geom, 25832)) STORED,
    -- The accessibility proxy stays centroid based.  The centroid is taken in
    -- projected space so that it is the same point used for both the nearest
    -- ordering and the reported metre distance.
    centroid_25832 geometry(Point, 25832)
        GENERATED ALWAYS AS (ST_Centroid(ST_Transform(geom, 25832))) STORED
);

ALTER TABLE raw.admin_regions ADD COLUMN IF NOT EXISTS geom_25832 geometry(MultiPolygon, 25832)
    GENERATED ALWAYS AS (ST_Transform(geom, 25832)) STORED;
ALTER TABLE raw.admin_regions ADD COLUMN IF NOT EXISTS centroid_25832 geometry(Point, 25832)
    GENERATED ALWAYS AS (ST_Centroid(ST_Transform(geom, 25832))) STORED;

CREATE UNIQUE INDEX IF NOT EXISTS raw_chargers_source_id_uq
    ON raw.chargers (source_id);
CREATE INDEX IF NOT EXISTS raw_chargers_geom_gix
    ON raw.chargers USING gist (geom);
CREATE INDEX IF NOT EXISTS raw_chargers_geom_25832_gix
    ON raw.chargers USING gist (geom_25832);
CREATE UNIQUE INDEX IF NOT EXISTS raw_admin_regions_nuts_code_uq
    ON raw.admin_regions (nuts_code);
CREATE INDEX IF NOT EXISTS raw_admin_regions_geom_gix
    ON raw.admin_regions USING gist (geom);
CREATE INDEX IF NOT EXISTS raw_admin_regions_geom_25832_gix
    ON raw.admin_regions USING gist (geom_25832);

CREATE TABLE IF NOT EXISTS scenario.proposed_chargers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    charging_points integer NOT NULL,
    power_kw numeric NOT NULL,
    -- Nullable for proposals that existed before the maximum-point-power
    -- contract.  The trigger below requires it for new inserts.
    max_point_power_kw numeric,
    -- A browser-generated idempotency key.  It is deliberately nullable for
    -- proposals saved before WFS-T clients could safely reconcile a timeout.
    request_id uuid DEFAULT gen_random_uuid(),
    nuts_code text NOT NULL,
    status text NOT NULL DEFAULT 'proposed',
    created_at timestamptz NOT NULL DEFAULT now(),
    geom geometry(Point, 4326) NOT NULL,
    CONSTRAINT proposed_chargers_name_check
        CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
    CONSTRAINT proposed_chargers_charging_points_check
        CHECK (charging_points BETWEEN 1 AND 100),
    CONSTRAINT proposed_chargers_power_kw_check
        CHECK (power_kw BETWEEN 1 AND 1000),
    CONSTRAINT proposed_chargers_status_check
        CHECK (status = 'proposed')
);

ALTER TABLE scenario.proposed_chargers ADD COLUMN IF NOT EXISTS max_point_power_kw numeric;
ALTER TABLE scenario.proposed_chargers ADD COLUMN IF NOT EXISTS request_id uuid;
ALTER TABLE scenario.proposed_chargers ALTER COLUMN request_id SET DEFAULT gen_random_uuid();
-- GeoServer WFS-T writes every discovered geometry attribute.  A generated
-- companion geometry is therefore not writable even when the client only
-- supplies `geom`.  Keep the projected column for analytics, but maintain it
-- in the validation trigger so the WFS-facing table remains insertable.
ALTER TABLE scenario.proposed_chargers ADD COLUMN IF NOT EXISTS geom_25832 geometry(Point, 25832);
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'scenario'
          AND table_name = 'proposed_chargers'
          AND column_name = 'geom_25832'
          AND is_generated = 'ALWAYS'
    ) THEN
        ALTER TABLE scenario.proposed_chargers ALTER COLUMN geom_25832 DROP EXPRESSION;
    END IF;
END
$$;
UPDATE scenario.proposed_chargers
SET geom_25832 = ST_Transform(geom, 25832)
WHERE geom_25832 IS DISTINCT FROM ST_Transform(geom, 25832);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'proposed_chargers_max_point_power_kw_check'
          AND conrelid = 'scenario.proposed_chargers'::regclass
    ) THEN
        ALTER TABLE scenario.proposed_chargers
            ADD CONSTRAINT proposed_chargers_max_point_power_kw_check
            CHECK (
                max_point_power_kw IS NULL
                OR (
                    max_point_power_kw > 0
                    AND max_point_power_kw <> 'Infinity'::numeric
                    AND max_point_power_kw <> '-Infinity'::numeric
                    AND max_point_power_kw <> 'NaN'::numeric
                    AND max_point_power_kw <= power_kw
                )
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS proposed_chargers_geom_gix
    ON scenario.proposed_chargers USING gist (geom);
CREATE INDEX IF NOT EXISTS proposed_chargers_geom_25832_gix
    ON scenario.proposed_chargers USING gist (geom_25832);
CREATE INDEX IF NOT EXISTS proposed_chargers_nuts_code_idx
    ON scenario.proposed_chargers (nuts_code);
-- A legacy proposal has no request identifier.  New browser requests must be
-- unique, which makes an uncertain WFS response safe to reconcile by lookup.
CREATE UNIQUE INDEX IF NOT EXISTS proposed_chargers_request_id_uq
    ON scenario.proposed_chargers (request_id)
    WHERE request_id IS NOT NULL;

CREATE OR REPLACE FUNCTION scenario.validate_proposed_charger()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    district_matches integer;
    matched_nuts_code text;
BEGIN
    NEW.name := btrim(NEW.name);
    IF NEW.name = '' THEN
        RAISE EXCEPTION 'Proposed charger name must not be empty';
    END IF;

    -- Existing proposals intentionally retain NULL after migration.  A new
    -- proposal must explicitly provide a finite, positive connector maximum
    -- no greater than its aggregate station power.
    IF TG_OP = 'INSERT' AND NEW.max_point_power_kw IS NULL THEN
        RAISE EXCEPTION 'New proposed charger requires max_point_power_kw';
    END IF;

    IF NEW.max_point_power_kw IS NOT NULL
       AND (
           NEW.max_point_power_kw <= 0
           OR NEW.max_point_power_kw IN ('Infinity'::numeric, '-Infinity'::numeric, 'NaN'::numeric)
           OR NEW.max_point_power_kw > NEW.power_kw
       )
    THEN
        RAISE EXCEPTION 'Proposed charger max_point_power_kw must be finite, positive, and no greater than power_kw';
    END IF;

    IF ST_IsEmpty(NEW.geom)
       OR ST_X(NEW.geom) NOT BETWEEN -180 AND 180
       OR ST_Y(NEW.geom) NOT BETWEEN -90 AND 90
    THEN
        RAISE EXCEPTION 'Proposed charger must use finite EPSG:4326 coordinates';
    END IF;

    SELECT COUNT(*), MIN(d.nuts_code)
    INTO district_matches, matched_nuts_code
    FROM raw.admin_regions d
    WHERE d.nuts_code LIKE 'DEA%'
      AND ST_Covers(d.geom, NEW.geom);

    IF district_matches <> 1 THEN
        RAISE EXCEPTION 'Proposed charger must fall inside exactly one NRW district';
    END IF;

    NEW.nuts_code := matched_nuts_code;
    NEW.status := 'proposed';
    NEW.geom_25832 := ST_Transform(NEW.geom, 25832);
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS proposed_chargers_validate_before_write
    ON scenario.proposed_chargers;
CREATE TRIGGER proposed_chargers_validate_before_write
BEFORE INSERT OR UPDATE ON scenario.proposed_chargers
FOR EACH ROW
EXECUTE FUNCTION scenario.validate_proposed_charger();

-- Provenance of each ingested source snapshot.  The publication date of a
-- source is a property of the file the loader consumed, not of any district, so
-- it is recorded once per source key and joined into the published projections.
-- A source whose ingestion does not yet capture a date simply has no row: the
-- published date is then NULL with a stated reason rather than a guess.
CREATE TABLE IF NOT EXISTS raw.source_snapshots (
    source_key text PRIMARY KEY,
    snapshot_date date,
    source_name text,
    source_note text,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

-- A canonical refresh has one immutable identity for every raw input it
-- consumed.  Runtime exports read this association from the same snapshot as
-- the analytical views; they must never infer provenance from a subsequently
-- refreshed local cache.
CREATE TABLE IF NOT EXISTS raw.ingest_runs (
    ingest_run_id uuid PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    is_current boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX IF NOT EXISTS raw_one_current_ingest_run
    ON raw.ingest_runs (is_current) WHERE is_current;

CREATE TABLE IF NOT EXISTS raw.ingest_source_inputs (
    ingest_run_id uuid NOT NULL REFERENCES raw.ingest_runs (ingest_run_id) ON DELETE CASCADE,
    source_key text NOT NULL,
    source_path text NOT NULL,
    bytes bigint NOT NULL CHECK (bytes >= 0),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    provenance jsonb,
    provenance_sha256 text,
    PRIMARY KEY (ingest_run_id, source_key)
);

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
    -- Straßen.NRW ZSTART: automatic permanent, temporary, or manual SVZ
    -- station.  Kept so that an unavailable traffic value stays auditable.
    counting_station_type text,
    source text,
    geom geometry(LineString, 4326),
    geom_25832 geometry(LineString, 25832)
        GENERATED ALWAYS AS (ST_Transform(geom, 25832)) STORED
);

ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS road_number text;
ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS traffic_total numeric;
ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS traffic_light numeric;
ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS traffic_heavy numeric;
ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS counting_station_type text;
ALTER TABLE raw.roads ADD COLUMN IF NOT EXISTS geom_25832 geometry(LineString, 25832)
    GENERATED ALWAYS AS (ST_Transform(geom, 25832)) STORED;

CREATE TABLE IF NOT EXISTS raw.grid_infrastructure (
    source_id text,
    asset_type text,
    voltage text,
    name text,
    geom geometry(Geometry, 4326),
    geom_25832 geometry(Geometry, 25832)
        GENERATED ALWAYS AS (ST_Transform(geom, 25832)) STORED
);

ALTER TABLE raw.grid_infrastructure ADD COLUMN IF NOT EXISTS geom_25832 geometry(Geometry, 25832)
    GENERATED ALWAYS AS (ST_Transform(geom, 25832)) STORED;

CREATE TABLE IF NOT EXISTS raw.osm_roads (
    source_id text NOT NULL,
    highway text NOT NULL,
    ref text,
    name text,
    geom geometry(Geometry, 4326) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS raw_osm_roads_source_id_uq
    ON raw.osm_roads (source_id);
CREATE INDEX IF NOT EXISTS raw_osm_roads_geom_gix
    ON raw.osm_roads USING gist (geom);

CREATE UNIQUE INDEX IF NOT EXISTS raw_grid_source_id_uq
    ON raw.grid_infrastructure (source_id);
CREATE INDEX IF NOT EXISTS raw_grid_geom_gix
    ON raw.grid_infrastructure USING gist (geom);
CREATE INDEX IF NOT EXISTS raw_grid_geom_25832_gix
    ON raw.grid_infrastructure USING gist (geom_25832);

CREATE TABLE IF NOT EXISTS raw.renewable_assets (
    source_id text,
    name text,
    operator text,
    asset_type text,
    technology text,
    capacity_mw numeric,
    status text,
    geom geometry(Point, 4326)
);

ALTER TABLE raw.renewable_assets ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE raw.renewable_assets ADD COLUMN IF NOT EXISTS operator text;

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
CREATE INDEX IF NOT EXISTS raw_roads_geom_25832_gix ON raw.roads USING gist (geom_25832);
CREATE UNIQUE INDEX IF NOT EXISTS raw_roads_source_id_uq ON raw.roads (osm_id);
CREATE INDEX IF NOT EXISTS raw_renewable_assets_geom_gix ON raw.renewable_assets USING gist (geom);
CREATE UNIQUE INDEX IF NOT EXISTS raw_renewable_assets_source_id_uq ON raw.renewable_assets (source_id);
CREATE INDEX IF NOT EXISTS raw_power_plants_geom_gix ON raw.power_plants USING gist (geom);

CREATE TABLE IF NOT EXISTS raw.energy_consumption_municipal (
    year integer NOT NULL,
    municipality_name text NOT NULL,
    district_name text NOT NULL,
    nuts_code text NOT NULL,
    ags text NOT NULL,
    consumption_gwh numeric NOT NULL,
    industry_gwh numeric,
    commerce_services_gwh numeric,
    households_gwh numeric,
    source text NOT NULL,
    PRIMARY KEY (year, ags)
);

CREATE TABLE IF NOT EXISTS raw.renewable_balance_municipal (
    year integer NOT NULL,
    municipality_name text NOT NULL,
    district_name text NOT NULL,
    nuts_code text NOT NULL,
    ags text NOT NULL,
    published_generation_mwh numeric,
    -- How many technology yields were installed but unpublished for this
    -- municipality.  A positive count is why published_generation_mwh is NULL;
    -- it is not a value and must never be treated as one.
    generation_components_unknown integer,
    wind_capacity_mw numeric,
    renewable_capacity_mw numeric,
    renewable_net_addition_mw numeric,
    source text NOT NULL,
    PRIMARY KEY (year, ags)
);

ALTER TABLE raw.renewable_balance_municipal
    ADD COLUMN IF NOT EXISTS generation_components_unknown integer;

-- Reject non-finite readings at the boundary (contract C03).  numeric accepts
-- NaN and Infinity, so a bad source value would otherwise reach an aggregate
-- and poison it silently.  The constraints are added NOT VALID so an upgrade
-- never fails on data already in the user's installation; every subsequent
-- write is still checked, and the loaders replace these tables wholesale.
DO $$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT *
        FROM (VALUES
            ('energy_consumption_municipal', 'energy_consumption_finite_chk',
             ARRAY['consumption_gwh', 'industry_gwh', 'commerce_services_gwh', 'households_gwh']),
            ('renewable_balance_municipal', 'renewable_balance_finite_chk',
             ARRAY['published_generation_mwh', 'wind_capacity_mw',
                   'renewable_capacity_mw', 'renewable_net_addition_mw'])
        ) AS t(table_name, constraint_name, numeric_columns)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = target.constraint_name
              AND conrelid = format('raw.%I', target.table_name)::regclass
        ) THEN
            EXECUTE format(
                'ALTER TABLE raw.%I ADD CONSTRAINT %I CHECK (%s) NOT VALID',
                target.table_name,
                target.constraint_name,
                (
                    SELECT string_agg(
                        format(
                            '(%1$I IS NULL OR %1$I NOT IN'
                            ' (''NaN''::numeric, ''Infinity''::numeric, ''-Infinity''::numeric))',
                            column_name
                        ),
                        ' AND '
                    )
                    FROM unnest(target.numeric_columns) AS column_name
                )
            );
        END IF;
    END LOOP;
END
$$;

CREATE INDEX IF NOT EXISTS raw_energy_consumption_nuts_year_idx
    ON raw.energy_consumption_municipal (nuts_code, year);
CREATE INDEX IF NOT EXISTS raw_renewable_balance_nuts_year_idx
    ON raw.renewable_balance_municipal (nuts_code, year);

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

-- raw.roads carries Strassen.NRW source classes (STRKL): A Bundesautobahn,
-- B Bundesstrasse, L Landesstrasse, K Kreisstrasse.  The published
-- accessibility layer is defined in terms of the OpenStreetMap-style classes
-- listed as major_road_classes in config/regions/nrw.yml, so the source class
-- is mapped explicitly instead of being compared against a vocabulary it never
-- uses.  Already normalised values pass through unchanged so that
-- OpenStreetMap-sourced fixtures and imports keep working.
CREATE OR REPLACE FUNCTION staging.normalize_road_class(source_class text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
SELECT CASE upper(btrim(COALESCE(source_class, '')))
    WHEN 'A' THEN 'motorway'
    WHEN 'B' THEN 'primary'
    WHEN 'L' THEN 'secondary'
    WHEN 'K' THEN 'tertiary'
    ELSE NULLIF(lower(btrim(COALESCE(source_class, ''))), '')
END;
$$;

CREATE OR REPLACE VIEW staging.nrw_roads AS
SELECT r.*, staging.normalize_road_class(r.road_class) AS road_class_normalized
FROM raw.roads r
JOIN staging.nrw_boundary b
  ON ST_Intersects(r.geom, ST_Buffer(b.geom::geography, 10000)::geometry)
WHERE staging.normalize_road_class(r.road_class)
      IN ('motorway', 'trunk', 'primary', 'secondary');

-- A point exactly on a shared district boundary is covered by both neighbours,
-- so containment alone would count the same station twice.  Every station is
-- assigned to exactly one district, deterministically the lowest NUTS code.
-- The ingestion validator applies the same ownership rule.  The proposal
-- trigger deliberately does not: an operator placing a new station exactly on a
-- district line is asked to move it rather than having a district chosen for
-- them, so an ambiguous insert is still rejected.
CREATE OR REPLACE VIEW staging.nrw_charger_districts AS
SELECT DISTINCT ON (c.source_id)
    c.source_id,
    d.nuts_code
FROM raw.chargers c
JOIN staging.nrw_districts d
  ON ST_Covers(d.geom, c.geom)
ORDER BY c.source_id, d.nuts_code;

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
    ST_Area(d.geom_25832) / 1000000.0 AS area_km2,
    p.population,
    -- The population figure is only interpretable with the year it describes,
    -- so the reference year and the registry it came from travel with it into
    -- every district projection (contract C02, manifest 9.3).
    p.reference_year AS population_source_year,
    p.source AS population_source,
    COUNT(c.source_id) AS chargers_total,
    COALESCE(
        SUM(COALESCE(c.charging_points, 1)) FILTER (WHERE c.source_id IS NOT NULL),
        0
    ) AS charging_points_total,
    COUNT(c.source_id) FILTER (WHERE c.max_point_power_kw >= 50) AS fast_chargers_total,
    COUNT(c.source_id) FILTER (WHERE c.max_point_power_kw < 50) AS normal_chargers_total,
    COUNT(c.source_id) FILTER (WHERE c.max_point_power_kw IS NULL) AS unknown_power_chargers_total,
    nearest.distance_to_nearest_charger_m,
    d.geom
FROM staging.nrw_districts d
LEFT JOIN staging.nrw_charger_districts a
  ON a.nuts_code = d.nuts_code
LEFT JOIN raw.chargers c
  ON c.source_id = a.source_id
LEFT JOIN LATERAL (
    -- Selection and measurement share EPSG:25832: the KNN ordering runs on the
    -- stored projected geometry, so the returned station is the one that is
    -- nearest in metres.  No charger at all leaves the distance NULL, which is
    -- an unavailable distance rather than a zero or an infinity.
    SELECT ST_Distance(d.centroid_25832, cn.geom_25832) AS distance_to_nearest_charger_m
    FROM raw.chargers cn
    ORDER BY d.centroid_25832 <-> cn.geom_25832
    LIMIT 1
) nearest ON true
LEFT JOIN raw.population p
  ON p.nuts_code = d.nuts_code OR p.ags = d.ags
GROUP BY d.nuts_code, d.ags, d.district_name, p.population,
         p.reference_year, p.source,
         nearest.distance_to_nearest_charger_m, d.geom, d.geom_25832;

-- publish.nrw_district_priority used to be defined twice with different
-- columns: here over analytics.nrw_priority_scores (bare district counts) and
-- again in db/nrw_analytics.sql over the scored baseline, so whichever document
-- ran last decided what the published layer meant (finding F20).  The scored
-- definition is the authoritative one and now lives only in db/nrw_analytics.sql.
-- Both obsolete relations are dropped here so an upgraded installation cannot
-- keep serving the unscored layout; the analytics document recreates the single
-- canonical view in the same load.  No CASCADE: if an unknown dependant exists
-- the upgrade aborts and rolls back rather than destroying it.
DROP VIEW IF EXISTS publish.nrw_district_priority;
DROP VIEW IF EXISTS analytics.nrw_priority_scores;

-- Historical published column layouts ------------------------------------
--
-- The published views below fix an explicit public field order.  A database
-- upgraded from before a column existed carries it wherever ALTER TABLE ADD
-- COLUMN appended it: an installation that predates max_point_power_kw has it
-- after geom, not after power_kw.  CREATE OR REPLACE VIEW can only append
-- columns, never rename or reorder them, so such a view has to be replaced
-- deliberately.
--
-- Only the mismatched view is dropped, and without CASCADE: if anything
-- depends on it the upgrade aborts and rolls back rather than destroying the
-- dependency.  Privileges are captured first and restored once the new
-- definition exists, so a reader role keeps its access across the upgrade.
CREATE TEMP TABLE nrw_publish_view_privileges (
    view_name text NOT NULL,
    grantee text NOT NULL,
    privilege_type text NOT NULL,
    is_grantable boolean NOT NULL
);

DO $$
DECLARE
    target record;
    existing_columns text[];
BEGIN
    FOR target IN
        SELECT *
        FROM (VALUES
            ('nrw_chargers', ARRAY[
                'source_id', 'operator', 'status', 'charger_type', 'charging_points',
                'power_kw', 'max_point_power_kw', 'street', 'postcode', 'city',
                'district_text', 'bundesland', 'geom']),
            ('nrw_grid_readiness', ARRAY[
                'source_id', 'asset_type', 'voltage', 'name', 'geom']),
            ('nrw_renewable_potential', ARRAY[
                'source_id', 'name', 'operator', 'asset_type', 'technology',
                'capacity_mw', 'status', 'geom']),
            ('nrw_accessibility', ARRAY[
                'osm_id', 'road_class', 'name', 'road_number', 'traffic_total',
                'traffic_light', 'traffic_heavy', 'source', 'geom',
                'road_class_normalized'])
        ) AS t(view_name, expected_columns)
    LOOP
        SELECT array_agg(a.attname::text ORDER BY a.attnum)
        INTO existing_columns
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = 'publish'
          AND c.relname = target.view_name
          AND c.relkind = 'v'
          AND a.attnum > 0
          AND NOT a.attisdropped;

        -- Absent on a fresh installation; already correct after a current run.
        CONTINUE WHEN existing_columns IS NULL;
        -- CREATE OR REPLACE VIEW handles the append-only case on its own.
        CONTINUE WHEN target.expected_columns[1:array_length(existing_columns, 1)]
                      = existing_columns;

        INSERT INTO nrw_publish_view_privileges (
            view_name, grantee, privilege_type, is_grantable
        )
        SELECT
            target.view_name,
            CASE WHEN acl.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(acl.grantee) END,
            acl.privilege_type,
            acl.is_grantable
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) AS acl
        WHERE n.nspname = 'publish' AND c.relname = target.view_name;

        RAISE NOTICE 'Replacing publish.% to restore the published field order', target.view_name;
        EXECUTE format('DROP VIEW publish.%I', target.view_name);
    END LOOP;
END
$$;

-- Published layers expose one geometry column only; geom_25832 is an internal
-- analytical companion and must not reach the service layer.
CREATE OR REPLACE VIEW publish.nrw_chargers AS
SELECT
    source_id,
    operator,
    status,
    charger_type,
    charging_points,
    power_kw,
    max_point_power_kw,
    street,
    postcode,
    city,
    district_text,
    bundesland,
    geom
FROM staging.nrw_chargers;

CREATE OR REPLACE VIEW publish.nrw_grid_readiness AS
SELECT
    source_id,
    asset_type,
    voltage,
    name,
    geom
FROM staging.nrw_grid;

CREATE OR REPLACE VIEW publish.nrw_renewable_potential AS
SELECT
    source_id,
    name,
    operator,
    asset_type,
    technology,
    capacity_mw,
    status,
    geom
FROM staging.nrw_renewables;

CREATE OR REPLACE VIEW publish.nrw_accessibility AS
SELECT
    osm_id,
    road_class,
    name,
    road_number,
    traffic_total,
    traffic_light,
    traffic_heavy,
    source,
    geom,
    road_class_normalized
FROM staging.nrw_roads;

DO $$
DECLARE
    saved record;
BEGIN
    FOR saved IN SELECT * FROM nrw_publish_view_privileges LOOP
        IF saved.privilege_type NOT IN (
            'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'
        ) THEN
            RAISE EXCEPTION 'Unexpected privilege % on publish.%',
                saved.privilege_type, saved.view_name;
        END IF;
        IF saved.grantee <> 'PUBLIC'
           AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = saved.grantee) THEN
            CONTINUE;
        END IF;
        EXECUTE format(
            'GRANT %s ON publish.%I TO %s%s',
            saved.privilege_type,
            saved.view_name,
            CASE WHEN saved.grantee = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(saved.grantee) END,
            CASE WHEN saved.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END
        );
    END LOOP;
END
$$;

DROP TABLE nrw_publish_view_privileges;

COMMIT;
