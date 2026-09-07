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
