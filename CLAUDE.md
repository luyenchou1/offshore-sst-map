# Offshore SST Analyzer — Project Context

## What This Is
Dash 4.0 web app ("Offshore SST Analyzer") showing daily sea-surface temperatures for the NJ-to-MA offshore corridor. Users pick any 7-day date window (back to June 2002) and animate through the week's SST evolution to spot fishing-relevant trends (warm eddies, cold upwelling, thermoclines).

## Tech Stack
- **Dash 4.0** + **dash-leaflet** + **dash-bootstrap-components** (Flatly theme)
- **NOAA CoastWatch ERDDAP** — MUR GHRSST (1km, preferred) with OISST (25km) fallback
- Data available from 2002-06-01 to ~2 days ago (MUR has ~2-day latency)
- Python 3.12, venv at `.venv/`
- Dev server: `.venv/bin/python -c "from app import app; app.run(...)"`  (port 8050)
- Production: gunicorn via Procfile

## Architecture

### Data Flow
```
User picks date → Fetch button → get_sst_multiday() → 7 parallel ERDDAP requests
  → orient_to_leaflet() → mask_aoi → mask_land (per day, 2D only)
  → compute unified vmin/vmax across all 7 days
  → pre-render 7 base64 PNGs → store in dcc.Store as JSON
  → render callback swaps PNG by frame index (instant)
```

### Key Files
| File | Purpose |
|------|---------|
| `app.py` | Main app, all callbacks (9 total), layout |
| `data/erddap.py` | ERDDAP search, fetch, multi-day parallel download |
| `data/geo.py` | Orient arrays, AOI mask, land mask (all 2D only) |
| `data/convert.py` | Kelvin→Fahrenheit, 2x visual upsample |
| `layout/sidebar.py` | Date picker, animation controls, POI picker, fetch button |
| `layout/mapview.py` | dl.Map with custom panes, loading overlay |
| `map/overlay.py` | Array → RGBA → base64 PNG |
| `map/colorscale.py` | Legend component, adaptive color bounds |
| `map/pois.py` | 20 fishing spots + The Dump rectangle, multi-select picker |
| `config.json` | AOI polygon, ERDDAP servers, search terms |

### Callback Structure
1. **show_loading_on_fetch** — Fast callback on button click, shows map overlay immediately
2. **fetch_sst_data** — Long-running: fetches 7 days, processes, pre-renders PNGs
3. **render_map_layers** — Swaps overlay PNG by frame index, hides loading overlay
4. **save_click_pos** — Stores clicked lat/lng in dcc.Store
5. **render_click_marker** — Shows temp reading for current frame at clicked position
6. **toggle_play_pause** — Toggles dcc.Interval for animation
7. **auto_advance_frame** — Increments frame slider on interval tick (wraps 6→0)
8. **step_frame** — Step forward/back buttons
9. **update_day_indicator** — Shows "Mar 15, 2026 (Day 3 of 7)"

## Critical Lessons Learned

### Dash 4.0 Gotchas
- **Never use clientside callbacks that manipulate DOM for elements also controlled by server callbacks.** Dash's virtual DOM reconciliation won't see the DOM change, so server callback updates get silently skipped, leaving the UI stuck. We burned multiple iterations on this with the Fetch button getting permanently disabled.
- **`allow_duplicate=True` requires `prevent_initial_call=True`** (or `"initial_duplicate"`). Dash 4 enforces this strictly.
- **`dbc.Spinner` v2 uses `spinner_class_name`**, not `className`. The constructor will error.
- **Callback timeouts are browser-side**, not server-side. If a synchronous Dash callback takes >30s, the browser gives up with "server did not respond" and the callback system can get stuck. Once stuck, subsequent clicks don't fire. Solution: keep callbacks under 30s or use time budgets.
- **Changing callback inputs/outputs changes the callback hash.** Browser-cached JS from a previous session will 500 with "Callback function not found." Users must hard-refresh after callback signature changes.
- **Avoid unicode icons in button labels.** Characters like ⏸ (U+23F8) and ❚❚ (U+275A) render inconsistently across browsers/OS. Use plain text labels ("Play"/"Pause") instead.
- **Server must be restarted for code changes to take effect.** Committing and pushing is not enough — the running Dash dev server serves from memory. Preview server must be stopped and restarted.

### ERDDAP Behavior
- **MUR data latency**: ~2 days behind. Always try `end_date - 0`, then `-1`, then `-2`.
- **Rate limiting (429)**: ERDDAP servers return 429 if hit with too many concurrent requests. Max 2 parallel workers with exponential backoff (2s, 4s, 6s retry).
- **All 3 configured servers redirect to the same backend** (coastwatch.pfeg.noaa.gov). Trying different "servers" doesn't help for rate limits.
- **Single multi-day NetCDF requests time out** for 7+ days of MUR data. Parallel individual-day fetches (2 at a time) are more reliable.
- **Time budget**: 25s for single-day, 90s for multi-day. Must finish before browser-side callback timeout.

### Leaflet Z-Index
Map layer panes and their z-index values matter enormously:
- **overlayPane**: 400 (SST ImageOverlay lives here)
- **poi-pane**: 450 (custom — above overlay, below tooltips)
- **click-pane**: 500 (custom — above POIs, below tooltips)
- **tooltipPane**: 650 (Leaflet default — tooltips render here)
- **popupPane**: 700 (Leaflet default — POI click popups render here)
- **Loading overlay**: 1000 (HTML div above everything)

If POI markers and tooltipPane have the same z-index, the markers render ON TOP of their own tooltips. The fix: put markers in custom panes below 650.

### ImageOverlay Must Be Non-Interactive
Set `interactive=False` on `dl.ImageOverlay` AND add `pointer-events: none !important` in CSS (`.leaflet-image-layer`). Otherwise the overlay swallows all mouse events, preventing clicks on POI markers underneath.

### Interaction Model — Unified Click System
All clicks route through a single `handle_map_click` callback. Only one tooltip visible at a time.
- **Click near a POI** (~0.08° threshold via `find_nearest_poi()`) → shows POI info (name, temp, coords) in the click-marker layer with slate-blue left border accent
- **Click empty map** → shows SST reading (temp + coords) with red circle marker
- **Measure mode** → two-click distance/bearing tool. If clicked near a POI, snaps to POI coordinates and shows POI name in the A/B labels
- POI markers are **visual-only** (`bubblingMouseEvents=True`, no Popup/Tooltip children). All info display is callback-driven through the click-marker layer
- This unified model works identically on mobile (tap = click)

### Measure Tool
- Toggle via "📏 Measure" button in sidebar
- Click point A, click point B → dashed indigo line with distance label at midpoint
- Shows: nautical miles, statute miles, bearing, compass direction (e.g. "42.3 nm (48.7 mi) • 225° SW")
- Snaps to POI coordinates when clicking near a fishing spot
- Math in `map/measure.py`: haversine distance + initial bearing formula

### Performance Notes
- **7-day payload size**: ~7 MB JSON in dcc.Store (7 raw arrays + 7 base64 PNGs). Acceptable but worth monitoring.
- **Pre-rendering PNGs server-side** with unified vmin/vmax makes frame switching instant — the render callback just selects a pre-built base64 URL.
- **2x upsample** (scipy zoom) is hardcoded. Higher factors showed negligible visual improvement for more processing cost.
- **AOI expansion** (39.5°N southern boundary) added ~15% more grid cells — negligible impact.

## POI Fishing Spots (20 points + 1 rectangle)
```
Original:
  Haabs Ledge          40.868, -71.838
  Butterfish Hole      40.836, -71.675
  July2025             40.896, -71.831
  CIA                  40.933, -71.717
  Gully                41.020, -71.417
  Wind Farm SW Corner  40.974, -71.273
  Tuna Ridge           40.917, -71.279

Added (source: marinebasin.com):
  Bacardi Wreck        39.883, -72.645
  Coimbra Wreck        40.401, -72.339
  Cartwright           41.000, -71.808
  Coxes Ledge          41.050, -71.158
  West Atlantis        40.075, -70.450
  East Atlantis        39.958, -69.917
  The Dip              39.908, -71.733
  Fish Tails           40.062, -71.355
  Jennie's Horn        40.813, -71.544
  Mud Hole             40.937, -71.417
  Ranger Wreck         40.588, -71.790

The Dump (source: saltycape.com):
  Rectangle: 40.667-40.833°N, 70.750-70.996°W
```

## Running Locally
```bash
cd "Offshore Trip Planner"
source .venv/bin/activate
python app.py
# → http://localhost:8050
```
