"""Schema, CRS and data-quality harmonization for NagarMap.

Every change is logged, and anything that still needs human review is reported
as an issue. An undeclared CRS is never silently assumed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import CRS

from spatial_engine import repair_geometry

TARGET_CRS = "EPSG:4326"

LAYER_SPECS = {
    "parcels": {
        "feature_type": "Parcel", "prefix": "P",
        "aliases": {
            "feature_id": ["feature_id", "parcel_id", "parcelid", "plot_id", "plot_no", "id"],
            "land_use": ["land_use", "landuse", "use", "usage"],
            "zone": ["zone", "zoning", "zone_code"],
            "declared_area_sqm": ["declared_area_sqm", "area_sqm", "area", "shape_area"],
            "last_updated": ["last_updated", "last_upd", "updated", "update_date", "modified"],
        },
        "required": ["land_use"],
    },
    "buildings": {
        "feature_type": "Building", "prefix": "B",
        "aliases": {
            "feature_id": ["feature_id", "bldg_id", "building_id", "buildingid", "id"],
            "use_type": ["use_type", "usage", "building_use", "use", "building"],
            "floors": ["floors", "storeys", "stories", "num_floors", "building_levels", "levels"],
            "last_updated": ["last_updated", "last_upd", "updated", "update_date", "modified"],
        },
        "required": ["use_type"],
    },
    "roads": {
        "feature_type": "Road", "prefix": "R",
        "aliases": {
            "feature_id": ["feature_id", "road_id", "roadid", "id"],
            "road_name": ["road_name", "name", "street"],
            "road_class": ["road_class", "class", "category", "highway"],
        },
        "required": [],
    },
    "corridors": {
        "feature_type": "Corridor", "prefix": "C",
        "aliases": {
            "feature_id": ["feature_id", "corridor_id", "id"],
            "corridor_name": ["corridor_name", "name"],
            "scenario_type": ["scenario_type", "type", "corridor_type"],
            "status": ["status"],
            "description": ["description", "notes"],
        },
        "required": ["scenario_type"],
    },
}


@dataclass
class HarmonizationResult:
    kind: str
    source_layer: str
    gdf: gpd.GeoDataFrame | None
    usable: bool = True
    source_crs: str = "unknown"
    changes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    duplicates_removed: int = 0
    # Counters recorded by the checks themselves (used by the Data Lab diagnostics view).
    checks_run: bool = False
    n_input: int = 0
    reprojected: bool = False
    renamed_count: int = 0
    missing_fields: list[str] = field(default_factory=list)
    n_missing_ids: int = 0
    n_invalid_geoms: int = 0
    n_empty_geoms: int = 0
    n_id_conflicts: int = 0
    # Set when a layer genuinely does not exist (e.g. no open parcel data for Delhi) - not an error.
    unavailable_reason: str | None = None

    @property
    def unavailable(self) -> bool:
        return self.unavailable_reason is not None

    @property
    def needs_review(self) -> bool:
        return bool(self.issues) or not self.usable


def unavailable_layer(kind: str, source_layer: str, reason: str) -> HarmonizationResult:
    """A layer that does not exist from any open source. Never filled with invented records."""
    return HarmonizationResult(kind=kind, source_layer=source_layer, gdf=None, usable=False,
                               source_crs="n/a", unavailable_reason=reason)


def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(name).strip().lower()).strip("_")


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""


def _same_crs(a, b) -> bool:
    return CRS.from_user_input(a) == CRS.from_user_input(b)


def load_geojson(path: str | Path):
    """Read a GeoJSON file. Returns ``(gdf_or_None, error_message_or_None)`` - never raises."""
    path = Path(path)
    if not path.exists():
        return None, f"File not found: {path.name}"
    try:
        return gpd.read_file(path), None
    except Exception as exc:  # noqa: BLE001 - surface any reader error to the UI
        return None, f"Could not read {path.name}: {exc}"


def harmonize_layer(gdf: gpd.GeoDataFrame | None, kind: str, source_layer: str,
                    target_crs: str = TARGET_CRS) -> HarmonizationResult:
    spec = LAYER_SPECS[kind]
    res = HarmonizationResult(kind=kind, source_layer=source_layer, gdf=None)

    if gdf is None or len(gdf) == 0:
        res.usable = False
        res.issues.append(f"{source_layer}: layer is empty or could not be read; excluded from analysis.")
        return res

    gdf = gdf.copy()
    res.checks_run = True
    res.n_input = len(gdf)

    # 1. CRS - never assume.
    if gdf.crs is None:
        res.usable = False
        res.source_crs = "undeclared"
        res.issues.append(f"{source_layer}: no CRS declared. The CRS was NOT assumed; the layer is "
                          "excluded until the correct CRS is confirmed.")
    else:
        res.source_crs = gdf.crs.to_string()
        if _same_crs(gdf.crs, target_crs):
            res.changes.append(f"{source_layer}: CRS already {target_crs}; no reprojection needed.")
        else:
            gdf = gdf.to_crs(target_crs)
            res.reprojected = True
            res.changes.append(f"{source_layer}: reprojected from {res.source_crs} to {target_crs}.")

    # 2. Field names -> common schema.
    lookup = {}
    for canonical, aliases in spec["aliases"].items():
        for alias in aliases:
            lookup.setdefault(alias, canonical)
    rename, used = {}, set()
    for col in gdf.columns:
        if col == gdf.geometry.name:
            continue
        canonical = lookup.get(_norm(col))
        if canonical and canonical not in used:
            used.add(canonical)
            if col != canonical:
                rename[col] = canonical
    keep = [c for c in gdf.columns if c in rename or c in used or c == gdf.geometry.name]
    dropped = [c for c in gdf.columns if c not in keep]
    res.renamed_count = len(rename)
    gdf = gdf[keep].rename(columns=rename)
    if rename:
        res.changes.append(f"{source_layer}: renamed fields " +
                           ", ".join(f"{a}→{b}" for a, b in rename.items()) + ".")
    if dropped:
        res.changes.append(f"{source_layer}: dropped non-schema fields: {', '.join(map(str, dropped))}.")
    for canonical in spec["aliases"]:
        if canonical not in gdf.columns:
            gdf[canonical] = None
            res.missing_fields.append(canonical)
            res.issues.append(f"{source_layer}: expected field '{canonical}' not found in source.")

    # 3. Types.
    gdf["feature_id"] = gdf["feature_id"].map(lambda v: None if _blank(v) else str(v).strip())
    for col in ("floors", "declared_area_sqm"):
        if col in gdf.columns:
            gdf[col] = pd.to_numeric(gdf[col], errors="coerce")
    if "last_updated" in gdf.columns:
        parsed = pd.to_datetime(gdf["last_updated"], errors="coerce")
        gdf["last_updated"] = parsed.dt.strftime("%Y-%m-%d")
    res.changes.append(f"{source_layer}: normalized field types (IDs as text, numeric fields, ISO dates).")

    gdf["source_layer"] = source_layer
    gdf["feature_type"] = spec["feature_type"]
    warnings = [[] for _ in range(len(gdf))]

    # 4. Missing IDs.
    gdf = gdf.reset_index(drop=True)
    n_missing = 0
    for i in range(len(gdf)):
        if _blank(gdf.at[i, "feature_id"]):
            n_missing += 1
            temp = f"{spec['prefix']}-MISSING-{n_missing:03d}"
            gdf.at[i, "feature_id"] = temp
            warnings[i].append(f"Source record has no identifier; temporary ID {temp} assigned, "
                               "record matching may be incomplete.")
    res.n_missing_ids = n_missing
    if n_missing:
        res.issues.append(f"{source_layer}: {n_missing} record(s) had no identifier; temporary IDs assigned.")

    # 5. Geometry validity.
    n_invalid = n_empty = 0
    geoms = list(gdf.geometry)
    for i, g in enumerate(geoms):
        fixed, note = repair_geometry(g)
        if note:
            if fixed is None:
                n_empty += 1
            else:
                n_invalid += 1
            warnings[i].append(note)
        geoms[i] = fixed
    gdf = gdf.set_geometry(gpd.GeoSeries(geoms, index=gdf.index, crs=gdf.crs))
    res.n_invalid_geoms = n_invalid
    res.n_empty_geoms = n_empty
    if n_invalid:
        res.issues.append(f"{source_layer}: {n_invalid} invalid geometr{'y' if n_invalid == 1 else 'ies'} "
                          "auto-repaired (make_valid); verify against the source.")
        res.changes.append(f"{source_layer}: repaired {n_invalid} invalid geometr"
                           f"{'y' if n_invalid == 1 else 'ies'}.")
    if n_empty:
        res.issues.append(f"{source_layer}: {n_empty} missing/empty geometr"
                          f"{'y' if n_empty == 1 else 'ies'}; these records cannot be analyzed.")

    # 6. Missing important attributes.
    for col in spec["required"]:
        for i in range(len(gdf)):
            if _blank(gdf.at[i, col]):
                warnings[i].append(f"Missing important attribute '{col}'.")

    # 7. Duplicates: same identifier AND identical (normalized) geometry.
    def geom_key(g):
        return None if g is None else g.normalize().wkb_hex

    keys = [(gdf.at[i, "feature_id"], geom_key(gdf.geometry.iloc[i])) for i in range(len(gdf))]
    seen, drop = set(), []
    for i, k in enumerate(keys):
        if k[1] is not None and k in seen:
            drop.append(i)
        else:
            seen.add(k)
    if drop:
        ids = sorted({gdf.at[i, "feature_id"] for i in drop})
        res.duplicates_removed = len(drop)
        res.changes.append(f"{source_layer}: removed {len(drop)} exact duplicate record(s) "
                           f"(same ID + identical geometry): {', '.join(ids)}.")
        keep_idx = [i for i in range(len(gdf)) if i not in set(drop)]
        gdf = gdf.iloc[keep_idx].reset_index(drop=True)
        warnings = [warnings[i] for i in keep_idx]
    # Same ID but different geometry -> flag for review (not auto-merged).
    counts = gdf["feature_id"].value_counts()
    conflicting = counts[counts > 1].index.tolist()
    res.n_id_conflicts = len(conflicting)
    for fid in conflicting:
        res.issues.append(f"{source_layer}: identifier {fid} appears on records with different geometry; "
                          "manual review needed.")
        for i in gdf.index[gdf["feature_id"] == fid]:
            warnings[i].append(f"Identifier {fid} is shared by records with different geometry.")

    gdf["dq_warnings"] = [" | ".join(w) for w in warnings]
    gdf["dq_status"] = ["Error" if g is None else ("Warning" if w else "Valid")
                        for g, w in zip(gdf.geometry, warnings)]
    res.gdf = gdf
    return res


def load_demo_data(data_dir: str | Path) -> dict[str, HarmonizationResult]:
    """Load and harmonize all four bundled layers. Never raises."""
    data_dir = Path(data_dir)
    out = {}
    for kind in ("parcels", "buildings", "roads", "corridors"):
        gdf, err = load_geojson(data_dir / f"{kind}.geojson")
        res = harmonize_layer(gdf, kind, f"{kind}.geojson")
        if err:
            res.issues.insert(0, err)
        out[kind] = res
    return out


def collect_log(layers: dict[str, HarmonizationResult]) -> tuple[list[str], list[str]]:
    """Return ``(changes, issues)`` across all layers."""
    changes, issues = [], []
    for res in layers.values():
        changes.extend(res.changes)
        issues.extend(res.issues)
    return changes, issues
