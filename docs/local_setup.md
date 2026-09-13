# Local setup

## Requirements

- Git, Python 3.11 or newer and uv.
- Docker with Compose 2.24.4 or newer, four CPUs and a 4 GiB memory allocation.
- Internet access for the first bootstrap and enough disk space for container
  images, databases and roughly 1 GB of raw inputs.

Keep memory available for the host and browser. On an 8 GiB Mac, allocating
nearly all RAM to Docker can cause swapping and WFS timeouts. The local profile
bounds web and task workers and uses amd64 GeoNode images on Apple Silicon.

## First installation

```bash
git clone https://github.com/zhittsova/nrw-charging-atlas.git
cd nrw-charging-atlas
uv sync --frozen
uv run python -m scripts.project_stack bootstrap
```

Bootstrap checks Docker resources, clones the pinned GeoNode source, generates
private configuration, builds and starts the services, fetches the public inputs,
seeds PostGIS, exports canonical snapshots, publishes the layers and verifies
the result. It does not require manual dataset uploads.

The dashboard is at http://localhost:8081, GeoNode at http://localhost:8000 and
GeoServer at http://localhost:8080/geoserver. All service ports bind to loopback.
The first image build and data import can take tens of minutes on a small
Docker VM. Later starts reuse the images and data.

Configuration is generated in `geonode/.env` with owner-only permissions.
Initialization also prepares build exclusions so local credentials, Git metadata
and generated Python caches stay out of the GeoNode image build.
Existing passwords are retained when initialization is repeated. Local GeoNode
administrator credentials are in that file; inspect them locally when signing
in to the catalogue. Keep the file private. Source downloads, runtime exports
and Docker volumes are local state and do not belong in Git.

## Docker Compose

The root `docker-compose.yaml` includes the pinned upstream stack and the
project overrides. The root `Dockerfile` has `frontend` and `etl` targets;
the frontend is the default build target. Python lifecycle commands use the
same root Compose file.

Prepare configuration before using Compose on a fresh clone:

```bash
uv run python -m scripts.project_stack init
docker compose config --quiet
```

Initialization alone does not download source datasets or seed the database.
Run `bootstrap` once for a complete installation. After that, direct service
commands are available from the repository root:

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail 100 geoserver
docker compose stop
```

The project `start` command also checks resources and waits for service health.
`rebuild` rebuilds both project images without cache and recreates containers
while preserving named volumes. To build the project images without starting
services:

```bash
docker compose --profile tools build frontend nrw-etl
```

Compose uses its [include mechanism](https://docs.docker.com/reference/compose-file/include/)
to preserve upstream paths and environment settings. The project overrides also
use `!override`, which requires Compose 2.24.4 or newer.

## Update source data

```bash
uv run python -m scripts.project_stack fetch
uv run python -m scripts.project_stack seed
uv run python -m scripts.project_stack export
uv run python -m scripts.project_stack publish
uv run python -m scripts.project_stack verify
```

`fetch` reuses files only when their recorded identity and checksums validate.
Use `fetch --refresh` to retrieve fresh inputs explicitly. `bootstrap
--skip-download` skips fetching and requires a complete valid local cache.

`seed` refreshes the project data in one transaction and preserves proposed
stations. If validation or import fails, previously published data remain
available. `export` reads the five canonical views in one repeatable-read
transaction and replaces `data/runtime/current/` only after the complete set
passes validation. The frontend mounts that directory read-only; full snapshots
are not copied into its image.

The renewable display export contains operating wind and ground-mounted solar.
District renewable totals use the broader analytical inventory. This distinction
is documented in the dashboard and [source guide](data_sources.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Compose reports missing files or configuration | Run `uv run python -m scripts.project_stack init` first. |
| Compose rejects `include` or `!override` | Check `docker compose version`; use 2.24.4 or newer. |
| Docker resource check fails | Allocate four CPUs and 4 GiB RAM, leaving memory for the host. |
| A service cannot bind its port | Check ports 8000, 8080, 8081, 8443 and 5433 for another installation. |
| GeoServer is still starting | Inspect `docker compose logs --tail 100 geoserver` and `docker compose ps`. |
| Source retrieval fails | Retry `fetch`; incomplete or mismatched files are validated before reuse. |
| Dashboard shows a snapshot or unavailable data | Inspect service health, then run `export`, `publish` and `verify` as needed. |

Use `stop` for normal shutdown. `docker compose down -v` deletes persistent
volumes, including databases and proposals. Do not use it for a normal restart.

## Backup and restore

The backup tool captures a logical dump of
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
other shared directories.
