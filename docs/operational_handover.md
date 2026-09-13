# Operational and professor handover

**Current status:** 13 September 2026. This tracked guide is the authoritative
English handover for the local NRW demonstrator. Historical material in `more/`
is preserved as provenance, not current instructions.

## Purpose and first visit

This demonstrator compares all **53 NRW NUTS-3 districts** for three screening
questions:

1. Which districts are underserved relative to population and accessibility?
2. Where should new EV charging stations be investigated first?
3. Which districts appear relatively prepared for electrification from mapped
   infrastructure context?

It is comparative screening, not a future-demand forecast, a measurement of
distribution-network capacity, a site recommendation, or a replacement for
engineering, land, permitting, and connection studies. Python validates public
inputs; PostGIS calculates canonical analytics; GeoServer/GeoNode publish
layers and catalogue records; and the Leaflet/TypeScript dashboard displays
results. The browser does not calculate a second score model.

1. From the repository root run `uv sync`, then `uv run python -m
   scripts.project_stack bootstrap`. Use `--skip-download` only with a complete
   validated `data/raw/` cache.
2. Open `http://localhost:8081`; GeoNode is at `http://localhost:8000/datasets`
   and GeoServer at `http://localhost:8080/geoserver`.
3. Use the three question controls, metric selector, legend, map, and ranking
   together: they select the same metric. Scroll to the end so all 53 districts
   are inspected. Known values are ranked; unavailable values are separately
   listed with their reason and can still be selected on the map.
4. Select a district to inspect components, source years, coverage, quality
   notes, and score-model terms. Station, road, and renewable overlays add
   context; official stations are read-only.
5. Add a local proposal with charging-point count and power only for comparison.
   Compare Baseline, Scenario, and Change. A scenario persists locally and
   changes charger-dependent district measures only; clear only proposals owned
   by this browser.

Keyboard users can tab through modal and ranking controls and return to the
selected district. The full ranking and proposal controls remain available on
small screens.

## Scores, sources, and scope

Components use official baseline 5th-95th-percentile bounds, clipped and
normalized to 0-100; higher-is-worse measures are inverted. Equal bounds are
50. A missing required measurement makes the affected score unknown; weights
are never redistributed. Published components and composites are rounded to one
decimal. `nrw_score_model` publishes terms, units, and bounds.

| Indicator | Formula |
| --- | --- |
| EV Readiness | 40% charging points/km² + 30% inverse nearest-station distance + 30% charging points/100,000 people |
| Charger Deficit | 100 − EV Readiness |
| Transport Load | 60% traffic intensity + 25% traffic-weighted road density + 15% inverse nearest-road distance |
| Grid Readiness Proxy | 45% inverse nearest-substation distance + 35% voltage-weighted line density + 20% substation density |
| Renewable Context | 70% installed renewable capacity/km² + 30% distinct operating technologies |
| Infrastructure Opportunity | 40% Transport Load + 35% Grid Readiness Proxy + 25% Renewable Context |
| Investment Priority | 60% Charger Deficit + 40% Infrastructure Opportunity |
| Grid Absorption Risk Proxy | 45% local energy balance + 30% renewable growth + 25% inverse Grid Readiness Proxy |

"Most underserved" is the largest charger deficit; "highest priority" also
uses infrastructure opportunity, so they can differ. In Change mode deltas are
signed and deterministically sorted; a larger value is not automatically an
improvement, and scenario investment-priority rank is context, not a delta
rank.

The authoritative source registries are `catalog/data_sources.csv` and
`catalog/nrw_infrastructure_sources.csv`; see [data sources](data_sources.md)
and [attribution](data-attribution.md). Sources include Eurostat boundaries and
population, Bundesnetzagentur charging, Straßen.NRW traffic, Energieatlas NRW,
Geofabrik OpenStreetMap, and Open Power System Data. Runtime exports record
source dates, hashes, feature counts, formula version, and artifact hashes in
`data/runtime/current/manifest.json`.

The renewable overlay deliberately displays only operating wind and
ground-mounted solar (`Windenergie` and `Photovoltaik Freifläche`). That map
filter does **not** narrow the broader renewable records used in district
analytical totals. Grid values are visible-infrastructure proxies, not evidence
of spare feeder capacity. Traffic coverage, source dates, and unavailable
values remain visible instead of being filled.

## Operation, refresh, and recovery

Install Python 3.11+, `uv`, Docker Compose, and Git. Allocate four Docker CPUs
and 4 GiB RAM. `status` reports health and `stop` preserves volumes.

```bash
uv run python -m scripts.project_stack status
uv run python -m scripts.project_stack fetch
uv run python -m scripts.project_stack seed
uv run python -m scripts.project_stack export
uv run python -m scripts.project_stack publish
uv run python -m scripts.project_stack verify
uv run python -m scripts.project_stack stop
```

For a routine refresh, use those actions in order, or use `bootstrap`.
`fetch --refresh` replaces a validated cache; `seed` retains prior usable
published data if validation/import fails; `export` atomically replaces a
complete canonical runtime set only after a successful seed. `rebuild`
recreates images without cache while preserving named volumes. The root
`docker-compose.yml`, pip/`requirements.txt` installs, static preview servers,
and old Streamlit dashboard are retired and unsupported. If Docker fails, free
disk/RAM, start Docker, run `status`, then retry the smallest relevant action.
Do not use `docker compose down -v`: it destroys persistent state. See [local
setup](local_setup.md) and [testing](testing.md) for lifecycle and verification.

Create restricted ignored backups and test restoration only in a fresh
`nrw-restore-*` target:

```bash
uv run python -m scripts.backup_restore backup
uv run python -m scripts.backup_restore drill
```

Backups include databases/roles, GeoServer configuration, media/static state,
needed local configuration, and runtime/source manifests. They can contain
secrets: retain owner-only permissions, never commit them, and never overwrite
a live stack for a restore test. Preserve proposed stations through normal
refreshes and verify a newer backup before discarding a known-good one.

## Licensing and deferred work

Project-owned code has **no public licence grant**. Source-data attribution,
licence notes, and upstream notices remain mandatory in the catalogues and
GeoNode metadata. Deferred scope: public hosting, multi-user scenarios,
location optimization, future-demand modelling, and measured DSO capacity.
