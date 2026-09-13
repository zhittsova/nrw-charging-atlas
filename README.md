# NRW Energy Infrastructure Intelligence

Local geospatial decision support for electric-vehicle charging infrastructure
in North Rhine-Westphalia. The project uses PostGIS for data preparation and
district metrics, GeoServer and GeoNode for publication, and a Leaflet/
TypeScript dashboard for baseline and proposed-station scenarios.

The analytical geography is the 53 NRW NUTS-3 districts. Grid results are
mapped-infrastructure planning proxies, not statements about available grid
capacity.

## Run

```bash
uv sync
uv run python -m scripts.project_stack bootstrap
```

Use four Docker CPUs and a 4 GiB memory allocation for the bounded local worker
pools. Leave RAM for macOS and the browser; allocating nearly all host memory
to Docker can make even small WFS reads time out. `bootstrap --skip-download` reuses a complete
local `data/raw` directory.

The supported Docker entry point is the project CLI above; do not run the
retired root `docker-compose.yml`. Use `uv run python -m scripts.project_stack
rebuild` to rebuild the local frontend and ETL images without cache while
preserving named volumes.

The local endpoints are:

```text
Dashboard: http://localhost:8081
GeoNode:   http://localhost:8000
GeoServer: http://localhost:8080/geoserver
```

## Verify

```bash
uv run pytest -q tests --ignore=tests/integration
uv run python -m scripts.run_postgis_tests

cd frontend
npm test -- --run
npm run build
```

The first command is the isolated unit suite; the second creates and removes
its own disposable PostGIS resources. See [testing](docs/testing.md) for
optional running-stack checks and failure/skip behavior.

## Backup and isolated restore

Create a restricted local backup outside Git, then restore it only into a fresh
`nrw-restore-*` Compose target. The drill checks database/catalog/layer and
scenario behavior against that isolated copy, then removes only the drill's
target containers, volumes, and private restore workspace.

```bash
uv run python -m scripts.backup_restore backup
uv run python -m scripts.backup_restore drill
```

Backups are created under ignored `backups/` with owner-only permissions. They
contain database dumps, GeoServer configuration, media/static state, required
local configuration, and runtime/source manifests; do not copy them to Git or
spec evidence. The operator owns retention: retain at least one recently
verified backup, test restoration before deleting an older known-good backup,
and handle the files as credentials. See [local setup](docs/local_setup.md)
for restore and safety details.

## Reference

- [Architecture](docs/architecture.md)
- [Local setup](docs/local_setup.md)
- [Data sources](docs/data_sources.md)
- [Data directory](data/README.md)
