# NRW Charging Atlas

[![CI](https://github.com/zhittsova/nrw-charging-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/zhittsova/nrw-charging-atlas/actions/workflows/ci.yml)

Explore charging coverage for electric vehicles (EV) across North Rhine-Westphalia
(NRW), Germany, compare district readiness, and test proposed charging scenarios.

The dashboard compares all 53 NRW districts using public charging, population,
transport, grid and energy data. It helps identify districts worth investigating
and shows how proposed stations change charger-dependent indicators.

![NRW district map with charging and infrastructure layers](docs/images/dashboard.png)

## What you can do

- Compare underserved districts, investment priority and relative EV readiness.
- Inspect the measurements, formulas, source years and data-quality notes behind a score.
- Explore official charging stations, roads, mapped power infrastructure, wind
  farms and ground-mounted solar installations.
- Add proposed stations and compare Baseline, Scenario and Change. Proposals
  persist across restarts; official records remain read-only.

Scores support district-level investigation. Grid indicators describe mapped
infrastructure, not available connection capacity. The application does not
forecast demand or determine whether a particular site can be built.

## Run locally

For a public read-only site with automated data refreshes, see
[GitHub Actions + Cloudflare Pages](docs/public_hosting.md). The deployment
configuration targets `nrw-ev-atlas.zhittsova.com` and uses only static Pages
hosting; scenario editing remains available in the local stack below.

Install Git, Python 3.11 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/)
and Docker with Compose 2.24.4 or newer. Allocate at least four CPUs and 4 GiB
RAM to Docker, with additional memory available for the host and browser.
The first run needs internet access for images, the pinned GeoNode checkout and
roughly 1 GB of source data. Allow additional disk space for images and databases.

```bash
git clone https://github.com/zhittsova/nrw-charging-atlas.git
cd nrw-charging-atlas
uv sync --frozen
uv run python -m scripts.project_stack bootstrap
```

Bootstrap builds the containers, downloads and validates the sources, prepares
PostGIS, exports dashboard snapshots, publishes the layers and verifies the
installation. The first image build and data import can take tens of minutes
on a small Docker VM. The command prints the service addresses when the
installation is ready.

| Service | Address |
| --- | --- |
| Dashboard | http://localhost:8081 |
| GeoNode catalogue | http://localhost:8000 |
| GeoServer | http://localhost:8080/geoserver |

For subsequent starts and stops:

```bash
uv run python -m scripts.project_stack start
uv run python -m scripts.project_stack status
uv run python -m scripts.project_stack stop
```

`stop` preserves databases and proposals. See [local setup](docs/local_setup.md)
for direct Docker Compose commands, source refresh, configuration and troubleshooting.

## GeoNode and PostGIS

[GeoNode](https://geonode.org/) provides the dataset catalogue, metadata and
publication permissions. [PostGIS](https://postgis.net/) stores the spatial data
and computes district measurements and scores. GeoServer publishes the layers
through WMS/WFS and handles proposed-station transactions through WFS-T.
The Leaflet and TypeScript frontend reads those results without implementing a
second scoring model.

Python loaders validate public inputs before an atomic database refresh. The
project exports canonical GeoJSON snapshots from PostGIS for explicitly labelled
fallback views. Editing becomes unavailable when live scenario services fail.

Read the [architecture](docs/architecture.md) and [source documentation](docs/data_sources.md)
for the data model, analytical definitions and provenance.

## Verify

```bash
uv run ruff check .
uv run pytest -q tests --ignore=tests/integration
uv run python -m scripts.run_postgis_tests
(cd frontend && npm ci && npm test && npm run build)
```

The PostGIS suite needs Docker and the `psql` client. It creates and removes its
own database container and volume. Node.js 22.12 or newer is needed for the frontend checks;
normal application startup builds the frontend inside Docker.
See [testing](docs/testing.md) for the checks and optional full-stack verification.

## Documentation

- [Local setup and troubleshooting](docs/local_setup.md)
- [Architecture and scoring](docs/architecture.md)
- [Demonstration and operation](docs/operational_handover.md)
- [Data sources](docs/data_sources.md)
- [Data directory](data/README.md)
- [Attribution and third-party notices](docs/data-attribution.md)

## Licence

Project-owned code has no public licence grant. Source-data terms and upstream
software licences are documented separately in [the attribution notice](docs/data-attribution.md).
