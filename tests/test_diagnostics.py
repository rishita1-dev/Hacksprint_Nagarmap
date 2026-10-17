"""Tests for the Data Lab diagnostics and HUD metrics (real-state derivation, no fabricated passes)."""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_harmonizer import harmonize_layer, load_demo_data  # noqa: E402
from diagnostics import (FAILED, NOT_RUN, PASS, REVIEW, WARNING, indicator_lights, layer_checks,  # noqa: E402
                         pipeline_stages, quality_stats, system_status)
from spatial_engine import run_analysis  # noqa: E402


def by_name(checks):
    return {c.name: c for c in checks}


def test_undeclared_crs_is_failed_never_pass():
    raw = gpd.GeoDataFrame({"PARCEL_ID": ["P-1"], "LAND_USE": ["Res"]}, geometry=[box(0, 0, 1, 1)], crs=None)
    c = by_name(layer_checks(harmonize_layer(raw, "parcels", "parcels.geojson")))
    assert c["CRS COMPATIBILITY"].status == FAILED
    assert "not assumed" in c["CRS COMPATIBILITY"].detail.lower()


def test_unreadable_layer_reports_not_run_not_pass():
    c = layer_checks(harmonize_layer(None, "parcels", "parcels.geojson"))
    assert c[0].name == "LOAD" and c[0].status == FAILED
    assert all(x.status == NOT_RUN for x in c[1:])


def test_demo_data_diagnostics_match_the_seeded_issues():
    layers = load_demo_data(ROOT / "data")
    p = by_name(layer_checks(layers["parcels"]))
    assert p["CRS COMPATIBILITY"].status == PASS
    assert p["IDENTIFIERS"].status == REVIEW          # P-105 has no ID
    assert p["GEOMETRY VALIDITY"].status == WARNING   # P-118 bow-tie repaired
    assert p["DUPLICATES"].status == WARNING          # P-122 duplicate removed
    assert p["EMPTY GEOMETRIES"].status == PASS
    r = by_name(layer_checks(layers["roads"]))
    assert r["CRS COMPATIBILITY"].status == PASS and "EPSG:32643" in r["CRS COMPATIBILITY"].detail
    b = by_name(layer_checks(layers["buildings"]))
    assert all(c.status == PASS for c in b.values())


def test_quality_stats_and_pipeline_progress_follow_real_state():
    layers = load_demo_data(ROOT / "data")
    assert quality_stats(layers) == (44, 46)  # 25 parcels (2 flagged) + 21 buildings
    assert [ok for _, ok in pipeline_stages(layers, None)] == [True, True, False, False]
    cor = layers["corridors"].gdf
    res = run_analysis(layers["parcels"].gdf, layers["buildings"].gdf, cor[cor["feature_id"] == "C-BASE-01"],
                       scenario_name="Baseline", corridor_id="C-BASE-01")
    assert [ok for _, ok in pipeline_stages(layers, res)] == [True, True, True, True]


def test_system_status_and_lights_reflect_state():
    layers = load_demo_data(ROOT / "data")
    assert system_status(layers, None) == ("SYSTEM READY · DATA REVIEW", "amber")  # seeded issues are real
    assert dict(indicator_lights(layers, None))["Scan"] == "muted"
    cor = layers["corridors"].gdf
    res = run_analysis(layers["parcels"].gdf, layers["buildings"].gdf, cor[cor["feature_id"] == "C-ROAD-01"],
                       scenario_name="Road", corridor_id="C-ROAD-01")
    assert system_status(layers, res)[0].startswith("SCAN COMPLETE")
    assert dict(indicator_lights(layers, res))["Scan"] == "green"
    layers["corridors"].gdf = None
    assert system_status(layers, None)[1] == "red"
