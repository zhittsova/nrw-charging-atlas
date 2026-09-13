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

Docker needs at least 6 GB RAM. `bootstrap --skip-download` reuses a complete
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

## Reference

- [Architecture](docs/architecture.md)
- [Local setup](docs/local_setup.md)
- [Data sources](docs/data_sources.md)
- [Data directory](data/README.md)
