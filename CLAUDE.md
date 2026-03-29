# GotOne Offshore SST Analyzer — Project Context

## What This Is
Dash 4.0 web app ("GotOne Offshore SST Analyzer") branded for [gotoneapp.com](https://www.gotoneapp.com). Shows daily sea-surface temperatures for the NJ-to-MA offshore corridor. Users pick any 7-day date window (back to June 2002) and animate through the week's SST evolution to spot fishing-relevant trends (warm eddies, cold upwelling, thermoclines).

## Tech Stack
- **Dash 4.0** + **dash-leaflet** + **dash-bootstrap-components** (Flatly theme + GotOne CSS overrides)
- **NOAA CoastWatch ERDDAP** — MUR GHRSST (1km, preferred) with OISST (25km) fallback
- Data available from 2002-06-01 to ~2 days ago (MUR has ~2-day latency)
- Python 3.12, venv at `.venv/`
- Dev server: `python app.py` (port 8050)
- Production: **Render Starter** ($7/month) at https://offshore-sst-map.onrender.com
  - Custom domain: `sst.gotoneapp.com` (CNAME → Render, SSL auto-provisioned)
  - Start command: `gunicorn app:server --bind 0.0.0.0:$PORT --workers 1 --timeout 180`
  - Must be 1 worker (raw data cache is in-memory per process)
  - Auto-deploy is OFF — use Manual Deploy in Render dashboard
- **Squarespace embed**: iframe on `gotoneapp.com/offshore-sst` loads the app inline
  - `Content-Security-Policy: frame-ancestors` header allows embedding from gotoneapp.com
  - Embed Block with `<iframe src="https://sst.gotoneapp.com" ...>`

## GotOne Branding
- **Primary blue**: `#0183fe` (buttons active states, slider, spinner, measure tool)
- **Dark navy**: `#0a1628` (header, sidebar background)
- **Logo**: `assets/gotone-logo.png` — white fish + "GotOne" logotype on transparent bg (56px height)
- **Source logos**: `Branding:Logo/` folder (not deployed — gitignored)
- **Buttons**: White pill-shaped (border-radius 100px) with dark text on dark sidebar
- **Inputs**: White background with dark navy text (#0a1628) for date picker, dropdown, calendar popup
- **CSS**: `assets/gotone.css` contains all brand overrides. Must use Dash 4 class names (see below)

## Architecture

### Data Flow
```
User picks date → Fetch button → get_sst_multiday() → 7 parallel ERDDAP requests
  → orient_to_leaflet() → mask_aoi → mask_land (per day, 2D only)
  → compute unified vmin/vmax across all 7 days
  → pre-render 7 base64 PNGs

Two-part storage:
  → dcc.Store (browser): PNG frames + metadata (~7 MB)
  → _raw_data_cache (server): raw float arrays for click lookups (~18 MB)
      ↑ populated BEFORE disk write (memory-first for reliability)
  → Disk cache: gzip JSON with base64-encoded numpy arrays
      ↑ written in background thread (non-blocking, memory-efficient)

render_static_layers sets initial overlay + POIs + legend
  → clientside callback swaps PNG by frame index (instant)
```

### Key Files
| File | Purpose |
|------|---------|
| `app.py` | Main app, all callbacks, layout, server-side raw data cache |
| `data/erddap.py` | ERDDAP search, fetch, multi-day parallel download |
| `data/geo.py` | Orient arrays, AOI mask, land mask (all 2D only) |
| `data/convert.py` | Kelvin→Fahrenheit, 2x visual upsample |
| `data/cache.py` | Disk-based gzip JSON cache with staleness checks |
| `layout/sidebar.py` | Date picker, animation controls, POI picker, fetch button |
| `layout/mapview.py` | dl.Map with custom panes, loading overlay |
| `map/overlay.py` | Array → RGBA → base64 PNG |
| `map/colorscale.py` | Legend component, adaptive color bounds |
| `map/pois.py` | 20 fishing spots + The Dump rectangle, multi-select picker |
| `map/measure.py` | Haversine distance, initial bearing, compass direction |
| `config.json` | AOI polygon, ERDDAP servers, search terms |
| `assets/gotone.css` | GotOne brand CSS overrides (colors, dark sidebar, inputs) |
| `assets/gotone-logo.png` | White fish logotype on transparent background |

### Callback Structure
1. **show_loading_on_fetch** — Fast callback on button click / auto-fetch, shows loading overlay immediately
2. **fetch_sst_data** — Synchronous: fetches 7 days (or reads disk cache), processes, pre-renders PNGs, stores raw data server-side (memory-first, then background disk write)
3. **render_static_layers** — Sets initial overlay PNG + bounds, POIs, legend, hides loading overlay. Also fires on POI picker / lock-scale changes
4. **clientside: swap overlay** — Swaps overlay PNG by frame index (sst-store as State, not Input)
5. **clientside: play/pause** — Toggles dcc.Interval
6. **clientside: auto-advance** — Increments frame slider on interval tick (wraps 6→0)
7. **clientside: step frame** — Step forward/back buttons
8. **clientside: day indicator** — Shows "Mar 15, 2026 (Day 3 of 7)" from `dates[]` array
9. **handle_map_click** — Routes clicks: POI info (reads server-side cache), SST reading (sets click-pos), or measure tool
10. **render_click_marker** — Shows temp reading at clicked position (reads server-side cache)
11. **toggle_measure** — Activates/deactivates measure mode
12. **update_poi_count** — Shows "(19/19)" count next to "Spots" label

## Critical Lessons Learned

### Dash 4.0 Gotchas
- **Never use clientside callbacks that manipulate DOM for elements also controlled by server callbacks.** Dash's virtual DOM reconciliation won't see the DOM change, so server callback updates get silently skipped, leaving the UI stuck. We burned multiple iterations on this with the Fetch button getting permanently disabled.
- **`allow_duplicate=True` requires `prevent_initial_call=True`** (or `"initial_duplicate"`). Dash 4 enforces this strictly.
- **`dbc.Spinner` v2 uses `spinner_class_name`**, not `className`. The constructor will error.
- **Callback timeouts are browser-side**, not server-side. If a synchronous Dash callback takes >30s, the browser gives up with "server did not respond" and the callback system can get stuck. Once stuck, subsequent clicks don't fire. Solution: keep callbacks under 30s or use time budgets.
- **Changing callback inputs/outputs changes the callback hash.** Browser-cached JS from a previous session will 500 with "Callback function not found." Users must hard-refresh after callback signature changes.
- **Avoid unicode icons in button labels.** Characters like ⏸ (U+23F8) and ❚❚ (U+275A) render inconsistently across browsers/OS. Use plain text labels ("Play"/"Pause") instead.
- **Server must be restarted for code changes to take effect.** Committing and pushing is not enough — the running Dash dev server serves from memory. Preview server must be stopped and restarted.
- **Dash 4 CSS class names differ from older versions.** Date picker uses `.dash-datepicker-input` (not `.DateInput_input`). Dropdown uses `.dash-dropdown-value`, `.dash-options-list-option-text` (not `.Select-value-label`). Calendar uses `.dash-datepicker-calendar`. Always inspect the actual DOM to find the right selectors — don't guess from docs for older versions.
- **Dark sidebar + white inputs require aggressive CSS overrides.** Dash components inherit text color from their parent. When the sidebar has light text, every input/dropdown/calendar inside it also gets light text. Must use `!important` on the specific Dash 4 class names for each component.
- **Initial overlay must be set by a server callback, not clientside.** Clientside callbacks that depend on `dcc.Store` data from background or long-running callbacks can fire before the store data is fully propagated. Use `render_static_layers` (server callback) to set the initial overlay, and clientside callbacks only for frame changes.

### Background Callbacks (`background=True`) — Do NOT Use on Render
- **Dash background callbacks use DiskcacheManager** to poll for results. The full callback return value is serialized and delivered via a polling HTTP response.
- **Render's proxy returns 502** when the polling response exceeds ~7-10 MB. Our original payload (~26 MB with raw arrays) caused consistent 502s.
- **Even with reduced payload**, background callbacks add complexity (diskcache dependency, forked processes) for marginal benefit on a single-worker setup.
- **Solution**: Use synchronous callbacks. Keep the payload under ~7 MB by splitting raw arrays to server-side memory. The disk cache ensures most fetches return in <2 seconds.

### Payload Size Management
- **dcc.Store payload must stay under ~7 MB** for reliable delivery on Render.
- **Raw float arrays are the biggest culprit**: 7 days × ~93K floats × ~8 bytes/float = ~5 MB as numpy. These MUST be kept server-side.
- **Store payload contents**: 7 base64 PNG frames (~6.5 MB) + dates + bounds + metadata = ~7 MB total.
- **Server-side `_raw_data_cache`**: In-memory dict keyed by `"{end_date}_{mode}"`. Stores numpy arrays for click-to-read-temp lookups. **Populated BEFORE disk write** so clicks work even if disk write fails. Falls back to disk cache on miss (handles restarts).
- **Disk cache** (`data/cache.py`): Stores the FULL payload with raw arrays as **base64-encoded numpy `.npy`** format (v2) instead of JSON lists (v1). Written in a **background thread** to avoid blocking the callback response. Backward-compatible: reads both v1 and v2 formats.

### Memory Management on Render (512 MB)
- **Never use `.tolist()` for disk cache serialization.** Converting numpy float64 arrays to Python lists triples memory usage (8 bytes/float → ~28 bytes as Python object). On Render's 512 MB, this can OOM the worker.
- **Use `_serialize_array()` / `_deserialize_array()`** — base64-encoded numpy `.npy` format. Only 1.3x memory overhead vs 3x+ for `.tolist()`.
- **Always populate `_raw_data_cache` before disk write.** If the disk write OOMs or crashes the worker, the memory cache is already set and clicks work. The callback response is also faster since the disk write happens asynchronously.
- **Disk write in background thread** — `threading.Thread(target=_write_disk_cache, daemon=True)`. Non-blocking, so the fetch callback returns immediately to the browser.

### Render Deployment
- **Must use 1 gunicorn worker.** The `_raw_data_cache` is per-process. With multiple workers, a click request may be served by a worker that doesn't have the data. The disk cache fallback mitigates this, but 1 worker is the intended config.
- **Start command must be set in Render Settings** (not blank — Render requires a value). Use: `gunicorn app:server --bind 0.0.0.0:$PORT --workers 1 --timeout 180`
- **Auto-deploy is currently OFF.** Use Manual Deploy after pushing to `main`.
- **Pre-warm thread** runs 15s after startup. Only loads from disk cache (no ERDDAP). Heavy ERDDAP fetches during startup starve the gunicorn worker and cause Render's health check to fail, hanging the deploy indefinitely.
- **Disk cache persists across deploys** (Render Starter has persistent filesystem). Previously fetched dates load instantly from cache.

### Custom Domain & Squarespace Integration
- **Custom domain**: `sst.gotoneapp.com` — CNAME in Squarespace DNS pointing to `offshore-sst-map.onrender.com`. Added as custom domain in Render dashboard; SSL auto-provisioned by Render.
- **Squarespace embed**: iframe on `gotoneapp.com/offshore-sst` page. Uses Squarespace Embed Block with `<iframe src="https://sst.gotoneapp.com">`.
- **iframe headers**: `@server.after_request` hook sets `Content-Security-Policy: frame-ancestors` to allow embedding from `gotoneapp.com`, `sst.gotoneapp.com`, and the Render domain. Also removes `X-Frame-Options` if set by middleware.
- **To update the embed URL**: change `src` in the Squarespace Embed Block. To change allowed embedding origins, update `set_iframe_headers()` in `app.py`.

### ERDDAP Behavior
- **MUR data latency**: ~2 days behind. Always try `end_date - 0`, then `-1`, then `-2`.
- **Rate limiting (429)**: ERDDAP servers return 429 if hit with too many concurrent requests. Max 2 parallel workers with exponential backoff (2s, 4s, 6s retry).
- **All 3 configured servers redirect to the same backend** (coastwatch.pfeg.noaa.gov). Trying different "servers" doesn't help for rate limits.
- **Single multi-day NetCDF requests time out** for 7+ days of MUR data. Parallel individual-day fetches (2 at a time) are more reliable.
- **Time budget**: 25s for single-day, 90s for multi-day. Must finish before browser-side callback timeout.

### Leaflet Z-Index
Map layer panes and their z-index values matter enormously:
- **tilePane**: 200 (CARTO basemap)
- **gebco-pane**: 390 (custom — GEBCO bathymetry WMS)
- **contours-pane**: 400 (custom — NOAA ENC nautical chart WMS)
- **sst-pane**: 410 (custom — SST ImageOverlay, opacity controlled by sidebar slider)
- **poi-pane**: 450 (custom — above SST, below tooltips)
- **click-pane**: 500 (custom — above POIs, below tooltips)
- **tooltipPane**: 650 (Leaflet default — tooltips render here)
- **popupPane**: 700 (Leaflet default — POI click popups render here)
- **Loading overlay**: 1000 (HTML div above everything)

Chart layers render BELOW the SST overlay. SST uses semi-transparent RGBA PNGs, so chart features show through. The SST opacity slider lets users fade SST to reveal more chart detail.

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
- Toggle via "Measure" button in sidebar
- Click point A, click point B → dashed indigo line with distance label at midpoint
- Shows: nautical miles, statute miles, bearing, compass direction (e.g. "42.3 nm (48.7 mi) • 225° SW")
- Snaps to POI coordinates when clicking near a fishing spot
- Math in `map/measure.py`: haversine distance + initial bearing formula

### Performance Notes
- **dcc.Store payload**: ~7 MB (7 base64 PNGs + dates + metadata). Raw arrays stored server-side.
- **Server-side raw data**: ~18 MB in memory per date window. Falls back to disk cache on miss.
- **Disk cache**: gzip JSON, ~250-275 KB per date window. Persistent across deploys.
- **Pre-rendering PNGs server-side** with unified vmin/vmax makes frame switching instant — the clientside callback just selects a pre-built base64 URL.
- **2x upsample** (scipy zoom) is hardcoded. Higher factors showed negligible visual improvement for more processing cost.
- **AOI expansion** (39.5°N southern boundary) added ~15% more grid cells — negligible impact.
- **Cache hit path**: disk read + decompress + parse → ~1-2s. No ERDDAP call needed.
- **Cache miss path**: 7 parallel ERDDAP fetches → 30-90s. Data cached for instant future loads.

## POI Fishing Spots (20 points + 1 rectangle)
```
Original:
  Haabs Ledge          40.868, -71.838
  Butterfish Hole      40.836, -71.675
  Rachel's Whales      40.896, -71.831
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
