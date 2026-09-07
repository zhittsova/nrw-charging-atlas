# Data sources

The project is scoped to NRW. Source files may cover a wider area, but loaders
filter their output to the 53 NRW NUTS-3 districts.

The source manifests are the canonical record of URLs, access type, licence
notes, local targets, and download limits:

- `catalog/data_sources.csv`
- `catalog/nrw_infrastructure_sources.csv`

| Domain | Implemented source | Use |
| --- | --- | --- |
| Districts | Eurostat GISCO NUTS 2024 | NRW geography and joins |
| Charging stations | Bundesnetzagentur Ladesäulenregister | Official charging baseline |
| Population | Eurostat `demo_r_pjanaggr3` | District population denominator |
| Traffic and roads | Straßen.NRW OpenGeodata | Transport context |
| Renewable sites and energy balance | Energieatlas NRW | Renewable and energy context |
| Conventional plants | Open Power System Data | Generation context |
| Power infrastructure | Geofabrik NRW OpenStreetMap extract | Grid-readiness proxy |

Raw downloads and provenance belong under `data/raw/` and are not committed.
The loaders and manifests, rather than this overview, define the current
pipeline. Sources that are not loaded must not be represented as inputs to a
published metric.

Licensing and attribution requirements remain with each manifest entry. OSM
data requires ODbL attribution. Grid results remain proxies because the public
inputs do not establish network capacity or connection availability.
