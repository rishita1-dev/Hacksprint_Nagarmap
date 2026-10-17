"""Tests for the OSM pipeline using a hand-built fixture (no network)."""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import osm_source as osm  # noqa: E402
from osm_fixture import LAT0, LON0, overpass_response  # noqa: E402
from report_generator import build_markdown_report, build_report_df, report_to_csv  # noqa: E402
from spatial_engine import run_analysis  # noqa: E402


def ok_fetcher(bbox):
    return overpass_response(), "https://example.test/api", "QUERY"


def failing_fetcher(bbox):
    raise osm.OverpassError("network down")


def pack(tmp_path, **kw):
    kw.setdefault("fetcher", ok_fetcher)
    return osm.load_osm_pack(LAT0, LON0, 600, cache_dir=tmp_path, area_label="Test", **kw)


def test_parse_overpass_counts_and_skips():
    parsed = osm.parse_overpass(overpass_response())
    assert len(parsed["buildings"]) == 5          # 4 ways + 1 relation; unclosed way skipped
    assert parsed["skipped"] == 1
    assert len(parsed["roads"]) == 2 and len(parsed["transit"]) == 1
    rel = parsed["buildings"][parsed["buildings"]["feature_id"] == "relation/14"].geometry.iloc[0]
    assert len(rel.interiors) == 1                # hole preserved


def test_live_fetch_writes_cache_then_cache_is_used_offline(tmp_path):
    p1 = pack(tmp_path)
    assert p1.prov["buildings"].kind == "LIVE API"
    assert osm.read_cache(osm.area_key(LAT0, LON0, 600), tmp_path) is not None
    p2 = pack(tmp_path, fetcher=failing_fetcher)            # offline: must not call the network
    assert p2.prov["buildings"].kind == "CACHED DOWNLOAD"
    assert p2.prov["buildings"].retrieved_utc == p1.prov["buildings"].retrieved_utc
    assert len(p2.layers["buildings"].gdf) == len(p1.layers["buildings"].gdf) == 5


def test_refresh_failure_falls_back_to_cache_and_says_so(tmp_path):
    pack(tmp_path)
    p = pack(tmp_path, refresh=True, fetcher=failing_fetcher)
    assert p.prov["buildings"].kind == "CACHED DOWNLOAD"
    assert any(l == "er" and "Live fetch failed" in m for l, m in p.messages)


def test_no_network_no_cache_gives_unavailable_not_fake_data(tmp_path):
    p = pack(tmp_path, fetcher=failing_fetcher)
    assert p.layers["buildings"].gdf is None and p.prov["buildings"].kind == "UNAVAILABLE"


def test_parcels_always_unavailable_for_osm_and_never_invented(tmp_path):
    p = pack(tmp_path)
    assert p.layers["parcels"].unavailable and p.layers["parcels"].gdf is None
    assert p.prov["parcels"].kind == "UNAVAILABLE"


def test_freshness_flags_stale_cache():
    prov = osm.Provenance("b", "s", "CACHED DOWNLOAD", retrieved_utc="2020-01-01 00:00:00 UTC")
    assert "STALE" in prov.freshness()
    assert osm.Provenance("b", "s", "UNAVAILABLE").freshness() == "n/a"


def test_corridors_from_real_lines_only_named_and_clipped(tmp_path):
    p = pack(tmp_path)
    c = osm.build_corridors(p.layers["roads"].gdf, p.transit_raw, p.bbox, 15)
    ids = set(c["feature_id"])
    assert ids == {"C-ROAD-TEST-AVENUE", "C-BASE-TEST-AVENUE", "C-TRANSIT-TEST-METRO-LINE"}   # unnamed road excluded
    utm = c.to_crs(c.estimate_utm_crs())
    road = utm[utm["feature_id"] == "C-ROAD-TEST-AVENUE"].geometry.iloc[0]
    assert abs(road.area - 30 * 1200) / (30 * 1200) < 0.01      # 30 m wide, clipped to the 1.2 km window (not 1.6 km)


def test_end_to_end_overlaps_match_hand_computed_areas(tmp_path):
    p = pack(tmp_path)
    corr = osm.corridor_layer(p, 15)
    row = corr.gdf[corr.gdf["feature_id"] == "C-ROAD-TEST-AVENUE"]
    res = run_analysis(None, p.layers["buildings"].gdf, row, scenario_name="Proposed road expansion",
                       corridor_id="C-ROAD-TEST-AVENUE")
    b = res.buildings.set_index("feature_id")
    assert set(b.index[b["affected"]]) == {"way/11", "way/13", "relation/14"}
    assert b.loc["way/11", "overlap_area_sqm"] == pytest.approx(200, rel=0.01)
    assert b.loc["way/13", "overlap_area_sqm"] == pytest.approx(400, rel=0.01)
    assert b.loc["relation/14", "overlap_area_sqm"] == pytest.approx(170, rel=0.01)
    assert not b.loc["way/12", "affected"]
    assert res.summary["parcels_total"] == 0 and any("Parcel layer" in n for n in res.notes)
    # baseline (3 m): only the building straddling the centreline remains
    base = corr.gdf[corr.gdf["feature_id"] == "C-BASE-TEST-AVENUE"]
    rb = run_analysis(None, p.layers["buildings"].gdf, base, scenario_name="b", corridor_id="C-BASE-TEST-AVENUE")
    bb = rb.buildings.set_index("feature_id")
    assert set(bb.index[bb["affected"]]) == {"way/13"}
    assert bb.loc["way/13", "overlap_area_sqm"] == pytest.approx(120, rel=0.01)
    # transit
    tr = corr.gdf[corr.gdf["feature_id"] == "C-TRANSIT-TEST-METRO-LINE"]
    rt = run_analysis(None, p.layers["buildings"].gdf, tr, scenario_name="t", corridor_id="x")
    assert list(rt.buildings.loc[rt.buildings["affected"], "feature_id"]) == ["way/22"]


def test_report_carries_provenance(tmp_path):
    p = pack(tmp_path)
    corr = osm.corridor_layer(p, 15)
    row = corr.gdf[corr.gdf["feature_id"] == "C-ROAD-TEST-AVENUE"]
    res = run_analysis(None, p.layers["buildings"].gdf, row, scenario_name="s", corridor_id="C-ROAD-TEST-AVENUE")
    df = build_report_df(res, {"Building": p.prov["buildings"]})
    assert set(df["data_licence"]) == {osm.OSM_LICENCE} and set(df["data_kind"]) == {"LIVE API"}
    assert b"way/11" in report_to_csv(df)
    md = build_markdown_report(res, df, [{"layer": "buildings", "source": "OSM", "kind": "LIVE API", "licence": "ODbL",
                                          "attribution": osm.OSM_ATTRIBUTION, "retrieved": "now"}], True).decode()
    assert "OpenStreetMap contributors" in md and "No legal parcel" not in md and "OSM contains no legal parcel" in md


def test_boundary_file_is_real_delhi_and_point_test_works():
    b = osm.load_delhi_boundary()
    assert b is not None and len(b) == 1
    assert osm.point_in_delhi(LAT0, LON0, b) is True
    assert osm.point_in_delhi(19.07, 72.87, b) is False        # Mumbai
    assert osm.point_in_delhi(LAT0, LON0, None) is None        # unknown, not guessed
    area_km2 = b.to_crs(b.estimate_utm_crs()).area.sum() / 1e6
    assert 1400 < area_km2 < 1600


def test_upload_parcels_enable_parcel_analysis_and_undeclared_crs_rejected(tmp_path):
    p = pack(tmp_path)
    parcels = gpd.GeoDataFrame({"PARCEL_ID": ["U-1"], "LAND_USE": ["Res"]}, geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    # build a parcel over the road window in WGS84
    from osm_fixture import pt
    a, b_ = pt(0, -5), pt(30, 5)
    parcels = gpd.GeoDataFrame({"PARCEL_ID": ["U-1"], "LAND_USE": ["Res"]}, geometry=[box(a["lon"], a["lat"], b_["lon"], b_["lat"])],
                               crs="EPSG:4326")
    data = parcels.to_json().encode()
    p2 = osm.apply_uploads(p, {"parcels": ("parcels.geojson", data)})
    assert not p2.layers["parcels"].unavailable and p2.layers["parcels"].usable
    assert p2.prov["parcels"].kind == "USER UPLOAD"
    corr = osm.corridor_layer(p2, 15)
    row = corr.gdf[corr.gdf["feature_id"] == "C-ROAD-TEST-AVENUE"]
    res = run_analysis(p2.layers["parcels"].gdf, p2.layers["buildings"].gdf, row, scenario_name="s", corridor_id="x")
    assert res.summary["parcels_affected"] == 1
    # undeclared CRS (raw coordinates in a .json without crs → GeoJSON defaults to WGS84, so test via gpkg-free path)
    d = json.loads(data)
    gdf_nocrs = gpd.GeoDataFrame({"PARCEL_ID": ["N"], "LAND_USE": ["x"]}, geometry=[box(0, 0, 1, 1)], crs=None)
    from data_harmonizer import harmonize_layer
    assert harmonize_layer(gdf_nocrs, "parcels", "n").usable is False and d


def test_upload_validation_errors():
    with pytest.raises(osm.UploadError):
        osm.read_upload("x.exe", b"abc")
    with pytest.raises(osm.UploadError):
        osm.read_upload("x.geojson", b"not json")
    with pytest.raises(osm.UploadError):
        osm.read_upload("x.zip", b"not a zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "hi")
    with pytest.raises(osm.UploadError):
        osm.read_upload("x.zip", buf.getvalue())


def test_upload_corridor_and_zipped_shapefile(tmp_path):
    p = pack(tmp_path)
    from osm_fixture import pt
    a, b_ = pt(-60, -20), pt(40, 20)
    g = gpd.GeoDataFrame({"name": ["My corridor"]}, geometry=[box(a["lon"], a["lat"], b_["lon"], b_["lat"])], crs="EPSG:4326")
    shp_dir = tmp_path / "shp"
    shp_dir.mkdir()
    g.to_file(shp_dir / "c.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in shp_dir.iterdir():
            z.write(f, f.name)
    p2 = osm.apply_uploads(p, {"corridors": ("c.zip", buf.getvalue())})
    layer = osm.corridor_layer(p2, 15)
    assert "UPLOAD-001" in set(layer.gdf["feature_id"]) and "C-ROAD-TEST-AVENUE" in set(layer.gdf["feature_id"])


def test_overpass_query_mentions_all_layers_and_bbox():
    q = osm.overpass_query(osm.bbox_from_center(LAT0, LON0, 600))
    assert '["building"]' in q and '["highway"' in q and '["railway"' in q and "out geom" in q
