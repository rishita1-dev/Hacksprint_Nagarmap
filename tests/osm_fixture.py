"""TEST FIXTURE ONLY: a tiny hand-built Overpass-format response. NOT real Delhi data.

Geometry is laid out in metres around the Connaught Place window centre so the expected overlap
areas can be worked out by hand (see test_osm_source.py).
"""
from __future__ import annotations

from pyproj import Transformer

from osm_source import STUDY_AREAS

_, LAT0, LON0 = STUDY_AREAS["connaught_place"]
_to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
_to_ll = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
X0, Y0 = _to_utm.transform(LON0, LAT0)


def pt(x, y):
    lon, lat = _to_ll.transform(X0 + x, Y0 + y)
    return {"lat": lat, "lon": lon}


def ring(x0, y0, x1, y1):
    return [pt(x0, y0), pt(x1, y0), pt(x1, y1), pt(x0, y1), pt(x0, y0)]


def way(eid, geom, **tags):
    return {"type": "way", "id": eid, "tags": tags, "geometry": geom}


def overpass_response():
    els = [
        way(1, [pt(-800, 0), pt(800, 0)], highway="primary", name="Test Avenue"),
        way(2, [pt(-800, 200), pt(800, 200)], highway="residential"),                 # unnamed: no corridor
        way(11, ring(0, 5, 20, 25), building="residential", **{"building:levels": "3"}),   # overlap 200 m2 (15 m), 0 (3 m)
        way(12, ring(0, 30, 20, 50), building="yes"),                                     # clear of both
        way(13, ring(-50, -12, -30, 8), building="commercial"),                           # 400 m2 (15 m), 120 m2 (3 m)
        {"type": "relation", "id": 14, "tags": {"building": "yes", "type": "multipolygon"}, "members": [
            {"type": "way", "ref": 141, "role": "outer", "geometry": ring(100, 10, 140, 50)},
            {"type": "way", "ref": 142, "role": "inner", "geometry": ring(110, 12, 120, 20)}]},  # 200 - 30 = 170 m2
        way(15, ring(500, 300, 520, 320)[:-1], building="yes"),                           # unclosed -> skipped
        way(21, [pt(300, -800), pt(300, 800)], railway="subway", name="Test Metro Line"),
        way(22, ring(310, 100, 330, 120), building="yes"),                                # overlap 100 m2 with 15 m transit buffer
    ]
    return {"version": 0.6, "osm3s": {"timestamp_osm_base": "2026-10-16T10:00:00Z"}, "elements": els}
