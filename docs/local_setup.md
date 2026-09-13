# Local setup

Install Python 3.11 or newer, `uv`, Docker with Compose, and Git. Allocate four
CPUs and 4 GiB RAM to Docker for the local worker profile. On an 8 GiB Mac,
allocating 7 GiB to Docker leaves too little RAM for macOS and the browser and
can cause severe swapping and WFS timeouts.

```bash
uv sync
uv run python -m scripts.project_stack bootstrap
```

`bootstrap` provisions the local GeoNode checkout, starts the stack, downloads
the public inputs, rebuilds `nrw_gis`, exports the canonical dashboard
snapshots, publishes the layers, and runs the project verifier. Use `bootstrap
--skip-download` only when `data/raw` already contains the required source
files.

## Everyday commands

```bash
uv run python -m scripts.project_stack start
uv run python -m scripts.project_stack rebuild
uv run python -m scripts.project_stack fetch
uv run python -m scripts.project_stack seed
uv run python -m scripts.project_stack export
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

`export` reads one repeatable-read transaction from the five canonical
`publish` views and atomically replaces `data/runtime/current/`. It writes the
five `/data/*.geojson` fallback files and `manifest.json`; the frontend serves
that ignored directory directly, so no snapshot is copied into an image. The
renewable fallback contains only operating `Windenergie` and `Photovoltaik
Freifläche` records. It does not change the raw renewable inventory or district
analytical totals.

The project CLI is the only supported Compose entry point. The root
`docker-compose.yml` is retired and must not be used. `start` builds changed
images; `rebuild` uses `--no-cache` and recreates containers without removing
named volumes. Before startup it reports Docker memory and disk use, and stops
if Docker reports less than 3.5 GiB usable RAM (allowing guest overhead in a
4 GiB VM) or fewer than four CPUs. The local override bounds uWSGI to one-two
workers, normal Celery tasks to one-two workers, and harvesting to zero-one
workers. Both task queues remain available. Local service ports bind only to loopback.

Run the Python and frontend checks listed in the root README before changing
the project.
