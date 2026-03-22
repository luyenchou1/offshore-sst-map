# Changelog

## 2026-03-22 — Phase 1: UI Polish, POIs, Expanded Coverage

### Added
- **20 fishing spots + The Dump**: Bacardi Wreck, Coimbra Wreck, Cartwright, Coxes Ledge, West/East Atlantis, The Dip, Fish Tails, Jennie's Horn, Mud Hole, Ranger Wreck. The Dump rendered as a dashed rectangle (10x10 mi box, source: saltycape.com).
- **POI multi-select picker**: Dropdown to choose exactly which fishing spots to display on the map.
- **Expanded AOI**: Southern boundary pushed from ~39.9°N to 39.5°N to cover all new spots (Bacardi, The Dip, Atlantis canyons, Fish Tails).

### Changed
- **Renamed to "Offshore SST Analyzer"** (dropped "NJ to MA AOI" reference).
- **POI markers restyled**: Hollow slate-colored rings (subtle, non-competing with SST overlay) replacing solid green circles.
- **POI tooltips enhanced**: Bold name, prominent temperature, lat/lon coordinates, slate-blue left border accent to distinguish from SST click-to-read tooltips.
- **POI interaction unified to click**: POIs now use `dl.Popup` (click to open) instead of `dl.Tooltip` (hover). Only one popup visible at a time. Works identically on mobile (tap = click).
- **Compact status text**: "MUR 1km • 7 days loaded" replaces verbose green alert box.
- **AOI boundary**: Light gray dashed line instead of solid black.
- **Play/Pause button**: Icon-only (▶/⏸), consistent sizing.
- **Date picker**: Friendlier "MMM D, YYYY" format.
- **Tighter sidebar**: Removed "Controls" header, condensed spacing.

## 2026-03-22 — 7-Day Animation & UX Overhaul

### Added
- **7-day SST animation**: Pick any end date (back to June 2002) and load a week of SST data. Play/pause, step forward/back, or scrub through the days with a slider.
- **Date picker**: Replaces the old days-back slider. Select any date from 2002 to present.
- **Animation controls**: Play/Pause button, step buttons, frame slider with day-of-month marks, day indicator showing full date and "Day X of N".
- **Unified loading overlay**: Centered map spinner with "Loading 7-day SST data..." and "This may take up to a minute" — shows on both initial load and re-fetch.
- **Grid resolution in legend**: Shows "1.1 km grid" (MUR) or "25 km grid" (OISST) in the color scale legend.
- **Parallel multi-day ERDDAP fetch**: 7 individual-day requests run 2-at-a-time with rate-limit retry (429 backoff). More reliable than a single large multi-day request.
- **Click-to-read auto-refresh**: Temperature reading at a clicked point updates automatically when switching frames or loading new data.

### Fixed
- **POI hover tooltips not working**: SST ImageOverlay was blocking mouse events. Fixed with `interactive=False`, `pointer-events: none` CSS, and custom Leaflet panes (z-index 450 for POIs, below tooltipPane at 650).
- **Tooltips rendering behind markers**: POI pane z-index was equal to tooltipPane. Lowered to 450 so tooltips always appear on top.
- **Fetch button getting permanently stuck**: Clientside callback DOM manipulation conflicted with Dash 4's virtual DOM. Removed all clientside callbacks.
- **Callback timeouts killing the UI**: ERDDAP retry loops could take minutes, exceeding browser-side timeout. Added 25s/90s time budgets.
- **Legend clipped below viewport**: Map wrapper now has bounded height with `overflow: hidden`.
- **Slider date labels overlapping**: Shortened to day-of-month numbers.

### Removed
- Visual resolution dropdown (1x/2x/3x upsample) — hardcoded to 2x since the visual difference was negligible.
- Days-back slider — replaced by date picker.
- Clientside callbacks — all UI state managed by Dash server callbacks only.

## 2026-03-22 — SST Reading UX Improvements

### Added
- **Click-to-read temperature**: Click anywhere on the SST overlay to see the temperature reading with styled tooltip (large bold temp, smaller coords below).
- **POI fishing spot labels**: 7 named fishing spots shown as green dots with hover tooltips displaying spot name and current SST.
- **Auto-fetch on page load**: SST data loads automatically when the page opens.
- **Styled tooltips**: Card-style tooltips with shadows and borders for both SST readings and POI hover labels.

### Fixed
- **SST overlay mis-aligned**: Orientation and masking bugs fixed in the data pipeline.
- **Popup requiring double-click**: Replaced `dl.Popup` (needs click to open, which re-triggers map click) with `dl.Tooltip` (permanent, shows immediately).

## 2026-03-08 — Dash Migration (v1.0)

### Changed
- **Migrated from Streamlit to Dash + Dash Leaflet**: Full rewrite for better map interactivity and performance.
- **Fixed SST data bugs**:
  - Bug 1: MUR preference — reject non-MUR datasets when primary terms include "MUR"
  - Bug 2: Removed 48°F hard floor that collapsed winter color maps
  - Bug 3: try/except around fetch_grid so failures fall through to next server
  - Bug 4: Auto-retry with older dates for MUR data latency
- **Faster rendering**: Vectorized color mapping (~100x faster than per-pixel loop).
- **Rasterized masking**: AOI and land masks use rasterio instead of per-pixel Shapely checks.

## 2025-08-11 — Initial Release (v0.9.5)

### Added
- Initial Streamlit-based SST visualization app.
- NOAA ERDDAP data fetching with server fallback.
- AOI polygon for NJ-to-MA offshore corridor.
- Basic color-mapped SST overlay.
