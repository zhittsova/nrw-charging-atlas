# Testing

Use the commands below from the repository root after `uv sync`.

## Unit suite

```bash
uv run pytest -q tests --ignore=tests/integration
```

This is the default fast suite. It has no Docker, database, network, or
`data/raw` requirement. Parser fixtures are deliberately synthetic and
labelled under `tests/fixtures/`; they are not source data.
`tests/fixtures/projected_geometry.py` is an independent EPSG:25832 oracle used
to check spatial results without restating the SQL that produced them, and
`tests/fixtures/geometry_cases.py` is a shared catalogue of polygon topology
cases whose GEOS verdicts are proved in `tests/test_geometry_cases.py` and
cross-checked against PostGIS in the SQL suite.

## Required PostGIS suite

```bash
uv run python -m scripts.run_postgis_tests
```

This starts a randomly named `nrw_test_<id>` PostGIS database on its own
explicit `127.0.0.1:<random-port>` endpoint, runs the six SQL integration
modules (currently 64 tests), then forcibly removes only its container and its
uniquely named temporary data volume. It does not use the project database.
The command is required integration coverage: a missing Docker daemon, `psql`,
image, or database readiness is an error, not a skip.

`test_nrw_spatial_semantics.py` covers the projected nearest-feature
selection, the Strassen.NRW road-class mapping and district containment; each of
its classes resets the schemas and loads its own fixture.
`test_nrw_schema_migration.py` starts from a reconstruction of the older
published column layout instead of a fresh schema, because the upgrade contract
only fails on a database that predates a column.
`test_nrw_missing_data_semantics.py` checks the energy, grid-voltage and traffic
coverage rules against `tests/fixtures/energy_coverage_cases.py`, whose expected
district roll-ups are derived by hand and proved self-consistent in
`tests/test_energy_coverage_cases.py`, and the voltage-tag catalogue in
`tests/fixtures/voltage_tag_cases.py`.
`test_nrw_verification_modules.py` builds a full 53-district fixture and
executes `db/verify_nrw_analytics.sql` and `db/verify_nrw_energy_balance.sql`
for real, including a district whose consumption is a measured zero, and proves
each module rejects quality metadata that contradicts its own data.

Do not invoke the SQL modules directly with a hand-written database URL. The
runner provides a current random run ID and exact loopback endpoint alongside
`SCENARIO_TEST_DATABASE_URL`; the integration modules require all three to
match before their schema-reset SQL runs. This rejects project, shared, stale,
and differently addressed targets.

## Optional running-stack checks

```bash
uv run pytest -q tests/integration/test_geonode_foundation.py
```

Without `GEONODE_INTEGRATION=1`, these three running-stack tests report as
skipped. To opt in, run the same command with `GEONODE_INTEGRATION=1` only
against a deliberately running local stack. A skip is not full-stack
acceptance.
