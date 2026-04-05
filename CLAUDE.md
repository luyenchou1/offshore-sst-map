# GotOne Offshore SST Analyzer — Project Context

## What This Is
Dash 4.0 web app ("GotOne Offshore SST Analyzer") branded for [gotoneapp.com](https://www.gotoneapp.com). Shows daily sea-surface temperatures for the Cape May, NJ to Portland, ME offshore corridor. Users pick any 7-day date window (back to June 2002) and animate through the week's SST evolution to spot fishing-relevant trends (warm eddies, cold upwelling, thermoclines).

## Tech Stack
- **Current version**: v1.0 (displayed in sidebar UI; next release: v1.01)
- **Dash 4.0** + **dash-leaflet** + **dash-bootstrap-components** (Flatly theme + GotOne CSS overrides)
- **NOAA CoastWatch ERDDAP** — MUR GHRSST (1km, preferred) with OISST (25km) fallback
- Data available from 2002-06-01 to ~2 days ago (MUR has ~2-day latency)
- Python 3.12, venv at `.venv/`
- Dev server: `python app.py` (port 8050)
- Production: **Render Starter** ($7/month) at https://offshore-sst-map.onrender.com
  - Custom domain: `sst.gotoneapp.com` (CNAME → Render, SSL auto-provisioned)
  - Start command: `gunicorn app:server --bind 0.0.0.0:$PORT --workers 1 --threads 16 --timeout 180`
  - Must be 1 worker (raw data cache is in-memory per process)
  - 16 threads allows concurrent I/O (GFW tile proxy, click callbacks) without separate processes
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

GFW Fishing Activity (separate data path):
  fetch_sst_data sets _gfw_date_range → updates gfw-layer URL with ?dr= param
  → Leaflet requests /api/gfw/{z}/{x}/{y}.png?dr=START,END
  → Flask proxy calls GFW generate-png (once per date range, cached)
  → Fetches styled tile with Bearer auth → returns PNG to browser
  → Tiles cached 24hr (Cache-Control: public, max-age=86400)
  → updateWhenZooming=False prevents proxy flood during zoom

Pre-cache (memory-efficient raw-only mode):
  /api/precache → _run_precache thread → for each date:
    get_sst_multiday() → _precache_single_date() (one day at a time):
      orient + mask → running percentiles (no np.concatenate)
      → serialize array → free → next day
    → put_cache() with frames=None (no PNG rendering)
    → 120s hard timeout per date via ThreadPoolExecutor
  On user load of raw-only cache entry:
    _build_payload_from_disk_cache() renders PNGs on-the-fly (~2-3s)
    → background thread upgrades cache with PNGs for instant future loads
```

### Key Files
| File | Purpose |
|------|---------|
| `app.py` | Main app, all callbacks, layout, server-side raw data cache |
| `data/erddap.py` | ERDDAP search, fetch, multi-day parallel download |
| `data/geo.py` | Orient arrays, AOI mask, land mask (all 2D only) |
| `data/convert.py` | Kelvin→Fahrenheit, 2x visual upsample |
| `data/cache.py` | Disk-based gzip JSON cache with staleness checks and fuzzy lookup |
| `layout/sidebar.py` | Date picker, animation controls, collapsible POI checklist, fetch button, version indicator |
| `layout/mapview.py` | dl.Map with custom panes, loading overlay, hamburger button |
| `map/overlay.py` | Array → RGBA → base64 PNG |
| `map/colorscale.py` | Legend component, adaptive color bounds |
| `map/pois.py` | 32 fishing spots + The Dump rectangle, multi-select picker |
| `map/measure.py` | Haversine distance, initial bearing, compass direction |
| `config.json` | AOI polygon, ERDDAP servers, search terms |
| `assets/gotone.css` | GotOne brand CSS overrides (colors, dark sidebar, inputs, mobile responsive) |
| `assets/tooltips.css` | Tooltip card styles + mobile wrapping |
| `assets/gotone-logo.png` | White fish logotype on transparent background |

### Callback Structure
1. **show_loading_on_fetch** — **Clientside** callback on button click / auto-fetch, shows loading overlay immediately (must be clientside — with 1 gunicorn worker, a server-side version gets blocked behind the slow fetch callback)
2. **fetch_sst_data** — Synchronous: fetches 7 days (or reads disk cache), processes, pre-renders PNGs, stores raw data server-side (memory-first, then background disk write). `lock-scale` is an **Input** (not State) so toggling triggers re-fetch. Also auto-closes mobile drawer.
3. **render_static_layers** — Sets initial overlay PNG + bounds, POIs, legend, hides loading overlay. Fires on POI picker changes.
4. **clientside: swap overlay** — Swaps overlay PNG by frame index (sst-store as State, not Input)
5. **clientside: play/pause** — Toggles dcc.Interval
6. **clientside: auto-advance** — Increments frame slider on interval tick (wraps 6→0)
7. **clientside: step frame** — Step forward/back buttons
8. **clientside: day indicator** — Shows "Mar 15, 2026 (Day 3 of 7)" from `dates[]` array
9. **handle_map_click** — Routes clicks: POI info (reads server-side cache), SST reading (sets click-pos), or measure tool
10. **render_click_marker** — Shows temp reading at clicked position (reads server-side cache)
11. **toggle_measure** — Activates/deactivates measure mode
12. **update_poi_count** — Shows "All" or "n/total" count next to "Spots" label
13. **toggle_layers** — Shows/hides NOAA ENC, GEBCO, and GFW fishing activity layers based on sidebar checklist
14. **update_sst_opacity** — Adjusts SST overlay opacity from sidebar slider
15. **clientside: drawer toggle** — Opens/closes mobile sidebar drawer + backdrop (toggles `drawer-open` class)
16. **clientside: POI collapse toggle** — Expands/collapses POI checklist, flips chevron (▾/▴)
17. **poi_select_all** — Server callback for Select all / Deselect all links in POI checklist

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

### Mobile Responsive Layout
- **CSS drawer pattern** chosen over `dbc.Collapse` (stacked, wastes vertical space) and `dbc.Offcanvas` (requires duplicate component IDs which Dash prohibits). The drawer transforms the existing `dbc.Col` sidebar via CSS `position: fixed` + a `drawer-open` class toggled by a clientside callback.
- **Media query**: `@media (max-width: 767.98px), (max-height: 500px)` — the `max-height` clause catches landscape phones (e.g. iPhone 844px wide but only ~390px tall) without affecting tablets or desktops.
- **Clientside callback for drawer toggle** is safe — it returns a className string, not DOM manipulation. Dash's virtual DOM sees the change. The backdrop is a sibling div toggled in the same callback.
- **Auto-close drawer after fetch**: `fetch_sst_data` has `Output("sidebar-col", "className", allow_duplicate=True)` to reset the drawer class after data loads.
- **Sidebar split on mobile**: `.sidebar-header` (date picker, fetch button) is `flex-shrink: 0` with `position: relative; z-index: 10`. `.sidebar-body` (playback, spots, layers) is `flex: 1; overflow-y: auto`. This lets the calendar popup float above the scrollable body.
- **iOS safe areas**: `viewport-fit=cover` meta tag in `app.py` + `body { background-color: #0a1628 }` eliminates white letterboxing from notch insets in landscape mode.
- **Hamburger button**: Positioned `top: 10px; right: 10px; z-index: 1100` on the map (above Leaflet zoom controls at z-index 1000).

### Radix / Dash 4 Calendar Stacking Context Gotchas
- **Radix renders the month dropdown as a `position: fixed` portal** inside the calendar popup. CSS transforms on ancestor elements (mobile drawer's `translate`, calendar popper's `translate`) create new containing blocks that break `position: fixed`, causing the portal to render at 0×0.
- **Fix**: Force the month dropdown's Radix popper wrapper to `position: absolute` with `[data-radix-popper-content-wrapper]:has(.dash-dropdown-content)`. Give `.dash-datepicker-controls` `z-index: 10` and `.dash-datepicker-calendar-container` `z-index: 1` so the dropdown paints above the date grid.
- **`.dash-datepicker-content` must have `overflow: visible`** so the month dropdown can escape the calendar popup's bounds.
- **Sidebar z-index: 2, map column z-index: 1**: Leaflet panes have z-indexes up to 700 (popupPane). Without isolating them in the map column's stacking context, they leak into the row and paint over the sidebar's calendar popup.
- **Sidebar `overflow: visible`** (desktop): Changed from `overflow-y: auto` so the calendar popup can extend beyond the sidebar's width. On mobile, the drawer uses the flex header/body split instead.

### Background Callbacks (`background=True`) — Do NOT Use on Render
- **Dash background callbacks use DiskcacheManager** to poll for results. The full callback return value is serialized and delivered via a polling HTTP response.
- **Render's proxy returns 502** when the polling response exceeds ~7-10 MB. Our original payload (~26 MB with raw arrays) caused consistent 502s.
- **Even with reduced payload**, background callbacks add complexity (diskcache dependency, forked processes) for marginal benefit on a single-worker setup.
- **Solution**: Use synchronous callbacks. Keep the payload under ~7 MB by splitting raw arrays to server-side memory. The disk cache ensures most fetches return in <2 seconds.

### Payload Size Management
- **dcc.Store payload must stay under ~7 MB** for reliable delivery on Render.
- **Raw float arrays are the biggest culprit**: 7 days × ~93K floats × ~8 bytes/float = ~5 MB as numpy. These MUST be kept server-side.
- **Store payload contents**: 7 base64 PNG frames (~6.5 MB) + dates + bounds + metadata = ~7 MB total.
- **Server-side `_raw_data_cache`**: In-memory dict keyed by `"{end_date}_{mode}"`. Stores numpy arrays for click-to-read-temp lookups. **Limited to 2 entries** (~25 MB each) via `_put_raw_cache()` to stay within Render's 512 MB. Oldest entry evicted when limit exceeded. **Populated BEFORE disk write** so clicks work even if disk write fails. Falls back to disk cache on miss (handles restarts).
- **Disk cache** (`data/cache.py`): Stores the FULL payload with raw arrays as **base64-encoded numpy `.npy`** format (v2) instead of JSON lists (v1). Written in a **background thread** to avoid blocking the callback response. Backward-compatible: reads both v1 and v2 formats.

### Memory Management on Render (512 MB)
- **Never use `.tolist()` for disk cache serialization.** Converting numpy float64 arrays to Python lists triples memory usage (8 bytes/float → ~28 bytes as Python object). On Render's 512 MB, this can OOM the worker.
- **Use `_serialize_array()` / `_deserialize_array()`** — base64-encoded numpy `.npy` format. Only 1.3x memory overhead vs 3x+ for `.tolist()`.
- **Always populate `_raw_data_cache` before disk write.** If the disk write OOMs or crashes the worker, the memory cache is already set and clicks work. The callback response is also faster since the disk write happens asynchronously.
- **Disk write in background thread** — `threading.Thread(target=_write_disk_cache, daemon=True)`. Non-blocking, so the fetch callback returns immediately to the browser.
- **Pre-cache uses raw-only mode** (`_precache_single_date`): Processes one day at a time, serializes immediately, frees the numpy array before moving to the next. Running percentiles instead of `np.concatenate` avoids the 16 MB temporary allocation. Skips PNG rendering entirely. Peak memory ~30-35 MB per iteration vs 65-70 MB with `_build_payload`.
- **Per-date timeout**: 120s hard limit via `ThreadPoolExecutor` prevents ERDDAP hangs from blocking the worker. Timed-out dates are skipped and logged as errors.

### Global Fishing Watch Integration
- **GFW 4Wings API**: Two-step process — POST to `generate-png` to get a styled tile URL template with color ramp, then GET individual tiles at `{z}/{x}/{y}` using that template.
- **Server-side tile proxy** (`/api/gfw/<z>/<x>/<y>.png`): Flask route proxies GFW tile requests with Bearer auth so the `GFW_API_TOKEN` stays server-side (Leaflet can't add custom auth headers to tile requests).
- **SSL domain fix**: GFW's `generate-png` returns URLs pointing to `gateway.api.prod.globalfishingwatch.org` which causes `SSLEOFError` in Python. Must replace with `gateway.api.globalfishingwatch.org`.
- **Date range sync**: GFW tiles use the same 7-day window as SST data. `fetch_sst_data` callback updates `_gfw_date_range` and sets a `?dr=` cache-busting query param on the tile URL so Leaflet re-fetches tiles when dates change.
- **Style cache**: `_gfw_style_cache` stores the URL template per date range. Only calls `generate-png` once per date range; subsequent tile fetches reuse the cached template.
- **Graceful degradation**: If `GFW_API_TOKEN` env var is not set, proxy returns 204 (no content) and tiles are transparent. The checkbox still appears but does nothing.
- **Layer z-index**: 420 (above SST at 410, below POI markers at 450) so fishing dots render on top of temperature colors.
- **Token**: JWT with 10-year expiry. Set as `GFW_API_TOKEN` env var on Render and locally.
- **AOI bounds clipping**: `bounds=[[38.80, -74.96], [43.80, -68.80]]` on the TileLayer prevents tiles from loading outside the SST coverage area. Reduces proxy requests and eliminates visual clutter.
- **Zoom-aware tile loading**: `updateWhenZooming=False` + `updateWhenIdle=True` prevents GFW tile requests from flooding the single worker during zoom animations. `keepBuffer=4` retains more off-screen tiles to reduce re-fetches on pan-back.
- **Server-side tile cache** (`_gfw_tile_cache`): In-memory dict caches both 200 and 404 responses keyed by `(z, x, y, date_range)`. ~Half of tile requests return 404 from GFW (empty ocean areas), each saving a ~1s round-trip. Cache cleared on date range change. Max 200 entries with simple eviction. Memory: ~5-10 KB per tile × 200 = ~2-4 MB max.
- **Browser cache**: `Cache-Control: max-age=86400` (24 hours). Historical GFW data is static, so aggressive caching is safe. Tiles for different date ranges use different proxy URLs (via `?dr=` param).

### Pre-Cache System
- **Endpoint**: `GET /api/precache` triggers background fetching of historical SST data for tuna season dates (Jun–Nov, weekly intervals, 2020–2025).
- **Status**: `GET /api/precache/status` returns JSON with `running`, `done`, `total`, `errors`, `cache_dir`, `cached_files`.
- **Raw-only mode**: Pre-cache uses `_precache_single_date()` which skips PNG rendering entirely. Processes each day individually (serialize → free → next), uses running percentiles instead of `np.concatenate`. Peak memory ~30-35 MB per iteration (down from 65-70 MB with `_build_payload`).
- **On-the-fly PNG rendering**: When a user loads a raw-only cache entry (`frames: None`), `_build_payload_from_disk_cache()` renders PNGs on-the-fly (~2-3s). A background thread then upgrades the cache entry with PNGs so subsequent loads are instant.
- **Per-date timeout**: 120s hard timeout via `ThreadPoolExecutor`. If ERDDAP hangs, the date is skipped and logged as an error instead of blocking the worker indefinitely. Prevents Render health-check failures.
- **Query params**: `start_year`, `end_year`, `months` (comma-sep), `interval` (days between dates, default 7), `delay` (seconds between fetches, default 45).
- **Skips already-cached dates**: Checks disk cache before fetching. Safe to re-run after interruptions or worker restarts.
- **Cache persistence**: Render Persistent Disk ($0.25/GB/month) at `/var/data/cache`. Set via `SST_CACHE_DIR` env var. Cache survives worker restarts and deploys. Without persistent disk, Render's ephemeral filesystem wipes cache on every container restart.
- **MAX_ENTRIES**: 500 in `data/cache.py` to accommodate pre-cached data.
- **Timing**: ~1 date per 90 seconds (45s fetch + 45s delay on Render). Full 180 dates takes ~4-5 hours.
- **Idempotent**: Safe to call `/api/precache` multiple times. Already-cached dates are skipped. If the worker restarts mid-run, just hit the endpoint again to resume.
- **`raw_only` parameter on `_build_payload_from_disk_cache()`**: When `True`, skips PNG rendering entirely (returns `frames=[]`). Used by `_get_raw_data()` for click-lookup paths and pre-warm thread, where only raw arrays are needed.

### Render Deployment
- **Must use 1 gunicorn worker.** The `_raw_data_cache` is per-process. With multiple workers, a click request may be served by a worker that doesn't have the data. The disk cache fallback mitigates this, but 1 worker is the intended config.
- **16 threads per worker** (`--threads 16`): GFW tile proxy and click callbacks are I/O-bound. With threads, `requests.get()` releases the GIL during network I/O, so tile fetches and click lookups run concurrently. After zoom, ~48 GFW tile requests flood the proxy — with 16 threads, most run concurrently (3 rounds vs 12 with 4 threads), leaving threads free for click callbacks. Python's GIL keeps dict operations on shared state (`_raw_data_cache`, `_gfw_style_cache`, `_gfw_tile_cache`) atomic. Memory overhead is minimal (~50-100KB RSS per thread for I/O-bound work).
- **Start command must be set in Render Settings** (not blank — Render requires a value). Use: `gunicorn app:server --bind 0.0.0.0:$PORT --workers 1 --threads 16 --timeout 180`
- **Auto-deploy is currently OFF.** Use Manual Deploy after pushing to `main`.
- **Pre-warm thread** runs 15s after startup. Only loads from disk cache (no ERDDAP, uses `raw_only=True` to skip PNG rendering). Heavy ERDDAP fetches during startup starve the gunicorn worker and cause Render's health check to fail, hanging the deploy indefinitely.
- **Persistent Disk**: 1 GB disk mounted at `/var/data/cache`. Set `SST_CACHE_DIR=/var/data/cache` in Render Environment. Cache survives worker restarts and deploys. Without this, Render's ephemeral filesystem wipes cache on every container restart — the pre-cache system is useless without persistent storage.
- **`GFW_API_TOKEN` env var** must be set in Render Environment for fishing activity layer. Without it, the layer degrades gracefully (transparent tiles).
- **Health check failures**: Caused by ERDDAP stalls blocking the single worker. The pre-cache 120s timeout and GFW tile `updateWhenZooming=False` mitigate this. If the worker restarts, persistent disk preserves cache files.

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
- **gfw-pane**: 420 (custom — GFW fishing activity tiles, above SST for visibility)
- **poi-pane**: 450 (custom — above SST, below tooltips)
- **click-pane**: 500 (custom — above POIs, below tooltips)
- **tooltipPane**: 650 (Leaflet default — tooltips render here)
- **popupPane**: 700 (Leaflet default — POI click popups render here)
- **Loading overlay**: 1000 (HTML div above everything)

Chart layers render BELOW the SST overlay. SST uses semi-transparent RGBA PNGs, so chart features show through. The SST opacity slider lets users fade SST to reveal more chart detail.

If POI markers and tooltipPane have the same z-index, the markers render ON TOP of their own tooltips. The fix: put markers in custom panes below 650.

**Stacking context isolation**: `.map-col` has `z-index: 1` and `.gotone-sidebar` has `z-index: 2`. This isolates Leaflet's internal z-indexes (up to 700) within the map column so they don't compete with the sidebar's calendar popup. Without this, the calendar renders behind the map.

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
- **dcc.Store payload**: ~2.5 MB (7 base64 PNGs at 1x native resolution + dates + metadata). Raw arrays stored server-side.
- **Server-side raw data**: ~25 MB in memory per date window. Falls back to disk cache on miss.
- **Disk cache**: gzip JSON, ~200 KB per date window. Persistent across deploys.
- **Pre-rendering PNGs server-side** with unified vmin/vmax makes frame switching instant — the clientside callback just selects a pre-built base64 URL.
- **1x native upsample**: Dropped from 2x to 1x when AOI expanded — reduces payload by ~62% with no visible quality difference at typical zoom levels. The `upsample_visual()` call with factor 1 returns the array unchanged.
- **AOI coverage**: Cape May, NJ (38.80°N) to Portland, ME (43.80°N), ~501×617 grid at MUR 1km. Bounding box: 5.00° lat × 6.16° lon.
- **Cache hit path**: disk read + decompress + parse → ~2-5s on Render. No ERDDAP call needed.
- **Fuzzy cache lookup**: `find_nearest_cached()` in `data/cache.py` checks ±3 days around the requested end date. With pre-cached entries every 7 days, most tuna-season dates hit a nearby cache within 3 days — avoiding ERDDAP entirely. Status shows "(nearest cache: +1d)" when offset. The `data_key` in `sst-store` reflects the actual cached date so click-to-read-temp works correctly.
- **Cache miss path**: 7 parallel ERDDAP fetches → 20-50s on Render (cloud-to-cloud), 60-120s+ on localhost (residential internet). Data cached for instant future loads.
- **Localhost timeout issue**: Expanded AOI ERDDAP fetches often exceed Dash's ~30s browser callback timeout, causing "server did not respond" and a stuck loading overlay. Data may still finish fetching server-side. On Render, faster network usually completes within timeout.
- **AOI change invalidates cache**: Disk-cached data has bounding box baked in. After AOI polygon changes, old caches will have wrong bounds — first fetch after change will be a cache miss.
- **Pre-cache strategy**: `/api/precache` endpoint populates disk cache for tuna season dates. Once cached, 2020–2025 Jun–Nov dates load in 2-5s instead of 30-50s. Raw-only cache entries add ~2-3s for on-the-fly PNG rendering on first user load; background thread upgrades to full cache (with PNGs) for instant subsequent loads.
- **GFW tile proxy blocking**: On zoom, Leaflet requests ~48 GFW tiles through the server-side proxy, each taking ~1s (GFW API latency). Fix: `--threads 16` allows concurrent I/O (tile fetches release the GIL), and server-side tile cache (`_gfw_tile_cache`) makes repeat requests instant. `updateWhenZooming=False` + `updateWhenIdle=True` + `keepBuffer=4` further reduce tile requests. First zoom to a new area still has ~3s of proxy latency (48 tiles / 16 threads); subsequent zooms to the same area are instant from cache.

## POI Fishing Spots (32 points + 1 rectangle)
```
Original (7):
  Haabs Ledge          40.868, -71.838
  Butterfish Hole      40.836, -71.675
  Rachel's Whales      40.896, -71.831
  CIA                  40.933, -71.717
  Gully                41.020, -71.417
  Wind Farm SW Corner  40.974, -71.273
  Tuna Ridge           40.917, -71.279

Named spots (11, source: marinebasin.com):
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

Canyons (7, shelf edge — major tuna grounds):
  Hudson Canyon        39.540, -72.050
  Block Canyon         39.730, -71.750
  Toms Canyon          39.500, -72.600
  Lindenkohl Canyon    39.450, -72.350
  Spencer Canyon       39.100, -73.150
  Veatch Canyon        40.050, -69.550
  Hydrographer Canyon  40.100, -69.300

Banks & ledges (7, tuna staging/feeding areas):
  17 Fathom Bank       39.650, -73.100
  Cholera Bank         40.050, -73.200
  The Fingers          40.700, -70.600
  Stellwagen Bank      42.350, -70.350
  Jeffreys Ledge       42.850, -70.100
  Platts Bank          43.200, -69.700
  Cashes Ledge         42.900, -69.000

The Dump (source: saltycape.com):
  Rectangle: 40.667-40.833°N, 70.750-70.996°W
```

## Running Locally
```bash
cd "Offshore Trip Planner"
source .venv/bin/activate
export GFW_API_TOKEN="your-token-here"  # optional, for fishing activity layer
python app.py
# → http://localhost:8050
```

## Environment Variables
| Variable | Required | Purpose |
|----------|----------|---------|
| `GFW_API_TOKEN` | No | Global Fishing Watch API token (JWT). Enables fishing activity overlay. Without it, layer degrades gracefully (transparent tiles). |
| `SST_CACHE_DIR` | Yes (Render) | Cache directory path. Set to `/var/data/cache` on Render (persistent disk mount). Default `./cache` for local dev. |
| `PORT` | Render only | Set automatically by Render for gunicorn binding. |
