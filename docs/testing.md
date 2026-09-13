# Testing

Use the commands below from the repository root after `uv sync --frozen`.

## Required CI checks

GitHub Actions runs these independent required jobs on pushes and pull requests:

```bash
uv run ruff check .
uv run pytest -q tests --ignore=tests/integration
cd frontend && npm ci && npm test && npm run build
uv run python -m scripts.run_postgis_tests
```

Python dependencies are installed with `uv sync --frozen --all-groups` and
frontend dependencies with `npm ci`, so the committed lockfiles are the CI
inputs. The PostGIS job starts a fresh labelled container and volume; it does
not require `data/raw`, `data/runtime`, a GeoNode checkout, a local `.env`, or
any ignored personal fixture. Its integration modules are passed as explicit
pytest paths by `scripts.run_postgis_tests`, so a missing test discovery cannot
turn the job green.

The Ruff baseline intentionally covers syntax, import/name errors and related
runtime-affecting checks (`E4`, `E7`, `E9`, and `F`), with the configured
compatibility allowance for existing unused imports/locals. Broader formatting
and cleanup are deliberately separate from the CI baseline.

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
explicit `127.0.0.1:<random-port>` endpoint, runs the runner's nine explicit
SQL integration modules, then forcibly removes only its container and its
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
`test_nrw_score_model.py` checks the canonical score model against
`tests/fixtures/score_model_cases.py`, an expectation catalogue derived from the
written contract rather than from the SQL and proved self-consistent in
`tests/test_score_model_catalogue.py`. Its six-district fixture is arranged so
that one district has no mapped grid line, one has no charging station, one pair
mirrors the other about the UTM zone 32 central meridian to produce a genuine
rank tie, and every district has exactly one renewable technology so the
equal-bound rule has to return 50.
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
# Prepare persistent deployment state here, outside every Actions workspace.
# Use the full commit SHA that will be dispatched; do not run the verifier from
# this checkout, because its state is intentionally rejected as non-disposable.
DEPLOYMENT_ROOT=/opt/nrw-full-stack/deployment
REVISION="<full commit SHA to verify>"
cd "$DEPLOYMENT_ROOT"
git checkout --detach "$REVISION"
REVISION="$(git rev-parse HEAD)"
uv run python -m scripts.project_stack bootstrap
printf '%s\n' "$REVISION" > revision

# Clone a separate, disposable tracked-source checkout for verification. This
# clone contains no generated deployment state. Remove VERIFY_ROOT afterwards.
VERIFY_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/nrw-full-stack-verify.XXXXXX")"
git clone --no-checkout "$DEPLOYMENT_ROOT" "$VERIFY_ROOT"
git -C "$VERIFY_ROOT" checkout --detach "$REVISION"
(
  cd "$VERIFY_ROOT"
  uv sync --frozen --all-groups
  GEONODE_INTEGRATION=1 uv run python -m scripts.run_full_stack_checks --enabled \
    --geonode-env-file "$DEPLOYMENT_ROOT/geonode/.env" \
    --runtime-root "$DEPLOYMENT_ROOT/data/runtime" \
    --deployment-revision-file "$DEPLOYMENT_ROOT/revision" \
    --expected-revision "$REVISION"
)
```

This path is intentionally opt-in: it needs a deliberately bootstrapped,
healthy local stack (including generated runtime assets and the local source
inputs required by bootstrap). It runs the GeoNode foundation checks and the
public end-to-end verifier, whose proposal mutation is owned and cleaned up by
request ID. `uv run python -m scripts.run_full_stack_checks` without
`--enabled` reports `SKIPPED`; it never calls that state a pass. Passing
`--enabled` without `GEONODE_INTEGRATION=1`, or with an unhealthy stack,
reports `FAILED` and returns nonzero.

For self-hosted Actions, `/opt/nrw-full-stack/deployment` is the required
preserved deployment/configuration checkout, not an Actions workspace. Before
dispatching a revision, the operator must check out that exact SHA there and
prepare its `geonode/.env`, `data/runtime/`, and `revision` file; `revision`
must contain the resolved exact SHA being dispatched. Manual verification must
likewise run from a separate disposable source checkout, with the deployment
paths passed as external state; running it from the deployment checkout is
rejected by design. The workflow deliberately performs a clean checkout in its
separate Actions workspace and passes all three external paths explicitly: the foundation test receives
`GEONODE_ENV_FILE`, and the end-to-end verifier receives `--runtime-root`. It
rejects a missing path, a path inside the disposable checkout, or a revision
mismatch before probing the stack. Keep deployment configuration and generated
inputs outside GitHub Actions' workspace; never test Actions cleanup against a
user installation.

The workflow has the same path as a manually dispatched `full-stack` job, but
only on a self-hosted runner labelled `nrw-full-stack` that an operator has
prepared with the required external state. Routine hosted CI deliberately does
not download large source archives or invent an environment for this optional
acceptance.
