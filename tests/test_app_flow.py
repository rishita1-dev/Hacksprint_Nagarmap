"""End-to-end UI flow tests using Streamlit's AppTest (no browser needed).

Covers: boot screen shown once, every nav screen, every mission, the real scan, KPI values,
and the CSV export. Run from the project root:  python -m pytest -q
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("NAGARMAP_DEFAULT_MODE", "demo")  # existing tests assert the synthetic numbers
APP = str(ROOT / "app.py")
SCREENS = ["CITY SELECT", "MISSION CONTROL", "MAP SCANNER", "DATA LAB", "CONFLICT DETECTOR", "MISSION REPORT"]
MISSION_LABELS = ["Proposed road expansion", "Proposed transit corridor", "Baseline (no planned corridor overlap)"]
EXPECTED = {  # mission -> (conflicts shown in HUD) from the real analysis of the bundled data, first corridor
    "Proposed road expansion": 8,        # 6 parcels + 2 buildings
    "Proposed transit corridor": 5,      # 4 parcels + 1 building
    "Baseline (no planned corridor overlap)": 0,
}


def started() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=90).run()
    assert not at.exception
    at.button(key="btn_start").click().run()
    assert not at.exception
    return at


def hud(at: AppTest) -> dict:
    for m in at.markdown:
        if 'class="nm-hud"' in m.value:
            return dict(re.findall(r'class="lab">([^<]+)</div><div class="val">([^<]+)</div>', m.value))
    return {}


def test_boot_screen_shows_once_and_is_skippable():
    at = AppTest.from_file(APP, default_timeout=90).run()
    assert [b.label for b in at.button] == ["▶ PRESS START"]
    assert any("INITIALIZING NAGARMAP" in m.value for m in at.markdown)
    at.button(key="btn_start").click().run()
    assert not any("INITIALIZING NAGARMAP" in m.value for m in at.markdown)
    at.button(key="btn_scan_side").click().run()  # later interactions must not replay the boot
    assert not any("INITIALIZING NAGARMAP" in m.value for m in at.markdown)


@pytest.mark.parametrize("screen", SCREENS)
def test_every_navigation_screen_renders_without_error(screen):
    at = started()
    at.radio(key="screen").set_value(screen).run()
    assert not at.exception, [e.value for e in at.exception]
    at.button(key="btn_scan_side").click().run()   # and again after a scan
    at.radio(key="screen").set_value(screen).run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("mission", MISSION_LABELS)
def test_each_mission_scan_matches_real_analysis(mission):
    at = started()
    at.radio(key="mission").set_value(mission).run()
    assert hud(at)["CONFLICTS DETECTED"] == "—"          # nothing fabricated before scanning
    at.button(key="btn_scan_side").click().run()
    assert not at.exception, [e.value for e in at.exception]
    values = hud(at)
    assert values["CONFLICTS DETECTED"] == str(EXPECTED[mission])
    assert values["PARCELS SCANNED"] == "25" and values["BUILDINGS ANALYZED"] == "21"
    assert values["SCAN PROGRESS"] == "4/4"
    assert at.button(key="btn_scan_side").disabled        # no accidental duplicate scans


def test_mission_control_buttons_change_mission_and_scan():
    at = started()
    at.radio(key="screen").set_value("MISSION CONTROL").run()
    at.button(key="pick_transit_corridor").click().run()
    assert at.session_state["mission"] == "Proposed transit corridor"
    at.button(key="btn_scan_main").click().run()
    assert not at.exception
    assert at.session_state["screen"] == "MAP SCANNER"
    assert hud(at)["CONFLICTS DETECTED"] == "5"


def test_data_lab_lists_real_check_statuses_and_log():
    at = started()
    at.radio(key="screen").set_value("DATA LAB").run()
    page = " ".join(m.value for m in at.markdown)
    assert "REVIEW REQUIRED" in page and "PASS" in page and "SYSTEM LOG" in page
    assert "reprojected from EPSG:32643" in page  # genuine harmonizer message for roads.geojson


def test_report_screen_and_reset():
    at = started()
    at.radio(key="screen").set_value("MISSION REPORT").run()
    assert any("No scan data yet" in m.value for m in at.markdown)
    at.button(key="btn_scan_side").click().run()
    at.radio(key="screen").set_value("MISSION REPORT").run()
    assert any("SCAN COMPLETE" in m.value and "nm-debrief" in m.value for m in at.markdown)
    assert any("download" in str(el).lower() for el in at.main)  # export control present
    at.button(key="btn_reset").click().run()
    assert not at.exception
    assert hud(at)["CONFLICTS DETECTED"] == "—"
