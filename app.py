"""NagarMap - Urban Intelligence System (arcade UI for the Smart City & Mobility demo).

Run from the project root:  streamlit run app.py
UI and orchestration only; geospatial logic lives in spatial_engine.py, data validation in
data_harmonizer.py / diagnostics.py, reporting in report_generator.py, styling in theme.py.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import theme
from data_harmonizer import collect_log
from diagnostics import (FAILED, NOT_RUN, PASS, all_checks, indicator_lights, pipeline_stages,
                         quality_stats, system_status, worst_status)
from osm_source import (CUSTOM_AREA, STUDY_AREAS, apply_uploads, corridor_layer, load_delhi_boundary, load_demo_pack,
                        load_osm_pack, read_cache, area_key, bbox_from_center, point_in_delhi, boundary_provenance)
from report_generator import (DISCLAIMER_REAL, DISCLAIMER_SYNTH, build_markdown_report, build_report_df,
                              build_summary_text, report_to_csv)
from spatial_engine import AnalysisError, choose_analysis_crs, run_analysis

APP_VERSION = "0.3.0 · ARCADE UI · DELHI DATA"
MODE_OSM, MODE_DEMO = "Delhi · OpenStreetMap", "Synthetic demo (not real)"
DEFAULT_MODE = MODE_DEMO if os.environ.get("NAGARMAP_DEFAULT_MODE", "osm").lower() == "demo" else MODE_OSM
DATA_DIR = Path(__file__).resolve().parent / "data"

# Original scenario identifiers and mapping are unchanged; MISSIONS only adds presentation text.
SCENARIOS = {
    "Proposed road expansion": "road_expansion",
    "Proposed transit corridor": "transit_corridor",
    "Baseline (no planned corridor overlap)": "baseline",
}
MISSIONS = {
    "Proposed road expansion": {
        "code": "MISSION 01", "name": "ROAD EXPANSION",
        "objective": "Find parcels and buildings that a proposed road widening would intersect."},
    "Proposed transit corridor": {
        "code": "MISSION 02", "name": "TRANSIT CORRIDOR",
        "objective": "Check a dedicated transit right-of-way against the parcel and building layers."},
    "Baseline (no planned corridor overlap)": {
        "code": "MISSION 03", "name": "SAFE GRID / BASELINE",
        "objective": "Reference run on open land beside the grid, to compare against the other missions."},
}
OSM_OBJECTIVES = {
    "road_expansion": "Buffer a real OSM road centreline by the chosen width and find the buildings it would intersect.",
    "transit_corridor": "Buffer a real OSM metro/rail line by the chosen width and find the buildings it would intersect.",
    "baseline": "Reference run: a narrow 3 m buffer on the same road, to compare with the expansion scenario.",
}
SCREENS = ["CITY SELECT", "MISSION CONTROL", "MAP SCANNER", "DATA LAB", "CONFLICT DETECTOR", "MISSION REPORT"]

C = {"cyan": "#00F5FF", "blue": "#3D7BFF", "mag": "#FF2BD6", "green": "#B6FF3B", "amber": "#FFB000", "red": "#FF4057"}

st.set_page_config(page_title="NagarMap · Urban Intelligence System", page_icon="🕹️", layout="wide")
st.markdown(theme.CSS, unsafe_allow_html=True)


# ------------------------------------------------------------------ data + state helpers
@st.cache_data(show_spinner=False)
def get_boundary():
    return load_delhi_boundary()


@st.cache_data(show_spinner=False)
def get_pack(mode: str, lat: float, lon: float, half_m: int, label: str, refresh_n: int, uploads: tuple):
    """Load a data pack. refresh_n > 0 forces a live Overpass fetch (then served from cache of this call)."""
    if mode == MODE_DEMO:
        pack = load_demo_pack(DATA_DIR)
    else:
        pack = load_osm_pack(lat, lon, half_m, refresh=refresh_n > 0, area_label=label, boundary=get_boundary())
    return apply_uploads(pack, {k: (n, b) for k, n, b in uploads})


def goto(screen: str):
    st.session_state["screen"] = screen


def select_mission(label: str):
    st.session_state["mission"] = label


def request_scan():
    st.session_state["scan_requested"] = True
    st.session_state["screen"] = "MAP SCANNER"


def start_machine():
    st.session_state["booted"] = True


def refresh_osm():
    st.session_state["refresh_n"] = st.session_state.get("refresh_n", 0) + 1
    for k in ("analysis", "analysis_key", "target", "cursor", "scan_requested"):
        st.session_state.pop(k, None)


def reset_demo():
    st.cache_data.clear()
    for k in ("analysis", "analysis_key", "target", "cursor", "scan_requested"):
        st.session_state.pop(k, None)
    st.session_state["screen"] = "MAP SCANNER"


def esc(v) -> str:
    return "—" if v is None or (isinstance(v, float) and pd.isna(v)) or str(v) == "" else str(v)


def prep(gdf: gpd.GeoDataFrame, cols: list[str]) -> gpd.GeoDataFrame:
    """Plain-string copy of selected columns, with missing geometries dropped, for map popups."""
    gdf = gdf[~gdf.geometry.isna() & ~gdf.geometry.is_empty].copy()
    out = gdf[["geometry"]].copy()
    for c in cols:
        if c in gdf.columns:
            out[c] = gdf[c].map(esc).astype(str)
    return out


def n_features(layers, key) -> int:
    g = layers[key].gdf
    return 0 if g is None else len(g)


# ------------------------------------------------------------------ map
MAP_CSS = """<style>
.leaflet-container{background:#05070D; font-family:Consolas,'Courier New',monospace;}
.leaflet-popup-content-wrapper{background:#111426; color:#F2F4FF; border:1px solid #00F5FF; border-radius:4px;
  box-shadow:0 0 14px rgba(0,245,255,.35);}
.leaflet-popup-tip{background:#111426; border:1px solid #00F5FF;}
.leaflet-popup-content{font-size:12px; margin:10px 12px;}
.leaflet-popup-content table{border-collapse:collapse;} .leaflet-popup-content th{color:#939BB8; text-align:left; padding-right:10px; font-weight:400;}
.leaflet-popup-content td{color:#00F5FF;}
.leaflet-tooltip{background:#111426; color:#00F5FF; border:1px solid #00F5FF; border-radius:3px; box-shadow:none; font-size:11px;}
.nm-dark-tiles{filter:invert(1) hue-rotate(180deg) saturate(.45) brightness(.85);}
.leaflet-bar a{background:#111426; color:#00F5FF; border-bottom:1px solid #2A2F5A;}
.leaflet-control-scale-line{background:rgba(5,7,13,.7); color:#939BB8; border-color:#939BB8;}
</style>"""


def build_map(layers, corridor_row, result, show, pack=None, boundary=None):
    parcels = layers["parcels"].gdf
    buildings = layers["buildings"].gdf
    roads = layers["roads"].gdf
    m = folium.Map(location=[corridor_row.geometry.centroid.y, corridor_row.geometry.centroid.x],
                   zoom_start=17, max_zoom=21, tiles=None, control_scale=True)
    if show["basemap"]:
        # OSM standard tiles (CartoDB tiles now need an API key); darkened with CSS to keep the arcade look.
        folium.TileLayer("OpenStreetMap", name="Basemap", max_zoom=19, opacity=0.6, className="nm-dark-tiles").add_to(m)
    m.get_root().html.add_child(folium.Element(MAP_CSS))

    bounds_geoms = [corridor_row.geometry]
    outline = {"ok_pre": C["cyan"], "ok": C["green"], "warn": C["amber"], "conflict": C["red"]}

    if show["parcels"] and parcels is not None:
        if result is not None and len(result.parcels):
            res = result.parcels.to_crs("EPSG:4326")
            res["cls"] = res.apply(lambda r: "conflict" if r["intersection_status"] == "Overlap"
                                   else ("warn" if (r["dq_status"] != "Valid"
                                                    or r["intersection_status"] == "Boundary contact only")
                                         else "ok"), axis=1)
            data = prep(res, ["feature_id", "intersection_status", "overlap_area_sqm", "dq_status", "cls"])
            data["overlap_area_sqm"] = data["overlap_area_sqm"].map(
                lambda v: f"{float(v):.1f} m²" if v not in ("—", "") else "—")
            meta = parcels.drop_duplicates("feature_id").set_index("feature_id")[["land_use", "zone"]].astype(str)
            data["land_use"] = res["feature_id"].map(meta["land_use"]).reindex(data.index).fillna("—")
            data["zone"] = res["feature_id"].map(meta["zone"]).reindex(data.index).fillna("—")
            fields = ["feature_id", "land_use", "zone", "intersection_status", "overlap_area_sqm", "dq_status"]
            aliases = ["PARCEL ID", "LAND USE", "ZONE", "CORRIDOR CHECK", "OVERLAP", "DATA QUALITY"]
        else:
            p = parcels.copy()
            p["cls"] = p["dq_status"].map(lambda s: "ok_pre" if s == "Valid" else "warn")
            data = prep(p, ["feature_id", "land_use", "zone", "dq_status", "cls"])
            fields = ["feature_id", "land_use", "zone", "dq_status"]
            aliases = ["PARCEL ID", "LAND USE", "ZONE", "DATA QUALITY"]
        data["ftype"] = "Parcel"
        fill = {"ok_pre": 0.05, "ok": 0.08, "warn": 0.3, "conflict": 0.5}
        folium.GeoJson(
            data, name="Parcels",
            style_function=lambda f: {"color": outline[f["properties"]["cls"]],
                                      "weight": 1.2 if f["properties"]["cls"] in ("ok", "ok_pre") else 2.5,
                                      "fillColor": outline[f["properties"]["cls"]],
                                      "fillOpacity": fill[f["properties"]["cls"]]},
            highlight_function=lambda f: {"weight": 4, "fillOpacity": 0.55},
            tooltip=folium.GeoJsonTooltip(fields=["feature_id"], aliases=["PARCEL"]),
            popup=folium.GeoJsonPopup(fields=fields, aliases=aliases, max_width=320),
        ).add_to(m)
        if result is not None:
            aff = result.parcels[result.parcels["affected"] == True]  # noqa: E712
            if len(aff):
                bounds_geoms.extend(aff.to_crs("EPSG:4326").geometry)

    if show["buildings"] and buildings is not None:
        if result is not None and len(result.buildings):
            res = result.buildings.to_crs("EPSG:4326")
            res["cls"] = res["intersection_status"].map(
                lambda s: "conflict" if s == "Overlap" else ("warn" if s == "Boundary contact only" else "ok"))
            data = prep(res, ["feature_id", "intersection_status", "overlap_area_sqm", "dq_status", "cls"])
            data["overlap_area_sqm"] = data["overlap_area_sqm"].map(
                lambda v: f"{float(v):.1f} m²" if v not in ("—", "") else "—")
            fields = ["feature_id", "intersection_status", "overlap_area_sqm", "dq_status"]
            aliases = ["BUILDING ID", "CORRIDOR CHECK", "OVERLAP", "DATA QUALITY"]
        else:
            b = buildings.copy()
            b["cls"] = "ok_pre"
            data = prep(b, ["feature_id", "use_type", "floors", "dq_status", "cls"])
            fields = ["feature_id", "use_type", "floors", "dq_status"]
            aliases = ["BUILDING ID", "USE", "FLOORS", "DATA QUALITY"]
        data["ftype"] = "Building"
        bfill = {"ok_pre": 0.3, "ok": 0.3, "warn": 0.45, "conflict": 0.75}
        folium.GeoJson(
            data, name="Buildings",
            style_function=lambda f: {"color": outline[f["properties"]["cls"]], "weight": 1.5,
                                      "fillColor": outline[f["properties"]["cls"]],
                                      "fillOpacity": bfill[f["properties"]["cls"]]},
            highlight_function=lambda f: {"weight": 3.5, "fillOpacity": 0.85},
            tooltip=folium.GeoJsonTooltip(fields=["feature_id"], aliases=["BUILDING"]),
            popup=folium.GeoJsonPopup(fields=fields, aliases=aliases, max_width=300),
        ).add_to(m)

    if show["roads"] and roads is not None:
        rd = prep(roads, ["feature_id", "road_name", "road_class"])
        rd["ftype"] = "Road"
        folium.GeoJson(
            rd, name="Roads",
            style_function=lambda f: {"color": C["blue"], "weight": 3.5, "opacity": 0.95},
            highlight_function=lambda f: {"weight": 6, "opacity": 1},
            tooltip=folium.GeoJsonTooltip(fields=["road_name", "road_class"], aliases=["ROAD", "CLASS"]),
        ).add_to(m)

    if show["corridor"]:
        cdata = prep(gpd.GeoDataFrame([corridor_row], crs="EPSG:4326"),
                     ["feature_id", "corridor_name", "status", "description"])
        cdata["ftype"] = "Corridor"
        folium.GeoJson(
            cdata, name="Proposed corridor",
            style_function=lambda f: {"color": C["mag"], "weight": 2.8, "dashArray": "7 5",
                                      "fillColor": C["mag"], "fillOpacity": 0.2},
            tooltip=folium.GeoJsonTooltip(fields=["corridor_name"], aliases=["CORRIDOR"]),
            popup=folium.GeoJsonPopup(fields=["feature_id", "corridor_name", "status", "description"],
                                      aliases=["ID", "NAME", "STATUS", "DESCRIPTION"], max_width=320),
        ).add_to(m)

    if show.get("boundary") and boundary is not None and len(boundary):
        folium.GeoJson(boundary[["geometry"]], name="Delhi boundary",
                       style_function=lambda f: {"color": C["amber"], "weight": 2, "fillOpacity": 0, "dashArray": "2 6"},
                       tooltip="Delhi boundary (DataMeet, CC BY-SA 2.5 IN)").add_to(m)
        bounds_geoms.extend(boundary.geometry)
        if pack is not None and pack.bbox:
            w, s, e, n = pack.bbox
            folium.Rectangle([[s, w], [n, e]], color=C["cyan"], weight=2, fill=False, tooltip="Study window").add_to(m)

    minx, miny, maxx, maxy = gpd.GeoSeries(bounds_geoms, crs="EPSG:4326").total_bounds
    m.fit_bounds([[miny, minx], [maxy, maxx]], padding=(30, 30))

    legend_src = "synthetic data" if (pack is None or not pack.is_real) else "© OpenStreetMap contributors (ODbL)"
    legend = f"""
    <div style="position:fixed; bottom:16px; left:12px; z-index:9999; background:rgba(8,10,18,.88); color:#F2F4FF;
      border:1px solid #2A2F5A; border-radius:4px; padding:7px 11px; font:11px Consolas,'Courier New',monospace; line-height:1.65;">
      <b style="letter-spacing:.15em;color:#00F5FF">LEGEND</b> <span style="color:#939BB8">· {legend_src}</span><br>
      <span style="color:{C['mag']}">▬ ▬</span> Proposed corridor<br>
      <span style="color:{C['red']}">■</span> Overlap - potential conflict<br>
      <span style="color:{C['amber']}">■</span> Warning / boundary contact<br>
      <span style="color:{C['green']}">■</span> Scanned, no overlap<br>
      <span style="color:{C['cyan']}">■</span> Not yet scanned<br>
      <span style="color:{C['blue']}">━</span> Road
    </div>"""
    m.get_root().html.add_child(folium.Element(legend))
    return m


# ------------------------------------------------------------------ small renderers
STATUS_CSS = {
    "Overlap": "background-color:rgba(255,64,87,.25);color:#FF4057;font-weight:700",
    "Boundary contact only": "background-color:rgba(255,176,0,.18);color:#FFB000",
    "No intersection": "color:#B6FF3B",
    "Not analyzed": "color:#939BB8",
}
DQ_CSS = {"Valid": "color:#B6FF3B", "Warning": "background-color:rgba(255,176,0,.18);color:#FFB000",
          "Error": "background-color:rgba(255,64,87,.25);color:#FF4057"}


def style_table(df: pd.DataFrame):
    sty = df.style.format({"overlap_area_sqm": "{:.1f}"}, na_rep="—")
    mapper = getattr(sty, "map", None) or sty.applymap
    sty = mapper(lambda v: STATUS_CSS.get(v, ""), subset=["intersection_status"])
    mapper = getattr(sty, "map", None) or sty.applymap
    return mapper(lambda v: DQ_CSS.get(v, ""), subset=["data_quality_status"])


REPORT_VIEW_COLS = ["feature_id", "feature_type", "intersection_status", "overlap_area_sqm",
                    "data_quality_status", "source_layer", "reason_for_flagging"]


def hud_modules(layers, result):
    valid, total = quality_stats(layers)
    stages = pipeline_stages(layers, result)
    done = sum(1 for _, ok in stages if ok)
    nxt = next((n for n, ok in stages if not ok), None)
    if result is not None:
        s = result.summary
        conflicts = s["parcels_affected"] + s["buildings_affected"]
        scanned_p = (str(s["parcels_analyzed"]), f"of {s['parcels_total']} records", "cyan")
        scanned_b = (str(s["buildings_analyzed"]), f"of {s['buildings_total']} records", "cyan")
        conf = (str(conflicts), f"{s['parcels_affected']} parcels · {s['buildings_affected']} buildings",
                "red" if conflicts else "green")
    else:
        scanned_p = (str(n_features(layers, "parcels")), "loaded · awaiting scan", "muted")
        scanned_b = (str(n_features(layers, "buildings")), "loaded · awaiting scan", "muted")
        conf = ("—", "press SCAN CITY", "muted")
    pct = f"{valid / total * 100:.0f}%" if total else "—"
    if layers["parcels"].unavailable:
        scanned_p = ("N/A", "no open Delhi parcel data", "muted")
    return [
        {"label": "PARCELS SCANNED", "value": scanned_p[0], "sub": scanned_p[1], "tone": scanned_p[2]},
        {"label": "CONFLICTS DETECTED", "value": conf[0], "sub": conf[1], "tone": conf[2]},
        {"label": "BUILDINGS ANALYZED", "value": scanned_b[0], "sub": scanned_b[1], "tone": scanned_b[2]},
        {"label": "DATA QUALITY", "value": pct, "sub": f"{valid}/{total} records unflagged",
         "tone": "green" if total and valid == total else "amber"},
        {"label": "SCAN PROGRESS", "value": f"{done}/{len(stages)}", "progress": done / len(stages), "tone": "mag",
         "sub": "all stages complete" if nxt is None else f"next: {nxt}"},
    ]


def target_panel(layers, result, key):
    """Game-style inspection screen for the feature last clicked on the map."""
    sel = st.session_state.get("target")
    if not sel or sel[0] != key:
        st.markdown('<div class="nm-panel nm-lock"><div class="hd">NO TARGET LOCKED</div>'
                    '<p>Click a parcel, building, road or the corridor on the map to inspect it.</p></div>',
                    unsafe_allow_html=True)
        return
    _, ftype, fid = sel
    pairs, tone, head = [("TYPE", ftype.upper()), ("ID", fid)], "", "TARGET LOCKED"
    reason = ""
    row = None
    if result is not None and ftype in ("Parcel", "Building"):
        frame = result.parcels if ftype == "Parcel" else result.buildings
        hit = frame[frame["feature_id"] == fid]
        row = hit.iloc[0] if len(hit) else None
    if row is not None:
        status, tone = {"Overlap": ("POTENTIAL CONFLICT", "red"), "Boundary contact only": ("BOUNDARY CONTACT", "amber"),
                        "No intersection": ("CLEAR OF CORRIDOR", "green"), "Not analyzed": ("NOT ANALYZED", "muted")
                        }[row["intersection_status"]]
        detection = {"Overlap": "CORRIDOR INTERSECTION", "Boundary contact only": "BOUNDARY CONTACT ONLY",
                     "No intersection": "NO INTERSECTION"}.get(row["intersection_status"], "—")
        area = row["overlap_area_sqm"]
        pairs += [("STATUS", status), ("DETECTION", detection),
                  ("OVERLAP AREA", "—" if pd.isna(area) else f"{area:.1f} m² ({row['overlap_pct_of_feature']:.1f}% of feature)"),
                  ("DATA QUALITY", str(row["dq_status"]).upper()), ("SOURCE", row["source_layer"])]
        reason = row["reason"]
    else:
        src = {"Parcel": "parcels", "Building": "buildings", "Road": "roads", "Corridor": "corridors"}.get(ftype)
        gdf = layers[src].gdf if src else None
        if gdf is not None:
            hit = gdf[gdf["feature_id"] == fid]
            if len(hit):
                r = hit.iloc[0]
                for col, lab in (("land_use", "LAND USE"), ("zone", "ZONE"), ("use_type", "USE"), ("floors", "FLOORS"),
                                 ("road_name", "NAME"), ("road_class", "CLASS"), ("corridor_name", "NAME"),
                                 ("status", "STATUS"), ("description", "DESCRIPTION")):
                    if col in r.index and not pd.isna(r[col]):
                        pairs.append((lab, str(r[col])))
                if "dq_status" in r.index:
                    pairs.append(("DATA QUALITY", str(r["dq_status"]).upper()))
        if ftype in ("Parcel", "Building"):
            pairs.append(("STATUS", "NOT YET SCANNED"))
    if pack.is_real and ftype in ("Building", "Road") and "/" in fid:
        pairs.append(("OSM OBJECT", f"openstreetmap.org/{fid}"))
    cur = st.session_state.get("cursor")
    st.markdown(f'<div class="nm-panel nm-lock"><div class="hd {tone}">{theme.esc(head)}</div>{theme.kv_html(pairs)}'
                + (f'<div class="nm-note">{theme.esc(reason)}</div>' if reason else "")
                + '<div class="nm-note">A detected intersection is a potential conflict requiring review - not proof '
                  'of encroachment or legal ownership.</div></div>', unsafe_allow_html=True)
    st.caption("COPY ID")
    st.code(fid, language=None)
    if cur:
        st.caption(f"CLICK COORDINATES (WGS84): {cur[0]:.6f}, {cur[1]:.6f}")


# ------------------------------------------------------------------ load data (+ boot screen)
boundary = get_boundary()
st.session_state.setdefault("screen", "MAP SCANNER")
st.session_state.setdefault("mission", list(SCENARIOS)[0])
st.session_state.setdefault("data_mode", DEFAULT_MODE)
st.session_state.setdefault("refresh_n", 0)

if not st.session_state.get("booted"):
    lines = [("", "INITIALIZING NAGARMAP...", "")]
    if boundary is not None:
        lines.append(("ok", "DELHI BOUNDARY LOADED", "static file · DataMeet · CC BY-SA 2.5 IN"))
    else:
        lines.append(("er", "DELHI BOUNDARY MISSING", "data/delhi_boundary.geojson not found"))
    if st.session_state["data_mode"] == MODE_DEMO:
        _p = get_pack(MODE_DEMO, 0.0, 0.0, 0, "Synthetic Ward A", 0, ())
        _L = _p.layers
        n = lambda k: n_features(_L, k)  # noqa: E731
        try:
            crs_txt = str(choose_analysis_crs(_L["corridors"].gdf))
        except Exception:  # noqa: BLE001 - boot screen must never crash the app
            crs_txt = None
        if all(_L[k].gdf is not None and _L[k].usable for k in ("parcels", "buildings", "corridors")):
            lines.append(("ok", "URBAN GRID LOADED", f"{n('parcels')} parcels · {n('buildings')} buildings · "
                          f"{n('roads')} roads · {n('corridors')} corridors"))
        else:
            lines.append(("er", "URBAN GRID: LOAD PROBLEM", "one or more layers could not be used - see DATA LAB"))
        lines.append(("ok", "GEO-SPATIAL ENGINE READY", f"analysis CRS {crs_txt}") if crs_txt
                     else ("er", "GEO-SPATIAL ENGINE: NO ANALYSIS CRS", "corridor layer unavailable"))
        issues = collect_log(_L)[1]
        lines.append(("wa", f"SYSTEM READY - {len(issues)} DATA ITEM(S) NEED REVIEW", "see DATA LAB") if issues
                     else ("ok", "SYSTEM READY.", ""))
        hint = "SYNTHETIC DEMO DATA · THIS BUTTON SKIPS THE INTRO"
    else:
        _, la, lo = STUDY_AREAS["connaught_place"]
        _c = read_cache(area_key(la, lo, 600))
        lines.append(("ok", "OSM CACHE FOUND", f"retrieved {_c['retrieved_utc']}") if _c else
                     ("wa", "NO OSM CACHE YET", "first START fetches live from the Overpass API (needs internet)"))
        lines.append(("wa", "PARCEL LAYER UNAVAILABLE", "no open Delhi cadastral data verified"))
        lines.append(("ok", "GEO-SPATIAL ENGINE READY", "analysis CRS projected UTM (metres)"))
        hint = "OPENSTREETMAP DATA © OSM CONTRIBUTORS (ODbL) · THIS BUTTON SKIPS THE INTRO"
    st.markdown(theme.boot_html(lines), unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1, 1])
    with mid:
        with st.container(key="start"):
            st.button("▶ PRESS START", on_click=start_machine, width="stretch", key="btn_start")
        st.markdown(f'<div class="nm-hint">{theme.esc(hint)}</div>', unsafe_allow_html=True)
    st.stop()

# ------------------------------------------------------------------ control panel (sidebar): data source
with st.sidebar:
    st.markdown('<div class="nm-side-title">◈ CONTROL PANEL</div>', unsafe_allow_html=True)
    st.markdown('<div class="nm-side-label">DATA SOURCE</div>', unsafe_allow_html=True)
    data_mode = st.selectbox("Data source", [MODE_OSM, MODE_DEMO], key="data_mode", label_visibility="collapsed")
    area_label, lat, lon, half_m = "Synthetic Ward A", 0.0, 0.0, 0
    if data_mode == MODE_OSM:
        area_opts = {k: v[0] for k, v in STUDY_AREAS.items()}
        area_opts[CUSTOM_AREA] = "Custom location (lat/lon)"
        area_sel = st.selectbox("Study area", list(area_opts), format_func=area_opts.get, key="area",
                                label_visibility="collapsed")
        if area_sel == CUSTOM_AREA:
            lat = st.number_input("Latitude", 28.40, 28.89, 28.6315, step=0.001, format="%.4f", key="lat")
            lon = st.number_input("Longitude", 76.83, 77.35, 77.2167, step=0.001, format="%.4f", key="lon")
            area_label = f"Custom {lat:.4f}, {lon:.4f}"
        else:
            area_label, lat, lon = STUDY_AREAS[area_sel]
        half_m = st.select_slider("Study window half-size (m)", [300, 400, 500, 600, 800, 1000], 600, key="half_m")
        width_m = st.slider("Corridor: metres each side of centreline", 5, 50, 15, key="width_m",
                            help="Hypothetical right-of-way = OSM road centreline buffered by this distance each side.")
        st.button("↻ REFRESH FROM OSM (LIVE)", on_click=refresh_osm, width="stretch", key="btn_refresh",
                  help="Re-download this window from the Overpass API and update the on-disk cache.")
        st.markdown('<div class="nm-hint">LIVE DOWNLOAD ON DEMAND · NOT AUTO-UPDATING</div>', unsafe_allow_html=True)
    else:
        width_m = 15
    with st.expander("UPLOAD YOUR OWN LAYERS"):
        st.caption("GeoJSON / JSON / GeoPackage / zipped shapefile, with a declared CRS (never assumed).")
        ups = {}
        for kind, lab in (("parcels", "Parcels (enables parcel analysis)"), ("buildings", "Buildings (replaces OSM)"),
                          ("corridors", "Corridor polygon(s) (added to scenarios)")):
            f = st.file_uploader(lab, type=["geojson", "json", "gpkg", "zip"], key=f"up_{kind}")
            if f is not None:
                ups[kind] = (kind, f.name, f.getvalue())
    uploads = tuple(ups.values())

with st.spinner("LOADING DATA…"):
    pack = get_pack(data_mode, float(lat), float(lon), int(half_m), area_label, st.session_state["refresh_n"], uploads)
layers = dict(pack.layers)
layers["corridors"] = corridor_layer(pack, width_m)
corridors = layers["corridors"].gdf
if pack.is_real and (corridors is None or len(corridors) == 0):
    pass  # handled below: corridor layer carries its own explanation

if corridors is None or not layers["corridors"].usable:
    why = " ".join(i for i in (layers["corridors"].issues + layers["buildings"].issues) if i) or "Check the data source."
    st.error(f"Corridor data could not be loaded. {why}")
    for lvl, msg in pack.messages:
        if lvl == "er":
            st.caption(msg)
    with st.sidebar:
        st.button("↺ RESET / RELOAD DATA", on_click=reset_demo, width="stretch", key="btn_reset")
    st.stop()
if layers["buildings"].gdf is None:
    st.error("Building data is unavailable for this window: " + " ".join(layers["buildings"].issues))
    with st.sidebar:
        st.button("↺ RESET / RELOAD DATA", on_click=reset_demo, width="stretch", key="btn_reset")
    st.stop()

# ------------------------------------------------------------------ control panel (sidebar): mission
with st.sidebar:
    st.caption(f"CITY: {area_label.upper()}")
    st.markdown('<div class="nm-side-label">MISSION SELECT</div>', unsafe_allow_html=True)
    with st.container(key="missionbox"):
        scenario_label = st.radio(
            "Mission", list(SCENARIOS), key="mission", label_visibility="collapsed",
            format_func=lambda s: f"{MISSIONS[s]['code'][-2:]} · {MISSIONS[s]['name']}")
    scenario_type = SCENARIOS[scenario_label]
    options = corridors[corridors["scenario_type"] == scenario_type]
    names = {r.feature_id: f"{r.feature_id} - {r.corridor_name}" for r in options.itertuples()}
    if not names:
        st.warning("No corridors available for this mission in this study window.")
        st.stop()
    st.markdown('<div class="nm-side-label">CORRIDOR</div>', unsafe_allow_html=True)
    corridor_id = st.selectbox("Corridor", list(names), format_func=names.get,
                               key=f"corridor_{scenario_type}_{pack.mode}_{area_label}",
                               label_visibility="collapsed")

    st.markdown('<div class="nm-side-label">MAP LAYERS</div>', unsafe_allow_html=True)
    show = {
        "parcels": st.checkbox("Parcels", True, key="lyr_parcels", disabled=layers["parcels"].unavailable),
        "buildings": st.checkbox("Buildings", True, key="lyr_buildings"),
        "roads": st.checkbox("Roads", True, key="lyr_roads"),
        "corridor": st.checkbox("Proposed corridor", True, key="lyr_corridor"),
        "boundary": st.checkbox("Delhi boundary + study window", False, key="lyr_boundary"),
        "basemap": st.checkbox("Dark basemap (OSM tiles, needs internet)", False, key="lyr_basemap"),
    }
    st.markdown("&nbsp;", unsafe_allow_html=True)
    key = (scenario_label, corridor_id, pack.mode, area_label, width_m, len(uploads), st.session_state["refresh_n"])
    scan_done = st.session_state.get("analysis_key") == key and st.session_state.get("analysis") is not None
    with st.container(key="scan_side"):
        st.button("✔ SCAN COMPLETE" if scan_done else "▶ SCAN CITY", on_click=request_scan,
                  disabled=scan_done, width="stretch", key="btn_scan_side", type="primary",
                  help="Already scanned. Pick another mission or corridor, or reset." if scan_done
                  else "Run the geospatial analysis for the selected corridor.")
    st.markdown('<div class="nm-hint">RUN GEOSPATIAL ANALYSIS</div>', unsafe_allow_html=True)
    st.button("✔ VALIDATE DATA (DATA LAB)", on_click=goto, args=("DATA LAB",), width="stretch", key="btn_validate")
    st.button("↺ RESET / RELOAD DATA", on_click=reset_demo, width="stretch", key="btn_reset")

corridor_row = corridors[corridors["feature_id"] == corridor_id].iloc[0]

# ------------------------------------------------------------------ run the real analysis
scan_ok = False
if st.session_state.pop("scan_requested", False):
    with st.spinner("SCANNING URBAN GRID…"):
        try:
            res_new = run_analysis(
                layers["parcels"].gdf, layers["buildings"].gdf, corridors[corridors["feature_id"] == corridor_id],
                scenario_name=scenario_label, corridor_id=corridor_id, corridor_name=corridor_row["corridor_name"],
                source_names={k: layers[k].source_layer for k in ("parcels", "buildings")},
                unavailable={"parcels": "Parcel analysis unavailable: " + (layers["parcels"].unavailable_reason or "")}
                if layers["parcels"].unavailable else None)
            st.session_state["analysis"] = res_new
            st.session_state["analysis_key"] = key
            s = res_new.summary
            flagged = s["parcels_affected"] + s["buildings_affected"]
            toasts = [(f"SCAN COMPLETE · {flagged} potential conflict(s) flagged for review", "⚠️") if flagged
                      else ("SCAN COMPLETE · no overlaps detected for this corridor", "✅")]
            if s["dq_warning_records"]:
                toasts.append((f"{s['dq_warning_records']} record(s) carry data-quality warnings", "🔎"))
            st.session_state["pending_toasts"] = toasts
            scan_ok = True
        except AnalysisError as exc:
            st.session_state.pop("analysis", None)
            st.session_state.pop("analysis_key", None)
            st.session_state["scan_error"] = str(exc)
            scan_ok = True
    if scan_ok:
        st.rerun()  # refresh the sidebar button state (SCAN CITY -> SCAN COMPLETE)

for _msg, _icon in st.session_state.pop("pending_toasts", []):
    st.toast(_msg, icon=_icon)
if st.session_state.get("scan_error"):
    st.error(f"Analysis could not run: {st.session_state.pop('scan_error')}")

result = st.session_state.get("analysis") if st.session_state.get("analysis_key") == key else None
prov_by_type = {"Parcel": pack.prov.get("parcels"), "Building": pack.prov.get("buildings")}
prov_by_type = {k: v for k, v in prov_by_type.items() if v is not None}
report = build_report_df(result, prov_by_type) if result is not None else None
disclaimer = DISCLAIMER_REAL if pack.is_real else DISCLAIMER_SYNTH


def prov_rows() -> list[dict]:
    rows = []
    for k in ("boundary", "parcels", "buildings", "roads", "corridors"):
        p = pack.prov.get(k)
        if p is None:
            continue
        rows.append({"layer": p.layer, "source": p.source, "kind": p.kind, "licence": p.licence,
                     "attribution": p.attribution or p.note, "retrieved": p.retrieved_utc, "freshness": p.freshness(),
                     "data_ts": p.data_timestamp, "url": p.url})
    return rows


def report_md() -> bytes:
    info = (f"{width_m:g} m each side of the OSM centreline (baseline 3 m)" if pack.is_real else "bundled synthetic polygon")
    return build_markdown_report(result, report, prov_rows(), pack.is_real, info)

# ------------------------------------------------------------------ header + HUD + navigation
status_label, status_tone = system_status(layers, result)
mission_meta = MISSIONS[scenario_label]
if pack.is_real:
    _kinds = {pack.prov[k].kind for k in ("buildings", "roads") if k in pack.prov}
    chip = (("OSM · LIVE API FETCH", "green") if "LIVE API" in _kinds else
            ("OSM · CACHED DOWNLOAD", "amber") if "CACHED DOWNLOAD" in _kinds else ("OSM · NO DATA", "red"))
    notes_ = ["<b>Real OpenStreetMap data</b> (© OpenStreetMap contributors, ODbL) for " + theme.esc(area_label)
              + ". <b>Parcel/cadastral analysis is " + ("ENABLED via your upload" if not layers["parcels"].unavailable else "UNAVAILABLE")
              + "</b>: no open Delhi parcel geometry was verified. Corridors are user-set scenarios, not official alignments. "
                "A detected intersection is a <b>potential conflict requiring review</b>."]
    if pack.boundary_ok is False:
        notes_.append(" <b>Warning:</b> this window's centre is outside the bundled Delhi boundary.")
    header_kw = {"data_chip": chip, "banner_html": "".join(notes_)}
elif pack.mode == "demo":
    header_kw = {}
st.markdown(theme.header_html(status_label, status_tone, f"{mission_meta['code']} {mission_meta['name']}",
                              APP_VERSION, indicator_lights(layers, result), **header_kw), unsafe_allow_html=True)
st.markdown(theme.hud_html(hud_modules(layers, result)), unsafe_allow_html=True)
with st.container(key="nav"):
    screen = st.radio("Navigation", SCREENS, key="screen", horizontal=True, label_visibility="collapsed")


# ------------------------------------------------------------------ screens
def screen_city():
    if pack.is_real:
        intro = (f'<b>{theme.esc(area_label.upper())}</b> · real OpenStreetMap roads and building footprints inside a '
                 f'{2 * half_m} m study window, checked against the bundled Delhi boundary'
                 + ("" if pack.boundary_ok is None else (" (centre inside Delhi)." if pack.boundary_ok
                                                          else " (<b>centre is OUTSIDE Delhi</b>)."))
                 + " Parcel analysis is unavailable unless you upload parcels.")
    else:
        intro = ('<b>SYNTHETIC WARD A</b> is an artificial 5×5 parcel grid generated for NagarMap tests and offline demos. '
                 'Switch DATA SOURCE to Delhi · OpenStreetMap for real data.')
    st.markdown(f'<div class="nm-panel"><h4>◈ CITY SELECT</h4><p>{intro}</p></div>', unsafe_allow_html=True)
    rows = []
    for k, res in layers.items():
        p = pack.prov.get(k)
        rows.append({"LAYER": k.upper(), "FILE": res.source_layer, "RECORDS READ": res.n_input,
                     "RECORDS IN USE": 0 if res.gdf is None else len(res.gdf), "SOURCE CRS": res.source_crs,
                     "STATUS": worst_status(all_checks({k: res})[k]),
                     "DATA TYPE": p.kind if p else "—", "RETRIEVED": (p.retrieved_utc or "—") if p else "—",
                     "FRESHNESS": p.freshness() if p else "—"})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    srows = []
    for r in prov_rows():
        srows.append((r["layer"].upper(), f"{r['source']} · {r['kind']} · licence {r['licence'] or 'n/a'} · "
                      f"{r['attribution']} · retrieved {r['retrieved'] or 'n/a'}"
                      + (f" · data as of {r['data_ts']}" if r["data_ts"] else "") + (f" · {r['url']}" if r["url"] else "")))
    st.markdown('<div class="nm-panel"><h4>◈ DATA SOURCES &amp; ATTRIBUTION</h4>' + theme.kv_html(srows)
                + '<div class="nm-note">LIVE API = downloaded from the Overpass API in this session. CACHED DOWNLOAD = read from disk, '
                  'with its original retrieval time shown. STATIC BUNDLED = file shipped with the app. NagarMap does not '
                  'auto-update or stream data; use ↻ REFRESH to re-download.</div></div>', unsafe_allow_html=True)
    st.button("▶ CONTINUE TO MISSION CONTROL", on_click=goto, args=("MISSION CONTROL",), key="btn_to_mission")


def screen_mission():
    cols = st.columns(3)
    for col, (label, meta) in zip(cols, MISSIONS.items()):
        ids = ", ".join(corridors.loc[corridors["scenario_type"] == SCENARIOS[label], "feature_id"])
        selected = label == scenario_label
        with col:
            st.markdown(theme.mission_card_html(meta["code"], meta["name"],
                                                OSM_OBJECTIVES[SCENARIOS[label]] if pack.is_real else meta["objective"],
                                                f"Corridor(s): {ids or '—'}", selected), unsafe_allow_html=True)
            st.button("✔ SELECTED" if selected else "SELECT MISSION", on_click=select_mission, args=(label,),
                      disabled=selected, width="stretch", key=f"pick_{SCENARIOS[label]}")
    st.markdown("&nbsp;", unsafe_allow_html=True)
    left, right = st.columns([2, 1])
    with left:
        pairs = [("MISSION", f"{mission_meta['code']} · {mission_meta['name']}"), ("CORRIDOR", f"{corridor_id} · {corridor_row['corridor_name']}"),
                 ("CORRIDOR STATUS", esc(corridor_row.get("status"))), ("DESCRIPTION", esc(corridor_row.get("description"))),
                 ("LAYERS CHECKED", "buildings only - parcels UNAVAILABLE (no open Delhi cadastral data)"
                                    if layers["parcels"].unavailable else "parcels, buildings (vs. selected corridor)")]
        st.markdown('<div class="nm-panel"><h4>◈ MISSION BRIEFING</h4>' + theme.kv_html(pairs)
                    + '<div class="nm-note">The scan intersects the corridor with every parcel and building in a projected '
                      '(metric) CRS. Results are potential conflicts for human review.</div></div>', unsafe_allow_html=True)
        if layers["parcels"].unavailable:
            st.markdown('<div class="nm-banner amber"><b>PARCELS UNAVAILABLE</b> · ' + theme.esc(layers["parcels"].unavailable_reason)
                        + '</div>', unsafe_allow_html=True)
    with right:
        with st.container(key="scan_main"):
            st.button("✔ SCAN COMPLETE" if scan_done else "▶ SCAN CITY", on_click=request_scan, disabled=scan_done,
                      width="stretch", key="btn_scan_main", type="primary")
        st.markdown('<div class="nm-hint">RUN GEOSPATIAL ANALYSIS</div>', unsafe_allow_html=True)


def screen_map():
    left, right = st.columns([3.3, 1.2])
    with left:
        cen = corridor_row.geometry.centroid
        crs_txt = result.analysis_crs if result is not None else "not yet selected"
        with st.container(key="mapframe"):
            st.markdown(f'<div class="nm-ticker"><span>GRID CENTRE <b>{cen.y:.6f}, {cen.x:.6f}</b></span>'
                        f'<span>DISPLAY <b>WGS84</b></span><span>ANALYSIS CRS <b>{theme.esc(crs_txt)}</b></span>'
                        f'<span>MODE <b>{"SCANNED" if result is not None else "PRE-SCAN"}</b></span></div>',
                        unsafe_allow_html=True)
            fmap = build_map(layers, corridor_row, result, show, pack, boundary)
            mkey = f"map-{key}-{result is not None}"
            returned = ["last_active_drawing", "last_object_clicked"]
            try:
                out = st_folium(fmap, height=520, use_container_width=True, returned_objects=returned, key=mkey)
            except TypeError:
                out = st_folium(fmap, height=520, width=1000, returned_objects=returned, key=mkey)
        if out:
            feat = out.get("last_active_drawing")
            props = feat.get("properties") if isinstance(feat, dict) else None
            if props and props.get("feature_id") and props.get("ftype"):
                st.session_state["target"] = (key, props["ftype"], props["feature_id"])
            click = out.get("last_object_clicked")
            if isinstance(click, dict) and "lat" in click:
                st.session_state["cursor"] = (click["lat"], click["lng"])
        if result is None:
            st.markdown('<div class="nm-banner">◈ PRE-SCAN VIEW · choose a mission and press <b>▶ SCAN CITY</b> '
                        'in the control panel to analyze this corridor.</div>', unsafe_allow_html=True)
        else:
            s = result.summary
            flagged = s["parcels_affected"] + s["buildings_affected"]
            tone = "amber" if (flagged or s["dq_warning_records"]) else ""
            st.markdown(f'<div class="nm-banner {tone}"><b>SCAN COMPLETE</b> · {flagged} potential conflict(s) flagged · '
                        f'{s["dq_warning_records"]} record(s) with data-quality warnings · review required before any conclusion</div>',
                        unsafe_allow_html=True)
            b1, b2, _ = st.columns([1, 1, 2])
            b1.button("OPEN CONFLICT DETECTOR", on_click=goto, args=("CONFLICT DETECTOR",), key="btn_to_conf")
            b2.button("OPEN MISSION REPORT", on_click=goto, args=("MISSION REPORT",), key="btn_to_rep")
    with right:
        target_panel(layers, result, key)


def screen_lab():
    checks = all_checks(layers)
    rows = [(c.layer, c.name, c.status, c.detail) for cs in checks.values() for c in cs]
    ran = sum(1 for r in rows if r[2] != NOT_RUN)
    fails = sum(1 for r in rows if r[2] == FAILED)
    st.markdown(f'<div class="nm-panel"><h4>◈ DATA LAB · HARMONIZATION DIAGNOSTICS</h4>'
                f'<p>{ran} of {len(rows)} checks ran · {sum(1 for r in rows if r[2] == PASS)} passed · '
                f'{fails} failed. A status is shown only for checks that actually ran; an undeclared CRS is never reported as a pass.</p>'
                + theme.diag_table_html(rows) + '</div>', unsafe_allow_html=True)
    changes, issues = collect_log(layers)
    log = [(lvl, m) for lvl, m in pack.messages]
    log += [("ok", c) for c in changes]
    for i in issues:
        log.append(("er" if ("NOT assumed" in i or "could not" in i or "excluded" in i) else "wa", i))
    if result is not None:
        s = result.summary
        log += [("in", f"Scan: {result.scenario_name} / corridor {result.corridor_id}"),
                ("in", f"Analysis CRS: {result.analysis_crs} · timestamp {result.timestamp}"),
                ("in", f"Parcels analyzed {s['parcels_analyzed']}/{s['parcels_total']} · buildings analyzed "
                       f"{s['buildings_analyzed']}/{s['buildings_total']}")]
        log += [("wa", n) for n in result.notes]
    st.markdown('<div class="nm-panel"><h4>◈ SYSTEM LOG</h4>' + theme.log_html(log or [("in", "No log entries.")])
                + '<div class="nm-note">Data quality = the share of records with no failed input check (missing ID/attribute, '
                  'repaired geometry, duplicate ID). It describes input data only - not accuracy, and not the certainty of any '
                  'planning or legal decision.</div></div>', unsafe_allow_html=True)


def need_scan(title):
    st.markdown(f'<div class="nm-panel"><h4>◈ {theme.esc(title)}</h4><p>No scan data yet for this mission and corridor. '
                'Press <b>▶ SCAN CITY</b> in the control panel (or in MISSION CONTROL).</p></div>', unsafe_allow_html=True)
    st.button("GO TO MISSION CONTROL", on_click=goto, args=("MISSION CONTROL",), key=f"btn_need_{title[:4]}")


def screen_conflicts():
    if result is None:
        return need_scan("CONFLICT DETECTOR")
    tab1, tab2, tab3 = st.tabs(["AFFECTED PARCELS", "AFFECTED BUILDINGS", "ALL FLAGGED RECORDS"])
    for tab, ftype in ((tab1, "Parcel"), (tab2, "Building")):
        with tab:
            sub = report[(report["feature_type"] == ftype) & (report["intersection_status"] == "Overlap")]
            if sub.empty:
                st.success(f"No {ftype.lower()}s overlap this corridor.")
            else:
                st.dataframe(style_table(sub[REPORT_VIEW_COLS]), width="stretch", hide_index=True)
    with tab3:
        if report.empty:
            st.success("No records were flagged for this corridor.")
        else:
            st.dataframe(style_table(report[REPORT_VIEW_COLS]), width="stretch", hide_index=True)
    with st.expander("FLAG EXPLANATIONS (one per flagged record)"):
        for r in report.itertuples():
            st.markdown(f"- `{r.feature_id}` — {r.reason_for_flagging}")
    with st.container(key="export_c"):
        st.download_button("↓ EXPORT MISSION REPORT", data=report_to_csv(report),
                           file_name=f"nagarmap_report_{corridor_id}.csv", mime="text/csv", key="dl_conf")


def screen_report():
    if result is None:
        return need_scan("MISSION REPORT")
    s = result.summary
    flagged = s["parcels_affected"] + s["buildings_affected"]
    st.markdown('<div class="nm-debrief"><div class="big">SCAN COMPLETE</div><div class="small">URBAN GRID ANALYSIS FINISHED · '
                f'{theme.esc(result.scenario_name.upper())} · {theme.esc(result.corridor_id)}</div></div>', unsafe_allow_html=True)
    st.markdown(theme.hud_html([
        {"label": "PARCELS ANALYZED", "value": s["parcels_analyzed"], "sub": f"of {s['parcels_total']} records", "tone": "cyan"},
        {"label": "BUILDINGS ANALYZED", "value": s["buildings_analyzed"], "sub": f"of {s['buildings_total']} records", "tone": "cyan"},
        {"label": "POTENTIAL CONFLICTS", "value": flagged, "sub": f"{s['parcels_affected']} parcels · {s['buildings_affected']} buildings",
         "tone": "red" if flagged else "green"},
        {"label": "DATA-QUALITY WARNINGS", "value": s["dq_warning_records"], "sub": "records with a failed input check",
         "tone": "amber" if s["dq_warning_records"] else "green"},
        {"label": "NEEDS HUMAN REVIEW", "value": len(report), "sub": "overlaps, contacts, warnings", "tone": "mag"},
    ]), unsafe_allow_html=True)
    st.markdown(build_summary_text(result))
    with st.container(key="export"):
        st.download_button("↓ EXPORT MISSION REPORT", data=report_to_csv(report),
                           file_name=f"nagarmap_report_{corridor_id}.csv", mime="text/csv", key="dl_report")
        st.download_button("↓ EXPORT READABLE REPORT (.md)", data=report_md(),
                           file_name=f"nagarmap_report_{corridor_id}.md", mime="text/markdown", key="dl_report_md")
    if not report.empty:
        st.dataframe(style_table(report[REPORT_VIEW_COLS]), width="stretch", hide_index=True)
    st.caption(disclaimer + " “SCAN COMPLETE” is a processing status only - not a measure of data accuracy or compliance.")


{"CITY SELECT": screen_city, "MISSION CONTROL": screen_mission, "MAP SCANNER": screen_map, "DATA LAB": screen_lab,
 "CONFLICT DETECTOR": screen_conflicts, "MISSION REPORT": screen_report}[screen]()
