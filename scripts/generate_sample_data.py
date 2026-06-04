import json
import math
from pathlib import Path


REGIONS = [
    {"id": "de-berlin", "name": "Berlin", "state": "Berlin", "population": 3769000, "areaKm2": 891, "box": [13.08, 52.35, 13.76, 52.68], "supply": 7.2, "grid": 84, "road": 88, "renewable": 44, "demand": 92},
    {"id": "de-hamburg", "name": "Hamburg", "state": "Hamburg", "population": 1899000, "areaKm2": 755, "box": [9.70, 53.39, 10.33, 53.74], "supply": 6.8, "grid": 82, "road": 86, "renewable": 50, "demand": 86},
    {"id": "de-munich", "name": "Munich Region", "state": "Bavaria", "population": 2980000, "areaKm2": 5500, "box": [11.05, 47.82, 12.05, 48.45], "supply": 8.4, "grid": 79, "road": 85, "renewable": 68, "demand": 91},
    {"id": "de-cologne", "name": "Cologne-Bonn Region", "state": "North Rhine-Westphalia", "population": 3560000, "areaKm2": 7300, "box": [6.55, 50.55, 7.45, 51.15], "supply": 5.2, "grid": 76, "road": 90, "renewable": 42, "demand": 89},
    {"id": "de-frankfurt", "name": "Frankfurt Rhine-Main", "state": "Hesse", "population": 5810000, "areaKm2": 14800, "box": [7.85, 49.65, 9.15, 50.45], "supply": 6.1, "grid": 86, "road": 92, "renewable": 47, "demand": 94},
    {"id": "de-stuttgart", "name": "Stuttgart Region", "state": "Baden-Wurttemberg", "population": 2800000, "areaKm2": 3650, "box": [8.75, 48.45, 9.65, 49.05], "supply": 7.8, "grid": 81, "road": 87, "renewable": 58, "demand": 88},
    {"id": "de-ruhr", "name": "Ruhr Area", "state": "North Rhine-Westphalia", "population": 5100000, "areaKm2": 4435, "box": [6.55, 51.18, 7.65, 51.75], "supply": 4.7, "grid": 83, "road": 91, "renewable": 38, "demand": 95},
    {"id": "de-duesseldorf", "name": "Dusseldorf-Lower Rhine", "state": "North Rhine-Westphalia", "population": 2260000, "areaKm2": 5300, "box": [6.20, 51.00, 6.95, 51.55], "supply": 5.8, "grid": 80, "road": 88, "renewable": 40, "demand": 84},
    {"id": "de-hannover", "name": "Hannover Region", "state": "Lower Saxony", "population": 1160000, "areaKm2": 2290, "box": [9.40, 52.15, 10.05, 52.60], "supply": 5.4, "grid": 74, "road": 77, "renewable": 63, "demand": 70},
    {"id": "de-bremen", "name": "Bremen-Oldenburg", "state": "Bremen / Lower Saxony", "population": 1760000, "areaKm2": 7500, "box": [8.30, 52.85, 9.15, 53.45], "supply": 4.2, "grid": 70, "road": 75, "renewable": 72, "demand": 73},
    {"id": "de-leipzig", "name": "Leipzig Region", "state": "Saxony", "population": 1050000, "areaKm2": 3900, "box": [11.85, 51.05, 12.75, 51.55], "supply": 4.8, "grid": 69, "road": 76, "renewable": 59, "demand": 68},
    {"id": "de-dresden", "name": "Dresden Region", "state": "Saxony", "population": 1340000, "areaKm2": 6200, "box": [12.80, 50.75, 14.10, 51.25], "supply": 5.0, "grid": 71, "road": 72, "renewable": 55, "demand": 66},
    {"id": "de-nuremberg", "name": "Nuremberg Region", "state": "Bavaria", "population": 1360000, "areaKm2": 7600, "box": [10.65, 49.10, 11.45, 49.75], "supply": 6.4, "grid": 74, "road": 80, "renewable": 66, "demand": 72},
    {"id": "de-karlsruhe", "name": "Karlsruhe Technology Region", "state": "Baden-Wurttemberg", "population": 1250000, "areaKm2": 3500, "box": [8.05, 48.75, 8.75, 49.30], "supply": 7.0, "grid": 77, "road": 82, "renewable": 57, "demand": 76},
    {"id": "de-mannheim", "name": "Rhine-Neckar", "state": "Baden-Wurttemberg / Hesse", "population": 2400000, "areaKm2": 5600, "box": [8.15, 49.25, 9.00, 49.80], "supply": 5.7, "grid": 78, "road": 83, "renewable": 48, "demand": 80},
    {"id": "de-freiburg", "name": "Freiburg-South Baden", "state": "Baden-Wurttemberg", "population": 1150000, "areaKm2": 6800, "box": [7.45, 47.62, 8.35, 48.25], "supply": 6.6, "grid": 67, "road": 68, "renewable": 82, "demand": 62},
    {"id": "de-saarbruecken", "name": "Saarland Region", "state": "Saarland", "population": 990000, "areaKm2": 2570, "box": [6.35, 49.05, 7.25, 49.65], "supply": 4.0, "grid": 73, "road": 74, "renewable": 41, "demand": 58},
    {"id": "de-mainz", "name": "Mainz-Wiesbaden", "state": "Rhineland-Palatinate / Hesse", "population": 1020000, "areaKm2": 2600, "box": [7.80, 49.85, 8.55, 50.25], "supply": 5.9, "grid": 75, "road": 81, "renewable": 49, "demand": 70},
    {"id": "de-muenster", "name": "Muensterland", "state": "North Rhine-Westphalia", "population": 1640000, "areaKm2": 5940, "box": [7.25, 51.75, 8.25, 52.35], "supply": 5.1, "grid": 72, "road": 76, "renewable": 70, "demand": 64},
    {"id": "de-bielefeld", "name": "Ostwestfalen-Lippe", "state": "North Rhine-Westphalia", "population": 2060000, "areaKm2": 6500, "box": [8.15, 51.70, 9.35, 52.35], "supply": 4.6, "grid": 68, "road": 74, "renewable": 65, "demand": 67},
    {"id": "de-kassel", "name": "North Hesse", "state": "Hesse", "population": 1010000, "areaKm2": 8300, "box": [8.65, 50.85, 10.25, 51.55], "supply": 3.7, "grid": 64, "road": 70, "renewable": 67, "demand": 55},
    {"id": "de-erfurt", "name": "Thuringia Central", "state": "Thuringia", "population": 1080000, "areaKm2": 7200, "box": [10.45, 50.55, 11.75, 51.20], "supply": 3.8, "grid": 63, "road": 69, "renewable": 62, "demand": 54},
    {"id": "de-magdeburg", "name": "Magdeburg-Borde", "state": "Saxony-Anhalt", "population": 780000, "areaKm2": 6400, "box": [10.75, 51.85, 12.30, 52.45], "supply": 3.4, "grid": 66, "road": 67, "renewable": 78, "demand": 48},
    {"id": "de-rostock", "name": "Rostock Coast", "state": "Mecklenburg-Vorpommern", "population": 650000, "areaKm2": 7100, "box": [11.65, 53.75, 12.75, 54.35], "supply": 3.2, "grid": 61, "road": 58, "renewable": 84, "demand": 44},
    {"id": "de-kiel", "name": "Kiel-Schleswig", "state": "Schleswig-Holstein", "population": 970000, "areaKm2": 7800, "box": [9.55, 53.95, 10.70, 54.65], "supply": 4.5, "grid": 65, "road": 63, "renewable": 88, "demand": 52},
    {"id": "de-schwerin", "name": "West Mecklenburg", "state": "Mecklenburg-Vorpommern", "population": 520000, "areaKm2": 7000, "box": [10.65, 53.15, 11.95, 53.85], "supply": 2.8, "grid": 58, "road": 55, "renewable": 82, "demand": 38},
    {"id": "de-potsdam", "name": "Potsdam-Havelland", "state": "Brandenburg", "population": 760000, "areaKm2": 6200, "box": [12.55, 52.20, 13.25, 52.65], "supply": 4.1, "grid": 68, "road": 66, "renewable": 74, "demand": 57},
    {"id": "de-augsburg", "name": "Augsburg-Swabia", "state": "Bavaria", "population": 1010000, "areaKm2": 5200, "box": [10.45, 47.95, 11.10, 48.55], "supply": 5.3, "grid": 70, "road": 75, "renewable": 73, "demand": 65},
    {"id": "de-regensburg", "name": "Regensburg-Upper Palatinate", "state": "Bavaria", "population": 850000, "areaKm2": 7200, "box": [11.75, 48.75, 12.65, 49.35], "supply": 4.9, "grid": 69, "road": 72, "renewable": 77, "demand": 57},
    {"id": "de-wuerzburg", "name": "Wuerzburg-Franconia", "state": "Bavaria", "population": 920000, "areaKm2": 6800, "box": [9.45, 49.55, 10.30, 50.10], "supply": 4.6, "grid": 67, "road": 73, "renewable": 69, "demand": 56},
    {"id": "de-trier", "name": "Trier-Mosel", "state": "Rhineland-Palatinate", "population": 630000, "areaKm2": 5100, "box": [6.35, 49.45, 7.20, 50.05], "supply": 3.3, "grid": 60, "road": 59, "renewable": 60, "demand": 42},
    {"id": "de-cottbus", "name": "Lausitz-Cottbus", "state": "Brandenburg / Saxony", "population": 700000, "areaKm2": 7800, "box": [13.85, 51.25, 14.75, 51.95], "supply": 2.9, "grid": 62, "road": 60, "renewable": 76, "demand": 43},
]

OPERATORS = ["Stadtwerke Mobility", "EnBW", "E.ON Drive", "Aral pulse", "Ionity", "Allego", "ChargeNow", "UrbanVolt"]
POWERS = [11, 22, 50, 75, 120, 150, 300]
STATUSES = ["available", "available", "available", "occupied", "maintenance"]


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def scale(value, minimum, maximum):
    if maximum == minimum:
        return 50
    return ((value - minimum) / (maximum - minimum)) * 100


def polygon_from_box(box):
    west, south, east, north = box
    return [[[west, south], [east, south], [east, north], [west, north], [west, south]]]


def with_scores():
    rows = []
    for region in REGIONS:
        station_count = round(clamp((region["population"] / 10000) * region["supply"], 90, 1250))
        chargers_per_km2 = station_count / region["areaKm2"]
        row = {
            **region,
            "stationCount": station_count,
            "chargerDensity": station_count / (region["population"] / 10000),
            "chargersPerKm2": chargers_per_km2,
            "populationDensity": region["population"] / region["areaKm2"],
            "averageDistanceKm": clamp(11 / math.sqrt(chargers_per_km2 + 0.04) - region["road"] / 45, 1.2, 18),
            "substationCount": round(clamp((region["grid"] / 100) * (region["areaKm2"] / 850) + region["population"] / 900000, 2, 24)),
        }
        row["substationDensity"] = row["substationCount"] / (row["areaKm2"] / 1000)
        rows.append(row)

    ranges = {
        key: (min(row[key] for row in rows), max(row[key] for row in rows))
        for key in ["chargerDensity", "chargersPerKm2", "populationDensity", "averageDistanceKm", "substationDensity"]
    }

    for row in rows:
        charger_density_score = scale(row["chargerDensity"], *ranges["chargerDensity"])
        charger_accessibility_score = 100 - scale(row["averageDistanceKm"], *ranges["averageDistanceKm"])
        coverage_score = scale(row["chargersPerKm2"], *ranges["chargersPerKm2"])
        demand_score = 0.55 * row["demand"] + 0.45 * scale(row["populationDensity"], *ranges["populationDensity"])
        ev_readiness = 0.4 * charger_density_score + 0.3 * charger_accessibility_score + 0.3 * coverage_score
        grid_readiness = 0.5 * row["grid"] + 0.3 * scale(row["substationDensity"], *ranges["substationDensity"]) + 0.2 * row["renewable"]
        deficit = 0.55 * demand_score + 0.45 * (100 - ev_readiness)
        priority = 0.4 * demand_score + 0.3 * deficit + 0.2 * grid_readiness + 0.1 * row["road"]

        row.update(
            chargerDensity=round(row["chargerDensity"], 2),
            chargersPerKm2=round(row["chargersPerKm2"], 3),
            averageDistanceKm=round(row["averageDistanceKm"], 1),
            substationDensity=round(row["substationDensity"], 2),
            demandScore=round(demand_score, 1),
            evReadinessScore=round(ev_readiness, 1),
            gridReadinessScore=round(grid_readiness, 1),
            chargerDeficitScore=round(deficit, 1),
            investmentPriorityScore=round(priority, 1),
        )
    return rows


def build_regions():
    return {
        "type": "FeatureCollection",
        "name": "germany_energy_regions_sample",
        "features": [
            {
                "type": "Feature",
                "properties": region,
                "geometry": {"type": "Polygon", "coordinates": polygon_from_box(region["box"])},
            }
            for region in with_scores()
        ],
    }


def build_stations(regions):
    station_index = 1
    features = []
    for feature in regions["features"]:
        region = feature["properties"]
        west, south, east, north = region["box"]
        columns = math.ceil(math.sqrt(region["stationCount"] * ((east - west) / (north - south))))
        rows = math.ceil(region["stationCount"] / columns)
        for local_index in range(region["stationCount"]):
            column = local_index % columns
            row = local_index // columns
            lon_jitter = (((local_index * 37) % 19) - 9) * 0.0014
            lat_jitter = (((local_index * 53) % 23) - 11) * 0.0011
            longitude = round(west + ((column + 0.5) / columns) * (east - west) + lon_jitter, 5)
            latitude = round(south + ((row + 0.5) / rows) * (north - south) + lat_jitter, 5)
            operator = OPERATORS[(station_index + local_index) % len(OPERATORS)]
            power = POWERS[(station_index + local_index * 2) % len(POWERS)]
            status = STATUSES[(station_index + local_index) % len(STATUSES)]
            station_id = f"ev-de-{station_index:05d}"
            station_index += 1
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "id": station_id,
                        "name": f'{region["name"]} Charging Asset {local_index + 1:04d}',
                        "operator": operator,
                        "region": region["name"],
                        "state": region["state"],
                        "address": f"Synthetic site {station_index - 1}, {region['name']}",
                        "connectors": 2 + ((station_index + local_index) % 7),
                        "power_kw": power,
                        "status": status,
                        "latitude": latitude,
                        "longitude": longitude,
                    },
                    "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                }
            )
    return {"type": "FeatureCollection", "name": "ev_charging_stations_germany_sample", "features": features}


def main():
    data_dir = Path(__file__).resolve().parents[1] / "frontend" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    regions = build_regions()
    stations = build_stations(regions)
    (data_dir / "germany_regions_sample.geojson").write_text(json.dumps(regions, indent=2) + "\n", encoding="utf-8")
    (data_dir / "ev_charging_stations_sample.geojson").write_text(json.dumps(stations, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(regions['features'])} regions")
    print(f"Wrote {len(stations['features'])} stations")


if __name__ == "__main__":
    main()
