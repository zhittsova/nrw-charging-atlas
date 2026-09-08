# Local setup

Install Python 3.11 or newer, `uv`, Docker with Compose, and Git. Allocate at
least 6 GB RAM to Docker.

```bash
uv sync
uv run python -m scripts.project_stack bootstrap
```

`bootstrap` provisions the local GeoNode checkout, starts the stack, downloads
the public inputs, rebuilds `nrw_gis`, publishes the layers, and runs the
project verifier. Use `bootstrap --skip-download` only when `data/raw` already
contains the required source files.

## Everyday commands

```bash
uv run python -m scripts.project_stack start
uv run python -m scripts.project_stack rebuild
uv run python -m scripts.project_stack fetch
uv run python -m scripts.project_stack seed
uv run python -m scripts.project_stack publish
uv run python -m scripts.project_stack verify
uv run python -m scripts.project_stack status
uv run python -m scripts.project_stack stop
```

The dashboard is at `http://localhost:8081`, GeoNode at
`http://localhost:8000`, and GeoServer at
`http://localhost:8080/geoserver`.

`geonode/.env`, raw downloads, generated outputs, and Docker volumes are
local state. Do not commit them. `stop` preserves volumes. `docker compose
down -v` removes persistent GeoNode and PostGIS data.

The project CLI is the only supported Compose entry point. The root
`docker-compose.yml` is retired and must not be used. `start` builds changed
images; `rebuild` uses `--no-cache` and recreates containers without removing
named volumes. Before startup it reports Docker memory and disk use, and stops
if Docker has less than 6 GiB RAM. Local service ports bind only to loopback.

Run the Python and frontend checks listed in the root README before changing
the project.
