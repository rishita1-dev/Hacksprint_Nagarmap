"""Generate the deterministic SYNTHETIC NagarMap sample data.

All geometry is constructed in a local metric grid (EPSG:32643, UTM 43N) around an
arbitrary origin. Parcels, buildings and corridors are written as EPSG:4326 GeoJSON;
roads are deliberately written in EPSG:32643 so the harmonizer has a real reprojection
to perform. Nothing here represents real parcels, owners or government records.

Run from the project root:  python scripts/generate_data.py
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Polygon, box

ORIGIN_X, ORIGIN_Y = 715000.0, 3165000.0  # arbitrary synthetic origin (UTM 43N metres)
PROJ_CRS = "EPSG:32643"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

PARCEL_W, PARCEL_H = 50.0, 40.0
PITCH_X, PITCH_Y = 60.0, 50.0  # 10 m lanes between parcels
COLS, ROWS = 5, 5


def local(x0, y0, x1, y1):
    return box(ORIGIN_X + x0, ORIGIN_Y + y0, ORIGIN_X + x1, ORIGIN_Y + y1)


def local_poly(points):
    return Polygon([(ORIGIN_X + x, ORIGIN_Y + y) for x, y in points])


LAND_USES = ["Residential", "Residential", "Commercial", "Mixed Use", "Institutional", "Open Space"]


def build_parcels():
    rows = []
    for j in range(ROWS):
        for i in range(COLS):
            n = j * COLS + i + 101
            x0, y0 = i * PITCH_X, j * PITCH_Y
            geom = local(x0, y0, x0 + PARCEL_W, y0 + PARCEL_H)
            land_use = LAND_USES[(i + 2 * j) % len(LAND_USES)]
            zone = "Z-" + "ABC"[(i + j) % 3]
            pid = f"P-{n}"
            if n == 105:
                pid = None  # deliberate data-quality issue: missing identifier
            if n == 118:
                # deliberate data-quality issue: self-intersecting "bow-tie" polygon
                geom = local_poly([(x0, y0), (x0 + 50, y0 + 40), (x0 + 50, y0), (x0, y0 + 40)])
            rows.append({
                "PARCEL_ID": pid,
                "LAND_USE": land_use,
                "ZONE": zone,
                "AREA_SQM": PARCEL_W * PARCEL_H,
                "LAST_UPDATED": f"2025-{(n % 12) + 1:02d}-15",
                "geometry": geom,
            })
    # deliberate data-quality issue: exact duplicate of P-122
    dup = next(r for r in rows if r["PARCEL_ID"] == "P-122")
    rows.append(dict(dup))
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=PROJ_CRS)


def build_buildings():
    skip = {103, 109, 120, 124}  # parcels without a building (open land)
    overrides = {
        107: (4, 4, 46, 38),   # top edge at y=88 -> reaches into road-expansion corridor
        111: (4, 2, 46, 36),   # bottom edge at y=102 -> reaches into road-expansion corridor
        122: (4, 4, 47, 36),   # right edge at x=107 -> reaches into transit corridor
    }
    rows, b = [], 1
    for j in range(ROWS):
        for i in range(COLS):
            n = j * COLS + i + 101
            if n in skip:
                continue
            x0, y0 = i * PITCH_X, j * PITCH_Y
            ax, ay, bx, by = overrides.get(n, (4, 4, 46, 36))
            rows.append({
                "BLDG_ID": f"B-{b:03d}",
                "FLOORS": 1 + (n % 5),
                "USE_TYPE": LAND_USES[(i + 2 * j) % len(LAND_USES)],
                "LAST_UPD": f"2025-{(n % 12) + 1:02d}-20",
                "geometry": local(x0 + ax, y0 + ay, x0 + bx, y0 + by),
            })
            b += 1
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=PROJ_CRS)


def build_roads():
    def line(pts):
        return LineString([(ORIGIN_X + x, ORIGIN_Y + y) for x, y in pts])

    rows = [
        {"ROAD_ID": "R-01", "NAME": "Central Avenue", "CLASS": "Arterial", "geometry": line([(-30, 95), (320, 95)])},
        {"ROAD_ID": "R-02", "NAME": "Spine Road", "CLASS": "Arterial", "geometry": line([(115, -30), (115, 280)])},
        {"ROAD_ID": "R-03", "NAME": "East Lane", "CLASS": "Collector", "geometry": line([(235, -30), (235, 280)])},
        {"ROAD_ID": "R-04", "NAME": "North Lane", "CLASS": "Local", "geometry": line([(-30, 145), (320, 145)])},
        {"ROAD_ID": "R-05", "NAME": "West Lane", "CLASS": "Local", "geometry": line([(55, -30), (55, 280)])},
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=PROJ_CRS)


def build_corridors():
    rows = [
        {
            "CORRIDOR_ID": "C-ROAD-01",
            "NAME": "Central Avenue widening (west section)",
            "SCENARIO_TYPE": "road_expansion",
            "STATUS": "Proposed",
            "DESCRIPTION": "Widens Central Avenue to 18 m between x=-10 and x=180 (synthetic grid).",
            "geometry": local(-10, 86, 180, 104),
        },
        {
            "CORRIDOR_ID": "C-ROAD-02",
            "NAME": "East Lane widening (north section)",
            "SCENARIO_TYPE": "road_expansion",
            "STATUS": "Proposed",
            "DESCRIPTION": "Widens East Lane to 16 m between y=-10 and y=140 (synthetic grid).",
            "geometry": local(227, -10, 243, 140),
        },
        {
            "CORRIDOR_ID": "C-TRANSIT-01",
            "NAME": "Spine Line transit corridor (south section)",
            "SCENARIO_TYPE": "transit_corridor",
            "STATUS": "Proposed",
            "DESCRIPTION": "18 m dedicated transit right-of-way along Spine Road from y=140 to y=250.",
            "geometry": local(106, 140, 124, 250),
        },
        {
            "CORRIDOR_ID": "C-BASE-01",
            "NAME": "Baseline - no planned corridor overlap",
            "SCENARIO_TYPE": "baseline",
            "STATUS": "Reference",
            "DESCRIPTION": "Reference corridor on open land east of the parcel grid; no overlaps expected.",
            "geometry": local(330, 0, 350, 240),
        },
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=PROJ_CRS)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    layers = {
        "parcels": build_parcels().to_crs("EPSG:4326"),
        "buildings": build_buildings().to_crs("EPSG:4326"),
        "roads": build_roads(),  # intentionally left in EPSG:32643
        "corridors": build_corridors().to_crs("EPSG:4326"),
    }
    for name, gdf in layers.items():
        path = DATA_DIR / f"{name}.geojson"
        if path.exists():
            path.unlink()
        gdf.to_file(path, driver="GeoJSON")
        print(f"wrote {path} ({len(gdf)} features, CRS {gdf.crs.to_string()})")


if __name__ == "__main__":
    main()
