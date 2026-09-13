# Data attribution and third-party notices

This demonstrator redistributes derived NRW geospatial outputs and displays
upstream-derived map layers. Dataset-specific source URLs, roles, and local
provenance records are maintained in `catalog/data_sources.csv` and
`catalog/nrw_infrastructure_sources.csv`. GeoNode metadata is derived only from
the database-bound, successful-ingest export at
`data/runtime/current/manifest.json`; mutable raw-download sidecars are not
used as publication evidence. GeoNode records each dataset's source set and
points to bounded per-source records under the project-owned
`nrw_project_provenance` metadata field. A source without a record in that
successful ingest is explicitly marked unavailable rather than inferred.
The same record carries the manifest formula version and authoritative database
source snapshots when available: for example, the Ladesäulenregister's stated
2026-04-22 snapshot date: so a catalogue record is tied to the exported run.

## Data sources and terms

| Source | Used for | Terms and required acknowledgement |
| --- | --- | --- |
| [Eurostat GISCO NUTS 2024](https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/territorial-units-statistics) | NRW district geometry | GISCO statistical-unit terms require visible `© EuroGeographics for the administrative boundaries` acknowledgement and restrict this downloaded boundary data to non-commercial use. |
| [Eurostat `demo_r_pjanaggr3`](https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/demo_r_pjanaggr3) | Population denominator | Attribute Eurostat and identify any adaptation; the catalogue preserves the dataset URL and access date. |
| [Bundesnetzagentur Ladesäulenregister](https://www.bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/E-Mobilitaet/Ladesaeulenkarte/start.html) | Official public charging baseline | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribute `bundesnetzagentur.de`; the register is a public-station list and does not claim complete charging infrastructure coverage. |
| [Straßen.NRW OpenGeodata traffic values](https://www.opengeodata.nrw.de/produkte/transport_verkehr/strassennetz/Verkehrswerte_EPSG25832_Shape.zip) | Traffic and road context | [Datenlizenz Deutschland - Namensnennung - Version 2.0](https://www.govdata.de/dl-de/by-2-0). Preserve provider, licence name/link, dataset URL, and identify modifications. |
| [Energieatlas NRW renewable sites and electricity balance](https://www.opengeodata.nrw.de/produkte/umwelt_klima/energie/ee/) | Renewable and energy context | [Datenlizenz Deutschland - Zero - Version 2.0](https://www.govdata.de/dl-de/zero-2-0). Source and dataset links remain recorded even though this licence does not require attribution. |
| [Open Power System Data conventional power plants](https://data.open-power-system-data.org/conventional_power_plants/) | Conventional-generation context | Attribute `Open Power System Data: Conventional power plants, 2020-10-01, DOI:10.25832/CONVENTIONAL_POWER_PLANTS/2020-10-01`, including its source list. This source is context only, not a scoring input. |
| [Geofabrik NRW OpenStreetMap extract](https://download.geofabrik.de/europe/germany/nordrhein-westfalen.html) | Mapped power infrastructure and map context | [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/). Attribute `© OpenStreetMap contributors` and link to the [OSM copyright notice](https://www.openstreetmap.org/copyright). Derived databases may carry additional share-alike obligations. |

The single Geofabrik NRW PBF is recorded under `geofabrik_nrw_osm_power` in the
successful-ingest manifest and is also the mapped-road input historically named
`geofabrik_nrw_osm_roads`; it is one upstream extract, not two independent
attributions. `nrw_accessibility` is road-accessibility context, not a
charger-access layer. `nrw_renewable_potential` exposes the published staging
renewable records; a runtime dashboard may filter those records for a view, but
that consumer behavior does not redefine the catalogue layer.

`strassen_nrw_counting_stations`, `strassen_nrw_road_sections`,
`energieatlas_nrw_renewable_potential`, `mastr_units_nrw`,
`openinframap_power_nrw`, and `bast_traffic_counts_nrw` are retained in the
catalogue as contextual or unused sources. They must not be described as scored
inputs unless a supported pipeline begins consuming them. Their terms are kept
as recorded in the source catalogues; entries marked "Check … terms" remain
unresolved rather than being assigned an invented licence.

## Derived outputs and software

Published scores and scenario views combine several inputs. Their GeoNode
licence field is therefore **Varied / Derived**, with the exact constituent
source terms in `nrw_project_provenance` and this notice. OpenStreetMap-only map
layers use GeoNode's ODbL/OSM licence entry. These labels do not waive any
upstream terms or imply a legal compliance conclusion.

The project includes GeoNode pinned at upstream commit `614b85…` (GPL-3.0),
GeoServer, PostGIS, Leaflet 1.9.4 (BSD-2-Clause), Vite 7.3.6 (MIT), TypeScript
5.9.3 (Apache-2.0), Vitest 4.1.11 (MIT), and `@types/leaflet` 1.9.21 (MIT).
Their licences belong to their respective distributions. Python-package and
container-image terms/digests have not been independently resolved here and
are deliberately marked unknown; consult the actual locked distribution or
image before redistribution. This notice does not replace bundled licence texts
or create a licence for project-owned code.

## Project-owned code

No public software licence has been selected for project-owned code. Do not add
MIT, Apache, or another public code licence without an explicit project-owner
decision.
