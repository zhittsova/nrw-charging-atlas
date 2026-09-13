-- Analytical assertions.
--
-- The weights, constants and bounds are read from publish.nrw_score_model
-- rather than restated here, so this module checks the model the installation
-- actually built instead of a second copy of the arithmetic (findings F09,
-- F20, F32).
DO $$
DECLARE
    relation_name text;
    row_count bigint;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'analytics.nrw_transport_raw',
        'analytics.nrw_transport_metrics',
        'analytics.nrw_grid_proxy_raw',
        'analytics.nrw_grid_proxy_metrics',
        'analytics.nrw_renewable_raw',
        'analytics.nrw_renewable_metrics',
        'analytics.nrw_infrastructure_opportunity',
        'analytics.nrw_ev_baseline_metrics',
        'publish.nrw_transport_load',
        'publish.nrw_grid_proxy',
        'publish.nrw_renewable_context',
        'publish.nrw_infrastructure_opportunity',
        'publish.nrw_ev_baseline_metrics',
        'publish.nrw_ev_scenario_metrics',
        'publish.nrw_district_priority'
    ]
    LOOP
        EXECUTE format('SELECT count(*) FROM %s', relation_name) INTO row_count;
        IF row_count <> 53 THEN
            RAISE EXCEPTION '% has % rows; expected 53', relation_name, row_count;
        END IF;
    END LOOP;

    -- Finding F20: exactly one active district-priority definition.  The
    -- retired one published bare district counts under the same layer name.
    IF to_regclass('analytics.nrw_priority_scores') IS NOT NULL THEN
        RAISE EXCEPTION 'analytics.nrw_priority_scores still exists; '
                        'the district-priority definition is duplicated again';
    END IF;

    SELECT count(*) INTO row_count
    FROM (
        SELECT attname, atttypid
        FROM pg_attribute
        WHERE attrelid = 'publish.nrw_district_priority'::regclass
          AND attnum > 0 AND NOT attisdropped
        EXCEPT
        SELECT attname, atttypid
        FROM pg_attribute
        WHERE attrelid = 'publish.nrw_ev_baseline_metrics'::regclass
          AND attnum > 0 AND NOT attisdropped
    ) divergent;
    IF row_count <> 0 THEN
        RAISE EXCEPTION 'publish.nrw_district_priority has % columns the canonical '
                        'baseline output does not', row_count;
    END IF;
END;
$$;

-- Every published composite must equal the weighted sum of the published
-- components beside it, and must be unavailable exactly when one of them is.
--   indicator_score = ROUND(SUM(constant_term + component_weight * component), 1)
DO $$
DECLARE
    mismatch_count bigint;
    model_rows bigint;
    unbounded_components bigint;
BEGIN
    SELECT count(*) INTO model_rows FROM publish.nrw_score_model;
    IF model_rows = 0 THEN
        RAISE EXCEPTION 'publish.nrw_score_model is empty; no score model is published';
    END IF;

    -- A term that weights another score has no measurement and therefore no
    -- bounds of its own.  A measured term normally publishes both bounds, but a
    -- quantity no district measured at all correctly has neither: what must
    -- never happen is a bound without a measurement, or half a bound pair.
    SELECT count(*) INTO unbounded_components
    FROM publish.nrw_score_model
    WHERE (measure IS NULL AND (lower_bound IS NOT NULL OR upper_bound IS NOT NULL))
       OR ((lower_bound IS NULL) <> (upper_bound IS NULL));
    IF unbounded_components <> 0 THEN
        RAISE EXCEPTION '% score-model terms publish bounds without a measurement',
            unbounded_components;
    END IF;

    -- The weights of each indicator must still be the agreed ones.
    SELECT count(*) INTO mismatch_count
    FROM (
        SELECT indicator_score, SUM(ABS(component_weight)) AS weight_total
        FROM publish.nrw_score_model
        GROUP BY indicator_score
    ) totals
    WHERE weight_total <> 1.00;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION '% indicators do not weight their components to one',
            mismatch_count;
    END IF;

    WITH district AS (
        SELECT d.nuts_code, to_jsonb(d) AS published
        FROM publish.nrw_ev_baseline_metrics d
    ),
    evaluated AS (
        SELECT
            d.nuts_code,
            m.indicator,
            m.indicator_score,
            bool_or((d.published ->> m.component_score) IS NULL) AS component_unavailable,
            ROUND(
                SUM(
                    m.constant_term
                    + m.component_weight * (d.published ->> m.component_score)::numeric
                ),
                1
            ) AS expected_score
        FROM district d
        CROSS JOIN publish.nrw_score_model m
        GROUP BY d.nuts_code, m.indicator, m.indicator_score
    )
    SELECT count(*) INTO mismatch_count
    FROM evaluated e
    JOIN district d USING (nuts_code)
    WHERE CASE
        WHEN e.component_unavailable
            THEN (d.published ->> e.indicator_score) IS NOT NULL
        ELSE (d.published ->> e.indicator_score) IS NULL
             OR (d.published ->> e.indicator_score)::numeric <> e.expected_score
    END;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION '% district indicator values do not follow the published score model',
            mismatch_count;
    END IF;
END;
$$;

DO $$
DECLARE
    row_count bigint;
BEGIN
    -- A score may legitimately be unavailable when a required input is not
    -- measured, but only when the owning domain says why.  Present scores must
    -- still be in range, and the composite must exist exactly when its
    -- components do (contract C03).
    SELECT count(*) INTO row_count
    FROM analytics.nrw_infrastructure_opportunity i
    WHERE (i.transport_load_score IS NOT NULL
           AND i.transport_load_score NOT BETWEEN 0 AND 100)
       OR (i.grid_readiness_proxy_score IS NOT NULL
           AND i.grid_readiness_proxy_score NOT BETWEEN 0 AND 100)
       OR (i.renewable_context_score IS NOT NULL
           AND i.renewable_context_score NOT BETWEEN 0 AND 100)
       OR (i.infrastructure_opportunity_score IS NOT NULL
           AND i.infrastructure_opportunity_score NOT BETWEEN 0 AND 100)
       OR (i.infrastructure_opportunity_score IS NULL)
          <> (i.infrastructure_data_quality_flag = 'missing_required_component');

    IF row_count <> 0 THEN
        RAISE EXCEPTION '% districts have out-of-range or unexplained infrastructure scores', row_count;
    END IF;

    SELECT count(*) INTO row_count
    FROM analytics.nrw_transport_metrics
    WHERE (transport_load_score IS NULL)
          AND transport_data_quality_flag = 'complete_traffic_coverage';
    IF row_count <> 0 THEN
        RAISE EXCEPTION '% districts drop transport load without an explanation', row_count;
    END IF;

    -- Exactly the three unavailable flags may accompany a missing proxy score,
    -- and each of them must accompany one.
    SELECT count(*) INTO row_count
    FROM analytics.nrw_grid_proxy_metrics
    WHERE (grid_readiness_proxy_score IS NULL)
          <> (grid_data_quality_flag IN (
                'no_mapped_substation', 'no_mapped_grid_lines', 'unknown_line_voltage'
             ));
    IF row_count <> 0 THEN
        RAISE EXCEPTION '% districts disagree about why the grid proxy is unavailable', row_count;
    END IF;

    -- Contract A01 / finding F10: readiness density is charging points per km2,
    -- and stations per km2 remains published as separately named context.  The
    -- two differ wherever a district has a station with more than one point, so
    -- a silent revert to station density would be caught here.
    SELECT count(*) INTO row_count
    FROM analytics.nrw_ev_baseline_metrics
    WHERE charging_points_total > chargers_total
      AND charging_points_per_km2 <= chargers_per_km2;
    IF row_count <> 0 THEN
        RAISE EXCEPTION '% districts publish a point density no larger than their station density',
            row_count;
    END IF;

    -- Every analytical output carries the formula version it was produced by,
    -- and one refresh produces exactly one version.
    SELECT count(DISTINCT formula_version) INTO row_count
    FROM analytics.nrw_ev_baseline_metrics;
    IF row_count <> 1 THEN
        RAISE EXCEPTION 'District outputs carry % formula versions; expected one', row_count;
    END IF;

    SELECT count(*) INTO row_count
    FROM analytics.nrw_ev_baseline_metrics
    WHERE formula_version IS NULL
       OR population_source_year IS NULL
       OR energy_reporting_year IS NULL;
    IF row_count <> 0 THEN
        RAISE EXCEPTION '% districts publish a score without its formula version or source years',
            row_count;
    END IF;

    -- S08-R01: operating assets drive Renewable Context, whereas municipal
    -- workbook capacity has its own value, reporting year and coverage.  The
    -- canonical projection must preserve both concepts without attaching the
    -- workbook's quality metadata to the asset inventory.
    SELECT count(*) INTO row_count
    FROM analytics.nrw_ev_baseline_metrics d
    JOIN analytics.nrw_renewable_metrics assets USING (nuts_code)
    JOIN analytics.nrw_local_energy_balance workbook USING (nuts_code)
    WHERE d.operating_asset_renewable_capacity_mw
              IS DISTINCT FROM assets.renewable_capacity_mw
       OR d.operating_asset_renewable_capacity_mw_per_km2
              IS DISTINCT FROM assets.renewable_capacity_mw_per_km2
       OR d.municipal_workbook_renewable_capacity_mw
              IS DISTINCT FROM workbook.renewable_capacity_mw
       OR d.municipal_workbook_capacity_reporting_year
              IS DISTINCT FROM workbook.reporting_year
       OR d.municipal_workbook_renewable_capacity_coverage
              IS DISTINCT FROM workbook.renewable_capacity_coverage
       OR d.municipal_workbook_renewable_capacity_unavailable_reason
              IS DISTINCT FROM workbook.renewable_capacity_unavailable_reason;
    IF row_count <> 0 THEN
        RAISE EXCEPTION '% districts mix renewable asset capacity with municipal workbook metadata',
            row_count;
    END IF;

    -- Ranking follows the SQL rank: ties share a rank and the next distinct
    -- score skips, and an unavailable priority is never ranked above a real one.
    SELECT count(*) INTO row_count
    FROM analytics.nrw_ev_baseline_metrics a
    JOIN analytics.nrw_ev_baseline_metrics b
      ON a.investment_priority_score IS NOT DISTINCT FROM b.investment_priority_score
     AND a.priority_rank <> b.priority_rank;
    IF row_count <> 0 THEN
        RAISE EXCEPTION '% district pairs share a priority score but not its rank', row_count;
    END IF;
END;
$$;

SELECT
    COUNT(*) AS districts,
    MIN(infrastructure_opportunity_score) AS minimum_score,
    MAX(infrastructure_opportunity_score) AS maximum_score,
    ROUND(AVG(infrastructure_opportunity_score), 1) AS average_score
FROM analytics.nrw_infrastructure_opportunity;
