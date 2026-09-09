DO $$
DECLARE
    relation_name text;
    row_count bigint;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'analytics.nrw_transport_metrics',
        'analytics.nrw_grid_proxy_metrics',
        'analytics.nrw_renewable_metrics',
        'analytics.nrw_infrastructure_opportunity',
        'publish.nrw_transport_load',
        'publish.nrw_grid_proxy',
        'publish.nrw_renewable_context',
        'publish.nrw_infrastructure_opportunity',
        'publish.nrw_district_priority'
    ]
    LOOP
        EXECUTE format('SELECT count(*) FROM %s', relation_name) INTO row_count;
        IF row_count <> 53 THEN
            RAISE EXCEPTION '% has % rows; expected 53', relation_name, row_count;
        END IF;
    END LOOP;

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
END;
$$;

SELECT
    COUNT(*) AS districts,
    MIN(infrastructure_opportunity_score) AS minimum_score,
    MAX(infrastructure_opportunity_score) AS maximum_score,
    ROUND(AVG(infrastructure_opportunity_score), 1) AS average_score
FROM analytics.nrw_infrastructure_opportunity;
