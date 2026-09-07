# Local setup

Install Python 3.11 or newer, `uv`, Docker with Compose, and Git. Allocate at
least 6 GB RAM to Docker.

```bash
uv sync
uv run python scripts/project_stack.py bootstrap
```

`bootstrap` provisions the local GeoNode checkout, starts the stack, downloads
the public inputs, rebuilds `nrw_gis`, publishes the layers, and runs the
project verifier. Use `bootstrap --skip-download` only when `data/raw` already
contains the required source files.

## Everyday commands

```bash
uv run python scripts/project_stack.py start
uv run python scripts/project_stack.py fetch
uv run python scripts/project_stack.py seed
uv run python scripts/project_stack.py publish
uv run python scripts/project_stack.py verify
uv run python scripts/project_stack.py status
uv run python scripts/project_stack.py stop
```

The dashboard is at `http://localhost:8081`, GeoNode at
`http://localhost:8000`, and GeoServer at
`http://localhost:8080/geoserver`.

`geonode/.env`, raw downloads, generated outputs, and Docker volumes are
local state. Do not commit them. `stop` preserves volumes. `docker compose
down -v` removes persistent GeoNode and PostGIS data.

Run the Python and frontend checks listed in the root README before changing
the project.
