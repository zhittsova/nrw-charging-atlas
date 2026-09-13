# Data sources

The project is scoped to NRW. Source files may cover a wider area, but loaders
filter their output to the 53 NRW NUTS-3 districts.

The source manifests are the canonical record of URLs, access type, source
role, licence notes, local targets, and configurable download limits:

- `catalog/data_sources.csv`
- `catalog/nrw_infrastructure_sources.csv`

| Domain | Implemented source | Use |
| --- | --- | --- |
| Districts | Eurostat GISCO NUTS 2024 | NRW geography and joins |
| Charging stations | Bundesnetzagentur Ladesäulenregister | Official charging baseline |
| Population | Eurostat `demo_r_pjanaggr3` | District population denominator |
| Traffic and roads | Straßen.NRW OpenGeodata (Verkehrswerte) | Transport load; see the traffic measure below |
| Renewable sites and energy balance | Energieatlas NRW | Renewable and energy context |
| Conventional plants | Open Power System Data | Generation context |
| Power infrastructure | Geofabrik NRW OpenStreetMap extract | Grid-readiness proxy |

## Source snapshot dates

A published figure is only interpretable with the date of the data behind it,
so each source's own stated date is carried into the database rather than
inferred from a download time.

The Ladesäulenregister states its publication date in the preamble above the
header row, for example `Letzte Aktualisierung vom: 22.04.2026`.
`scripts/nrw_raw_inputs.py` reads it, and `scripts/load_nrw_postgis.py` records
it in `raw.source_snapshots` under the key `bnetza_ladesaeulenregister`. The
canonical district projection publishes it as `charger_snapshot_date`. A file
whose preamble carries no readable date leaves the field NULL with
`charger_snapshot_unavailable_reason`; no substitute date is invented, and a
load without a date clears any earlier one rather than leaving a stale date
attached to new data.

The population reference year travels the same way, from
`raw.population.reference_year` to `population_source_year`, and the energy
reporting year from the municipal aggregation to `energy_reporting_year`.

## Traffic measure

Verified against the publisher's own field description, *Open Data -
Datenbeschreibung zum Datenbestand Straßennetz Landesbetrieb Straßenbau NRW*,
02.07.2025, section *Verkehrswerte*:

| Field | Publisher's definition | Used as |
| --- | --- | --- |
| `DTVKFZA` | "Wert der durchschnittlichen täglichen Verkehrsstärke (DTV) für KFZ-Verkehr alle Tage" | `traffic_total` :  average daily traffic volume, all motor vehicles, all days |
| `DTVLVA` | "DTV-Wert für Leichtverkehr alle Tage (Krad, PKW und Lieferwagen)" | `traffic_light` :  motorcycles, cars and vans |
| `DTVSVA` | "DTV-Wert für Schwerverkehr alle Tage (Busse, LKW >3,5t zul. GG und Lastzüge)" | `traffic_heavy` :  buses, lorries over 3.5 t and road trains |
| `ZSTART` | "Zählstellenart (automatische Dauerzählstelle, manuelle Zählstelle)" | `counting_station_type` :  why a value may be absent |

The unit is vehicles per day. The `A` suffix means all days; the same document
defines `W` as working days, `U` as holiday-season working days and `S` as
Sundays and public holidays. Only the all-days values are loaded, and no other
day type is presented as an annual figure.

**Network coverage.** The same document states that "Daten an Bundesautobahnen
sind NICHT mehr enthalten", and the snapshot's `STRKL` values are only `B`, `L`
and `K`. The counted network therefore covers federal, state and district roads;
Autobahn traffic is not part of this source and no Autobahn traffic value is
published anywhere in the project. Autobahn geometry comes from OpenStreetMap
as an unmeasured map overlay.

**Absent values :  a project inference, not a publisher rule.** Everything above
is quoted from the publisher. The rule below is not: Straßen.NRW documents *no*
no-data code, so the project decides how to read a zero, and that decision is
recorded here so it can be reviewed and reversed.

The inference is about a whole road section, not about one vehicle class. When
every day-type total (`DTVKFZA`, `DTVKFZW`, `DTVKFZU`, `DTVKFZS`) is zero at
once, the section's station published nothing and the total, light and heavy
values are all stored as unknown. The supporting evidence: in the current
snapshot all 1,022 such rows sit at manual SVZ counting stations and none at an
automatic permanent or temporary one, and a classified B, L or K road carrying
no vehicles on any day type is not a credible measurement.

Every other zero is kept as a measured observation. A road with positive total
and light traffic and a heavy count of zero genuinely has no heavy goods
traffic, and that zero is stored. The one exception is arithmetic rather than
inferred: a positive total cannot consist of neither light nor heavy vehicles,
so a zero for both classes under a positive total is an absent class split.

**Limitation.** Only the all-days fields are stored, so the day-type
corroboration is not retained per row. The loader therefore re-checks the
inference on every load and fails loudly if a section ever reports a zero
all-days total alongside a positive day-type total. `counting_station_type`
(`ZSTART`) is stored so the station kind behind an unknown value stays visible.

`analytics.nrw_transport_metrics` publishes the measured share of each
district's counted length so that the gap is visible instead of quietly
lowering the intensity. `publish.nrw_traffic_assumptions` carries the measure,
unit, scope and citation alongside the data.

## Coverage and availability fields

Aggregates publish the coverage behind each figure so a consumer can say why a
value is missing rather than showing a bare em dash:

| Domain | Fields |
| --- | --- |
| Energy | `expected_municipalities`, `consumption_municipalities_reported`, `consumption_municipal_coverage`, `renewable_municipalities_reported`, `renewable_municipal_coverage`, `growth_years_required`, `growth_years_reported`, `generation_components_unknown`, `energy_source`, `energy_data_quality_flag`, `energy_unavailable_reason` |
| Renewable capacity | `renewable_capacity_municipalities_reported`, `renewable_capacity_coverage`, `renewable_capacity_unavailable_reason` |
| Grid | `grid_line_length_km_known_voltage`, `line_voltage_coverage`, `substation_voltage_coverage`, `grid_data_quality_flag` |
| Traffic | `traffic_measured_length_km`, `traffic_length_coverage`, `transport_data_quality_flag`, `counting_station_type` |

Each published total is gated by the coverage of the field it is made of, not by
a neighbouring field's coverage. Installed renewable capacity is context rather
than a composite input, so its own coverage decides its availability and an
unknown capacity never invalidates an independently known yield. A fully
reported consumption of zero keeps its measured value and reports
`zero_consumption_denominator`, because nothing can be divided by it.
`grid_data_quality_flag` names every reason the grid proxy can be unavailable,
including an unmapped substation, which carries 45% of that score.

## Downloaded but not scored

These files are fetched and retained as context. They are not inputs to any
published metric, and must not be described as if they were:

- `strassen_nrw_counting_stations` (`ZAEHLSTELLEN_point`) :  counting-station
  locations; the traffic values already carry their station number and type.
- `strassen_nrw_road_sections` (`ABSCHNITTEAESTE_line`) :  the complete road
  network; the project's road geometry comes from the traffic values and from
  the OpenStreetMap extract.
- `energieatlas_nrw_renewable_potential` :  potential study, not observed stock.
- `bast_traffic_counts_nrw` in `catalog/data_sources.csv` :  listed for future
  work and not downloaded by the supported pipeline.

Conventional power plants (Open Power System Data) are loaded and shown as
generation context only; they are not an input to any score.

Raw downloads and provenance belong under `data/raw/` and are not committed.
The loaders and manifests, rather than this overview, define the current
pipeline. Sources that are not loaded must not be represented as inputs to a
published metric.

## Reproducible fetch cache

`python scripts/project_stack.py fetch` reuses a source only when its final
file and `data/raw/provenance/<dataset_id>.json` agree on byte count and
SHA-256. The record names the resolved URL, fetch timestamp, source date when
supplied by the registry, licence reference, cache status and resume outcome.
Use `fetch --refresh` to request a new input; a stale cache is never presented
as a newly fetched snapshot. Population uses the same explicit cache boundary
for its assembled Eurostat response.

Downloads write `*.part` candidates and validate their configured size and
checksum before atomically replacing the final input. An interrupted candidate
is not a cache hit. Resume requires a matching URL and strong ETag plus a
validated Content-Range; candidates without that identity restart safely.
Declared transfer lengths and source-format checks run before replacement,
while the prior final input stays available. Format checks cover CSV headers
and an initial data row, GeoJSON structure, archive integrity and the OSM PBF
header; the import pipeline retains full domain validation. Registry roles distinguish
`required_input`, `contextual_input`, and `contextual_unused`; the latter are
never represented as scoring inputs merely because their files were fetched.

Licensing and attribution requirements remain with each manifest entry. OSM
data requires ODbL attribution. Grid results remain proxies because the public
inputs do not establish network capacity or connection availability.

## Attribution and code licence

The two source registries are the authoritative record of source URLs, access
conditions, attribution, and licence notes. Preserve their notices in exports,
GeoNode metadata, presentations, and any later redistribution. Project-owned
code has no public licence grant; that decision is separate from permissions
for the underlying public data and upstream software.
