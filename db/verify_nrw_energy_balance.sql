-- Energy-balance assertions.
--
-- The selected reporting year and the wind full-load-hour assumption are read
-- from the analytics layer rather than written here as constants, so the module
-- keeps testing the dataset that was actually built (F32). Districts whose
-- municipal or year coverage is incomplete are expected to publish NULL with a
-- stated reason; that is a correct outcome, not a failure, but a NULL without a
-- reason is.
DO $$
DECLARE
    row_count bigint;
    mismatch_count bigint;
    selected_year integer;
    full_load_hours numeric;
    complete_districts bigint;
BEGIN
    SELECT count(*) INTO row_count FROM analytics.nrw_local_energy_balance;
    IF row_count <> 53 THEN
        RAISE EXCEPTION 'analytics.nrw_local_energy_balance has % rows; expected 53', row_count;
    END IF;

    SELECT count(*) INTO row_count FROM publish.nrw_local_energy_balance;
    IF row_count <> 53 THEN
        RAISE EXCEPTION 'publish.nrw_local_energy_balance has % rows; expected 53', row_count;
    END IF;

    SELECT count(*) INTO row_count FROM publish.nrw_grid_absorption_risk;
    IF row_count <> 53 THEN
        RAISE EXCEPTION 'publish.nrw_grid_absorption_risk has % rows; expected 53', row_count;
    END IF;

    SELECT count(DISTINCT reporting_year) INTO row_count
    FROM analytics.nrw_local_energy_balance;
    IF row_count <> 1 THEN
        RAISE EXCEPTION 'Districts report % different energy years; expected one', row_count;
    END IF;

    SELECT DISTINCT reporting_year INTO selected_year
    FROM analytics.nrw_local_energy_balance;
    IF selected_year IS NULL THEN
        RAISE EXCEPTION 'No energy reporting year could be selected from the municipal inputs';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM raw.energy_consumption_municipal WHERE year = selected_year
    ) THEN
        RAISE EXCEPTION 'Selected energy year % has no municipal consumption rows', selected_year;
    END IF;

    SELECT wind_full_load_hours INTO full_load_hours FROM analytics.nrw_energy_assumptions;

    -- An unavailable required input must name its reason, and an available one
    -- must not claim a reason it does not have.
    SELECT count(*) INTO mismatch_count
    FROM analytics.nrw_local_energy_balance
    WHERE (energy_data_quality_flag = 'missing_required_input')
          <> (energy_unavailable_reason IS NOT NULL);
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION '% district energy rows disagree about why an input is unavailable',
            mismatch_count;
    END IF;

    -- Coverage counts must be internally consistent: never more reported than
    -- expected, and a complete district must actually be complete.
    SELECT count(*) INTO mismatch_count
    FROM analytics.nrw_local_energy_balance
    WHERE consumption_municipalities_reported > expected_municipalities
       OR renewable_municipalities_reported > expected_municipalities
       OR growth_years_reported > growth_years_required
       OR (consumption_mwh IS NOT NULL
           AND consumption_municipalities_reported <> expected_municipalities)
       OR (renewable_net_addition_3y_mw IS NOT NULL
           AND growth_years_reported <> growth_years_required);
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION '% district energy rows publish a total without complete coverage',
            mismatch_count;
    END IF;

    -- Scores exist exactly where the required inputs are complete, and are in
    -- range and consistent with the agreed weights where they exist.
    SELECT count(*) INTO mismatch_count
    FROM analytics.nrw_local_energy_balance
    WHERE (grid_absorption_risk_proxy_score IS NULL)
          <> (energy_data_quality_flag = 'missing_required_input')
       OR (grid_absorption_risk_proxy_score IS NOT NULL
           AND (
               local_energy_balance_score NOT BETWEEN 0 AND 100
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
               ) > 0.05
           ));
    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION '% district energy rows are inconsistent or out of range', mismatch_count;
    END IF;

    SELECT count(*) INTO complete_districts
    FROM analytics.nrw_local_energy_balance
    WHERE consumption_mwh IS NOT NULL;
    IF complete_districts = 0 THEN
        RAISE EXCEPTION 'No district has complete municipal consumption coverage for %', selected_year;
    END IF;

    -- Published district totals must reconcile to the municipal inputs of the
    -- selected year, over the districts that publish a total at all.
    SELECT count(*) INTO mismatch_count
    FROM (
        SELECT
            ABS(
                SUM(a.consumption_mwh)
                - (
                    SELECT SUM(c.consumption_gwh) * 1000
                    FROM raw.energy_consumption_municipal c
                    WHERE c.year = selected_year
                      AND c.nuts_code IN (
                          SELECT nuts_code FROM analytics.nrw_local_energy_balance
                          WHERE consumption_mwh IS NOT NULL
                      )
                )
            ) AS consumption_difference,
            ABS(
                SUM(a.total_renewable_generation_mwh)
                - (
                    SELECT SUM(r.published_generation_mwh + r.wind_capacity_mw * full_load_hours)
                    FROM raw.renewable_balance_municipal r
                    WHERE r.year = selected_year
                      AND r.nuts_code IN (
                          SELECT nuts_code FROM analytics.nrw_local_energy_balance
                          WHERE total_renewable_generation_mwh IS NOT NULL
                      )
                )
            ) AS generation_difference
        FROM analytics.nrw_local_energy_balance a
        WHERE a.consumption_mwh IS NOT NULL
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
    COUNT(*) FILTER (WHERE energy_unavailable_reason IS NULL) AS complete_districts,
    COUNT(*) FILTER (WHERE energy_unavailable_reason IS NOT NULL) AS unavailable_districts,
    ROUND(MIN(renewable_coverage_pct), 1) AS minimum_coverage_pct,
    ROUND(MAX(renewable_coverage_pct), 1) AS maximum_coverage_pct,
    ROUND(AVG(grid_absorption_risk_proxy_score), 1) AS average_risk_score
FROM analytics.nrw_local_energy_balance
GROUP BY reporting_year;
