-- Applied as one transaction: the refresh below drops every analytical
-- relation with CASCADE before rebuilding it, so a partial run would
-- otherwise leave the installation without its published analytics.
--
-- One score model, one place.  Measurements are materialized; every score is a
-- live view over them, so a published composite can never disagree with the
-- published components it is made of (findings F09, F10, F20).  The complete
-- model -- indicator, component, weight, measured quantity, normalization
-- bounds and formula version -- is itself published as
-- analytics.nrw_score_model, and db/verify_nrw_analytics.sql recomputes every
-- district's composites from that relation rather than from a second copy of
-- the arithmetic.
BEGIN;

CREATE OR REPLACE FUNCTION analytics.normalize_5_95(
    metric double precision,
    lower_bound double precision,
    upper_bound double precision,
    inverse_metric boolean DEFAULT false
) RETURNS numeric
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
SELECT (CASE
    WHEN metric IS NULL OR lower_bound IS NULL OR upper_bound IS NULL THEN NULL
    WHEN upper_bound = lower_bound THEN 50.0
    WHEN inverse_metric THEN
        100.0 - 100.0 * (LEAST(GREATEST(metric, lower_bound), upper_bound) - lower_bound)
        / (upper_bound - lower_bound)
    ELSE
        100.0 * (LEAST(GREATEST(metric, lower_bound), upper_bound) - lower_bound)
        / (upper_bound - lower_bound)
END)::numeric;
$$;

-- S08 splits each analytical relation into a materialized measurement and a
-- live scored view, so several names change relation kind on an upgrade.
-- DROP MATERIALIZED VIEW refuses a plain view and DROP VIEW refuses a
-- materialized one, so the actual kind decides the statement.  A name that does
-- not exist yet is skipped.
DO $$
DECLARE
    target text;
    target_schema text;
    target_relation text;
    target_kind "char";
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'publish.nrw_district_priority',
        'publish.nrw_ev_scenario_metrics',
        'publish.nrw_ev_baseline_metrics',
        'publish.nrw_score_model',
        'publish.nrw_infrastructure_opportunity',
        'publish.nrw_grid_absorption_risk',
        'publish.nrw_local_energy_balance',
        'publish.nrw_renewable_context',
        'publish.nrw_grid_proxy',
        'publish.nrw_transport_load',
        'analytics.nrw_score_model',
        'analytics.nrw_ev_scenario_metrics',
        'analytics.nrw_ev_baseline_metrics',
        'analytics.nrw_ev_baseline_bounds',
        'analytics.nrw_ev_baseline_raw',
        'analytics.nrw_infrastructure_opportunity',
        'analytics.nrw_local_energy_balance',
        'analytics.nrw_energy_bounds',
        'analytics.nrw_energy_balance_raw',
        'analytics.nrw_renewable_metrics',
        'analytics.nrw_renewable_bounds',
        'analytics.nrw_renewable_raw',
        'analytics.nrw_grid_proxy_metrics',
        'analytics.nrw_grid_proxy_bounds',
        'analytics.nrw_grid_proxy_raw',
        'analytics.nrw_transport_metrics',
        'analytics.nrw_transport_bounds',
        'analytics.nrw_transport_raw',
        'analytics.nrw_formula_version',
        -- Retired with finding F20: an unscored second definition of the
        -- published district-priority layer.
        'analytics.nrw_priority_scores'
    ]
    LOOP
        target_schema := split_part(target, '.', 1);
        target_relation := split_part(target, '.', 2);
        target_kind := NULL;
        SELECT c.relkind
        INTO target_kind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = target_schema
          AND c.relname = target_relation;

        IF target_kind = 'm' THEN
            EXECUTE format('DROP MATERIALIZED VIEW %I.%I CASCADE', target_schema, target_relation);
        ELSIF target_kind = 'v' THEN
            EXECUTE format('DROP VIEW %I.%I CASCADE', target_schema, target_relation);
        END IF;
    END LOOP;
END
$$;

-- The published formula version.  Every analytical output carries it so that a
-- snapshot, a screenshot and a database row can be told apart when the model
-- changes (contract C02, manifest section 9.3).
CREATE VIEW analytics.nrw_formula_version AS
SELECT
    'nrw-2026.09.1'::text AS formula_version,
    DATE '2026-09-09' AS formula_version_date,
    'unversioned pre-S08 model'::text AS supersedes_formula_version,
    'EV readiness density is charging points per km2 (was stations per km2); '
    'every composite is the weighted sum of its published one-decimal component '
    'scores; normalization bounds are published in analytics.nrw_score_model.'
        ::text AS formula_version_note;

-- What the traffic measure actually is, taken from the publisher's own field
-- description (Open Data - Datenbeschreibung zum Datenbestand Strassennetz
-- Landesbetrieb Strassenbau NRW, 02.07.2025, "Verkehrswerte"):
--   DTVKFZA "Wert der durchschnittlichen taeglichen Verkehrsstaerke (DTV) fuer
--            KFZ-Verkehr alle Tage"
--   DTVLVA  "DTV-Wert fuer Leichtverkehr alle Tage (Krad, PKW und Lieferwagen)"
--   DTVSVA  "DTV-Wert fuer Schwerverkehr alle Tage (Busse, LKW >3,5t zul. GG
--            und Lastzuege)"
-- The same document states that Autobahn data is no longer included, which is
-- why the counted network covers Bundes-, Landes- and Kreisstrassen only.
CREATE OR REPLACE VIEW analytics.nrw_traffic_assumptions AS
SELECT
    'DTVKFZA'::text AS traffic_measure_field,
    'Average daily traffic volume, all motor vehicles, all days'::text AS traffic_measure,
    'vehicles per day'::text AS traffic_measure_unit,
    'Bundes-, Landes- and Kreisstrassen; Autobahn traffic is not part of this source'::text
        AS traffic_network_scope,
    'Strassen.NRW Verkehrswerte (Strassenverkehrszaehlung SVZ)'::text AS traffic_source,
    'Open Data - Datenbeschreibung Strassennetz Landesbetrieb Strassenbau NRW, 02.07.2025'::text
        AS traffic_source_documentation;

-- Transport ---------------------------------------------------------------
-- The measured quantities are materialized because they need line
-- intersections against every district; the bounds and the scores derived from
-- them are views, so they cannot go stale against the measurements.
CREATE MATERIALIZED VIEW analytics.nrw_transport_raw AS
WITH road_parts AS (
    SELECT
        d.nuts_code,
        ST_Length(ST_Intersection(d.geom_25832, r.geom_25832)) / 1000.0 AS length_km,
        r.traffic_total
    FROM staging.nrw_districts d
    JOIN raw.roads r ON ST_Intersects(d.geom, r.geom)
)
SELECT
    d.nuts_code,
    COALESCE(SUM(p.length_km), 0) AS traffic_road_length_km,
    -- A section whose counting station published no value contributes
    -- length but no traffic, so the measured share has to travel with the
    -- metric instead of the absence quietly lowering it.
    COALESCE(SUM(p.length_km) FILTER (WHERE p.traffic_total IS NOT NULL), 0)
        AS traffic_measured_length_km,
    SUM(p.length_km) FILTER (WHERE p.traffic_total IS NOT NULL)
        / NULLIF(SUM(p.length_km), 0) AS traffic_length_coverage,
    SUM(p.length_km * p.traffic_total)
        / NULLIF(SUM(p.length_km) FILTER (WHERE p.traffic_total IS NOT NULL), 0)
        AS traffic_intensity_dtv,
    SUM(p.length_km * p.traffic_total) / NULLIF(m.area_km2, 0)
        AS traffic_weighted_road_density,
    nearest.distance_to_nearest_road_m
FROM staging.nrw_districts d
JOIN analytics.nrw_district_metrics m USING (nuts_code)
LEFT JOIN road_parts p USING (nuts_code)
LEFT JOIN LATERAL (
    -- Projected KNN ordering and projected measurement, so the reported
    -- distance belongs to the road that is actually nearest in metres.
    SELECT ST_Distance(d.centroid_25832, r.geom_25832) AS distance_to_nearest_road_m
    FROM raw.roads r
    ORDER BY d.centroid_25832 <-> r.geom_25832
    LIMIT 1
) nearest ON true
GROUP BY d.nuts_code, m.area_km2, nearest.distance_to_nearest_road_m;

CREATE UNIQUE INDEX nrw_transport_raw_nuts_uq
    ON analytics.nrw_transport_raw (nuts_code);

CREATE VIEW analytics.nrw_transport_bounds AS
SELECT
    percentile_cont(0.05) WITHIN GROUP (ORDER BY traffic_intensity_dtv) AS intensity_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY traffic_intensity_dtv) AS intensity_high,
    percentile_cont(0.05) WITHIN GROUP (ORDER BY traffic_weighted_road_density) AS density_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY traffic_weighted_road_density) AS density_high,
    percentile_cont(0.05) WITHIN GROUP (ORDER BY distance_to_nearest_road_m) AS distance_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY distance_to_nearest_road_m) AS distance_high
FROM analytics.nrw_transport_raw;

CREATE VIEW analytics.nrw_transport_metrics AS
WITH components AS (
    SELECT
        a.*,
        ROUND(analytics.normalize_5_95(
            a.traffic_intensity_dtv, b.intensity_low, b.intensity_high
        ), 1) AS traffic_intensity_score,
        ROUND(analytics.normalize_5_95(
            a.traffic_weighted_road_density, b.density_low, b.density_high
        ), 1) AS traffic_road_density_score,
        ROUND(analytics.normalize_5_95(
            a.distance_to_nearest_road_m, b.distance_low, b.distance_high, true
        ), 1) AS road_proximity_score
    FROM analytics.nrw_transport_raw a
    CROSS JOIN analytics.nrw_transport_bounds b
)
SELECT
    c.*,
    -- Weighted from the published component scores, so the composite a reader
    -- sees is exactly the one they can recompute from the components next to
    -- it.  A NULL component propagates: weights are never redistributed.
    ROUND(
        0.60 * c.traffic_intensity_score
        + 0.25 * c.traffic_road_density_score
        + 0.15 * c.road_proximity_score,
        1
    ) AS transport_load_score,
    CASE
        WHEN c.traffic_road_length_km = 0 THEN 'no_counted_road_network'
        WHEN c.traffic_measured_length_km = 0 THEN 'no_published_traffic_value'
        WHEN c.traffic_length_coverage < 1 THEN 'partial_traffic_coverage'
        ELSE 'complete_traffic_coverage'
    END AS transport_data_quality_flag
FROM components c;

-- Grid readiness proxy -----------------------------------------------------
CREATE MATERIALIZED VIEW analytics.nrw_grid_proxy_raw AS
WITH parsed_grid AS (
    SELECT
        g.*,
        (
            -- An untagged, unparseable or non-positive OpenStreetMap voltage is
            -- an unknown voltage, not a measured zero (contract C03).
            --
            -- The whole token has to match a recognised form.  An unsigned
            -- substring search would read "-110000" as 110 kV and would accept
            -- any number embedded in arbitrary text, so a negative or malformed
            -- tag would become positive measured evidence.  Semicolon-separated
            -- tags keep their valid parts: the highest usable value wins.
            SELECT MAX(parsed.voltage_kv)
            FROM (
                SELECT
                    CASE
                        WHEN btrim(token) ~ '^-?[0-9]+(\.[0-9]+)?$'
                        THEN btrim(token)::numeric / 1000.0
                        WHEN btrim(token) ~* '^-?[0-9]+(\.[0-9]+)?[[:space:]]*kv$'
                        THEN substring(btrim(token) FROM '^(-?[0-9]+(?:\.[0-9]+)?)')::numeric
                    END AS voltage_kv
                FROM regexp_split_to_table(COALESCE(g.voltage, ''), ';') AS token
            ) parsed
            WHERE parsed.voltage_kv > 0
        ) AS voltage_kv
    FROM raw.grid_infrastructure g
),
line_parts AS (
    SELECT
        d.nuts_code,
        ST_Length(ST_Intersection(d.geom_25832, g.geom_25832)) / 1000.0 AS length_km,
        g.voltage_kv
    FROM staging.nrw_districts d
    JOIN parsed_grid g
      ON g.asset_type IN ('line', 'cable', 'minor_line')
     AND ST_Intersects(d.geom, g.geom)
),
line_agg AS (
    SELECT
        nuts_code,
        SUM(length_km) AS grid_line_length_km,
        SUM(length_km) FILTER (WHERE voltage_kv IS NOT NULL)
            AS grid_line_length_km_known_voltage,
        SUM(length_km * voltage_kv) AS voltage_weighted_line_kv_km,
        COUNT(*) AS grid_line_segments,
        COUNT(voltage_kv)::numeric / NULLIF(COUNT(*), 0) AS line_voltage_coverage
    FROM line_parts
    GROUP BY nuts_code
),
substation_agg AS (
    SELECT
        d.nuts_code,
        COUNT(g.source_id) AS substation_count,
        MAX(g.voltage_kv) AS maximum_mapped_voltage_kv,
        COUNT(g.voltage_kv)::numeric / NULLIF(COUNT(g.source_id), 0) AS substation_voltage_coverage
    FROM staging.nrw_districts d
    LEFT JOIN parsed_grid g
      ON g.asset_type IN ('substation', 'transformer')
     AND ST_Intersects(d.geom, g.geom)
    GROUP BY d.nuts_code
)
SELECT
    d.nuts_code,
    COALESCE(l.grid_line_length_km, 0) AS grid_line_length_km,
    COALESCE(l.grid_line_length_km_known_voltage, 0)
        AS grid_line_length_km_known_voltage,
    COALESCE(l.grid_line_segments, 0) AS grid_line_segments,
    COALESCE(s.substation_count, 0) AS substation_count,
    s.maximum_mapped_voltage_kv,
    l.line_voltage_coverage,
    s.substation_voltage_coverage,
    -- No COALESCE to zero: with no voltage-tagged line anywhere in the
    -- district there is no voltage evidence at all, so the component is
    -- unavailable and the proxy composite stays unavailable with it rather
    -- than being computed from an invented zero.
    l.voltage_weighted_line_kv_km / NULLIF(m.area_km2, 0)
        AS voltage_weighted_line_density,
    COALESCE(s.substation_count, 0) / NULLIF(m.area_km2, 0) AS substation_density,
    nearest.distance_to_nearest_substation_m
FROM staging.nrw_districts d
JOIN analytics.nrw_district_metrics m USING (nuts_code)
LEFT JOIN line_agg l USING (nuts_code)
LEFT JOIN substation_agg s USING (nuts_code)
LEFT JOIN LATERAL (
    SELECT ST_Distance(d.centroid_25832, g.geom_25832) AS distance_to_nearest_substation_m
    FROM raw.grid_infrastructure g
    WHERE g.asset_type IN ('substation', 'transformer')
    ORDER BY d.centroid_25832 <-> g.geom_25832
    LIMIT 1
) nearest ON true;

CREATE UNIQUE INDEX nrw_grid_proxy_raw_nuts_uq
    ON analytics.nrw_grid_proxy_raw (nuts_code);

CREATE VIEW analytics.nrw_grid_proxy_bounds AS
SELECT
    percentile_cont(0.05) WITHIN GROUP (ORDER BY distance_to_nearest_substation_m) AS distance_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY distance_to_nearest_substation_m) AS distance_high,
    percentile_cont(0.05) WITHIN GROUP (ORDER BY voltage_weighted_line_density) AS line_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY voltage_weighted_line_density) AS line_high,
    percentile_cont(0.05) WITHIN GROUP (ORDER BY substation_density) AS substation_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY substation_density) AS substation_high
FROM analytics.nrw_grid_proxy_raw;

CREATE VIEW analytics.nrw_grid_proxy_metrics AS
WITH components AS (
    SELECT
        a.*,
        -- The three weighted components are published in their own right: the
        -- interface has to be able to show the formula that produced the proxy,
        -- and the verifier recomputes the proxy from exactly these values.
        ROUND(analytics.normalize_5_95(
            a.distance_to_nearest_substation_m, b.distance_low, b.distance_high, true
        ), 1) AS substation_proximity_score,
        ROUND(analytics.normalize_5_95(
            a.voltage_weighted_line_density, b.line_low, b.line_high
        ), 1) AS voltage_line_density_score,
        ROUND(analytics.normalize_5_95(
            a.substation_density, b.substation_low, b.substation_high
        ), 1) AS substation_density_score
    FROM analytics.nrw_grid_proxy_raw a
    CROSS JOIN analytics.nrw_grid_proxy_bounds b
)
SELECT
    c.*,
    ROUND(
        0.45 * c.substation_proximity_score
        + 0.35 * c.voltage_line_density_score
        + 0.20 * c.substation_density_score,
        1
    ) AS grid_readiness_proxy_score,
    -- The flag has to explain every reason the proxy can be unavailable, not
    -- only the voltage one: substation proximity carries 45% of the score, so
    -- an unmapped substation leaves it unavailable just as an untagged line
    -- does.  The first three values are exactly the cases where the score is
    -- NULL; partial voltage coverage is published together with its coverage.
    CASE
        WHEN c.distance_to_nearest_substation_m IS NULL THEN 'no_mapped_substation'
        WHEN c.grid_line_segments = 0 THEN 'no_mapped_grid_lines'
        WHEN COALESCE(c.line_voltage_coverage, 0) = 0 THEN 'unknown_line_voltage'
        WHEN c.line_voltage_coverage < 1 THEN 'partial_line_voltage'
        ELSE 'complete_grid_inputs'
    END AS grid_data_quality_flag
FROM components c;

-- Renewable context --------------------------------------------------------
CREATE MATERIALIZED VIEW analytics.nrw_renewable_raw AS
SELECT
    d.nuts_code,
    COUNT(r.source_id) AS renewable_installation_count,
    COALESCE(SUM(r.capacity_mw), 0) AS renewable_capacity_mw,
    COUNT(DISTINCT r.technology) FILTER (WHERE r.capacity_mw > 0) AS renewable_technology_count,
    COALESCE(SUM(r.capacity_mw), 0) / NULLIF(m.area_km2, 0) AS renewable_capacity_mw_per_km2
FROM staging.nrw_districts d
JOIN analytics.nrw_district_metrics m USING (nuts_code)
LEFT JOIN raw.renewable_assets r
  ON r.status = 'In Betrieb'
 AND ST_Intersects(d.geom, r.geom)
GROUP BY d.nuts_code, m.area_km2;

CREATE UNIQUE INDEX nrw_renewable_raw_nuts_uq
    ON analytics.nrw_renewable_raw (nuts_code);

CREATE VIEW analytics.nrw_renewable_bounds AS
SELECT
    percentile_cont(0.05) WITHIN GROUP (ORDER BY renewable_capacity_mw_per_km2) AS capacity_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY renewable_capacity_mw_per_km2) AS capacity_high,
    percentile_cont(0.05) WITHIN GROUP (ORDER BY renewable_technology_count) AS technology_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY renewable_technology_count) AS technology_high
FROM analytics.nrw_renewable_raw;

CREATE VIEW analytics.nrw_renewable_metrics AS
WITH components AS (
    SELECT
        a.*,
        ROUND(analytics.normalize_5_95(
            a.renewable_capacity_mw_per_km2, b.capacity_low, b.capacity_high
        ), 1) AS renewable_capacity_density_score,
        ROUND(analytics.normalize_5_95(
            a.renewable_technology_count, b.technology_low, b.technology_high
        ), 1) AS renewable_technology_diversity_score
    FROM analytics.nrw_renewable_raw a
    CROSS JOIN analytics.nrw_renewable_bounds b
)
SELECT
    c.*,
    ROUND(
        0.70 * c.renewable_capacity_density_score
        + 0.30 * c.renewable_technology_diversity_score,
        1
    ) AS renewable_context_score
FROM components c;

-- Local energy balance -----------------------------------------------------
CREATE OR REPLACE VIEW analytics.nrw_energy_assumptions AS
SELECT
    2000::numeric AS wind_full_load_hours,
    3::integer AS renewable_growth_window_years,
    'Wind generation planning assumption; replace when reviewed direct generation data is available'
        ::text AS wind_estimate_note;

-- The materialized part is the municipal aggregation and its coverage
-- bookkeeping only.  The grid readiness proxy this composite also needs is
-- joined live in the scored view below, so an absorption-risk score can never
-- be built from a stale copy of a proxy score that is published elsewhere with
-- a different value.
CREATE MATERIALIZED VIEW analytics.nrw_energy_balance_raw AS
WITH reporting AS (
    SELECT MAX(c.year) AS reporting_year
    FROM (
        SELECT DISTINCT year
        FROM raw.energy_consumption_municipal
    ) c
    JOIN (
        SELECT DISTINCT year
        FROM raw.renewable_balance_municipal
        WHERE published_generation_mwh IS NOT NULL
          AND wind_capacity_mw IS NOT NULL
    ) r USING (year)
),
-- Every municipality the source has ever reported for a district.  Coverage is
-- measured against this universe so that a year missing a municipality is
-- visible instead of silently producing a smaller district total.
municipal_universe AS (
    SELECT nuts_code, COUNT(DISTINCT ags) AS expected_municipalities
    FROM (
        SELECT nuts_code, ags FROM raw.energy_consumption_municipal
        UNION
        SELECT nuts_code, ags FROM raw.renewable_balance_municipal
    ) municipalities
    GROUP BY nuts_code
),
consumption AS (
    SELECT
        c.nuts_code,
        COUNT(*) FILTER (WHERE c.consumption_gwh IS NOT NULL) AS municipalities_reported,
        SUM(c.consumption_gwh) * 1000.0 AS consumption_mwh_reported,
        MIN(c.source) AS energy_source
    FROM raw.energy_consumption_municipal c
    CROSS JOIN reporting y
    WHERE c.year = y.reporting_year
    GROUP BY c.nuts_code
),
-- Each published total is gated by the coverage of the field it is actually
-- made of.  Generation and wind capacity together feed the balance ratio, while
-- installed renewable capacity is separate context: a municipality can report
-- its yield and wind capacity while its total technology capacity is unknown,
-- and summing over that gap would publish a short total as a complete one.
renewable_stock AS (
    SELECT
        r.nuts_code,
        COUNT(*) FILTER (
            WHERE r.published_generation_mwh IS NOT NULL AND r.wind_capacity_mw IS NOT NULL
        ) AS municipalities_reported,
        COUNT(*) FILTER (WHERE r.renewable_capacity_mw IS NOT NULL)
            AS capacity_municipalities_reported,
        SUM(r.published_generation_mwh) AS published_generation_mwh_reported,
        SUM(r.wind_capacity_mw) AS wind_capacity_mw_reported,
        SUM(r.renewable_capacity_mw) AS renewable_capacity_mw_reported,
        COALESCE(SUM(r.generation_components_unknown), 0) AS generation_components_unknown
    FROM raw.renewable_balance_municipal r
    CROSS JOIN reporting y
    WHERE r.year = y.reporting_year
    GROUP BY r.nuts_code
),
-- Three-year growth needs the complete required series: a year counts only when
-- every expected municipality reported a net addition for it.
growth_by_year AS (
    SELECT
        r.nuts_code,
        r.year,
        COUNT(*) FILTER (WHERE r.renewable_net_addition_mw IS NOT NULL)
            AS municipalities_reported,
        SUM(r.renewable_net_addition_mw) AS net_addition_mw
    FROM raw.renewable_balance_municipal r
    CROSS JOIN reporting y
    CROSS JOIN analytics.nrw_energy_assumptions a
    WHERE r.year BETWEEN
        y.reporting_year - (a.renewable_growth_window_years - 1)
        AND y.reporting_year
    GROUP BY r.nuts_code, r.year
),
renewable_growth AS (
    SELECT
        g.nuts_code,
        COUNT(*) FILTER (WHERE g.municipalities_reported = u.expected_municipalities)
            AS growth_years_reported,
        SUM(g.net_addition_mw) AS net_addition_3y_mw_reported
    FROM growth_by_year g
    JOIN municipal_universe u USING (nuts_code)
    GROUP BY g.nuts_code
)
SELECT
    d.nuts_code,
    d.ags,
    d.district_name,
    y.reporting_year,
    m.area_km2,
    u.expected_municipalities,
    COALESCE(c.municipalities_reported, 0) AS consumption_municipalities_reported,
    COALESCE(s.municipalities_reported, 0) AS renewable_municipalities_reported,
    COALESCE(s.capacity_municipalities_reported, 0)
        AS renewable_capacity_municipalities_reported,
    a.renewable_growth_window_years AS growth_years_required,
    COALESCE(g.growth_years_reported, 0) AS growth_years_reported,
    COALESCE(s.generation_components_unknown, 0) AS generation_components_unknown,
    c.energy_source,
    -- A partial municipal sum is never published as a complete district
    -- total (contract C03); it becomes unavailable with a stated reason.
    consumption_complete.value AS consumption_mwh,
    stock_complete.published_generation_mwh,
    stock_complete.wind_capacity_mw,
    stock_complete.wind_capacity_mw * a.wind_full_load_hours
        AS estimated_wind_generation_mwh,
    stock_complete.published_generation_mwh
        + stock_complete.wind_capacity_mw * a.wind_full_load_hours
        AS total_renewable_generation_mwh,
    stock_complete.renewable_capacity_mw,
    growth_complete.value AS renewable_net_addition_3y_mw,
    growth_complete.value / NULLIF(m.area_km2, 0)
        AS renewable_growth_density_mw_per_km2,
    -- A fully reported consumption of zero is a measured value and is
    -- published as one, but nothing can be divided by it, so the ratio and
    -- every score built on it are unavailable with a stated reason.  The same
    -- holds for any non-positive denominator, so that a score is missing
    -- exactly when its reason says so.
    CASE WHEN consumption_complete.value > 0 THEN
        (
            stock_complete.published_generation_mwh
            + stock_complete.wind_capacity_mw * a.wind_full_load_hours
        ) / consumption_complete.value
    END AS renewable_balance_ratio,
    a.wind_full_load_hours,
    d.geom
FROM staging.nrw_districts d
CROSS JOIN reporting y
CROSS JOIN analytics.nrw_energy_assumptions a
JOIN analytics.nrw_district_metrics m USING (nuts_code)
LEFT JOIN municipal_universe u USING (nuts_code)
LEFT JOIN consumption c USING (nuts_code)
LEFT JOIN renewable_stock s USING (nuts_code)
LEFT JOIN renewable_growth g USING (nuts_code)
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN c.municipalities_reported IS NOT NULL
         AND c.municipalities_reported = u.expected_municipalities
        THEN c.consumption_mwh_reported
    END AS value
) consumption_complete
CROSS JOIN LATERAL (
    SELECT
        CASE WHEN completeness.generation_complete
             THEN s.published_generation_mwh_reported END AS published_generation_mwh,
        CASE WHEN completeness.generation_complete
             THEN s.wind_capacity_mw_reported END AS wind_capacity_mw,
        CASE WHEN completeness.capacity_complete
             THEN s.renewable_capacity_mw_reported END AS renewable_capacity_mw
    FROM (
        SELECT
            s.municipalities_reported IS NOT NULL
                AND s.municipalities_reported = u.expected_municipalities
                AS generation_complete,
            s.capacity_municipalities_reported IS NOT NULL
                AND s.capacity_municipalities_reported = u.expected_municipalities
                AS capacity_complete
    ) completeness
) stock_complete
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN g.growth_years_reported = a.renewable_growth_window_years
        THEN g.net_addition_3y_mw_reported
    END AS value
) growth_complete;

CREATE UNIQUE INDEX nrw_energy_balance_raw_nuts_uq
    ON analytics.nrw_energy_balance_raw (nuts_code);
CREATE INDEX nrw_energy_balance_raw_geom_gix
    ON analytics.nrw_energy_balance_raw USING gist (geom);

CREATE VIEW analytics.nrw_energy_bounds AS
SELECT
    percentile_cont(0.05) WITHIN GROUP (ORDER BY renewable_balance_ratio)
        AS balance_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY renewable_balance_ratio)
        AS balance_high,
    percentile_cont(0.05) WITHIN GROUP (ORDER BY renewable_growth_density_mw_per_km2)
        AS growth_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY renewable_growth_density_mw_per_km2)
        AS growth_high
FROM analytics.nrw_energy_balance_raw;

CREATE VIEW analytics.nrw_local_energy_balance AS
WITH components AS (
    SELECT
        r.*,
        p.grid_readiness_proxy_score,
        ROUND(analytics.normalize_5_95(
            r.renewable_balance_ratio, b.balance_low, b.balance_high
        ), 1) AS local_energy_balance_score,
        ROUND(analytics.normalize_5_95(
            r.renewable_growth_density_mw_per_km2, b.growth_low, b.growth_high
        ), 1) AS renewable_growth_score
    FROM analytics.nrw_energy_balance_raw r
    CROSS JOIN analytics.nrw_energy_bounds b
    LEFT JOIN analytics.nrw_grid_proxy_metrics p USING (nuts_code)
)
SELECT
    nuts_code,
    ags,
    district_name,
    reporting_year,
    area_km2,
    expected_municipalities,
    consumption_municipalities_reported,
    consumption_municipalities_reported::numeric
        / NULLIF(expected_municipalities, 0) AS consumption_municipal_coverage,
    renewable_municipalities_reported,
    renewable_municipalities_reported::numeric
        / NULLIF(expected_municipalities, 0) AS renewable_municipal_coverage,
    renewable_capacity_municipalities_reported,
    renewable_capacity_municipalities_reported::numeric
        / NULLIF(expected_municipalities, 0) AS renewable_capacity_coverage,
    growth_years_required,
    growth_years_reported,
    generation_components_unknown,
    energy_source,
    consumption_mwh,
    published_generation_mwh,
    estimated_wind_generation_mwh,
    total_renewable_generation_mwh,
    renewable_capacity_mw,
    renewable_net_addition_3y_mw,
    renewable_growth_density_mw_per_km2,
    renewable_balance_ratio,
    renewable_balance_ratio * 100.0 AS renewable_coverage_pct,
    local_energy_balance_score,
    renewable_growth_score,
    grid_readiness_proxy_score,
    -- Weighted from the published component scores and nothing else, so a
    -- missing component propagates on its own and no weight is redistributed.
    ROUND(
        0.45 * local_energy_balance_score
        + 0.30 * renewable_growth_score
        + 0.25 * (100 - grid_readiness_proxy_score),
        1
    ) AS grid_absorption_risk_proxy_score,
    CASE
        WHEN local_energy_balance_score IS NULL
          OR renewable_growth_score IS NULL
          OR grid_readiness_proxy_score IS NULL
        THEN 'missing_required_input'
        WHEN wind_capacity_mw > 0 THEN 'hybrid_complete'
        ELSE 'published_complete_no_wind'
    END AS energy_data_quality_flag,
    -- Why a required input is unavailable, in the order the aggregation
    -- discovers it, so the interface can explain the gap instead of showing a
    -- bare em dash (contract C03; consumed by S15).
    CASE
        WHEN expected_municipalities IS NULL THEN 'no_municipal_energy_data'
        WHEN consumption_mwh IS NULL THEN 'incomplete_consumption_coverage'
        -- A fully reported consumption of zero is a measured value, kept as
        -- published, but nothing can be divided by it: the ratio and every
        -- score built on it are unavailable for a stated reason rather than
        -- silently missing.
        WHEN consumption_mwh <= 0 THEN 'zero_consumption_denominator'
        WHEN published_generation_mwh IS NULL OR wind_capacity_mw IS NULL
            THEN 'incomplete_renewable_coverage'
        WHEN renewable_net_addition_3y_mw IS NULL THEN 'incomplete_growth_window'
        WHEN grid_readiness_proxy_score IS NULL THEN 'unavailable_grid_readiness_proxy'
    END AS energy_unavailable_reason,
    -- Installed capacity is context rather than a composite input, so its own
    -- coverage decides its availability without invalidating a known yield.
    CASE
        WHEN expected_municipalities IS NULL THEN 'no_municipal_energy_data'
        WHEN renewable_capacity_mw IS NULL THEN 'incomplete_capacity_coverage'
    END AS renewable_capacity_unavailable_reason,
    wind_full_load_hours,
    geom
FROM components;

-- Infrastructure opportunity ------------------------------------------------
-- A live view: it is arithmetic over 53 rows, and materializing it would let a
-- published composite drift away from the components published beside it.
CREATE VIEW analytics.nrw_infrastructure_opportunity AS
SELECT
    d.nuts_code,
    d.ags,
    d.district_name,
    m.area_km2,
    m.population,
    m.chargers_total,
    m.charging_points_total,
    t.transport_load_score,
    g.grid_readiness_proxy_score,
    r.renewable_context_score,
    ROUND(
        0.40 * t.transport_load_score
        + 0.35 * g.grid_readiness_proxy_score
        + 0.25 * r.renewable_context_score,
        1
    ) AS infrastructure_opportunity_score,
    CASE
        WHEN t.transport_load_score IS NULL
          OR g.grid_readiness_proxy_score IS NULL
          OR r.renewable_context_score IS NULL
        THEN 'missing_required_component'
        ELSE 'complete_proxy_inputs'
    END AS infrastructure_data_quality_flag,
    d.geom
FROM staging.nrw_districts d
JOIN analytics.nrw_district_metrics m USING (nuts_code)
LEFT JOIN analytics.nrw_transport_metrics t USING (nuts_code)
LEFT JOIN analytics.nrw_grid_proxy_metrics g USING (nuts_code)
LEFT JOIN analytics.nrw_renewable_metrics r USING (nuts_code);

-- EV readiness baseline -----------------------------------------------------
CREATE VIEW analytics.nrw_ev_baseline_raw AS
SELECT
    m.nuts_code,
    m.ags,
    m.district_name,
    m.area_km2,
    m.population,
    m.population_source_year,
    m.population_source,
    m.chargers_total,
    m.charging_points_total,
    m.fast_chargers_total,
    m.normal_chargers_total,
    m.unknown_power_chargers_total,
    -- The readiness density is charging points per km2 (contract C02, A01,
    -- finding F10).  Stations per km2 stays published beside it as separately
    -- named station-density context and feeds no score.
    m.charging_points_total / NULLIF(m.area_km2, 0) AS charging_points_per_km2,
    m.chargers_total / NULLIF(m.area_km2, 0) AS chargers_per_km2,
    m.charging_points_total * 100000.0 / NULLIF(m.population, 0)
        AS charging_points_per_100k_population,
    m.distance_to_nearest_charger_m,
    m.geom
FROM analytics.nrw_district_metrics m;

-- The official baseline bounds.  Scenario scores reuse them unchanged, so
-- adding a proposal moves a district along a fixed scale instead of moving the
-- scale (contract C02).
CREATE VIEW analytics.nrw_ev_baseline_bounds AS
SELECT
    percentile_cont(0.05) WITHIN GROUP (ORDER BY charging_points_per_km2)
        AS charging_point_density_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY charging_points_per_km2)
        AS charging_point_density_high,
    percentile_cont(0.05) WITHIN GROUP (ORDER BY distance_to_nearest_charger_m)
        AS charger_distance_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY distance_to_nearest_charger_m)
        AS charger_distance_high,
    percentile_cont(0.05) WITHIN GROUP (ORDER BY charging_points_per_100k_population)
        AS population_coverage_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY charging_points_per_100k_population)
        AS population_coverage_high
FROM analytics.nrw_ev_baseline_raw;

CREATE VIEW analytics.nrw_ev_baseline_metrics AS
WITH components AS (
    SELECT
        r.*,
        ROUND(analytics.normalize_5_95(
            r.charging_points_per_km2,
            b.charging_point_density_low,
            b.charging_point_density_high
        ), 1) AS charger_density_score,
        ROUND(analytics.normalize_5_95(
            r.distance_to_nearest_charger_m,
            b.charger_distance_low,
            b.charger_distance_high,
            true
        ), 1) AS charger_accessibility_score,
        ROUND(analytics.normalize_5_95(
            r.charging_points_per_100k_population,
            b.population_coverage_low,
            b.population_coverage_high
        ), 1) AS population_adjusted_coverage_score
    FROM analytics.nrw_ev_baseline_raw r
    CROSS JOIN analytics.nrw_ev_baseline_bounds b
),
readiness AS (
    SELECT
        c.*,
        ROUND(
            0.40 * c.charger_density_score
            + 0.30 * c.charger_accessibility_score
            + 0.30 * c.population_adjusted_coverage_score,
            1
        ) AS ev_readiness_score
    FROM components c
),
scored AS (
    SELECT
        r.*,
        ROUND(100.0 - r.ev_readiness_score, 1) AS charger_deficit_score,
        i.infrastructure_opportunity_score,
        ROUND(
            0.60 * (100.0 - r.ev_readiness_score)
            + 0.40 * i.infrastructure_opportunity_score,
            1
        ) AS investment_priority_score,
        CASE
            WHEN r.ev_readiness_score IS NULL THEN 'missing_ev_input'
            WHEN i.infrastructure_opportunity_score IS NULL THEN 'missing_infrastructure_input'
            ELSE 'complete_proxy_inputs'
        END AS data_quality_flag
    FROM readiness r
    LEFT JOIN analytics.nrw_infrastructure_opportunity i USING (nuts_code)
),
ranked AS (
    SELECT
        s.*,
        RANK() OVER (ORDER BY s.investment_priority_score DESC NULLS LAST) AS priority_rank
    FROM scored s
)
-- The canonical district output also carries the component, energy, grid-proxy,
-- coverage and provenance detail the interface promises (contract A11, manifest
-- section 9).  Everything below is joined by nuts_code from relations that
-- already exist: no second scoring engine and no new indicator.
SELECT
    r.*,
    t.transport_load_score,
    t.traffic_intensity_score,
    t.traffic_road_density_score,
    t.road_proximity_score,
    t.traffic_intensity_dtv,
    t.traffic_weighted_road_density,
    t.traffic_road_length_km,
    t.traffic_measured_length_km,
    t.traffic_length_coverage,
    t.distance_to_nearest_road_m,
    t.transport_data_quality_flag,
    g.grid_readiness_proxy_score,
    g.substation_proximity_score,
    g.voltage_line_density_score,
    g.substation_density_score,
    g.voltage_weighted_line_density,
    g.substation_density,
    g.distance_to_nearest_substation_m,
    g.grid_line_length_km,
    g.substation_count,
    g.maximum_mapped_voltage_kv,
    g.line_voltage_coverage,
    g.substation_voltage_coverage,
    g.grid_data_quality_flag,
    rn.renewable_context_score,
    rn.renewable_capacity_density_score,
    rn.renewable_technology_diversity_score,
    -- Renewable Context is based on operating assets.  These deliberately
    -- explicit names prevent its asset inventory from being confused with the
    -- separate municipal-workbook stock reported below.
    rn.renewable_capacity_mw AS operating_asset_renewable_capacity_mw,
    rn.renewable_capacity_mw_per_km2 AS operating_asset_renewable_capacity_mw_per_km2,
    -- Retained as a compatibility alias for the established score-model
    -- measure.  It is operating-asset density only and must never be paired
    -- with municipal-workbook coverage or an unavailable reason.
    rn.renewable_capacity_mw_per_km2,
    rn.renewable_technology_count,
    rn.renewable_installation_count,
    e.reporting_year AS energy_reporting_year,
    e.energy_source,
    e.local_energy_balance_score,
    e.renewable_growth_score,
    e.grid_absorption_risk_proxy_score,
    e.renewable_balance_ratio,
    e.renewable_coverage_pct,
    e.consumption_mwh,
    e.published_generation_mwh,
    e.estimated_wind_generation_mwh,
    e.total_renewable_generation_mwh,
    e.renewable_net_addition_3y_mw,
    e.renewable_growth_density_mw_per_km2,
    e.expected_municipalities,
    e.consumption_municipal_coverage,
    e.renewable_municipal_coverage,
    -- Municipal workbook capacity is a different measurement from operating
    -- asset capacity.  Keep its value, reporting year and quality together so
    -- exporters and consumers cannot apply its coverage to the asset measure.
    e.renewable_capacity_mw AS municipal_workbook_renewable_capacity_mw,
    e.reporting_year AS municipal_workbook_capacity_reporting_year,
    e.renewable_capacity_coverage AS municipal_workbook_renewable_capacity_coverage,
    e.growth_years_required,
    e.growth_years_reported,
    e.energy_data_quality_flag,
    e.energy_unavailable_reason,
    e.renewable_capacity_unavailable_reason
        AS municipal_workbook_renewable_capacity_unavailable_reason,
    i.infrastructure_data_quality_flag,
    a.wind_full_load_hours,
    a.renewable_growth_window_years,
    a.wind_estimate_note,
    fv.formula_version,
    fv.formula_version_date,
    cs.snapshot_date AS charger_snapshot_date,
    cs.source_name AS charger_snapshot_source,
    -- Absent rather than guessed: the field exists so a consumer can say the
    -- snapshot date is unknown instead of omitting the question (manifest 9.3).
    CASE
        WHEN cs.snapshot_date IS NULL THEN 'charger_snapshot_date_not_recorded'
    END AS charger_snapshot_unavailable_reason
FROM ranked r
LEFT JOIN analytics.nrw_transport_metrics t USING (nuts_code)
LEFT JOIN analytics.nrw_grid_proxy_metrics g USING (nuts_code)
LEFT JOIN analytics.nrw_renewable_metrics rn USING (nuts_code)
LEFT JOIN analytics.nrw_local_energy_balance e USING (nuts_code)
LEFT JOIN analytics.nrw_infrastructure_opportunity i USING (nuts_code)
LEFT JOIN raw.source_snapshots cs ON cs.source_key = 'bnetza_ladesaeulenregister'
CROSS JOIN analytics.nrw_energy_assumptions a
CROSS JOIN analytics.nrw_formula_version fv;

-- EV readiness scenario comparison ------------------------------------------
CREATE VIEW analytics.nrw_ev_scenario_metrics AS
WITH effective_chargers AS (
    SELECT
        'official:' || c.source_id AS feature_id,
        c.charging_points,
        c.power_kw,
        c.max_point_power_kw,
        c.geom,
        c.geom_25832
    FROM raw.chargers c
    UNION ALL
    SELECT
        'proposal:' || p.id::text,
        p.charging_points,
        p.power_kw,
        p.max_point_power_kw,
        p.geom,
        p.geom_25832
    FROM scenario.proposed_chargers p
),
-- Same single-district rule as the baseline: a station on a shared boundary is
-- covered by both neighbours and would otherwise be counted twice.
effective_charger_districts AS (
    SELECT DISTINCT ON (c.feature_id)
        c.feature_id,
        d.nuts_code
    FROM effective_chargers c
    JOIN staging.nrw_districts d
      ON ST_Covers(d.geom, c.geom)
    ORDER BY c.feature_id, d.nuts_code
),
scenario_raw AS (
    SELECT
        d.nuts_code,
        d.ags,
        d.district_name,
        m.area_km2,
        m.population,
        COUNT(c.feature_id) AS chargers_total,
        COALESCE(
            SUM(COALESCE(c.charging_points, 1)) FILTER (WHERE c.feature_id IS NOT NULL),
            0
        ) AS charging_points_total,
        COUNT(c.feature_id) FILTER (WHERE c.max_point_power_kw >= 50)
            AS fast_chargers_total,
        COUNT(c.feature_id) FILTER (WHERE c.max_point_power_kw < 50)
            AS normal_chargers_total,
        COUNT(c.feature_id) FILTER (WHERE c.max_point_power_kw IS NULL)
            AS unknown_power_chargers_total,
        COALESCE(
            SUM(COALESCE(c.charging_points, 1)) FILTER (WHERE c.feature_id IS NOT NULL),
            0
        ) / NULLIF(m.area_km2, 0) AS charging_points_per_km2,
        COUNT(c.feature_id) / NULLIF(m.area_km2, 0) AS chargers_per_km2,
        COALESCE(
            SUM(COALESCE(c.charging_points, 1)) FILTER (WHERE c.feature_id IS NOT NULL),
            0
        ) * 100000.0 / NULLIF(m.population, 0)
            AS charging_points_per_100k_population,
        nearest.distance_to_nearest_charger_m,
        d.geom
    FROM staging.nrw_districts d
    JOIN analytics.nrw_district_metrics m USING (nuts_code)
    LEFT JOIN effective_charger_districts a ON a.nuts_code = d.nuts_code
    LEFT JOIN effective_chargers c ON c.feature_id = a.feature_id
    LEFT JOIN LATERAL (
        -- The scenario nearest station is selected and measured in EPSG:25832
        -- exactly like the baseline, so a scenario delta never mixes a
        -- degree-ordered selection with a metre-valued distance.
        SELECT ST_Distance(d.centroid_25832, cn.geom_25832) AS distance_to_nearest_charger_m
        FROM effective_chargers cn
        ORDER BY d.centroid_25832 <-> cn.geom_25832
        LIMIT 1
    ) nearest ON true
    GROUP BY
        d.nuts_code,
        d.ags,
        d.district_name,
        m.area_km2,
        m.population,
        nearest.distance_to_nearest_charger_m,
        d.geom
),
components AS (
    SELECT
        r.*,
        ROUND(analytics.normalize_5_95(
            r.charging_points_per_km2,
            b.charging_point_density_low,
            b.charging_point_density_high
        ), 1) AS charger_density_score,
        ROUND(analytics.normalize_5_95(
            r.distance_to_nearest_charger_m,
            b.charger_distance_low,
            b.charger_distance_high,
            true
        ), 1) AS charger_accessibility_score,
        ROUND(analytics.normalize_5_95(
            r.charging_points_per_100k_population,
            b.population_coverage_low,
            b.population_coverage_high
        ), 1) AS population_adjusted_coverage_score
    FROM scenario_raw r
    CROSS JOIN analytics.nrw_ev_baseline_bounds b
),
readiness AS (
    SELECT
        c.*,
        ROUND(
            0.40 * c.charger_density_score
            + 0.30 * c.charger_accessibility_score
            + 0.30 * c.population_adjusted_coverage_score,
            1
        ) AS ev_readiness_score
    FROM components c
),
scored AS (
    SELECT
        r.*,
        ROUND(100.0 - r.ev_readiness_score, 1) AS charger_deficit_score,
        i.infrastructure_opportunity_score,
        ROUND(
            0.60 * (100.0 - r.ev_readiness_score)
            + 0.40 * i.infrastructure_opportunity_score,
            1
        ) AS investment_priority_score,
        CASE
            WHEN r.ev_readiness_score IS NULL THEN 'missing_ev_input'
            WHEN i.infrastructure_opportunity_score IS NULL THEN 'missing_infrastructure_input'
            ELSE 'complete_proxy_inputs'
        END AS data_quality_flag
    FROM readiness r
    LEFT JOIN analytics.nrw_infrastructure_opportunity i USING (nuts_code)
),
ranked AS (
    SELECT
        s.*,
        RANK() OVER (ORDER BY s.investment_priority_score DESC NULLS LAST)
            AS priority_rank
    FROM scored s
)
SELECT
    s.nuts_code,
    s.ags,
    s.district_name,
    s.area_km2,
    s.population,
    b.chargers_total AS baseline_chargers_total,
    s.chargers_total AS scenario_chargers_total,
    s.chargers_total - b.chargers_total AS chargers_total_delta,
    b.charging_points_total AS baseline_charging_points_total,
    s.charging_points_total AS scenario_charging_points_total,
    s.charging_points_total - b.charging_points_total AS charging_points_total_delta,
    b.fast_chargers_total AS baseline_fast_chargers_total,
    s.fast_chargers_total AS scenario_fast_chargers_total,
    s.fast_chargers_total - b.fast_chargers_total AS fast_chargers_total_delta,
    b.normal_chargers_total AS baseline_normal_chargers_total,
    s.normal_chargers_total AS scenario_normal_chargers_total,
    s.normal_chargers_total - b.normal_chargers_total AS normal_chargers_total_delta,
    b.unknown_power_chargers_total AS baseline_unknown_power_chargers_total,
    s.unknown_power_chargers_total AS scenario_unknown_power_chargers_total,
    s.unknown_power_chargers_total - b.unknown_power_chargers_total
        AS unknown_power_chargers_total_delta,
    -- The readiness density and the station-density context both travel through
    -- baseline, scenario and change, so a consumer never has to infer one from
    -- the other (contract A01).
    b.charging_points_per_km2 AS baseline_charging_points_per_km2,
    s.charging_points_per_km2 AS scenario_charging_points_per_km2,
    s.charging_points_per_km2 - b.charging_points_per_km2
        AS charging_points_per_km2_delta,
    b.chargers_per_km2 AS baseline_chargers_per_km2,
    s.chargers_per_km2 AS scenario_chargers_per_km2,
    s.chargers_per_km2 - b.chargers_per_km2 AS chargers_per_km2_delta,
    b.charging_points_per_100k_population
        AS baseline_charging_points_per_100k_population,
    s.charging_points_per_100k_population
        AS scenario_charging_points_per_100k_population,
    s.charging_points_per_100k_population
        - b.charging_points_per_100k_population
        AS charging_points_per_100k_population_delta,
    b.distance_to_nearest_charger_m AS baseline_distance_to_nearest_charger_m,
    s.distance_to_nearest_charger_m AS scenario_distance_to_nearest_charger_m,
    s.distance_to_nearest_charger_m - b.distance_to_nearest_charger_m
        AS distance_to_nearest_charger_m_delta,
    b.charger_density_score AS baseline_charger_density_score,
    s.charger_density_score AS scenario_charger_density_score,
    s.charger_density_score - b.charger_density_score AS charger_density_score_delta,
    b.charger_accessibility_score AS baseline_charger_accessibility_score,
    s.charger_accessibility_score AS scenario_charger_accessibility_score,
    s.charger_accessibility_score - b.charger_accessibility_score
        AS charger_accessibility_score_delta,
    b.population_adjusted_coverage_score
        AS baseline_population_adjusted_coverage_score,
    s.population_adjusted_coverage_score
        AS scenario_population_adjusted_coverage_score,
    s.population_adjusted_coverage_score - b.population_adjusted_coverage_score
        AS population_adjusted_coverage_score_delta,
    b.ev_readiness_score AS baseline_ev_readiness_score,
    s.ev_readiness_score AS scenario_ev_readiness_score,
    s.ev_readiness_score - b.ev_readiness_score AS ev_readiness_score_delta,
    b.charger_deficit_score AS baseline_charger_deficit_score,
    s.charger_deficit_score AS scenario_charger_deficit_score,
    s.charger_deficit_score - b.charger_deficit_score AS charger_deficit_score_delta,
    -- Baseline-only context: proposing a charger changes no transport, grid or
    -- renewable measurement, so this value is identical in both modes and its
    -- change is zero whenever it is known at all (contract C02, manifest 9.4).
    s.infrastructure_opportunity_score,
    b.investment_priority_score AS baseline_investment_priority_score,
    s.investment_priority_score AS scenario_investment_priority_score,
    s.investment_priority_score - b.investment_priority_score
        AS investment_priority_score_delta,
    b.priority_rank AS baseline_priority_rank,
    s.priority_rank AS scenario_priority_rank,
    s.data_quality_flag,
    b.formula_version,
    s.geom
FROM ranked s
JOIN analytics.nrw_ev_baseline_metrics b USING (nuts_code);

-- The score model ----------------------------------------------------------
-- One row per weighted term of every published indicator: the component it
-- weights, the constant it adds, and, for a term that is normalized from a
-- measurement, that measurement's unit and its official baseline bounds.
--
--   indicator_score = ROUND(SUM(constant_term + component_weight * component), 1)
--
-- db/verify_nrw_analytics.sql evaluates exactly that expression against every
-- district, so this relation and the SQL above cannot drift apart unnoticed.
CREATE VIEW analytics.nrw_score_model AS
SELECT
    fv.formula_version,
    model.*
FROM analytics.nrw_formula_version fv
CROSS JOIN (
    SELECT 1 AS sort_order, 'EV Readiness'::text AS indicator,
           'ev_readiness_score'::text AS indicator_score,
           'charger_density_score'::text AS component_score,
           0.40::numeric AS component_weight, 0::numeric AS constant_term,
           'charging_points_per_km2'::text AS measure,
           'charging points per km2'::text AS measure_unit,
           false AS inverse_measure,
           b.charging_point_density_low AS lower_bound,
           b.charging_point_density_high AS upper_bound
    FROM analytics.nrw_ev_baseline_bounds b
    UNION ALL
    SELECT 2, 'EV Readiness', 'ev_readiness_score', 'charger_accessibility_score',
           0.30, 0, 'distance_to_nearest_charger_m', 'metres', true,
           b.charger_distance_low, b.charger_distance_high
    FROM analytics.nrw_ev_baseline_bounds b
    UNION ALL
    SELECT 3, 'EV Readiness', 'ev_readiness_score', 'population_adjusted_coverage_score',
           0.30, 0, 'charging_points_per_100k_population',
           'charging points per 100 000 people', false,
           b.population_coverage_low, b.population_coverage_high
    FROM analytics.nrw_ev_baseline_bounds b
    UNION ALL
    SELECT 4, 'Charger Deficit', 'charger_deficit_score', 'ev_readiness_score',
           -1.00, 100, NULL, NULL, NULL,
           NULL::double precision, NULL::double precision
    UNION ALL
    SELECT 5, 'Transport Load', 'transport_load_score', 'traffic_intensity_score',
           0.60, 0, 'traffic_intensity_dtv', 'vehicles per day', false,
           b.intensity_low, b.intensity_high
    FROM analytics.nrw_transport_bounds b
    UNION ALL
    SELECT 6, 'Transport Load', 'transport_load_score', 'traffic_road_density_score',
           0.25, 0, 'traffic_weighted_road_density',
           'vehicle-kilometres per day per km2', false,
           b.density_low, b.density_high
    FROM analytics.nrw_transport_bounds b
    UNION ALL
    SELECT 7, 'Transport Load', 'transport_load_score', 'road_proximity_score',
           0.15, 0, 'distance_to_nearest_road_m', 'metres', true,
           b.distance_low, b.distance_high
    FROM analytics.nrw_transport_bounds b
    UNION ALL
    SELECT 8, 'Grid Readiness Proxy', 'grid_readiness_proxy_score',
           'substation_proximity_score', 0.45, 0,
           'distance_to_nearest_substation_m', 'metres', true,
           b.distance_low, b.distance_high
    FROM analytics.nrw_grid_proxy_bounds b
    UNION ALL
    SELECT 9, 'Grid Readiness Proxy', 'grid_readiness_proxy_score',
           'voltage_line_density_score', 0.35, 0,
           'voltage_weighted_line_density', 'kV-kilometres per km2', false,
           b.line_low, b.line_high
    FROM analytics.nrw_grid_proxy_bounds b
    UNION ALL
    SELECT 10, 'Grid Readiness Proxy', 'grid_readiness_proxy_score',
           'substation_density_score', 0.20, 0,
           'substation_density', 'substations per km2', false,
           b.substation_low, b.substation_high
    FROM analytics.nrw_grid_proxy_bounds b
    UNION ALL
    SELECT 11, 'Renewable Context', 'renewable_context_score',
           'renewable_capacity_density_score', 0.70, 0,
           'renewable_capacity_mw_per_km2', 'MW per km2', false,
           b.capacity_low, b.capacity_high
    FROM analytics.nrw_renewable_bounds b
    UNION ALL
    SELECT 12, 'Renewable Context', 'renewable_context_score',
           'renewable_technology_diversity_score', 0.30, 0,
           'renewable_technology_count', 'distinct operating technologies', false,
           b.technology_low, b.technology_high
    FROM analytics.nrw_renewable_bounds b
    UNION ALL
    SELECT 13, 'Infrastructure Opportunity', 'infrastructure_opportunity_score',
           'transport_load_score', 0.40, 0, NULL, NULL, NULL,
           NULL::double precision, NULL::double precision
    UNION ALL
    SELECT 14, 'Infrastructure Opportunity', 'infrastructure_opportunity_score',
           'grid_readiness_proxy_score', 0.35, 0, NULL, NULL, NULL,
           NULL::double precision, NULL::double precision
    UNION ALL
    SELECT 15, 'Infrastructure Opportunity', 'infrastructure_opportunity_score',
           'renewable_context_score', 0.25, 0, NULL, NULL, NULL,
           NULL::double precision, NULL::double precision
    UNION ALL
    SELECT 16, 'Investment Priority', 'investment_priority_score',
           'charger_deficit_score', 0.60, 0, NULL, NULL, NULL,
           NULL::double precision, NULL::double precision
    UNION ALL
    SELECT 17, 'Investment Priority', 'investment_priority_score',
           'infrastructure_opportunity_score', 0.40, 0, NULL, NULL, NULL,
           NULL::double precision, NULL::double precision
    UNION ALL
    SELECT 18, 'Grid Absorption Risk Proxy', 'grid_absorption_risk_proxy_score',
           'local_energy_balance_score', 0.45, 0,
           'renewable_balance_ratio',
           'generation divided by consumption', false,
           b.balance_low, b.balance_high
    FROM analytics.nrw_energy_bounds b
    UNION ALL
    SELECT 19, 'Grid Absorption Risk Proxy', 'grid_absorption_risk_proxy_score',
           'renewable_growth_score', 0.30, 0,
           'renewable_growth_density_mw_per_km2',
           'MW added per km2 over the growth window', false,
           b.growth_low, b.growth_high
    FROM analytics.nrw_energy_bounds b
    UNION ALL
    -- 0.25 * (100 - grid readiness proxy), written as the constant and the
    -- signed weight that make the whole model one summation.
    SELECT 20, 'Grid Absorption Risk Proxy', 'grid_absorption_risk_proxy_score',
           'grid_readiness_proxy_score', -0.25, 25, NULL, NULL, NULL,
           NULL::double precision, NULL::double precision
) model;

-- Published layers ----------------------------------------------------------
CREATE VIEW publish.nrw_transport_load AS
SELECT d.*, a.geom
FROM analytics.nrw_transport_metrics d
JOIN staging.nrw_districts a USING (nuts_code);

CREATE OR REPLACE VIEW publish.nrw_autobahns AS
SELECT source_id, highway, ref, name, geom
FROM raw.osm_roads
WHERE highway = 'motorway';

CREATE OR REPLACE VIEW publish.nrw_regional_roads AS
SELECT
    osm_id AS source_id,
    road_class,
    road_number,
    name,
    traffic_total,
    traffic_light,
    traffic_heavy,
    source,
    geom
FROM raw.roads
WHERE staging.normalize_road_class(road_class) IN ('primary', 'secondary');

CREATE VIEW publish.nrw_grid_proxy AS
SELECT d.*, a.geom
FROM analytics.nrw_grid_proxy_metrics d
JOIN staging.nrw_districts a USING (nuts_code);

CREATE VIEW publish.nrw_renewable_context AS
SELECT d.*, a.geom
FROM analytics.nrw_renewable_metrics d
JOIN staging.nrw_districts a USING (nuts_code);

CREATE VIEW publish.nrw_local_energy_balance AS
SELECT
    nuts_code,
    ags,
    district_name,
    reporting_year,
    energy_source,
    expected_municipalities,
    consumption_municipalities_reported,
    consumption_municipal_coverage,
    renewable_municipalities_reported,
    renewable_municipal_coverage,
    renewable_capacity_municipalities_reported,
    renewable_capacity_coverage,
    renewable_capacity_unavailable_reason,
    growth_years_required,
    growth_years_reported,
    generation_components_unknown,
    energy_unavailable_reason,
    consumption_mwh,
    published_generation_mwh,
    estimated_wind_generation_mwh,
    total_renewable_generation_mwh,
    renewable_capacity_mw,
    -- The growth window travels with its own coverage counts so a consumer can
    -- say why a three-year figure is unavailable instead of showing nothing.
    renewable_net_addition_3y_mw,
    renewable_growth_density_mw_per_km2,
    renewable_balance_ratio,
    renewable_coverage_pct,
    local_energy_balance_score,
    energy_data_quality_flag,
    wind_full_load_hours,
    geom
FROM analytics.nrw_local_energy_balance;

CREATE OR REPLACE VIEW publish.nrw_traffic_assumptions AS
SELECT * FROM analytics.nrw_traffic_assumptions;

CREATE VIEW publish.nrw_grid_absorption_risk AS
SELECT
    nuts_code,
    ags,
    district_name,
    reporting_year,
    energy_unavailable_reason,
    renewable_net_addition_3y_mw,
    renewable_growth_density_mw_per_km2,
    renewable_growth_score,
    grid_readiness_proxy_score,
    local_energy_balance_score,
    grid_absorption_risk_proxy_score,
    energy_data_quality_flag,
    geom
FROM analytics.nrw_local_energy_balance;

CREATE VIEW publish.nrw_infrastructure_opportunity AS
SELECT * FROM analytics.nrw_infrastructure_opportunity;

-- The weights, measured quantities and official baseline bounds behind every
-- published score, so a consumer can show the formula it is displaying instead
-- of restating it from a document (contract C02, manifest 9.3).
CREATE VIEW publish.nrw_score_model AS
SELECT * FROM analytics.nrw_score_model;

CREATE VIEW publish.nrw_ev_baseline_metrics AS
SELECT * FROM analytics.nrw_ev_baseline_metrics;

CREATE VIEW publish.nrw_ev_scenario_metrics AS
SELECT * FROM analytics.nrw_ev_scenario_metrics;

-- The one district-priority definition.  db/nrw_schema.sql used to publish a
-- second, unscored one over analytics.nrw_priority_scores; both are retired
-- (finding F20).
CREATE VIEW publish.nrw_district_priority AS
SELECT * FROM analytics.nrw_ev_baseline_metrics;

COMMIT;
