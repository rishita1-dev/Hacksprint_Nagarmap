"""Pre-download real OpenStreetMap data for Delhi study windows into the offline cache.

Run from the project root (needs internet):
    python scripts/fetch_delhi_data.py --all
    python scripts/fetch_delhi_data.py --area connaught_place --half-m 600
    python scripts/fetch_delhi_data.py --lat 28.5677 --lon 77.2433 --half-m 500

The cache (data/cache/*.json) stores the raw Overpass response with its retrieval time, so the app can
run offline afterwards. Data © OpenStreetMap contributors, ODbL 1.0.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from osm_source import STUDY_AREAS, area_key, load_delhi_boundary, load_osm_pack  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="fetch every preset study area")
    ap.add_argument("--area", choices=sorted(STUDY_AREAS), help="preset study area")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--half-m", type=int, default=600, choices=[300, 400, 500, 600, 800, 1000])
    args = ap.parse_args()
    targets = []
    if args.all:
        targets = [(v[0], v[1], v[2]) for v in STUDY_AREAS.values()]
    elif args.area:
        targets = [STUDY_AREAS[args.area]]
    elif args.lat is not None and args.lon is not None:
        targets = [(f"{args.lat:.4f}, {args.lon:.4f}", args.lat, args.lon)]
    else:
        ap.error("give --all, --area NAME, or --lat/--lon")
    boundary = load_delhi_boundary()
    failed = 0
    for label, lat, lon in targets:
        pack = load_osm_pack(lat, lon, args.half_m, refresh=True, area_label=label, boundary=boundary)
        p = pack.prov["buildings"]
        n_b = 0 if pack.layers["buildings"].gdf is None else len(pack.layers["buildings"].gdf)
        print(f"{label:34s} {p.kind:16s} buildings={n_b:5d} key={area_key(lat, lon, args.half_m)}"
              + ("" if pack.boundary_ok is not False else "  [WARNING: outside Delhi boundary]"))
        if p.kind != "LIVE API":
            failed += 1
            for lvl, m in pack.messages:
                if lvl == "er":
                    print("   ", m)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
