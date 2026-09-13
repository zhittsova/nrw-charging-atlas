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

## Backup and restore

S19 provides a local, restricted backup tool. It captures a logical dump of
the integrated PostGIS cluster (both project and GeoNode databases and roles),
the GeoServer data directory, media/static state, shared data and generated
nginx configuration/certificates, the local environment configuration, and the
canonical runtime/source manifests. Backups are intentionally written to the
ignored `backups/` directory with owner-only permissions; they may contain
passwords and certificates.

```bash
uv run python -m scripts.backup_restore backup
uv run python -m scripts.backup_restore drill
```

`drill` creates a new backup and restores it into a new `nrw-restore-*`
Compose project using separate loopback ports. It checks the backup manifest
and every component checksum before restoring, runs the database verifier and
the public catalog/WFS persisted-scenario verifier against the copy, and
removes only the drill's target resources afterwards. It never deletes source
volumes or changes the live stack.

To retain an isolated restored copy for investigation, use a fresh target name
and pass `--verify`; the tool refuses a name outside `nrw-restore-*`, any
pre-existing target volume, any active target project, and any overlap with a
source volume:

```bash
uv run python -m scripts.backup_restore restore backups/<backup-directory> \
  --target nrw-restore-investigation --verify
```

Restore targets are not live replacements. Remove an investigation target only
after its evidence is recorded, using Docker commands that name that exact
`nrw-restore-*` project and its volumes. The operator owns retention: keep at
least one recently verified backup, verify a newer backup before discarding a
known-good older one, and never place backups or their manifests in Git or
`specs/`.
