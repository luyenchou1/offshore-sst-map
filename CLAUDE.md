# Offshore SST Map — Project Context

## What This Is
Dash 4.0 web app showing daily sea-surface temperatures (SST) for the NJ-to-MA offshore corridor. Users pick a 7-day date window and animate through the week's SST evolution to spot fishing-relevant trends (warm eddies, cold upwelling, thermoclines).

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
| `app.py` | Main app, all callbacks (7 total), layout |
| `data/erddap.py` | ERDDAP search, fetch, multi-day parallel download |
| `data/geo.py` | Orient arrays, AOI mask, land mask (all 2D only) |
| `data/convert.py` | Kelvin→Fahrenheit, 2x visual upsample |
| `layout/sidebar.py` | Date picker, animation controls, fetch button |
| `layout/mapview.py` | dl.Map with custom panes, loading overlay |
| `map/overlay.py` | Array → RGBA → base64 PNG |
| `map/colorscale.py` | Legend component, adaptive color bounds |
| `map/pois.py` | 7 fishing spots with temp lookups |
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
- **Loading overlay**: 1000 (HTML div above everything)

If POI markers and tooltipPane have the same z-index, the markers render ON TOP of their own tooltips. The fix: put markers in custom panes below 650.

### ImageOverlay Must Be Non-Interactive
Set `interactive=False` on `dl.ImageOverlay` AND add `pointer-events: none !important` in CSS (`.leaflet-image-layer`). Otherwise the overlay swallows all mouse events, preventing hover on POI markers underneath.

### Performance Notes
- **7-day payload size**: ~7 MB JSON in dcc.Store (7 raw arrays + 7 base64 PNGs). Acceptable but worth monitoring.
- **Pre-rendering PNGs server-side** with unified vmin/vmax makes frame switching instant — the render callback just selects a pre-built base64 URL.
- **2x upsample** (scipy zoom) is hardcoded. Higher factors showed negligible visual improvement for more processing cost.

## POI Fishing Spots
```
Haabs Ledge        40.868250, -71.838200
Butterfish Hole    40.836467, -71.674900
July2025           40.895550, -71.830817
CIA                40.933433, -71.716667
Gully              41.020483, -71.416950
Wind Farm SW Corner 40.973983, -71.273300
Tuna Ridge         40.916667, -71.279167
```

## Running Locally
```bash
cd "Offshore Trip Planner"
source .venv/bin/activate
python app.py
# → http://localhost:8050
```
