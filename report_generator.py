"""CSV planning report and on-screen summary for NagarMap."""
from __future__ import annotations

import pandas as pd

from spatial_engine import (DQ_VALID, STATUS_NONE, STATUS_OVERLAP, STATUS_TOUCH, AnalysisResult)

REPORT_COLUMNS = [
    "scenario_name", "corridor_id", "feature_id", "feature_type", "intersection_status",
    "overlap_area_sqm", "source_layer", "data_quality_status", "data_quality_warnings",
    "reason_for_flagging", "analysis_timestamp",
]

DISCLAIMER_SYNTH = ("Preliminary spatial screening on synthetic sample data. A spatial intersection is a "
                    "potential conflict for human review - not a legal conclusion about ownership, "
                    "encroachment, compensation or planning approval.")
DISCLAIMER_REAL = ("Preliminary spatial screening on OpenStreetMap data (crowd-sourced; completeness and positional accuracy "
                   "vary) and a user-set corridor width. A spatial intersection is a potential conflict for human review - "
                   "not a legal conclusion about ownership, encroachment, compensation or planning approval. "
                   "OSM contains no legal parcel boundaries.")
DISCLAIMER = DISCLAIMER_SYNTH  # backwards-compatible name


def _combined(result: AnalysisResult) -> pd.DataFrame:
    frames = [pd.DataFrame(df.drop(columns="geometry")) for df in (result.parcels, result.buildings) if len(df)]
    if not frames:
        return pd.DataFrame(columns=[c for c in REPORT_COLUMNS])
    return pd.concat(frames, ignore_index=True)


PROV_COLUMNS = ["data_source", "data_kind", "data_licence", "data_retrieved_utc"]


def build_report_df(result: AnalysisResult, prov: dict | None = None) -> pd.DataFrame:
    """Rows needing attention: overlaps, boundary contacts, and any record with a data-quality flag.

    ``prov`` optionally maps "Parcel"/"Building" to a Provenance; its fields are added as columns so
    every exported row records where its data came from.
    """
    df = _combined(result)
    cols = REPORT_COLUMNS + (PROV_COLUMNS if prov else [])
    if df.empty:
        return pd.DataFrame(columns=cols)
    keep = (df["intersection_status"].isin([STATUS_OVERLAP, STATUS_TOUCH])
            | (df["dq_status"] != DQ_VALID))
    df = df[keep].copy()
    df = df.rename(columns={"dq_status": "data_quality_status", "dq_warnings": "data_quality_warnings",
                            "reason": "reason_for_flagging"})
    df["overlap_area_sqm"] = df["overlap_area_sqm"].astype(float).round(2)
    order = {STATUS_OVERLAP: 0, STATUS_TOUCH: 1, STATUS_NONE: 2}
    df["_o"] = df["intersection_status"].map(order).fillna(3)
    df["_t"] = df["feature_type"].map({"Parcel": 0, "Building": 1}).fillna(2)
    df = df.sort_values(["_o", "_t", "feature_id"]).drop(columns=["_o", "_t"])
    if prov:
        for col, attr in (("data_source", "source"), ("data_kind", "kind"), ("data_licence", "licence"),
                          ("data_retrieved_utc", "retrieved_utc")):
            df[col] = df["feature_type"].map(lambda t, a=attr: (getattr(prov.get(t), a, "") or "") if prov.get(t) else "")
    return df[cols].reset_index(drop=True)


def report_to_csv(df: pd.DataFrame) -> bytes:
    """UTF-8 (with BOM, so Excel on Windows opens it correctly)."""
    return df.to_csv(index=False).encode("utf-8-sig")


def build_markdown_report(result: AnalysisResult, report: pd.DataFrame, prov_rows: list[dict],
                          is_real: bool, corridor_info: str = "") -> bytes:
    """Human-readable report (Markdown) with KPIs, provenance and the flagged records."""
    s = result.summary
    L = [f"# NagarMap mission report - {result.scenario_name}", "",
         f"- Corridor: `{result.corridor_id}` ({result.corridor_name})", f"- Analysis CRS: {result.analysis_crs}",
         f"- Generated: {result.timestamp}", ""]
    if corridor_info:
        L += [f"- Corridor definition: {corridor_info}", ""]
    L += ["## KPIs", "",
          f"- Parcels analyzed: {s['parcels_analyzed']} of {s['parcels_total']} (affected: {s['parcels_affected']})",
          f"- Buildings analyzed: {s['buildings_analyzed']} of {s['buildings_total']} (affected: {s['buildings_affected']})",
          f"- Building overlap area: {s['building_overlap_area_sqm']:.1f} m²",
          f"- Parcel overlap area: {s['parcel_overlap_area_sqm']:.1f} m²",
          f"- Records with data-quality warnings: {s['dq_warning_records']}", ""]
    for n in result.notes:
        L.append(f"- Note: {n}")
    L += ["", "## Data sources", ""]
    for p in prov_rows:
        L.append(f"- **{p['layer']}** - {p['source']} · {p['kind']} · licence: {p['licence'] or 'n/a'} · "
                 f"retrieved: {p['retrieved'] or 'n/a'} · {p['attribution']}")
    L += ["", "## Flagged records", ""]
    if report.empty:
        L.append("None.")
    else:
        for r in report.itertuples():
            L.append(f"- `{r.feature_id}` ({r.feature_type}, {r.intersection_status}): {r.reason_for_flagging}")
    L += ["", "---", DISCLAIMER_REAL if is_real else DISCLAIMER_SYNTH, ""]
    return "\n".join(L).encode("utf-8")


def build_summary_text(result: AnalysisResult) -> str:
    s = result.summary
    lines = [
        f"**{result.scenario_name}** - corridor `{result.corridor_id}` ({result.corridor_name}).",
        f"- {s['parcels_affected']} of {s['parcels_analyzed']} parcels and "
        f"{s['buildings_affected']} of {s['buildings_analyzed']} buildings intersect the corridor "
        f"(approx. {s['parcel_overlap_area_sqm']:.0f} m² of parcel area).",
    ]
    touch = s["parcels_boundary_contact"] + s["buildings_boundary_contact"]
    if touch:
        lines.append(f"- {touch} feature(s) only touch the corridor boundary and are not counted as affected.")
    if s["dq_warning_records"]:
        lines.append(f"- {s['dq_warning_records']} record(s) carry data-quality warnings; "
                     "investigate these before relying on their results.")
    if s["parcels_affected"] == 0 and s["buildings_affected"] == 0:
        lines.append("- No potential conflicts were detected for this corridor.")
    for n in result.notes:
        lines.append(f"- Note: {n}")
    return "\n".join(lines)
