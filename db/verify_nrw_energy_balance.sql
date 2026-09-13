DO $$
DECLARE
    row_count bigint;
    mismatch_count bigint;
BEGIN
    SELECT count(*) INTO row_count
    FROM analytics.nrw_local_energy_balance;
    IF row_count <> 53 THEN
        RAISE EXCEPTION 'analytics.nrw_local_energy_balance has % rows; expected 53', row_count;
    END IF;

    SELECT count(*) INTO row_count
    FROM publish.nrw_local_energy_balance;
    IF row_count <> 53 THEN
        RAISE EXCEPTION 'publish.nrw_local_energy_balance has % rows; expected 53', row_count;
    END IF;

    SELECT count(*) INTO row_count
    FROM publish.nrw_grid_absorption_risk;
    IF row_count <> 53 THEN
        RAISE EXCEPTION 'publish.nrw_grid_absorption_risk has % rows; expected 53', row_count;
    END IF;

    SELECT count(*) INTO mismatch_count
    FROM analytics.nrw_local_energy_balance
    WHERE reporting_year <> 2024
       OR consumption_mwh IS NULL
       OR total_renewable_generation_mwh IS NULL
       OR local_energy_balance_score NOT BETWEEN 0 AND 100
       OR renewable_growth_score NOT BETWEEN 0 AND 100
       OR grid_absorption_risk_proxy_score NOT BETWEEN 0 AND 100
       OR ABS(
            grid_absorption_risk_proxy_score
            - ROUND(
                0.45 * local_energy_balance_score
                + 0.30 * renewable_growth_score
                + 0.25 * (100 - grid_readiness_proxy_score),
                1
            )
       ) > 0.05;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION '% district energy rows are incomplete or inconsistent', mismatch_count;
    END IF;

    SELECT count(*) INTO mismatch_count
    FROM (
        SELECT
            ABS(
                SUM(a.consumption_mwh)
                - (
                    SELECT SUM(c.consumption_gwh) * 1000
                    FROM raw.energy_consumption_municipal c
                    WHERE c.year = 2024
                )
            ) AS consumption_difference,
            ABS(
                SUM(a.total_renewable_generation_mwh)
                - (
                    SELECT SUM(
                        r.published_generation_mwh
                        + r.wind_capacity_mw * 2000
                    )
                    FROM raw.renewable_balance_municipal r
                    WHERE r.year = 2024
                )
            ) AS generation_difference
        FROM analytics.nrw_local_energy_balance a
    ) totals
    WHERE consumption_difference > 0.01
       OR generation_difference > 0.01;
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'District energy totals do not reconcile to municipal inputs';
    END IF;
END;
$$;

SELECT
    reporting_year,
    COUNT(*) AS districts,
    ROUND(MIN(renewable_coverage_pct), 1) AS minimum_coverage_pct,
    ROUND(MAX(renewable_coverage_pct), 1) AS maximum_coverage_pct,
    ROUND(AVG(grid_absorption_risk_proxy_score), 1) AS average_risk_score
FROM analytics.nrw_local_energy_balance
GROUP BY reporting_year;
