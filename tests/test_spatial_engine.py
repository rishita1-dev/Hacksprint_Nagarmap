"""Tests for the NagarMap spatial engine, harmonizer and report generator.

Geometry is built in metres (EPSG:32643) so expected overlaps are exact.
Run from the project root:  python -m pytest -q
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_harmonizer import harmonize_layer, load_demo_data  # noqa: E402
from report_generator import REPORT_COLUMNS, build_report_df, report_to_csv  # noqa: E402
from spatial_engine import (STATUS_NONE, STATUS_OVERLAP, STATUS_SKIPPED, STATUS_TOUCH,  # noqa: E402
                            AnalysisError, UnknownCRSError, run_analysis, to_analysis_crs)

CRS = "EPSG:32643"
X0, Y0 = 715000.0, 3165000.0
TS = "2026-01-01 00:00:00 UTC"


def sq(x0, y0, x1, y1):
    return box(X0 + x0, Y0 + y0, X0 + x1, Y0 + y1)


def layer(geoms, ids, crs=CRS, **extra):
    data = {"feature_id": ids, "dq_warnings": [""] * len(ids), **extra}
    return gpd.GeoDataFrame(data, geometry=geoms, crs=crs)


def corridor(geom=None, crs=CRS):
    geom = geom if geom is not None else sq(0, 0, 100, 10)
    return gpd.GeoDataFrame({"feature_id": ["C-T"]}, geometry=[geom], crs=crs)


def analyze(parcels, cor=None, buildings=None):
    return run_analysis(parcels, buildings, cor if cor is not None else corridor(),
                        scenario_name="Test", corridor_id="C-T", corridor_name="Test corridor",
                        timestamp=TS, analysis_crs=CRS)


def row(result, fid):
    return result.parcels[result.parcels["feature_id"] == fid].iloc[0]


# 1. intersecting parcel ---------------------------------------------------
def test_parcel_intersecting_corridor_is_flagged_with_area():
    parcels = layer([sq(10, 5, 30, 25)], ["P-1"])  # 20 x 5 m inside corridor = 100 m²
    r = row(analyze(parcels), "P-1")
    assert r["intersection_status"] == STATUS_OVERLAP
    assert r["affected"]
    assert r["overlap_area_sqm"] == pytest.approx(100.0, abs=1e-6)
    assert "intersects" in r["reason"]


# 2. non-intersecting parcel -----------------------------------------------
def test_parcel_far_from_corridor_is_not_flagged():
    r = row(analyze(layer([sq(10, 50, 30, 70)], ["P-2"])), "P-2")
    assert r["intersection_status"] == STATUS_NONE
    assert not r["affected"]
    assert r["overlap_area_sqm"] == 0


# 3. boundary-touch only ---------------------------------------------------
def test_feature_touching_only_boundary_is_not_counted_as_affected():
    parcels = layer([sq(10, 10, 30, 30)], ["P-3"])  # shares the edge y=10 with the corridor
    res = analyze(parcels)
    r = row(res, "P-3")
    assert r["intersection_status"] == STATUS_TOUCH
    assert not r["affected"]
    assert res.summary["parcels_affected"] == 0
    assert res.summary["parcels_boundary_contact"] == 1


# 4. invalid / empty geometry ----------------------------------------------
def test_invalid_geometry_is_repaired_and_warned():
    bowtie = Polygon([(X0 + 10, Y0), (X0 + 30, Y0 + 20), (X0 + 30, Y0), (X0 + 10, Y0 + 20)])
    assert not bowtie.is_valid
    r = row(analyze(layer([bowtie], ["P-4"])), "P-4")
    assert r["dq_status"] == "Warning"
    assert "invalid" in r["dq_warnings"].lower()
    assert "requires validation" in r["reason"]
    assert r["intersection_status"] == STATUS_OVERLAP  # repaired triangles still overlap


def test_empty_and_missing_geometry_do_not_crash():
    parcels = layer([Polygon(), None, sq(10, 5, 30, 25)], ["P-E1", "P-E2", "P-OK"])
    res = analyze(parcels)
    assert row(res, "P-E1")["intersection_status"] == STATUS_SKIPPED
    assert row(res, "P-E2")["intersection_status"] == STATUS_SKIPPED
    assert row(res, "P-E1")["dq_status"] == "Error"
    assert res.summary["parcels_analyzed"] == 1
    assert res.summary["parcels_affected"] == 1


def test_empty_dataset_does_not_crash():
    res = analyze(gpd.GeoDataFrame({"feature_id": []}, geometry=[], crs=CRS))
    assert res.summary["parcels_analyzed"] == 0
    assert any("empty" in n.lower() for n in res.notes)
    assert report_to_csv(build_report_df(res)).decode("utf-8-sig").startswith("scenario_name")


# 5. CRS mismatch / unknown CRS --------------------------------------------
def test_layers_in_different_known_crs_give_same_result():
    parcels = layer([sq(10, 5, 30, 25)], ["P-5"])
    expected = row(analyze(parcels), "P-5")["overlap_area_sqm"]
    parcels_wgs = parcels.to_crs("EPSG:4326")  # mismatching source CRS, known
    r = run_analysis(parcels_wgs, None, corridor(), scenario_name="T", corridor_id="C-T",
                     timestamp=TS, analysis_crs=CRS)
    assert row(r, "P-5")["overlap_area_sqm"] == pytest.approx(expected, rel=1e-4)


def test_unknown_crs_is_never_assumed():
    no_crs = layer([sq(10, 5, 30, 25)], ["P-6"], crs=None)
    with pytest.raises(UnknownCRSError):
        to_analysis_crs(no_crs, CRS)
    res = analyze(no_crs)  # run_analysis degrades gracefully instead of crashing
    assert res.summary["parcels_analyzed"] == 0
    assert any("no declared CRS" in n for n in res.notes)

    harm = harmonize_layer(gpd.GeoDataFrame({"PARCEL_ID": ["P-6"]}, geometry=[sq(0, 0, 1, 1)], crs=None),
                           "parcels", "parcels.geojson")
    assert not harm.usable and harm.needs_review
    assert any("NOT assumed" in i for i in harm.issues)


def test_unknown_corridor_crs_raises_analysis_error():
    with pytest.raises(AnalysisError):
        analyze(layer([sq(0, 0, 1, 1)], ["P"]), cor=corridor(crs=None))


def test_harmonizer_reprojects_known_crs_and_normalizes_fields():
    raw = gpd.GeoDataFrame({"PARCEL_ID": ["P-7"], "LAND_USE": ["Residential"], "AREA_SQM": ["2000"]},
                           geometry=[sq(0, 0, 50, 40)], crs=CRS)
    h = harmonize_layer(raw, "parcels", "parcels.geojson")
    assert h.usable and str(h.gdf.crs) == "EPSG:4326"
    assert {"feature_id", "land_use", "declared_area_sqm"} <= set(h.gdf.columns)
    assert h.gdf["declared_area_sqm"].iloc[0] == 2000
    assert any("reprojected" in c for c in h.changes)


def test_harmonizer_flags_missing_ids_and_removes_exact_duplicates():
    g = sq(0, 0, 50, 40)
    raw = gpd.GeoDataFrame({"PARCEL_ID": ["P-8", "P-8", None], "LAND_USE": ["Res", "Res", "Res"]},
                           geometry=[g, g, sq(60, 0, 110, 40)], crs="EPSG:4326")
    h = harmonize_layer(raw, "parcels", "parcels.geojson")
    assert h.duplicates_removed == 1
    assert len(h.gdf) == 2
    missing = h.gdf[h.gdf["feature_id"].str.contains("MISSING")].iloc[0]
    assert missing["dq_status"] == "Warning" and "no identifier" in missing["dq_warnings"]


# 6. zero-affected scenario -------------------------------------------------
def test_baseline_scenario_has_zero_affected_features():
    layers = load_demo_data(Path(__file__).resolve().parent.parent / "data")
    cor = layers["corridors"].gdf
    base = cor[cor["feature_id"] == "C-BASE-01"]
    res = run_analysis(layers["parcels"].gdf, layers["buildings"].gdf, base,
                       scenario_name="Baseline", corridor_id="C-BASE-01", timestamp=TS)
    s = res.summary
    assert s["parcels_affected"] == 0 and s["buildings_affected"] == 0
    assert s["parcels_analyzed"] == 25 and s["buildings_analyzed"] == 21


def test_bundled_scenarios_give_different_deterministic_results():
    layers = load_demo_data(Path(__file__).resolve().parent.parent / "data")
    cor = layers["corridors"].gdf
    counts = {}
    for cid in cor["feature_id"]:
        res = run_analysis(layers["parcels"].gdf, layers["buildings"].gdf, cor[cor["feature_id"] == cid],
                           scenario_name=cid, corridor_id=cid, timestamp=TS)
        again = run_analysis(layers["parcels"].gdf, layers["buildings"].gdf, cor[cor["feature_id"] == cid],
                             scenario_name=cid, corridor_id=cid, timestamp=TS)
        assert res.summary == again.summary  # deterministic
        counts[cid] = (res.summary["parcels_affected"], res.summary["buildings_affected"])
    assert counts["C-ROAD-01"] == (6, 2)
    assert counts["C-ROAD-02"] == (6, 0)
    assert counts["C-TRANSIT-01"] == (4, 1)
    assert counts["C-BASE-01"] == (0, 0)


# 7. CSV export -------------------------------------------------------------
def test_csv_export_contains_expected_fields_and_rows():
    parcels = layer([sq(10, 5, 30, 25), sq(10, 50, 30, 70), sq(40, 10, 60, 30)], ["P-A", "P-B", "P-C"])
    res = analyze(parcels, buildings=layer([sq(12, 6, 20, 14)], ["B-1"]))
    csv_bytes = report_to_csv(build_report_df(res))
    df = pd.read_csv(io.StringIO(csv_bytes.decode("utf-8-sig")))
    assert list(df.columns) == REPORT_COLUMNS
    assert set(df["feature_id"]) == {"P-A", "P-C", "B-1"}  # P-B has no intersection -> not in report
    assert df.loc[df["feature_id"] == "P-A", "intersection_status"].iloc[0] == STATUS_OVERLAP
    assert df.loc[df["feature_id"] == "P-C", "intersection_status"].iloc[0] == STATUS_TOUCH
    assert (df["analysis_timestamp"] == TS).all()
    assert (df["scenario_name"] == "Test").all()
