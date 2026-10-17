"""Real Delhi data sources for NagarMap: OpenStreetMap (Overpass API) + bundled Delhi boundary.

Design rules
------------
* Nothing is invented. If a dataset does not exist (Delhi parcels), the layer is marked
  *unavailable* with the reason - it is never filled with made-up records.
* Every layer carries a :class:`Provenance` (source, licence, attribution, retrieval time, kind).
* "LIVE API" is only claimed for data fetched from the Overpass API in this very session.
  Anything read from disk is labelled CACHED DOWNLOAD with its original retrieval time.
  NagarMap does not stream or auto-update data; refreshing is an explicit user action.
* The raw Overpass JSON response is cached on disk, and parsed on load, so offline use works
  for every area that was fetched once.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import linemerge, polygonize, unary_union

from data_harmonizer import (HarmonizationResult, harmonize_layer, load_demo_data,
                             unavailable_layer)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CACHE_DIR = Path(os.environ.get("NAGARMAP_CACHE", DATA_DIR / "cache"))
BOUNDARY_FILE = DATA_DIR / "delhi_boundary.geojson"

USER_AGENT = "NagarMap/0.3 (Delhi geospatial MVP; open-source hackathon project)"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OSM_LICENCE = "ODbL 1.0 (Open Database Licence)"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
DEFAULT_MAX_AGE_DAYS = 30

# Study-window centres. These are only *search windows*; all geometry inside comes from OSM.
STUDY_AREAS = {
    "connaught_place": ("Connaught Place / Central Delhi", 28.6315, 77.2167),
    "karol_bagh": ("Karol Bagh", 28.6519, 77.1907),
    "lajpat_nagar": ("Lajpat Nagar", 28.5677, 77.2433),
    "saket": ("Saket", 28.5245, 77.2066),
    "dwarka_s10": ("Dwarka Sector 10", 28.5810, 77.0580),
    "rohini": ("Rohini Sector 7", 28.7041, 77.1025),
}
CUSTOM_AREA = "custom"
HIGHWAY_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
                   "motorway_link", "trunk_link", "primary_link", "secondary_link")
MAJOR_HIGHWAYS = ("motorway", "trunk", "primary", "secondary", "tertiary")
TRANSIT_RAILWAY = ("subway", "light_rail", "rail", "monorail")
BASELINE_HALF_WIDTH_M = 3.0
MAX_CORRIDORS_PER_KIND = 12
MIN_CORRIDOR_LENGTH_M = 30.0
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PARCELS_UNAVAILABLE_REASON = (
    "No open, machine-readable parcel/cadastral geometry for Delhi could be verified, so NagarMap "
    "bundles and fetches none. Parcel analysis is unavailable until you upload an authorised parcel file.")


class OverpassError(RuntimeError):
    """Every Overpass endpoint failed."""


class UploadError(ValueError):
    """An uploaded file could not be read."""


# ------------------------------------------------------------------ provenance
@dataclass
class Provenance:
    layer: str
    source: str
    kind: str                      # LIVE API | CACHED DOWNLOAD | STATIC BUNDLED | USER UPLOAD | SYNTHETIC DEMO | UNAVAILABLE
    licence: str = ""
    attribution: str = ""
    url: str = ""
    retrieved_utc: str | None = None   # when NagarMap obtained the data
    data_timestamp: str | None = None  # data's own "as of" time, if the source reports one
    note: str = ""

    def age_days(self, now: datetime | None = None) -> float | None:
        if not self.retrieved_utc:
            return None
        try:
            t = datetime.strptime(self.retrieved_utc, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return max(0.0, ((now or datetime.now(timezone.utc)) - t).total_seconds() / 86400)

    def freshness(self, now: datetime | None = None, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> str:
        if self.kind == "UNAVAILABLE":
            return "n/a"
        if self.kind == "SYNTHETIC DEMO":
            return "n/a (synthetic)"
        age = self.age_days(now)
        if age is None:
            return "unknown"
        if age < 1 / 24:
            txt = "fetched just now"
        elif age < 1:
            txt = f"{age * 24:.0f} h old"
        else:
            txt = f"{age:.0f} day(s) old"
        if self.kind == "CACHED DOWNLOAD" and age > max_age_days:
            txt += f" · STALE (> {max_age_days} d) - refresh recommended"
        return txt


@dataclass
class SourcePack:
    mode: str                                 # "osm" | "demo"
    area_label: str
    layers: dict[str, HarmonizationResult]
    prov: dict[str, Provenance]
    bbox: tuple[float, float, float, float] | None = None   # (west, south, east, north) WGS84
    transit_raw: gpd.GeoDataFrame | None = None
    messages: list[tuple[str, str]] = field(default_factory=list)   # (level, text) for the system log
    boundary_ok: bool | None = None           # study window centre inside Delhi boundary?
    upload_corridors: HarmonizationResult | None = None

    @property
    def is_real(self) -> bool:
        return self.mode == "osm"


# ------------------------------------------------------------------ geometry helpers
def bbox_from_center(lat: float, lon: float, half_m: float) -> tuple[float, float, float, float]:
    dlat = half_m / 111_320.0
    dlon = half_m / (111_320.0 * math.cos(math.radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def area_key(lat: float, lon: float, half_m: int) -> str:
    return f"osm_{lat:.4f}_{lon:.4f}_{int(half_m)}".replace(".", "p").replace("-", "m")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


# ------------------------------------------------------------------ Delhi boundary (bundled, static)
def load_delhi_boundary(path: Path = BOUNDARY_FILE) -> gpd.GeoDataFrame | None:
    if not Path(path).exists():
        return None
    try:
        g = gpd.read_file(path)
        return g.to_crs("EPSG:4326") if g.crs is not None else None
    except Exception:  # noqa: BLE001
        return None


def point_in_delhi(lat: float, lon: float, boundary: gpd.GeoDataFrame | None) -> bool | None:
    """True/False, or None when the boundary file is unavailable (never guessed)."""
    if boundary is None or len(boundary) == 0:
        return None
    return bool(boundary.geometry.union_all().contains(Point(lon, lat)))


def boundary_provenance() -> Provenance:
    return Provenance(
        layer="Delhi boundary", source="DataMeet Municipal_Spatial_Data · Delhi_Boundary.geojson",
        kind="STATIC BUNDLED", licence="CC BY-SA 2.5 India",
        attribution="DataMeet community (scraped from an ArcGIS Online map; unofficial, not a survey boundary)",
        url="https://github.com/datameet/Municipal_Spatial_Data/tree/master/Delhi",
        retrieved_utc="2026-10-17 00:00:00 UTC",
        note="Static file bundled with the app; it is never updated automatically.")


# ------------------------------------------------------------------ Overpass: query, fetch, cache
def overpass_query(bbox: tuple[float, float, float, float], timeout_s: int = 90) -> str:
    w, s, e, n = bbox
    bb = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}"
    hw = "|".join(HIGHWAY_CLASSES)
    rw = "|".join(TRANSIT_RAILWAY)
    return (f'[out:json][timeout:{timeout_s}];\n(\n'
            f'  way["building"]({bb});\n  relation["building"]({bb});\n'
            f'  way["highway"~"^({hw})$"]({bb});\n'
            f'  way["railway"~"^({rw})$"]({bb});\n);\nout geom tags;')


def fetch_overpass(bbox, endpoints=None, session=None, timeout=(10, 120)) -> tuple[dict, str, str]:
    """POST the query; try each endpoint in turn. Returns (json, endpoint, query). Raises OverpassError."""
    import requests  # local import: keeps module import cheap and tests independent of the network
    sess = session or requests.Session()
    query = overpass_query(bbox)
    errors = []
    for url in (endpoints or OVERPASS_ENDPOINTS):
        try:
            r = sess.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            if r.status_code != 200:
                errors.append(f"{url}: HTTP {r.status_code}")
                continue
            js = r.json()
            if not isinstance(js, dict) or "elements" not in js:
                errors.append(f"{url}: unexpected response")
                continue
            if js.get("remark") and not js["elements"]:
                errors.append(f"{url}: server remark: {js['remark']}")
                continue
            return js, url, query
        except Exception as exc:  # noqa: BLE001 - network/JSON errors are all "try next mirror"
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    raise OverpassError("All Overpass endpoints failed - " + "; ".join(errors))


def cache_path(key: str, cache_dir: Path | None = None) -> Path:
    base = cache_dir or os.environ.get("NAGARMAP_CACHE") or CACHE_DIR
    return Path(base) / f"{key}.json"


def write_cache(key: str, response: dict, endpoint: str, query: str, retrieved: datetime,
                cache_dir: Path | None = None) -> Path:
    p = cache_path(key, cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"nagarmap_cache_version": 1, "retrieved_utc": _fmt(retrieved), "endpoint": endpoint,
               "query": query, "response": response}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(p)
    return p


def read_cache(key: str, cache_dir: Path | None = None) -> dict | None:
    p = cache_path(key, cache_dir)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        if payload.get("nagarmap_cache_version") == 1 and "elements" in payload.get("response", {}):
            return payload
    except Exception:  # noqa: BLE001 - corrupt cache is treated as absent
        pass
    return None


# ------------------------------------------------------------------ Overpass JSON -> GeoDataFrames
def _ring(geom_pts) -> list[tuple[float, float]]:
    return [(p["lon"], p["lat"]) for p in geom_pts]


def _relation_polygon(rel: dict):
    outers, inners = [], []
    for m in rel.get("members", []):
        pts = m.get("geometry")
        if m.get("type") != "way" or not pts or len(pts) < 2:
            continue
        line = LineString(_ring(pts))
        (inners if m.get("role") == "inner" else outers).append(line)
    if not outers:
        return None
    shells = list(polygonize(linemerge(outers)))
    if not shells:
        return None
    holes = list(polygonize(linemerge(inners))) if inners else []
    geom = unary_union(shells)
    if holes:
        geom = geom.difference(unary_union(holes))
    return geom if not geom.is_empty else None


def _levels(v):
    try:
        return float(str(v).split(";")[0].strip())
    except (TypeError, ValueError):
        return None


def parse_overpass(js: dict) -> dict[str, gpd.GeoDataFrame | int]:
    """Split an Overpass response into buildings / roads / transit (all EPSG:4326) + skip counters."""
    bld, roads, transit = [], [], []
    skipped = 0
    for el in js.get("elements", []):
        tags = el.get("tags") or {}
        etype, eid = el.get("type"), el.get("id")
        if etype is None or eid is None:
            skipped += 1
            continue
        fid = f"{etype}/{eid}"
        try:
            if "building" in tags:
                if etype == "way":
                    pts = el.get("geometry") or []
                    if len(pts) < 4 or pts[0] != pts[-1]:
                        skipped += 1
                        continue
                    geom = Polygon(_ring(pts))
                elif etype == "relation":
                    geom = _relation_polygon(el)
                else:
                    geom = None
                if geom is None:
                    skipped += 1
                    continue
                bld.append({"feature_id": fid, "use_type": tags.get("building"),
                            "floors": _levels(tags.get("building:levels")), "last_updated": None, "geometry": geom})
            elif etype == "way" and tags.get("highway") in HIGHWAY_CLASSES:
                pts = el.get("geometry") or []
                if len(pts) < 2:
                    skipped += 1
                    continue
                roads.append({"feature_id": fid, "road_name": tags.get("name") or tags.get("ref"),
                              "road_class": tags.get("highway"), "geometry": LineString(_ring(pts))})
            elif etype == "way" and tags.get("railway") in TRANSIT_RAILWAY:
                pts = el.get("geometry") or []
                if len(pts) < 2:
                    skipped += 1
                    continue
                transit.append({"feature_id": fid, "road_name": tags.get("name") or tags.get("ref"),
                                "road_class": tags.get("railway"), "geometry": LineString(_ring(pts))})
        except Exception:  # noqa: BLE001 - one bad element must not kill the load
            skipped += 1

    def mk(rows, cols):
        if not rows:
            return gpd.GeoDataFrame({c: [] for c in cols}, geometry=gpd.GeoSeries([], crs="EPSG:4326"), crs="EPSG:4326")
        return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")

    return {"buildings": mk(bld, ["feature_id", "use_type", "floors", "last_updated"]),
            "roads": mk(roads, ["feature_id", "road_name", "road_class"]),
            "transit": mk(transit, ["feature_id", "road_name", "road_class"]),
            "skipped": skipped}


# ------------------------------------------------------------------ corridors derived from real OSM lines
def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").upper()[:24] or "UNNAMED"


def build_corridors(roads: gpd.GeoDataFrame | None, transit: gpd.GeoDataFrame | None,
                    bbox: tuple[float, float, float, float], half_width_m: float) -> gpd.GeoDataFrame:
    """Hypothetical right-of-way polygons: a buffer of ``half_width_m`` either side of a real OSM centreline.

    The corridor is a *scenario*, not an official alignment. Lines are clipped to the study window so
    that nothing is reported outside the area for which building data was actually downloaded.
    """
    cols = ["feature_id", "corridor_name", "scenario_type", "status", "description"]
    empty = gpd.GeoDataFrame({c: [] for c in cols}, geometry=gpd.GeoSeries([], crs="EPSG:4326"), crs="EPSG:4326")
    frames = [g for g in (roads, transit) if g is not None and len(g)]
    if not frames:
        return empty
    ref = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")
    utm = ref.estimate_utm_crs()
    window = gpd.GeoSeries([box(*bbox)], crs="EPSG:4326").to_crs(utm).iloc[0]

    rows = []

    def add(group_gdf, kind_prefix, scen_roads, scen_base, label_fn):
        g = group_gdf.to_crs(utm)
        out = []
        for (name, cls), sub in g.groupby(["road_name", "road_class"], dropna=True):
            u = unary_union(list(sub.geometry))
            merged = u if u.geom_type == "LineString" else linemerge(u)
            clipped = merged.intersection(window)
            length = float(clipped.length)
            if clipped.is_empty or length < MIN_CORRIDOR_LENGTH_M:
                continue
            out.append((length, str(name), str(cls), clipped))
        out.sort(key=lambda t: -t[0])
        for length, name, cls, geom in out[:MAX_CORRIDORS_PER_KIND]:
            slug = _slug(name)
            for scen, prefix, hw, status in scen_roads(half_width_m) + scen_base():
                poly = geom.buffer(hw, cap_style="flat", join_style="mitre", mitre_limit=2.0)
                rows.append({
                    "feature_id": f"{prefix}-{slug}", "corridor_name": f"{name} ({cls}) · {length:,.0f} m in window",
                    "scenario_type": scen, "status": status,
                    "description": label_fn(name, cls, hw, length), "geometry": poly})

    major = roads[roads["road_class"].isin(MAJOR_HIGHWAYS) & roads["road_name"].notna()] if roads is not None and len(roads) else None
    if major is not None and len(major):
        add(major, "C", lambda w: [("road_expansion", "C-ROAD", w, "Hypothetical scenario")],
            lambda: [("baseline", "C-BASE", BASELINE_HALF_WIDTH_M, "Reference")],
            lambda n, c, w, l: (f"OSM {c} road '{n}', {l:,.0f} m inside the study window, buffered {w:g} m each side "
                                f"of its centreline ({2 * w:g} m total). User-set scenario, not an official alignment."))
    if transit is not None and len(transit):
        tt = transit[transit["road_name"].notna()]
        add(tt, "T", lambda w: [("transit_corridor", "C-TRANSIT", w, "Hypothetical scenario")], lambda: [],
            lambda n, c, w, l: (f"OSM railway={c} line '{n}', {l:,.0f} m inside the study window, buffered {w:g} m each side. "
                                "User-set scenario, not an official alignment."))
    if not rows:
        return empty
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=utm).to_crs("EPSG:4326")
    return gdf.drop_duplicates("feature_id").reset_index(drop=True)


# ------------------------------------------------------------------ OSM pack (live / cache)
def load_osm_pack(lat: float, lon: float, half_m: int = 600, *, refresh: bool = False,
                  cache_dir: Path | None = None, fetcher=fetch_overpass, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
                  area_label: str | None = None, boundary: gpd.GeoDataFrame | None = None) -> SourcePack:
    """Return real OSM data for a study window. Cache first unless ``refresh``; live API otherwise.

    If a live fetch fails but a cache exists, the cache is used and the failure is reported.
    If neither works the returned pack has no building data and says so (no fabricated fallback).
    """
    bbox = bbox_from_center(lat, lon, half_m)
    key = area_key(lat, lon, half_m)
    label = area_label or f"{lat:.4f}, {lon:.4f}"
    msgs: list[tuple[str, str]] = []
    cached = read_cache(key, cache_dir)
    payload, kind = None, None

    use_cache = cached is not None and not refresh
    if use_cache:
        payload, kind = cached, "CACHED DOWNLOAD"
        msgs.append(("in", f"Using cached OSM download for this window (retrieved {cached['retrieved_utc']})."))
    else:
        try:
            js, endpoint, query = fetcher(bbox)
            now = _now()
            write_cache(key, js, endpoint, query, now, cache_dir)
            payload = {"retrieved_utc": _fmt(now), "endpoint": endpoint, "response": js}
            kind = "LIVE API"
            msgs.append(("ok", f"Fetched live from Overpass API ({endpoint}); cached to disk for offline use."))
        except Exception as exc:  # noqa: BLE001
            msgs.append(("er", f"Live fetch failed: {exc}"))
            if cached is not None:
                payload, kind = cached, "CACHED DOWNLOAD"
                msgs.append(("wa", f"Falling back to cached download from {cached['retrieved_utc']}."))

    prov: dict[str, Provenance] = {"boundary": boundary_provenance(),
                                   "parcels": Provenance("parcels", "None available", "UNAVAILABLE",
                                                         note=PARCELS_UNAVAILABLE_REASON)}
    layers: dict[str, HarmonizationResult] = {
        "parcels": unavailable_layer("parcels", "parcels (unavailable)", PARCELS_UNAVAILABLE_REASON)}
    transit_raw = None
    if payload is None:
        reason = "No OSM data: live fetch failed and no cache exists for this window. Try again online, or use the synthetic demo."
        layers["buildings"] = HarmonizationResult(kind="buildings", source_layer="OSM buildings", gdf=None, usable=False,
                                                  issues=[reason])
        layers["roads"] = HarmonizationResult(kind="roads", source_layer="OSM roads", gdf=None, usable=False, issues=[reason])
        for k in ("buildings", "roads"):
            prov[k] = Provenance(k, "OpenStreetMap (Overpass API)", "UNAVAILABLE", note=reason)
    else:
        js = payload["response"]
        parsed = parse_overpass(js)
        base = js.get("osm3s", {}).get("timestamp_osm_base")
        for k, label_k in (("buildings", "OSM buildings (Overpass)"), ("roads", "OSM roads (Overpass)")):
            res = harmonize_layer(parsed[k], k, label_k)
            if parsed["skipped"] and k == "buildings":
                res.issues.append(f"{label_k}: {parsed['skipped']} OSM element(s) skipped (unclosed way / broken multipolygon / empty).")
            if len(parsed[k]) == 0:
                res.usable = True
                res.issues.append(f"{label_k}: no features inside this study window.")
            layers[k] = res
            prov[k] = Provenance(k, "OpenStreetMap via Overpass API", kind, OSM_LICENCE, OSM_ATTRIBUTION, OSM_COPYRIGHT_URL,
                                 payload["retrieved_utc"], base,
                                 note="On-demand download; OSM itself changes continuously, this copy does not.")
        transit_raw = parsed["transit"]
        msgs.append(("in", f"Parsed {len(parsed['buildings'])} building(s), {len(parsed['roads'])} road segment(s), "
                           f"{len(parsed['transit'])} rail/metro segment(s)."))

    return SourcePack(mode="osm", area_label=label, layers=layers, prov=prov, bbox=bbox, transit_raw=transit_raw,
                      messages=msgs, boundary_ok=point_in_delhi(lat, lon, boundary))


def load_demo_pack(data_dir: Path = DATA_DIR) -> SourcePack:
    layers = load_demo_data(data_dir)
    prov = {k: Provenance(k, f"{k}.geojson (generated by scripts/generate_data.py)", "SYNTHETIC DEMO",
                          note="Artificial data for demonstration and tests. Not real, not Delhi.")
            for k in layers}
    prov["boundary"] = boundary_provenance()
    return SourcePack(mode="demo", area_label="Synthetic Ward A", layers=layers, prov=prov,
                      messages=[("wa", "Synthetic demo data in use - nothing here is real.")])


def corridor_layer(pack: SourcePack, half_width_m: float) -> HarmonizationResult:
    """Harmonized corridor layer for the current pack (+ any uploaded corridors)."""
    if pack.mode == "demo":
        res = pack.layers["corridors"]
    else:
        raw = build_corridors(pack.layers["roads"].gdf, pack.transit_raw, pack.bbox, half_width_m)
        if len(raw):
            res = harmonize_layer(raw, "corridors", "OSM-derived corridors")
            res.changes.append(f"corridors: {len(raw)} scenario(s) derived from OSM centrelines, {half_width_m:g} m each side "
                               f"({BASELINE_HALF_WIDTH_M:g} m for baseline).")
        else:
            res = HarmonizationResult(kind="corridors", source_layer="OSM-derived corridors", gdf=None, usable=False,
                                      issues=["No named major road or rail/metro line of useful length inside this window; "
                                              "pick another area or upload a corridor file."])
    up = pack.upload_corridors
    if up is not None and up.usable and up.gdf is not None:
        if res.gdf is not None and len(res.gdf):
            both = pd.concat([res.gdf, up.gdf], ignore_index=True)
            merged = HarmonizationResult(kind="corridors", source_layer=f"{res.source_layer} + {up.source_layer}",
                                         gdf=gpd.GeoDataFrame(both, geometry="geometry", crs="EPSG:4326"),
                                         source_crs=res.source_crs, changes=res.changes + up.changes,
                                         issues=res.issues + up.issues, checks_run=True, n_input=res.n_input + up.n_input)
            for f in ("reprojected", "renamed_count", "n_missing_ids", "n_invalid_geoms", "n_empty_geoms", "n_id_conflicts",
                      "duplicates_removed"):
                setattr(merged, f, getattr(res, f) + getattr(up, f))
            merged.missing_fields = res.missing_fields + up.missing_fields
            return merged
        return up
    return res


# ------------------------------------------------------------------ user uploads
def read_upload(name: str, data: bytes) -> gpd.GeoDataFrame:
    """Read GeoJSON / JSON / GeoPackage / zipped shapefile. Raises UploadError with a plain message."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(f"{name}: file is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
    ext = Path(name).suffix.lower()
    if ext not in (".geojson", ".json", ".gpkg", ".zip"):
        raise UploadError(f"{name}: unsupported type '{ext}'. Use .geojson, .json, .gpkg or a zipped shapefile (.zip).")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / ("upload" + ext)
        p.write_bytes(data)
        try:
            if ext == ".zip":
                with zipfile.ZipFile(p) as z:
                    names = z.namelist()
                    if any(n.startswith(("/", "..")) or ".." in Path(n).parts for n in names):
                        raise UploadError(f"{name}: unsafe paths inside zip.")
                    if not any(n.lower().endswith(".shp") for n in names):
                        raise UploadError(f"{name}: zip contains no .shp file.")
                    z.extractall(td)
                shp = next(Path(td).rglob("*.shp"))
                gdf = gpd.read_file(shp)
            else:
                gdf = gpd.read_file(p)
        except UploadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"{name}: could not be read as geospatial data ({type(exc).__name__}: {exc}).") from exc
    if len(gdf) == 0:
        raise UploadError(f"{name}: contains no features.")
    return gdf


def apply_uploads(pack: SourcePack, uploads: dict[str, tuple[str, bytes]]) -> SourcePack:
    """Overlay user-supplied layers on a pack. ``uploads``: kind -> (filename, bytes).

    Layers with an undeclared CRS are rejected by the harmonizer (never assumed). Uploaded
    parcels are the only way parcel analysis becomes available for Delhi.
    """
    if not uploads:
        return pack
    sha = lambda b: hashlib.sha256(b).hexdigest()[:10]  # noqa: E731
    layers, prov = dict(pack.layers), dict(pack.prov)
    msgs = list(pack.messages)
    up_corr = None
    now = _fmt(_now())
    for kind, (fname, data) in uploads.items():
        try:
            gdf = read_upload(fname, data)
        except UploadError as exc:
            msgs.append(("er", str(exc)))
            continue
        if kind == "corridors":
            gdf = gdf.copy()
            if not any(re.sub(r"[^0-9a-z]+", "_", c.lower()).strip("_") in ("scenario_type", "type", "corridor_type")
                       for c in gdf.columns):
                gdf["scenario_type"] = "road_expansion"
            if not any(re.sub(r"[^0-9a-z]+", "_", c.lower()).strip("_") in ("feature_id", "corridor_id", "id") for c in gdf.columns):
                gdf["feature_id"] = [f"UPLOAD-{i + 1:03d}" for i in range(len(gdf))]
        res = harmonize_layer(gdf, kind, f"upload:{fname}")
        res.changes.append(f"{fname}: user upload (sha256 {sha(data)}…).")
        p = Provenance(kind, f"User upload: {fname}", "USER UPLOAD", "User-supplied; not verified by NagarMap",
                       "As declared by the uploader", retrieved_utc=now,
                       note="Correctness, authority and licence of this file are the uploader's responsibility.")
        if kind == "corridors":
            up_corr = res
            prov["corridors"] = p
        elif kind in ("parcels", "buildings"):
            layers[kind] = res
            prov[kind] = p
        msgs.append(("ok" if res.usable else "er", f"Upload {fname} → {kind}: {len(gdf)} feature(s) read"
                     + ("" if res.usable else " but unusable (see DATA LAB)") + "."))
    return SourcePack(mode=pack.mode, area_label=pack.area_label, layers=layers, prov=prov, bbox=pack.bbox,
                      transit_raw=pack.transit_raw, messages=msgs, boundary_ok=pack.boundary_ok, upload_corridors=up_corr)
