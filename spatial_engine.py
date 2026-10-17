"""Geospatial calculations for NagarMap: geometry repair, CRS handling and corridor analysis.

All area maths is done in a projected (metric) CRS. A spatial intersection is a
potential conflict for human review - it is NOT a legal conclusion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import geopandas as gpd
import pandas as pd
from shapely.geometry import GeometryCollection
from shapely.ops import unary_union
from shapely import make_valid

STATUS_OVERLAP = "Overlap"
STATUS_TOUCH = "Boundary contact only"
STATUS_NONE = "No intersection"
STATUS_SKIPPED = "Not analyzed"

DQ_VALID = "Valid"
DQ_WARNING = "Warning"
DQ_ERROR = "Error"

# Overlaps smaller than this (m^2) are treated as numerical noise, not an overlap.
MIN_OVERLAP_SQM = 0.05
# Features closer than this (m) to the corridor are treated as touching its boundary.
TOUCH_TOLERANCE_M = 0.05

RESULT_COLUMNS = [
    "scenario_name", "corridor_id", "feature_id", "feature_type", "intersection_status",
    "affected", "overlap_area_sqm", "overlap_pct_of_feature", "feature_area_sqm",
    "source_layer", "dq_status", "dq_warnings", "reason", "analysis_timestamp", "geometry",
]


class UnknownCRSError(ValueError):
    """Raised when a layer has no declared CRS (we never guess)."""


class AnalysisError(ValueError):
    """Raised when an analysis cannot be run at all (e.g. unusable corridor)."""


def _polygonal(geom):
    """Return only the polygonal part of a geometry (or None)."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    if isinstance(geom, GeometryCollection) or hasattr(geom, "geoms"):
        parts = [p for p in (_polygonal(g) for g in geom.geoms) if p is not None]
        return unary_union(parts) if parts else None
    return None


def repair_geometry(geom):
    """Return ``(geometry_or_None, note_or_None)``.

    Missing/empty -> (None, note). Invalid polygonal geometry is repaired with
    ``make_valid`` and a note is returned so the repair is never silent.
    """
    if geom is None or not hasattr(geom, "is_empty") or geom.is_empty:
        return None, "Geometry is missing or empty and cannot be analyzed; requires validation."
    if geom.is_valid:
        return geom, None
    fixed = make_valid(geom)
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        fixed = _polygonal(fixed)
    if fixed is None or fixed.is_empty:
        return None, "Geometry is invalid and could not be repaired; requires validation."
    return fixed, ("Geometry was invalid (e.g. self-intersection) and was auto-repaired with "
                   "make_valid; requires validation before the result can be trusted.")


def to_analysis_crs(gdf: gpd.GeoDataFrame, crs) -> gpd.GeoDataFrame:
    """Reproject to ``crs``. Raises UnknownCRSError if the source CRS is undeclared."""
    if gdf.crs is None:
        raise UnknownCRSError("Layer has no declared CRS; refusing to assume one.")
    if gdf.crs == crs:
        return gdf
    return gdf.to_crs(crs)


def choose_analysis_crs(reference: gpd.GeoDataFrame):
    """Pick a deterministic projected (UTM) CRS suited to the reference layer's location."""
    if reference.crs is None:
        raise UnknownCRSError("Reference layer has no declared CRS.")
    return reference.estimate_utm_crs()


def _split_warnings(value) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    return [w.strip() for w in str(value).split(" | ") if w.strip()]


def empty_result() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({c: [] for c in RESULT_COLUMNS}, geometry="geometry")


def analyze_layer(layer: gpd.GeoDataFrame | None, corridor_geom, *, corridor_name: str,
                  corridor_id: str, scenario_name: str, feature_type: str, source_layer: str,
                  analysis_crs, timestamp: str | None = None,
                  min_overlap_sqm: float = MIN_OVERLAP_SQM,
                  touch_tolerance_m: float = TOUCH_TOLERANCE_M) -> gpd.GeoDataFrame:
    """Intersect every feature of ``layer`` with ``corridor_geom`` (already in analysis_crs).

    Returns a GeoDataFrame in ``analysis_crs`` with one row per input feature.
    Missing or empty geometries do not raise; they are reported as "Not analyzed".
    """
    if layer is None or len(layer) == 0:
        out = empty_result()
        out.set_crs(analysis_crs, inplace=True)
        return out
    timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    layer = to_analysis_crs(layer, analysis_crs)

    rows = []
    for pos, (_, row) in enumerate(layer.iterrows()):
        fid = row.get("feature_id")
        warnings = _split_warnings(row.get("dq_warnings"))
        if fid is None or (isinstance(fid, float) and pd.isna(fid)) or str(fid).strip() == "":
            fid = f"UNKNOWN-{pos + 1:03d}"
            warnings.append("Record has no identifier; record matching may be incomplete.")
        fid = str(fid)

        geom, note = repair_geometry(row.geometry)
        if note:
            warnings.append(note)

        overlap, pct, area = None, None, None
        if geom is None:
            status = STATUS_SKIPPED
            reason = f"{feature_type} {fid} has a missing or empty geometry and could not be analyzed."
        else:
            area = float(geom.area)
            inter = geom.intersection(corridor_geom)
            inter_area = float(inter.area)
            if inter_area >= min_overlap_sqm:
                status = STATUS_OVERLAP
                overlap = inter_area
                pct = (inter_area / area * 100.0) if area > 0 else None
                reason = (f"{feature_type} {fid} intersects the selected corridor "
                          f"({corridor_name}); approx. {inter_area:.1f} m² overlap"
                          + (f", {pct:.1f}% of the feature." if pct is not None else "."))
            elif geom.distance(corridor_geom) <= touch_tolerance_m:
                status = STATUS_TOUCH
                overlap = 0.0
                pct = 0.0
                reason = (f"{feature_type} {fid} only touches the corridor boundary (no meaningful "
                          "overlap); not counted as affected.")
            else:
                status = STATUS_NONE
                overlap = 0.0
                pct = 0.0
                reason = f"{feature_type} {fid} does not intersect the selected corridor."

        if warnings:
            reason += " Review required: " + " ".join(warnings)
        dq = DQ_ERROR if geom is None else (DQ_WARNING if warnings else DQ_VALID)
        rows.append({
            "scenario_name": scenario_name,
            "corridor_id": corridor_id,
            "feature_id": fid,
            "feature_type": feature_type,
            "intersection_status": status,
            "affected": status == STATUS_OVERLAP,
            "overlap_area_sqm": overlap,
            "overlap_pct_of_feature": pct,
            "feature_area_sqm": area,
            "source_layer": source_layer,
            "dq_status": dq,
            "dq_warnings": " | ".join(warnings),
            "reason": reason,
            "analysis_timestamp": timestamp,
            "geometry": geom,
        })
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=analysis_crs)
    return out[RESULT_COLUMNS]


@dataclass
class AnalysisResult:
    scenario_name: str
    corridor_id: str
    corridor_name: str
    analysis_crs: str
    parcels: gpd.GeoDataFrame
    buildings: gpd.GeoDataFrame
    corridor_geometry_wgs84: object
    notes: list[str] = field(default_factory=list)
    timestamp: str = ""

    @property
    def summary(self) -> dict:
        return summarize(self.parcels, self.buildings)


def summarize(parcels: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame) -> dict:
    def count(df, col, value):
        return int((df[col] == value).sum()) if len(df) else 0

    p_area = float(parcels.loc[parcels["affected"] == True, "overlap_area_sqm"].sum()) if len(parcels) else 0.0  # noqa: E712
    b_area = float(buildings.loc[buildings["affected"] == True, "overlap_area_sqm"].sum()) if len(buildings) else 0.0  # noqa: E712
    warn = 0
    for df in (parcels, buildings):
        if len(df):
            warn += int((df["dq_status"] != DQ_VALID).sum())
    return {
        "parcels_analyzed": int((parcels["intersection_status"] != STATUS_SKIPPED).sum()) if len(parcels) else 0,
        "buildings_analyzed": int((buildings["intersection_status"] != STATUS_SKIPPED).sum()) if len(buildings) else 0,
        "parcels_total": int(len(parcels)),
        "buildings_total": int(len(buildings)),
        "parcels_affected": count(parcels, "intersection_status", STATUS_OVERLAP),
        "buildings_affected": count(buildings, "intersection_status", STATUS_OVERLAP),
        "parcels_boundary_contact": count(parcels, "intersection_status", STATUS_TOUCH),
        "buildings_boundary_contact": count(buildings, "intersection_status", STATUS_TOUCH),
        "dq_warning_records": warn,
        "parcel_overlap_area_sqm": p_area,
        "building_overlap_area_sqm": b_area,
    }


def run_analysis(parcels: gpd.GeoDataFrame | None, buildings: gpd.GeoDataFrame | None,
                 corridor: gpd.GeoDataFrame, *, scenario_name: str, corridor_id: str,
                 corridor_name: str | None = None, timestamp: str | None = None,
                 analysis_crs=None, source_names: dict | None = None,
                 unavailable: dict | None = None) -> AnalysisResult:
    """Run the full corridor analysis. ``corridor`` is a single-row GeoDataFrame.

    Layer-level problems (unknown CRS, empty layer) are recorded in ``notes`` and the
    layer is skipped; only an unusable corridor raises AnalysisError.
    """
    timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    corridor_name = corridor_name or corridor_id
    if corridor is None or len(corridor) == 0:
        raise AnalysisError("No corridor selected.")
    if corridor.crs is None:
        raise AnalysisError("Corridor layer has no declared CRS; analysis cannot run safely.")

    crs = analysis_crs or choose_analysis_crs(corridor)
    corr = to_analysis_crs(corridor, crs)
    corr_geom, note = repair_geometry(corr.geometry.iloc[0])
    if corr_geom is None:
        raise AnalysisError(f"Corridor {corridor_id} has a missing or empty geometry.")

    notes: list[str] = []
    if note:
        notes.append(f"Corridor {corridor_id}: {note}")

    source_names = source_names or {}
    unavailable = unavailable or {}
    results = {}
    for key, layer, ftype, src in (("parcels", parcels, "Parcel", source_names.get("parcels", "parcels.geojson")),
                                   ("buildings", buildings, "Building", source_names.get("buildings", "buildings.geojson"))):
        if layer is None or len(layer) == 0:
            notes.append(unavailable.get(key) or f"{ftype} layer is empty or unavailable; skipped.")
            results[key] = empty_result()
            continue
        try:
            results[key] = analyze_layer(
                layer, corr_geom, corridor_name=corridor_name, corridor_id=corridor_id,
                scenario_name=scenario_name, feature_type=ftype, source_layer=src,
                analysis_crs=crs, timestamp=timestamp)
        except UnknownCRSError as exc:
            notes.append(f"{ftype} layer skipped: {exc}")
            results[key] = empty_result()

    wgs = gpd.GeoSeries([corr_geom], crs=crs).to_crs("EPSG:4326").iloc[0]
    return AnalysisResult(
        scenario_name=scenario_name, corridor_id=corridor_id, corridor_name=corridor_name,
        analysis_crs=str(crs), parcels=results["parcels"], buildings=results["buildings"],
        corridor_geometry_wgs84=wgs, notes=notes, timestamp=timestamp)
