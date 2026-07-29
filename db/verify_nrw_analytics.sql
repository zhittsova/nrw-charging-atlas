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

    SELECT count(*) INTO row_count
    FROM analytics.nrw_infrastructure_opportunity
    WHERE transport_load_score IS NULL
       OR grid_readiness_proxy_score IS NULL
       OR renewable_context_score IS NULL
       OR infrastructure_opportunity_score IS NULL
       OR transport_load_score NOT BETWEEN 0 AND 100
       OR grid_readiness_proxy_score NOT BETWEEN 0 AND 100
       OR renewable_context_score NOT BETWEEN 0 AND 100
       OR infrastructure_opportunity_score NOT BETWEEN 0 AND 100;

    IF row_count <> 0 THEN
        RAISE EXCEPTION '% districts have missing or out-of-range infrastructure scores', row_count;
    END IF;
END;
$$;

SELECT
    COUNT(*) AS districts,
    MIN(infrastructure_opportunity_score) AS minimum_score,
    MAX(infrastructure_opportunity_score) AS maximum_score,
    ROUND(AVG(infrastructure_opportunity_score), 1) AS average_score
FROM analytics.nrw_infrastructure_opportunity;
