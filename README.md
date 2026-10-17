# NagarMap — Delhi Geospatial Intelligence MVP (arcade edition)

Smart City & Mobility track · built from SIH26013 (*Automated Integration & Intelligent Harmonization of Multi-source Geospatial Data for Urban Land Record Management*).

> A detected spatial intersection is a **potential conflict for human review**. NagarMap does not establish legal ownership, prove encroachment, compute compensation or replace planning approval.

## What is real, and what is not (read this first)

| Layer | Status | Source |
|---|---|---|
| **Roads** (Delhi) | **Real** - downloaded on demand | OpenStreetMap via Overpass API (© OSM contributors, ODbL 1.0) |
| **Building footprints** (Delhi) | **Real** - downloaded on demand | OpenStreetMap via Overpass API |
| **Metro / rail lines** (Delhi) | **Real** - downloaded on demand | OpenStreetMap (`railway=subway/light_rail/rail/monorail`) |
| **Delhi boundary** | **Real**, static file bundled (`data/delhi_boundary.geojson`) | DataMeet *Municipal_Spatial_Data* (CC BY-SA 2.5 India); unofficial, scraped from an ArcGIS Online map; retrieved 2026-10-17 |
| **Land parcels / cadastre** | **UNAVAILABLE** | No open, machine-readable Delhi parcel geometry could be verified. NagarMap bundles/fetches none and shows "UNAVAILABLE". Upload your own authorised parcel file to enable parcel analysis. |
| Synthetic demo (Ward A) | Fake, bundled for tests/offline demo only | `scripts/generate_data.py`; selectable as "Synthetic demo (not real)" |

**Corridors are scenarios, not official alignments.** Mission corridors are a real OSM road (or metro/rail) centreline buffered by a width you set (default 15 m each side). The baseline mission is the same road with a 3 m buffer. Lines are clipped to the study window, so nothing is reported outside the area for which buildings were downloaded.

**No data is bundled for Delhi buildings/roads.** The author's build environment had no access to OSM servers, so no OSM extract could be shipped. The first run needs internet (or run the prefetch script once); after that each area works offline from `data/cache/`.

### Live vs cached vs static (what the labels mean)
- **LIVE API** — downloaded from the Overpass API in this app session (chip in the header, CITY SELECT table). Not a stream: NagarMap never auto-updates; press **↻ REFRESH FROM OSM (LIVE)** to re-download.
- **CACHED DOWNLOAD** — read from `data/cache/*.json` (raw Overpass response + retrieval time). Age is shown; older than 30 days is flagged STALE. If a refresh fails, the cache is used and the failure is logged in DATA LAB.
- **STATIC BUNDLED** — files shipped with the app (Delhi boundary).
- **USER UPLOAD** — your file; correctness, authority and licence are your responsibility.
- **UNAVAILABLE** — does not exist from a verified source; analysis for that layer is switched off, never faked.

## Windows setup and run (PowerShell, project root = folder containing `app.py`)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m pytest -q
python scripts\fetch_delhi_data.py --all        # optional: pre-download all preset Delhi windows for offline use (needs internet)
streamlit run app.py
```
If activation is blocked: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`. App: http://localhost:8501.
To start in synthetic mode: `$env:NAGARMAP_DEFAULT_MODE="demo"; streamlit run app.py`. Custom cache folder: `$env:NAGARMAP_CACHE="D:\nagarmap_cache"`.
Python 3.10+ recommended (developed on 3.12; Streamlit 1.66, GeoPandas 1.2).

## Using the app
1. **DATA SOURCE** (control panel): *Delhi · OpenStreetMap* (default) or *Synthetic demo*. Pick a preset study area or *Custom location* (lat/lon inside Delhi; the window centre is tested against the Delhi boundary and a warning shows if it is outside), window size (300–1000 m half-size) and corridor width.
2. **MISSION SELECT** → choose the road / metro corridor → **▶ SCAN CITY**. Buildings are intersected with the corridor polygon in a projected UTM CRS (metres); overlap area, % of feature and a plain-language reason are produced for each hit. Features that merely touch the boundary are not counted.
3. **MAP SCANNER** highlights overlaps; click a feature for *TARGET LOCKED* (including an `openstreetmap.org/way/…` object reference for OSM features). **DATA LAB** shows harmonization checks, source log and fetch/cache messages. **CONFLICT DETECTOR** / **MISSION REPORT** list findings.
4. **Export**: `↓ EXPORT MISSION REPORT` (CSV with per-row data source, type, licence, retrieval time) and `↓ EXPORT READABLE REPORT (.md)` (KPIs, sources, flagged records, disclaimer).
5. **UPLOAD YOUR OWN LAYERS** (sidebar): parcels (enables parcel analysis), buildings (replace OSM), corridor polygons (added to scenarios). Formats: `.geojson`, `.json`, `.gpkg`, zipped shapefile `.zip`, max 25 MB. A file with no declared CRS is rejected, never guessed.

## Architecture (changes in v0.3.0)

```
Overpass API ──► osm_source.py ─► disk cache (data/cache) ─► parse ─► data_harmonizer ─┐
Delhi boundary (static) ───────────────────────────────────────────────────────────────┤
User uploads ──► osm_source.read_upload ─► data_harmonizer ────────────────────────────┤
                                                              spatial_engine (UTM) ◄───┘ ─► map / KPIs / CSV + MD report
```
- **New:** `osm_source.py` (Overpass client with mirror fallback, cache, OSM→GeoDataFrame parser incl. multipolygon buildings, OSM-derived corridors, uploads, provenance), `scripts/fetch_delhi_data.py`, `data/delhi_boundary.geojson`, `tests/test_osm_source.py`, `tests/test_osm_app_flow.py`, `tests/osm_fixture.py`.
- **Modified (additive):** `app.py` (data-source controls, provenance, parcels-unavailable handling, uploads, exports), `theme.py` (header chip/banner can be data-aware; arcade styling untouched), `data_harmonizer.py` (OSM field aliases, `unavailable_layer`), `diagnostics.py` (UNAVAILABLE status), `spatial_engine.py`/`report_generator.py` (source names, provenance columns, Markdown report).
- Unchanged: the arcade look, navigation, mission cards, HUD, Data Lab, scan logic.

## Verified vs not verified

Verified in the build sandbox (51 automated tests, plus a headless `streamlit run` returning HTTP 200 and healthy):
- Overpass JSON parsing (ways, multipolygon relations with holes, unclosed ways skipped), cache write/read, offline use from cache, live-failure fallback to cache, no-data error state.
- Corridor generation from OSM-style lines and **hand-computed overlap areas** (200 / 400 / 170 / 120 / 100 m²) matched by the engine.
- Upload validation (bad type / bad JSON / bad zip), zipped-shapefile and corridor upload, parcel upload enabling parcel analysis, undeclared CRS rejected.
- Every arcade screen renders in both data modes before and after a scan; CSV/MD report content; the bundled Delhi boundary loads (≈1,488 km²) and point-in-Delhi checks work.

**NOT verified (no OSM network access or browser in the build environment):**
- A real Overpass download. The query and parser are tested only against a hand-built fixture in Overpass JSON format (`tests/osm_fixture.py`, clearly not real data). Public Overpass servers may rate-limit or time out on dense windows; the app then falls back to cache or shows an error. Mirror URLs may change.
- Actual real-world result counts for any Delhi corridor, and rendering/performance of several thousand buildings in the browser (keep windows ≤ 600 m in dense areas).
- Visual rendering in a real browser (the Streamlit AppTest harness does not render the folium map); the OSM basemap tiles; file-upload widget interaction through the browser (upload logic is tested at function level).

## Limitations
- OSM is crowd-sourced: building completeness, footprint accuracy and tagging vary across Delhi; OSM contains **no legal parcel boundaries**.
- Overpass `out geom` data is a snapshot; the `data as of` time shown is the OSM database timestamp reported by the server.
- Corridor width is a user assumption. Overlap areas are for screening, not survey-grade.
- Duplicate detection is exact-match only; invalid geometries are auto-repaired with `make_valid` and flagged.
- Unnamed roads are not offered as corridors; only motorway→tertiary named roads and named rail/metro lines.
- The OSM basemap is lightly used and is subject to the OSM tile usage policy.
- The arcade CSS targets Streamlit's DOM; Streamlit upgrades could change styling but not behaviour.

## Data sources & attribution
- OpenStreetMap data © OpenStreetMap contributors, licensed under the ODbL 1.0 — https://www.openstreetmap.org/copyright (queried through the Overpass API, https://overpass-api.de/)
- Delhi boundary: DataMeet Municipal_Spatial_Data — https://github.com/datameet/Municipal_Spatial_Data/tree/master/Delhi — CC BY-SA 2.5 India (http://creativecommons.org/licenses/by-sa/2.5/in/); derived from an ArcGIS Online map (item 7c4f1b9be6cc4cecbcd28ee5136898f7). Share-alike applies to this file.
- Delhi parcels: none (see above).

## Arcade redesign notes (v0.2.0, still accurate)

The dashboard is styled as a futuristic arcade cabinet; the geospatial engine, harmonizer, scenario logic and report generator are unchanged.

| Screen (top menu) | What it really does |
|---|---|
| **CITY SELECT** | Lists the four loaded layers with record counts, source CRS and check status (one bundled city: Synthetic Ward A). |
| **MISSION CONTROL** | Three mission cards (Road Expansion, Transit Corridor, Safe Grid / Baseline) mapped to the original scenarios, a briefing for the selected corridor, and the **▶ SCAN CITY** button. |
| **MAP SCANNER** | The central map, a HUD, and a *TARGET LOCKED* inspection panel for whichever feature you click (real ID, overlap area, data quality, reason, copyable ID). |
| **DATA LAB** | Harmonization diagnostics: PASS / REVIEW REQUIRED / WARNING / FAILED per layer and check, plus a log of what the harmonizer actually did. |
| **CONFLICT DETECTOR** | Affected parcels, affected buildings, all flagged records (sortable, searchable) and per-feature explanations. |
| **MISSION REPORT** | *SCAN COMPLETE* debrief cards and **↓ EXPORT MISSION REPORT** (the same CSV as before). |

Design notes: palette `#080A12 / #00F5FF / #FF2BD6 / #B6FF3B / #FFB000 / #FF4057`; fonts use local fallbacks only (Orbitron / Inter / JetBrains Mono if installed, otherwise system fonts), so nothing breaks offline; animations respect `prefers-reduced-motion`; the app is silent.

Honesty rules built into the UI:
- Every HUD number is computed from the loaded data or the analysis. Before a scan, "conflicts" shows `—`.
- **SCAN PROGRESS** is the count of real pipeline stages completed (data loaded, harmonized, corridor analyzed, report ready).
- **DATA QUALITY** is the share of records with no failed input check - not an accuracy score.
- A check shows a status only if it ran; an undeclared CRS is `FAILED`, never `PASS`.
- The boot sequence appears once per session and reports the real load result; **▶ PRESS START** doubles as skip.
- *SCAN COMPLETE* is a processing status, not a measure of accuracy or compliance.

New files: `theme.py` (CSS + HTML components), `diagnostics.py` (Data Lab checks, HUD metrics). Modified: `app.py` (UI), `data_harmonizer.py` (additive check counters only). New tests: `tests/test_diagnostics.py`, `tests/test_app_flow.py`.

