# Architecture

The project covers NRW at NUTS-3 district level. `config/regions/nrw.yml`
defines the region, CRS, bounds, and input paths.

```text
Public sources -> Python loaders -> PostGIS -> GeoServer/GeoNode -> Leaflet dashboard
                         raw -> staging -> analytics -> publish
```

PostGIS owns the project data model and calculations. GeoServer publishes the
selected `publish` relations, GeoNode provides catalogue and permission
services, and the browser reads published layers and submits local proposed
stations through WFS-T.

`EPSG:4326` is used for web interchange. Area, length, and distance
calculations use `EPSG:25832`.

The main project database is `nrw_gis` with `raw`, `staging`, `analytics`,
`publish`, and `scenario` schemas. GeoNode keeps its own `geonode` and
`geonode_data` databases. The analytical SQL lives in `db/`; the lifecycle,
loaders, publication, and verification commands live in `scripts/`.

The dashboard supports official baseline data and local proposed-station
scenarios. Official records remain read-only. Grid indicators describe mapped
infrastructure context and do not measure distribution-network hosting
capacity.

## The analytical score model

PostGIS is the only scoring implementation. Each analytical domain is split in
two: a materialized view holding the measurements that need spatial work
(`analytics.nrw_transport_raw`, `nrw_grid_proxy_raw`, `nrw_renewable_raw`,
`nrw_energy_balance_raw`, and `nrw_district_metrics` in the schema document),
and live views that normalize and weight them. A score is therefore never
stored beside components it could disagree with.

Normalization is `analytics.normalize_5_95`: clip the measurement into the 5th
and 95th percentile of the official baseline, then map it onto 0-100, inverting
where a larger measurement is worse. Equal bounds give 50. A missing
measurement gives no score, and no weight is ever redistributed onto the
components that remain.

Every published score is rounded to one decimal, and every composite is the
weighted sum of its published components, rounded again. That makes each
composite reproducible by hand from the numbers shown next to it.

`analytics.nrw_score_model`, published as `publish.nrw_score_model`, states the
whole model as data: one row per weighted term, with the component it weights,
its weight and constant, the measured quantity and unit behind it, and that
measurement's official baseline bounds. `db/verify_nrw_analytics.sql`
recomputes every district's composites from that relation, so the published
model and the SQL cannot drift apart unnoticed.

The readiness density is charging points per km2. Stations per km2 remains
published as separately named station-density context and feeds no score.

`analytics.nrw_formula_version` carries the version these outputs were produced
by, and it travels on the canonical district projection together with the
population reference year, the energy reporting year and the charging-register
snapshot date. Version `nrw-2026.09.1` replaced an unversioned model that
normalized station density and weighted composites from unrounded components.

## Operational boundaries

The supported lifecycle is the `uv run python -m scripts.project_stack ...`
workflow documented in [local setup](local_setup.md). It creates canonical
runtime snapshots from the published PostGIS views; neither the browser nor a
separate Python dashboard recalculates scores. `catalog/data_sources.csv` and
`catalog/nrw_infrastructure_sources.csv` are the source registry, while each
runtime manifest records the exact source dates, hashes, feature counts, bounds
and formula version used for an export.

The system is local-first. Service ports bind to loopback; official station
data are read-only; the only write path is the local proposed-station scenario.
Scenario results are a comparison aid, not a future-demand forecast or an
engineering siting decision. See the current [demonstration and
operation](operational_handover.md) for the supported workflow,
limitations, recovery procedure, and deferred research.

## Public static deployment

The public build (`npm run build:public`) selects canonical exports immediately
and makes no GeoServer or scenario-service requests. GitHub Actions can refresh
those exports using an isolated PostGIS + Python batch job, verify them and deploy
the frontend to Cloudflare Pages. The hosted site has no application server or
database. See [public hosting](public_hosting.md) for setup and operation.
