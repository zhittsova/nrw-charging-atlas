# Testing

Use the commands below from the repository root after `uv sync`.

## Unit suite

```bash
uv run pytest -q tests --ignore=tests/integration
```

This is the default fast suite. It has no Docker, database, network, or
`data/raw` requirement. Parser fixtures are deliberately synthetic and
labelled under `tests/fixtures/`; they are not source data.

## Required PostGIS suite

```bash
uv run python -m scripts.run_postgis_tests
```

This starts a randomly named `nrw_test_<id>` PostGIS database on its own
explicit `127.0.0.1:<random-port>` endpoint, runs the two SQL integration
modules (currently 11 tests), then forcibly removes only its container and its
uniquely named temporary data volume. It does not use the project database.
The command is required integration coverage: a missing Docker daemon, `psql`,
image, or database readiness is an error, not a skip.

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
