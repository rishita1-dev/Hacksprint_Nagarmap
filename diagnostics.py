"""Data Lab diagnostics and HUD metrics for the NagarMap arcade UI.

Everything here is derived from what the harmonizer and analysis actually did.
A check is only given a status if it ran; an undeclared CRS is a FAILED check,
never a PASS.
"""
from __future__ import annotations

from dataclasses import dataclass

from data_harmonizer import TARGET_CRS, HarmonizationResult
from spatial_engine import AnalysisResult

PASS = "PASS"
REVIEW = "REVIEW REQUIRED"
WARNING = "WARNING"
FAILED = "FAILED"
NOT_RUN = "NOT RUN"
UNAVAILABLE = "UNAVAILABLE"

SEVERITY = {PASS: 0, NOT_RUN: 1, UNAVAILABLE: 1, WARNING: 2, REVIEW: 3, FAILED: 4}


@dataclass
class Check:
    layer: str
    name: str
    status: str
    detail: str


def layer_checks(res: HarmonizationResult) -> list[Check]:
    """Return the diagnostic checks for one harmonized layer."""
    L = res.source_layer
    if res.unavailable:
        checks = [Check(L, "LOAD", UNAVAILABLE, res.unavailable_reason)]
        for name in ("CRS COMPATIBILITY", "SCHEMA NORMALIZATION", "IDENTIFIERS", "GEOMETRY VALIDITY",
                     "EMPTY GEOMETRIES", "DUPLICATES"):
            checks.append(Check(L, name, NOT_RUN, "No data exists for this layer, so this check did not run."))
        return checks
    if not res.checks_run:
        reason = res.issues[0] if res.issues else "Layer is empty or unreadable."
        checks = [Check(L, "LOAD", FAILED, reason)]
        for name in ("CRS COMPATIBILITY", "SCHEMA NORMALIZATION", "IDENTIFIERS", "GEOMETRY VALIDITY",
                     "EMPTY GEOMETRIES", "DUPLICATES"):
            checks.append(Check(L, name, NOT_RUN, "Layer could not be loaded, so this check did not run."))
        return checks

    checks = [Check(L, "LOAD", PASS, f"{res.n_input} feature(s) read.")]

    if res.source_crs == "undeclared":
        checks.append(Check(L, "CRS COMPATIBILITY", FAILED,
                            "No CRS declared. It was not assumed; layer excluded until confirmed."))
    elif res.reprojected:
        checks.append(Check(L, "CRS COMPATIBILITY", PASS,
                            f"Known CRS {res.source_crs} reprojected to {TARGET_CRS}."))
    else:
        checks.append(Check(L, "CRS COMPATIBILITY", PASS, f"Already {TARGET_CRS}."))

    if res.missing_fields:
        checks.append(Check(L, "SCHEMA NORMALIZATION", REVIEW,
                            "Expected field(s) not found: " + ", ".join(res.missing_fields) + "."))
    else:
        checks.append(Check(L, "SCHEMA NORMALIZATION", PASS,
                            f"{res.renamed_count} field name(s) mapped to the common schema."))

    if res.n_missing_ids:
        checks.append(Check(L, "IDENTIFIERS", REVIEW,
                            f"{res.n_missing_ids} record(s) had no identifier; temporary ID(s) assigned."))
    else:
        checks.append(Check(L, "IDENTIFIERS", PASS, "All records carry an identifier."))

    if res.n_invalid_geoms:
        checks.append(Check(L, "GEOMETRY VALIDITY", WARNING,
                            f"{res.n_invalid_geoms} invalid geometr{'y' if res.n_invalid_geoms == 1 else 'ies'} "
                            "auto-repaired (make_valid); verify against source."))
    else:
        checks.append(Check(L, "GEOMETRY VALIDITY", PASS, "No invalid geometries found."))

    if res.n_empty_geoms:
        checks.append(Check(L, "EMPTY GEOMETRIES", FAILED,
                            f"{res.n_empty_geoms} missing/empty geometr{'y' if res.n_empty_geoms == 1 else 'ies'}; "
                            "those records cannot be analyzed."))
    else:
        checks.append(Check(L, "EMPTY GEOMETRIES", PASS, "No empty geometries found."))

    if res.n_id_conflicts:
        checks.append(Check(L, "DUPLICATES", REVIEW,
                            f"{res.n_id_conflicts} identifier(s) shared by records with different geometry."))
    elif res.duplicates_removed:
        checks.append(Check(L, "DUPLICATES", WARNING,
                            f"{res.duplicates_removed} exact duplicate record(s) removed (same ID + geometry)."))
    else:
        checks.append(Check(L, "DUPLICATES", PASS, "No duplicates found."))
    return checks


def all_checks(layers: dict[str, HarmonizationResult]) -> dict[str, list[Check]]:
    return {name: layer_checks(res) for name, res in layers.items()}


def worst_status(checks: list[Check]) -> str:
    return max((c.status for c in checks), key=lambda s: SEVERITY[s], default=NOT_RUN)


def quality_stats(layers: dict[str, HarmonizationResult]) -> tuple[int, int]:
    """(records with no data-quality flag, total records) across parcels and buildings.

    This is a share of records that passed the explicit input checks - it is not an
    accuracy measure and says nothing about any planning or legal decision.
    """
    valid = total = 0
    for key in ("parcels", "buildings"):
        gdf = layers[key].gdf if key in layers else None
        if gdf is None or len(gdf) == 0:
            continue
        total += len(gdf)
        valid += int((gdf["dq_status"] == "Valid").sum())
    return valid, total


def pipeline_stages(layers: dict[str, HarmonizationResult], result: AnalysisResult | None) -> list[tuple[str, bool]]:
    """Real pipeline stages and whether each has actually completed."""
    loaded = all(layers[k].gdf is not None or layers[k].unavailable for k in ("parcels", "buildings", "corridors"))
    harmonized = all(layers[k].checks_run or layers[k].unavailable for k in layers)
    return [
        ("DATA LOADED", loaded),
        ("HARMONIZED", harmonized),
        ("CORRIDOR ANALYZED", result is not None),
        ("REPORT READY", result is not None),
    ]


def system_status(layers: dict[str, HarmonizationResult], result: AnalysisResult | None) -> tuple[str, str]:
    """Return (label, tone) for the header status display, from real state."""
    if layers["corridors"].gdf is None or not layers["corridors"].usable:
        return "OFFLINE · NO CORRIDOR DATA", "red"
    if any(not layers[k].usable and not layers[k].unavailable for k in ("parcels", "buildings")):
        return "DEGRADED · LAYER UNUSABLE", "red"
    worst = max((worst_status(c) for c in all_checks(layers).values()), key=lambda s: SEVERITY[s])
    if result is not None:
        return ("SCAN COMPLETE · REVIEW DATA" if worst != PASS else "SCAN COMPLETE"), ("amber" if worst != PASS else "green")
    if worst == PASS:
        return "SYSTEM READY", "green"
    return "SYSTEM READY · DATA REVIEW", "amber"


def indicator_lights(layers: dict[str, HarmonizationResult], result: AnalysisResult | None) -> list[tuple[str, str]]:
    """Three real-state lights: (label, tone) for DATA, ENGINE, SCAN."""
    if layers["corridors"].gdf is None or any(not layers[k].usable and not layers[k].unavailable
                                               for k in ("parcels", "buildings")):
        data = "red"
    else:
        worst = max((worst_status(c) for c in all_checks(layers).values()), key=lambda s: SEVERITY[s])
        data = "green" if worst == PASS else "amber"
    if result is None:
        engine, scan = "muted", "muted"
    else:
        engine = "amber" if result.notes else "green"
        scan = "green"
    return [("Data layers", data), ("Analysis engine", engine), ("Scan", scan)]
