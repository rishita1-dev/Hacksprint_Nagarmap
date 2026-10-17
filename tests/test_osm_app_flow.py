"""UI flow in real-data (OSM) mode, driven from an on-disk cache built from the hand-made fixture. No network."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import osm_source as osm  # noqa: E402
from osm_fixture import LAT0, LON0, overpass_response  # noqa: E402

APP = str(ROOT / "app.py")


@pytest.fixture()
def osm_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAGARMAP_DEFAULT_MODE", "osm")
    monkeypatch.setenv("NAGARMAP_CACHE", str(tmp_path))
    osm.write_cache(osm.area_key(LAT0, LON0, 600), overpass_response(), "https://example.test", "Q",
                    datetime.now(timezone.utc), tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    import streamlit as st
    st.cache_data.clear()      # st.cache_data is process-wide; start every test from a clean slate
    yield
    st.cache_data.clear()


def hud(at):
    for m in at.markdown:
        if 'class="nm-hud"' in m.value:
            return dict(re.findall(r'class="lab">([^<]+)</div><div class="val">([^<]+)</div>', m.value))
    return {}


def page(at):
    return " ".join(m.value for m in at.markdown)


def start():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    at.button(key="btn_start").click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_osm_mode_scan_uses_real_pipeline_and_labels_sources(osm_env):
    at = start()
    p = page(at)
    assert "OSM · CACHED DOWNLOAD" in p and "UNAVAILABLE" in p and "SYNTHETIC DEMO DATA" not in p
    assert hud(at)["PARCELS SCANNED"] == "N/A"
    at.button(key="btn_scan_side").click().run()
    assert not at.exception, [e.value for e in at.exception]
    h = hud(at)
    assert h["CONFLICTS DETECTED"] == "3"            # way/11, way/13, relation/14 for C-ROAD-TEST-AVENUE
    assert h["BUILDINGS ANALYZED"] == "5"
    assert h["PARCELS SCANNED"] == "N/A"


def test_all_screens_render_in_osm_mode_before_and_after_scan(osm_env):
    at = start()
    for scan in (False, True):
        if scan:
            at.button(key="btn_scan_side").click().run()
        for s in ["CITY SELECT", "MISSION CONTROL", "MAP SCANNER", "DATA LAB", "CONFLICT DETECTOR", "MISSION REPORT"]:
            at.radio(key="screen").set_value(s).run()
            assert not at.exception, (s, [e.value for e in at.exception])
    at.radio(key="screen").set_value("CITY SELECT").run()
    p = page(at)
    assert "© OpenStreetMap contributors" in p and "ODbL" in p and "CACHED DOWNLOAD" in p and "CC BY-SA 2.5 India" in p


def test_missions_other_than_road_expansion(osm_env):
    at = start()
    at.radio(key="mission").set_value("Proposed transit corridor").run()
    at.button(key="btn_scan_side").click().run()
    assert hud(at)["CONFLICTS DETECTED"] == "1"
    at.radio(key="mission").set_value("Baseline (no planned corridor overlap)").run()
    at.button(key="btn_scan_side").click().run()
    assert hud(at)["CONFLICTS DETECTED"] == "1"      # only the building straddling the centreline


def test_offline_without_cache_shows_error_not_fake_data(tmp_path, monkeypatch):
    monkeypatch.setenv("NAGARMAP_DEFAULT_MODE", "osm")
    monkeypatch.setenv("NAGARMAP_CACHE", str(tmp_path))          # empty cache
    monkeypatch.setattr(osm, "OVERPASS_ENDPOINTS", ["http://127.0.0.1:9/api/interpreter"])  # nothing listens here
    at = start()
    assert any("could not be loaded" in e.value or "unavailable" in e.value.lower() for e in at.error)
    assert not hud(at).get("CONFLICTS DETECTED")


def test_refresh_button_attempts_live_fetch_and_falls_back_to_cache(osm_env, monkeypatch):
    monkeypatch.setattr(osm, "OVERPASS_ENDPOINTS", ["http://127.0.0.1:9/api/interpreter"])
    at = start()
    at.button(key="btn_refresh").click().run()
    assert not at.exception, [e.value for e in at.exception]
    at.radio(key="screen").set_value("DATA LAB").run()
    p = page(at)
    assert "Live fetch failed" in p and "Falling back to cached download" in p
