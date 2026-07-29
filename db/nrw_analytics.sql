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

DROP MATERIALIZED VIEW IF EXISTS analytics.nrw_local_energy_balance CASCADE;
DROP MATERIALIZED VIEW IF EXISTS analytics.nrw_infrastructure_opportunity CASCADE;
DROP MATERIALIZED VIEW IF EXISTS analytics.nrw_transport_metrics CASCADE;
DROP MATERIALIZED VIEW IF EXISTS analytics.nrw_grid_proxy_metrics CASCADE;
DROP MATERIALIZED VIEW IF EXISTS analytics.nrw_renewable_metrics CASCADE;
DROP VIEW IF EXISTS publish.nrw_district_priority;

CREATE MATERIALIZED VIEW analytics.nrw_transport_metrics AS
WITH road_parts AS (
    SELECT
        d.nuts_code,
        ST_Length(
            ST_Intersection(ST_Transform(d.geom, 25832), ST_Transform(r.geom, 25832))
        ) / 1000.0 AS length_km,
        r.traffic_total
    FROM staging.nrw_districts d
    JOIN raw.roads r ON ST_Intersects(d.geom, r.geom)
),
aggregated AS (
    SELECT
        d.nuts_code,
        COALESCE(SUM(p.length_km), 0) AS traffic_road_length_km,
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
        SELECT ST_Distance(
            ST_Centroid(ST_Transform(d.geom, 25832)),
            ST_Transform(r.geom, 25832)
        ) AS distance_to_nearest_road_m
        FROM raw.roads r
        ORDER BY ST_Centroid(d.geom) <-> r.geom
        LIMIT 1
    ) nearest ON true
    GROUP BY d.nuts_code, m.area_km2, nearest.distance_to_nearest_road_m
),
bounds AS (
    SELECT
        percentile_cont(0.05) WITHIN GROUP (ORDER BY traffic_intensity_dtv) AS intensity_low,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY traffic_intensity_dtv) AS intensity_high,
        percentile_cont(0.05) WITHIN GROUP (ORDER BY traffic_weighted_road_density) AS density_low,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY traffic_weighted_road_density) AS density_high,
        percentile_cont(0.05) WITHIN GROUP (ORDER BY distance_to_nearest_road_m) AS distance_low,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY distance_to_nearest_road_m) AS distance_high
    FROM aggregated
)
SELECT
    a.*,
    ROUND(analytics.normalize_5_95(a.traffic_intensity_dtv, b.intensity_low, b.intensity_high), 1)
        AS traffic_intensity_score,
    ROUND(analytics.normalize_5_95(
        a.traffic_weighted_road_density, b.density_low, b.density_high
    ), 1) AS traffic_road_density_score,
    ROUND(analytics.normalize_5_95(
        a.distance_to_nearest_road_m, b.distance_low, b.distance_high, true
    ), 1) AS road_proximity_score,
    ROUND(
        0.60 * analytics.normalize_5_95(a.traffic_intensity_dtv, b.intensity_low, b.intensity_high)
        + 0.25 * analytics.normalize_5_95(
            a.traffic_weighted_road_density, b.density_low, b.density_high
        )
        + 0.15 * analytics.normalize_5_95(
            a.distance_to_nearest_road_m, b.distance_low, b.distance_high, true
        ),
        1
    ) AS transport_load_score
FROM aggregated a
CROSS JOIN bounds b;

CREATE UNIQUE INDEX nrw_transport_metrics_nuts_uq
    ON analytics.nrw_transport_metrics (nuts_code);

CREATE MATERIALIZED VIEW analytics.nrw_grid_proxy_metrics AS
WITH parsed_grid AS (
    SELECT
        g.*,
        (
            SELECT MAX(
                CASE
                    WHEN token ~* 'kv' THEN NULLIF(substring(token FROM '([0-9]+(?:\.[0-9]+)?)'), '')::numeric
                    ELSE NULLIF(substring(token FROM '([0-9]+(?:\.[0-9]+)?)'), '')::numeric / 1000.0
                END
            )
            FROM regexp_split_to_table(COALESCE(g.voltage, ''), ';') AS token
        ) AS voltage_kv
    FROM raw.grid_infrastructure g
),
line_parts AS (
    SELECT
        d.nuts_code,
        ST_Length(
            ST_Intersection(ST_Transform(d.geom, 25832), ST_Transform(g.geom, 25832))
        ) / 1000.0 AS length_km,
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
),
aggregated AS (
    SELECT
        d.nuts_code,
        COALESCE(l.grid_line_length_km, 0) AS grid_line_length_km,
        COALESCE(l.grid_line_segments, 0) AS grid_line_segments,
        COALESCE(s.substation_count, 0) AS substation_count,
        s.maximum_mapped_voltage_kv,
        l.line_voltage_coverage,
        s.substation_voltage_coverage,
        COALESCE(l.voltage_weighted_line_kv_km, 0) / NULLIF(m.area_km2, 0)
            AS voltage_weighted_line_density,
        COALESCE(s.substation_count, 0) / NULLIF(m.area_km2, 0) AS substation_density,
        nearest.distance_to_nearest_substation_m
    FROM staging.nrw_districts d
    JOIN analytics.nrw_district_metrics m USING (nuts_code)
    LEFT JOIN line_agg l USING (nuts_code)
    LEFT JOIN substation_agg s USING (nuts_code)
    LEFT JOIN LATERAL (
        SELECT ST_Distance(
            ST_Centroid(ST_Transform(d.geom, 25832)),
            ST_Transform(g.geom, 25832)
        ) AS distance_to_nearest_substation_m
        FROM raw.grid_infrastructure g
        WHERE g.asset_type IN ('substation', 'transformer')
        ORDER BY ST_Centroid(d.geom) <-> g.geom
        LIMIT 1
    ) nearest ON true
),
bounds AS (
    SELECT
        percentile_cont(0.05) WITHIN GROUP (ORDER BY distance_to_nearest_substation_m) AS distance_low,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY distance_to_nearest_substation_m) AS distance_high,
        percentile_cont(0.05) WITHIN GROUP (ORDER BY voltage_weighted_line_density) AS line_low,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY voltage_weighted_line_density) AS line_high,
        percentile_cont(0.05) WITHIN GROUP (ORDER BY substation_density) AS substation_low,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY substation_density) AS substation_high
    FROM aggregated
)
SELECT
    a.*,
    ROUND(
        0.45 * analytics.normalize_5_95(
            a.distance_to_nearest_substation_m, b.distance_low, b.distance_high, true
        )
        + 0.35 * analytics.normalize_5_95(
            a.voltage_weighted_line_density, b.line_low, b.line_high
        )
        + 0.20 * analytics.normalize_5_95(
            a.substation_density, b.substation_low, b.substation_high
        ),
        1
    ) AS grid_readiness_proxy_score
FROM aggregated a
CROSS JOIN bounds b;

CREATE UNIQUE INDEX nrw_grid_proxy_metrics_nuts_uq
    ON analytics.nrw_grid_proxy_metrics (nuts_code);

CREATE MATERIALIZED VIEW analytics.nrw_renewable_metrics AS
WITH aggregated AS (
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
    GROUP BY d.nuts_code, m.area_km2
),
bounds AS (
    SELECT
        percentile_cont(0.05) WITHIN GROUP (ORDER BY renewable_capacity_mw_per_km2) AS capacity_low,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY renewable_capacity_mw_per_km2) AS capacity_high,
        percentile_cont(0.05) WITHIN GROUP (ORDER BY renewable_technology_count) AS technology_low,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY renewable_technology_count) AS technology_high
    FROM aggregated
)
SELECT
    a.*,
    ROUND(
        0.70 * analytics.normalize_5_95(
            a.renewable_capacity_mw_per_km2, b.capacity_low, b.capacity_high
        )
        + 0.30 * analytics.normalize_5_95(
            a.renewable_technology_count, b.technology_low, b.technology_high
        ),
        1
    ) AS renewable_context_score
FROM aggregated a
CROSS JOIN bounds b;

CREATE UNIQUE INDEX nrw_renewable_metrics_nuts_uq
    ON analytics.nrw_renewable_metrics (nuts_code);

CREATE OR REPLACE VIEW analytics.nrw_energy_assumptions AS
SELECT
    2000::numeric AS wind_full_load_hours,
    3::integer AS renewable_growth_window_years,
    'Wind generation planning assumption; replace when reviewed direct generation data is available'
        ::text AS wind_estimate_note;

CREATE MATERIALIZED VIEW analytics.nrw_local_energy_balance AS
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
consumption AS (
    SELECT
        c.nuts_code,
        SUM(c.consumption_gwh) * 1000.0 AS consumption_mwh
    FROM raw.energy_consumption_municipal c
    CROSS JOIN reporting y
    WHERE c.year = y.reporting_year
    GROUP BY c.nuts_code
),
renewable_stock AS (
    SELECT
        r.nuts_code,
        SUM(r.published_generation_mwh) AS published_generation_mwh,
        SUM(r.wind_capacity_mw) AS wind_capacity_mw,
        SUM(r.renewable_capacity_mw) AS renewable_capacity_mw
    FROM raw.renewable_balance_municipal r
    CROSS JOIN reporting y
    WHERE r.year = y.reporting_year
    GROUP BY r.nuts_code
),
renewable_growth AS (
    SELECT
        r.nuts_code,
        SUM(r.renewable_net_addition_mw) AS renewable_net_addition_3y_mw
    FROM raw.renewable_balance_municipal r
    CROSS JOIN reporting y
    CROSS JOIN analytics.nrw_energy_assumptions a
    WHERE r.year BETWEEN
        y.reporting_year - (a.renewable_growth_window_years - 1)
        AND y.reporting_year
    GROUP BY r.nuts_code
),
raw_metrics AS (
    SELECT
        d.nuts_code,
        d.ags,
        d.district_name,
        y.reporting_year,
        m.area_km2,
        c.consumption_mwh,
        s.published_generation_mwh,
        s.wind_capacity_mw,
        s.wind_capacity_mw * a.wind_full_load_hours AS estimated_wind_generation_mwh,
        s.published_generation_mwh
            + s.wind_capacity_mw * a.wind_full_load_hours
            AS total_renewable_generation_mwh,
        s.renewable_capacity_mw,
        g.renewable_net_addition_3y_mw,
        g.renewable_net_addition_3y_mw / NULLIF(m.area_km2, 0)
            AS renewable_growth_density_mw_per_km2,
        (
            s.published_generation_mwh
            + s.wind_capacity_mw * a.wind_full_load_hours
        ) / NULLIF(c.consumption_mwh, 0) AS renewable_balance_ratio,
        p.grid_readiness_proxy_score,
        a.wind_full_load_hours,
        d.geom
    FROM staging.nrw_districts d
    CROSS JOIN reporting y
    CROSS JOIN analytics.nrw_energy_assumptions a
    JOIN analytics.nrw_district_metrics m USING (nuts_code)
    LEFT JOIN consumption c USING (nuts_code)
    LEFT JOIN renewable_stock s USING (nuts_code)
    LEFT JOIN renewable_growth g USING (nuts_code)
    LEFT JOIN analytics.nrw_grid_proxy_metrics p USING (nuts_code)
),
bounds AS (
    SELECT
        percentile_cont(0.05) WITHIN GROUP (ORDER BY renewable_balance_ratio)
            AS balance_low,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY renewable_balance_ratio)
            AS balance_high,
        percentile_cont(0.05) WITHIN GROUP (ORDER BY renewable_growth_density_mw_per_km2)
            AS growth_low,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY renewable_growth_density_mw_per_km2)
            AS growth_high
    FROM raw_metrics
),
scored AS (
    SELECT
        r.*,
        analytics.normalize_5_95(
            r.renewable_balance_ratio,
            b.balance_low,
            b.balance_high
        ) AS local_energy_balance_score_raw,
        analytics.normalize_5_95(
            r.renewable_growth_density_mw_per_km2,
            b.growth_low,
            b.growth_high
        ) AS renewable_growth_score_raw
    FROM raw_metrics r
    CROSS JOIN bounds b
)
SELECT
    nuts_code,
    ags,
    district_name,
    reporting_year,
    area_km2,
    consumption_mwh,
    published_generation_mwh,
    estimated_wind_generation_mwh,
    total_renewable_generation_mwh,
    renewable_capacity_mw,
    renewable_net_addition_3y_mw,
    renewable_growth_density_mw_per_km2,
    renewable_balance_ratio,
    renewable_balance_ratio * 100.0 AS renewable_coverage_pct,
    ROUND(local_energy_balance_score_raw, 1) AS local_energy_balance_score,
    ROUND(renewable_growth_score_raw, 1) AS renewable_growth_score,
    grid_readiness_proxy_score,
    CASE
        WHEN consumption_mwh IS NULL
          OR consumption_mwh <= 0
          OR published_generation_mwh IS NULL
          OR wind_capacity_mw IS NULL
          OR renewable_net_addition_3y_mw IS NULL
          OR grid_readiness_proxy_score IS NULL
        THEN NULL
        ELSE ROUND(
            0.45 * ROUND(local_energy_balance_score_raw, 1)
            + 0.30 * ROUND(renewable_growth_score_raw, 1)
            + 0.25 * (100 - grid_readiness_proxy_score),
            1
        )
    END AS grid_absorption_risk_proxy_score,
    CASE
        WHEN consumption_mwh IS NULL
          OR consumption_mwh <= 0
          OR published_generation_mwh IS NULL
          OR wind_capacity_mw IS NULL
          OR renewable_net_addition_3y_mw IS NULL
          OR grid_readiness_proxy_score IS NULL
        THEN 'missing_required_input'
        WHEN wind_capacity_mw > 0 THEN 'hybrid_complete'
        ELSE 'published_complete_no_wind'
    END AS energy_data_quality_flag,
    wind_full_load_hours,
    geom
FROM scored;

CREATE UNIQUE INDEX nrw_local_energy_balance_nuts_uq
    ON analytics.nrw_local_energy_balance (nuts_code);
CREATE INDEX nrw_local_energy_balance_geom_gix
    ON analytics.nrw_local_energy_balance USING gist (geom);

CREATE MATERIALIZED VIEW analytics.nrw_infrastructure_opportunity AS
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
    CASE
        WHEN t.transport_load_score IS NULL
          OR g.grid_readiness_proxy_score IS NULL
          OR r.renewable_context_score IS NULL
        THEN NULL
        ELSE ROUND(
            0.40 * t.transport_load_score
            + 0.35 * g.grid_readiness_proxy_score
            + 0.25 * r.renewable_context_score,
            1
        )
    END AS infrastructure_opportunity_score,
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

CREATE UNIQUE INDEX nrw_infrastructure_opportunity_nuts_uq
    ON analytics.nrw_infrastructure_opportunity (nuts_code);
CREATE INDEX nrw_infrastructure_opportunity_geom_gix
    ON analytics.nrw_infrastructure_opportunity USING gist (geom);

CREATE OR REPLACE VIEW publish.nrw_transport_load AS
SELECT d.*, a.geom
FROM analytics.nrw_transport_metrics d
JOIN staging.nrw_districts a USING (nuts_code);

CREATE OR REPLACE VIEW publish.nrw_grid_proxy AS
SELECT d.*, a.geom
FROM analytics.nrw_grid_proxy_metrics d
JOIN staging.nrw_districts a USING (nuts_code);

CREATE OR REPLACE VIEW publish.nrw_renewable_context AS
SELECT d.*, a.geom
FROM analytics.nrw_renewable_metrics d
JOIN staging.nrw_districts a USING (nuts_code);

CREATE OR REPLACE VIEW publish.nrw_local_energy_balance AS
SELECT
    nuts_code,
    ags,
    district_name,
    reporting_year,
    consumption_mwh,
    published_generation_mwh,
    estimated_wind_generation_mwh,
    total_renewable_generation_mwh,
    renewable_capacity_mw,
    renewable_balance_ratio,
    renewable_coverage_pct,
    local_energy_balance_score,
    energy_data_quality_flag,
    wind_full_load_hours,
    geom
FROM analytics.nrw_local_energy_balance;

CREATE OR REPLACE VIEW publish.nrw_grid_absorption_risk AS
SELECT
    nuts_code,
    ags,
    district_name,
    reporting_year,
    renewable_net_addition_3y_mw,
    renewable_growth_density_mw_per_km2,
    renewable_growth_score,
    grid_readiness_proxy_score,
    local_energy_balance_score,
    grid_absorption_risk_proxy_score,
    energy_data_quality_flag,
    geom
FROM analytics.nrw_local_energy_balance;

CREATE OR REPLACE VIEW publish.nrw_infrastructure_opportunity AS
SELECT * FROM analytics.nrw_infrastructure_opportunity;

CREATE OR REPLACE VIEW publish.nrw_district_priority AS
SELECT * FROM analytics.nrw_infrastructure_opportunity;
