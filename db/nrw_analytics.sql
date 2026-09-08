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
DROP VIEW IF EXISTS publish.nrw_ev_scenario_metrics;
DROP VIEW IF EXISTS publish.nrw_ev_baseline_metrics;
DROP VIEW IF EXISTS analytics.nrw_ev_scenario_metrics;
DROP VIEW IF EXISTS analytics.nrw_ev_baseline_metrics;
DROP VIEW IF EXISTS analytics.nrw_ev_baseline_bounds;
DROP VIEW IF EXISTS analytics.nrw_ev_baseline_raw;

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

CREATE OR REPLACE VIEW analytics.nrw_ev_baseline_raw AS
SELECT
    m.nuts_code,
    m.ags,
    m.district_name,
    m.area_km2,
    m.population,
    m.chargers_total,
    m.charging_points_total,
    m.fast_chargers_total,
    m.normal_chargers_total,
    m.unknown_power_chargers_total,
    m.chargers_total / NULLIF(m.area_km2, 0) AS chargers_per_km2,
    m.charging_points_total * 100000.0 / NULLIF(m.population, 0)
        AS charging_points_per_100k_population,
    m.distance_to_nearest_charger_m,
    m.geom
FROM analytics.nrw_district_metrics m;

CREATE OR REPLACE VIEW analytics.nrw_ev_baseline_bounds AS
SELECT
    percentile_cont(0.05) WITHIN GROUP (ORDER BY chargers_per_km2)
        AS charger_density_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY chargers_per_km2)
        AS charger_density_high,
    percentile_cont(0.05) WITHIN GROUP (ORDER BY distance_to_nearest_charger_m)
        AS charger_distance_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY distance_to_nearest_charger_m)
        AS charger_distance_high,
    percentile_cont(0.05) WITHIN GROUP (ORDER BY charging_points_per_100k_population)
        AS population_coverage_low,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY charging_points_per_100k_population)
        AS population_coverage_high
FROM analytics.nrw_ev_baseline_raw;

CREATE OR REPLACE VIEW analytics.nrw_ev_baseline_metrics AS
WITH components AS (
    SELECT
        r.*,
        ROUND(analytics.normalize_5_95(
            r.chargers_per_km2,
            b.charger_density_low,
            b.charger_density_high
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
        CASE
            WHEN c.charger_density_score IS NULL
              OR c.charger_accessibility_score IS NULL
              OR c.population_adjusted_coverage_score IS NULL
            THEN NULL
            ELSE ROUND(
                0.40 * c.charger_density_score
                + 0.30 * c.charger_accessibility_score
                + 0.30 * c.population_adjusted_coverage_score,
                1
            )
        END AS ev_readiness_score
    FROM components c
),
scored AS (
    SELECT
        r.*,
        CASE
            WHEN r.ev_readiness_score IS NULL THEN NULL
            ELSE ROUND(100.0 - r.ev_readiness_score, 1)
        END AS charger_deficit_score,
        i.infrastructure_opportunity_score,
        CASE
            WHEN r.ev_readiness_score IS NULL
              OR i.infrastructure_opportunity_score IS NULL
            THEN NULL
            ELSE ROUND(
                0.60 * (100.0 - r.ev_readiness_score)
                + 0.40 * i.infrastructure_opportunity_score,
                1
            )
        END AS investment_priority_score,
        CASE
            WHEN r.ev_readiness_score IS NULL THEN 'missing_ev_input'
            WHEN i.infrastructure_opportunity_score IS NULL THEN 'missing_infrastructure_input'
            ELSE 'complete_proxy_inputs'
        END AS data_quality_flag
    FROM readiness r
    LEFT JOIN analytics.nrw_infrastructure_opportunity i USING (nuts_code)
)
SELECT
    s.*,
    RANK() OVER (ORDER BY s.investment_priority_score DESC NULLS LAST) AS priority_rank
FROM scored s;

CREATE OR REPLACE VIEW analytics.nrw_ev_scenario_metrics AS
WITH effective_chargers AS (
    SELECT
        c.charging_points,
        c.power_kw,
        c.max_point_power_kw,
        c.geom
    FROM raw.chargers c
    UNION ALL
    SELECT
        p.charging_points,
        p.power_kw,
        p.max_point_power_kw,
        p.geom
    FROM scenario.proposed_chargers p
),
scenario_raw AS (
    SELECT
        d.nuts_code,
        d.ags,
        d.district_name,
        m.area_km2,
        m.population,
        COUNT(c.geom) AS chargers_total,
        COALESCE(
            SUM(COALESCE(c.charging_points, 1)) FILTER (WHERE c.geom IS NOT NULL),
            0
        ) AS charging_points_total,
        COUNT(c.geom) FILTER (WHERE c.max_point_power_kw >= 50)
            AS fast_chargers_total,
        COUNT(c.geom) FILTER (WHERE c.max_point_power_kw < 50)
            AS normal_chargers_total,
        COUNT(c.geom) FILTER (WHERE c.max_point_power_kw IS NULL)
            AS unknown_power_chargers_total,
        COUNT(c.geom) / NULLIF(m.area_km2, 0) AS chargers_per_km2,
        COALESCE(
            SUM(COALESCE(c.charging_points, 1)) FILTER (WHERE c.geom IS NOT NULL),
            0
        ) * 100000.0 / NULLIF(m.population, 0)
            AS charging_points_per_100k_population,
        nearest.distance_to_nearest_charger_m,
        d.geom
    FROM staging.nrw_districts d
    JOIN analytics.nrw_district_metrics m USING (nuts_code)
    LEFT JOIN effective_chargers c ON ST_Intersects(c.geom, d.geom)
    LEFT JOIN LATERAL (
        SELECT ST_Distance(
            ST_Centroid(ST_Transform(d.geom, 25832)),
            ST_Transform(cn.geom, 25832)
        ) AS distance_to_nearest_charger_m
        FROM effective_chargers cn
        ORDER BY ST_Centroid(d.geom) <-> cn.geom
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
            r.chargers_per_km2,
            b.charger_density_low,
            b.charger_density_high
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
        CASE
            WHEN c.charger_density_score IS NULL
              OR c.charger_accessibility_score IS NULL
              OR c.population_adjusted_coverage_score IS NULL
            THEN NULL
            ELSE ROUND(
                0.40 * c.charger_density_score
                + 0.30 * c.charger_accessibility_score
                + 0.30 * c.population_adjusted_coverage_score,
                1
            )
        END AS ev_readiness_score
    FROM components c
),
scored AS (
    SELECT
        r.*,
        CASE
            WHEN r.ev_readiness_score IS NULL THEN NULL
            ELSE ROUND(100.0 - r.ev_readiness_score, 1)
        END AS charger_deficit_score,
        i.infrastructure_opportunity_score,
        CASE
            WHEN r.ev_readiness_score IS NULL
              OR i.infrastructure_opportunity_score IS NULL
            THEN NULL
            ELSE ROUND(
                0.60 * (100.0 - r.ev_readiness_score)
                + 0.40 * i.infrastructure_opportunity_score,
                1
            )
        END AS investment_priority_score,
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
    s.infrastructure_opportunity_score,
    b.investment_priority_score AS baseline_investment_priority_score,
    s.investment_priority_score AS scenario_investment_priority_score,
    s.investment_priority_score - b.investment_priority_score
        AS investment_priority_score_delta,
    b.priority_rank AS baseline_priority_rank,
    s.priority_rank AS scenario_priority_rank,
    s.data_quality_flag,
    s.geom
FROM ranked s
JOIN analytics.nrw_ev_baseline_metrics b USING (nuts_code);

CREATE OR REPLACE VIEW publish.nrw_transport_load AS
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
WHERE road_class IN ('B', 'L');

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

CREATE OR REPLACE VIEW publish.nrw_ev_baseline_metrics AS
SELECT * FROM analytics.nrw_ev_baseline_metrics;

CREATE OR REPLACE VIEW publish.nrw_ev_scenario_metrics AS
SELECT * FROM analytics.nrw_ev_scenario_metrics;

CREATE OR REPLACE VIEW publish.nrw_district_priority AS
SELECT * FROM analytics.nrw_ev_baseline_metrics;
