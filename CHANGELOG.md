# Changelog

## 2026-04-04 — Pre-Cache Optimization & Persistent Storage (v2.4)

### Added
- **Raw-only pre-cache mode** (`_precache_single_date`): Processes each day individually — serialize, free, move to next. Uses running percentiles instead of `np.concatenate`. Skips PNG rendering entirely. Peak memory drops from ~65-70 MB to ~30-35 MB per iteration.
- **On-the-fly PNG rendering**: Raw-only cache entries (`frames: null`) get PNGs rendered when a user first loads them (~2-3s). Background thread then upgrades the cache entry with PNGs for instant subsequent loads.
- **Per-date timeout**: 120s hard limit via `ThreadPoolExecutor` in pre-cache loop. ERDDAP hangs get skipped instead of blocking the worker indefinitely, preventing Render health-check failures.
- **Render Persistent Disk**: 1 GB disk at `/var/data/cache` ($0.25/GB/month). Cache survives worker restarts and deploys. Set via `SST_CACHE_DIR` env var.
- **Cache diagnostics**: `/api/precache/status` now includes `cache_dir` and `cached_files` count for verifying persistent disk setup.

### Changed
- **GFW tile loading optimized**: `updateWhenZooming=False` + `updateWhenIdle=True` + `keepBuffer=4` on GFW TileLayer. Prevents proxy request flood during zoom animations that was blocking click callbacks and causing tooltip delay.
- **GFW tile browser cache bumped to 24 hours** (from 1 hour). Historical fishing activity data is static.
- **Pre-cache default delay increased to 45s** (from 30s) to reduce ERDDAP rate limiting.
- **Pre-warm thread uses `raw_only=True`** to skip unnecessary PNG rendering on startup.

### Fixed
- **Pre-cache OOM on Render 512 MB**: Old `_build_payload` held all 7 days + PNGs + serialized arrays simultaneously (~65-70 MB peak). New `_precache_single_date` processes one day at a time (~30-35 MB peak).
- **Pre-cache losing progress on restart**: Render's ephemeral filesystem wiped cache files on every container restart. Persistent disk solves this permanently.
- **Tooltip delay after zoom**: GFW tile proxy requests flooding the single worker prevented click callbacks from responding. Zoom-aware tile loading fixes this.

---

## 2026-04-02 — Fishing Activity Overlay & Pre-Cache (v2.3)

### Added
- **Global Fishing Watch integration**: New "Fishing activity" layer in sidebar shows commercial fishing vessel activity (draggers, trawlers) as colored dots overlaid on the map. Helps tuna fishermen spot productive areas.
- **Server-side tile proxy** (`/api/gfw/`): Fetches GFW 4Wings fishing effort tiles with Bearer auth so the API token stays server-side. Requires `GFW_API_TOKEN` env var.
- **Date-synced fishing data**: GFW tile date range automatically matches the SST 7-day window. Cache-busting query param ensures tiles refresh when dates change.
- **Pre-cache endpoint** (`/api/precache`): Background worker pre-fetches and caches SST data for tuna season dates (Jun–Nov, 2020–2025). Status at `/api/precache/status`. Reduces load times from 30-50s to 2-5s for historical dates.
- **`.env` in `.gitignore`**: Prevents accidental commit of API tokens.

### Changed
- **GFW layer renders above SST** (zIndex 420, between SST at 410 and POI markers at 450) for clear visibility of fishing activity dots over temperature colors.
- **GFW tiles clipped to AOI boundary**: `bounds` on TileLayer prevents loading/rendering fishing dots outside SST coverage area. Cleaner visual + fewer proxy requests.
- **Cache MAX_ENTRIES bumped to 500** (from 200) to accommodate pre-cached historical data.

---

## 2026-03-31 — Expanded Coverage & Tuna Spots (v2.2)

### Added
- **14 new tuna fishing POIs**: 7 canyons (Hudson, Block, Toms, Lindenkohl, Spencer, Veatch, Hydrographer) and 7 banks/ledges (17 Fathom Bank, Cholera Bank, The Fingers, Stellwagen Bank, Jeffreys Ledge, Platts Bank, Cashes Ledge). Total spots: 32 points + The Dump rectangle.

### Changed
- **AOI expanded to Cape May → Portland**: Coverage now spans 38.80°N to 43.80°N (was 39.50°N to 42.80°N). Southern boundary covers Cape May, NJ and offshore canyons. Northern boundary covers Gulf of Maine up to Portland, ME — Stellwagen Bank, Jeffreys Ledge, Platts Bank, and Cashes Ledge are all within coverage.
- **Eastern boundary widened** to -68.80°W (from -68.92°W) to include Cashes Ledge.
- **Visual upsample dropped from 2x to 1x**: Native MUR 1km resolution. Reduces dcc.Store payload from ~6.5 MB to ~2.5 MB despite the larger AOI. No visible quality difference at typical zoom levels.
- **PNG compression**: Added `compress_level=9` to overlay PNG rendering for smaller payloads.
- **Map center adjusted** to [41.2, -71.5] to better frame the expanded coverage area.

---

## 2026-03-30 — Mobile-Responsive UI (v2.1)

### Added
- **Mobile-responsive CSS drawer sidebar**: Fixed slide-out drawer (280px) triggered by hamburger button. Backdrop overlay dismisses on tap. Auto-closes after data fetch. Uses clientside callback to toggle `drawer-open` class — safe pattern per Dash 4 rules.
- **Collapsible POI spot picker**: Bordered toggle row ("Spots · All ▾") expands a `dbc.Collapse` with `dbc.Checklist` and Select all / Deselect all links. Replaces the cramped multi-select `dcc.Dropdown`.
- **iPhone landscape support**: Added `(max-height: 500px)` to mobile media query so landscape phones (844px+ wide but short viewport) still use drawer mode instead of desktop sidebar.
- **iOS safe area handling**: `viewport-fit=cover` meta tag + dark navy body background (`#0a1628`) eliminates white letterboxing from notch insets.
- **Touch-friendly controls**: 44px playback buttons, 24px slider handles per Apple HIG minimum touch targets.
- **Mobile tooltip wrapping**: `white-space: normal; max-width: 200px` prevents tooltips from overflowing the viewport.
- **Sidebar header/body split** (mobile): `.sidebar-header` is non-scrolling (date picker + fetch button), `.sidebar-body` is scrollable. Calendar popup floats above both via z-index.

### Fixed
- **Lock Scale toggle not working**: Was `State` in `fetch_sst_data` — toggling didn't trigger re-fetch. Changed to `Input` so toggling re-renders PNGs with locked/adaptive scale. Also added as `Input` to loading overlay callback.
- **Calendar month picker hidden behind date grid**: Radix renders the month dropdown as a `position: fixed` portal, but CSS transforms on ancestor elements (mobile drawer, calendar popper) break fixed positioning. Fixed by forcing the popper wrapper to `position: absolute`, giving `.dash-datepicker-controls` z-index 10, and `.dash-datepicker-calendar-container` z-index 1.
- **Calendar popup clipped by map on desktop**: Leaflet panes have z-indexes up to 700 that leaked into the row's stacking context, painting over the sidebar's calendar. Fixed by adding `z-index: 1` on `.map-col` (isolates Leaflet) and `z-index: 2` on sidebar.
- **Calendar month picker text unreadable**: CSS was scoped to `.gotone-sidebar` but Radix portal renders outside that context. Re-scoped to `.dash-datepicker-controls .dash-dropdown-option`.
- **Slider tooltip white-on-white**: Inherited sidebar's light text color. Fixed with `.gotone-sidebar .dash-slider-tooltip { color: #0a1628 }`.
- **Date picker text too light**: Changed `font-weight` from 400 to 600.

### Changed
- **POI picker UX**: `dcc.Dropdown` (multi-select) → collapsible `dbc.Checklist` with chevron toggle (▾/▴).
- **Lock Scale moved to Layers section** (from Map Tools). More logical grouping with other display options.
- **Sidebar overflow**: Changed from `overflow-y: auto` to `overflow: visible` (desktop) so calendar popup extends beyond sidebar width. Mobile uses flex header/scroll body pattern instead.
- **POI count format**: Shows "All" when all spots selected, otherwise "n/total" (e.g., "15/20").
- **Body background**: Set to `#0a1628` (dark navy) to match app chrome and prevent white flashing.

---

## 2026-03-29 — v2.0 Release

Full feature release combining nautical chart/bathymetry layers, SST opacity control, UI polish, loading spinner fix, and Squarespace integration.

### Added
- **NOAA ENC nautical chart layer**: Toggleable WMS overlay showing depth contour lines, soundings, navigation aids, and chart features. Source: NOAA Maritime Chart Service.
- **GEBCO bathymetry layer**: Toggleable WMS overlay showing ocean depth shading (continental shelf, canyons, underwater terrain). Source: GEBCO 2024 grid.
- **SST opacity slider**: Adjustable SST overlay transparency (0.1–1.0) so users can fade SST colors to reveal chart features underneath.
- **Layers sidebar section**: New "Layers" section with on/off toggles for Nautical chart / Bathymetry and SST opacity slider.
- **Custom domain**: App served at `sst.gotoneapp.com` via CNAME to Render. SSL auto-provisioned.
- **Squarespace embed**: iframe on `gotoneapp.com/offshore-sst` loads the full SST analyzer inline on the GotOne website.
- **iframe security headers**: `Content-Security-Policy: frame-ancestors` allows embedding from `gotoneapp.com` and related domains.

### Fixed
- **Loading spinner not showing on Render**: Converted `show_loading_on_fetch` from server-side to clientside callback. With 1 gunicorn worker, the server-side version could be blocked behind the slow ERDDAP fetch, leaving users with no visual feedback for 30-90s.
- **Click callbacks returning no data on Render**: Memory-first cache pattern + base64 numpy serialization + background thread disk writes.

### Changed
- **POI markers restyled**: Solid white fill with dark navy border — visible against any background (SST, charts, bathymetry). Replaces hollow slate-gray rings.
- **Z-index restructure**: Chart layers render below SST (GEBCO z=390, NOAA z=400, SST z=410). SST opacity slider controls visibility.
- **Sidebar spacing**: Increased horizontal padding, wider section divider margins, more space above section labels.
- **POI rename**: "July2025" → "Rachel's Whales"

---

## 2026-03-29 — Nautical Chart & Bathymetry Layers (v1.2)

### Added
- **NOAA ENC nautical chart layer**: Toggleable WMS overlay showing depth contour lines, soundings, navigation aids, and chart features. Source: NOAA Maritime Chart Service.
- **GEBCO bathymetry layer**: Toggleable WMS overlay showing ocean depth shading (continental shelf, canyons, underwater terrain). Source: GEBCO 2024 grid.
- **SST opacity slider**: Adjustable SST overlay transparency (0.1–1.0) so users can fade SST colors to reveal chart features underneath.
- **Layers sidebar section**: New "Layers" section with checkboxes for Nautical chart / Bathymetry and the SST opacity slider.

### Changed
- **POI markers restyled**: Solid white fill with dark navy border (`#0a1628`) — pops against any background (SST, charts, bathymetry). Replaces previous hollow slate-gray rings that were invisible over busy map layers.
- **Z-index restructure**: Chart layers render below SST overlay (GEBCO z=390, NOAA z=400, SST z=410). SST opacity slider controls how much chart detail shows through.
- **Sidebar spacing**: Increased horizontal padding (`1rem 1.25rem`), wider section divider margins (`1rem`), and more space above section labels for better readability.
- **POI rename**: "July2025" → "Rachel's Whales"

## 2026-03-29 — Custom Domain & Squarespace Integration

### Added
- **Custom domain**: App now served at `sst.gotoneapp.com` via CNAME to Render. SSL auto-provisioned.
- **Squarespace embed**: iframe on `gotoneapp.com/offshore-sst` loads the full SST analyzer inline on the GotOne website.
- **iframe security headers**: `Content-Security-Policy: frame-ancestors` allows embedding from `gotoneapp.com` and related domains. Set via Flask `after_request` hook in `app.py`.

## 2026-03-29 — Disk Cache & Render Performance Fix (v1.1)

### Added
- **Disk-based SST cache** (`data/cache.py`): Gzip-compressed JSON files (~500 KB–1 MB each) keyed by `{end_date}_{adaptive|locked}`. Cached dates return in ~50 ms instead of 30–90s ERDDAP fetches.
- **Cache invalidation**: Dates >3 days old cached permanently (MUR data is finalized). Recent dates re-fetched if cache >12 hours old. LRU eviction at 200 entries.
- **Two-part storage architecture**: Browser receives only PNG frames + metadata (~7 MB via dcc.Store). Raw float arrays (~18 MB) stored server-side in `_raw_data_cache` dict with disk cache fallback.
- **Pre-warm on startup**: Daemon thread loads current week from disk cache (no ERDDAP) so first visitor gets instant results if previously cached.
- **Disk cache fallback for click readings**: If server memory cache misses (restart, eviction), raw data is rebuilt from disk cache automatically.

### Fixed
- **Render 502 errors**: Original ~26 MB payload (raw arrays + PNGs + metadata in single dcc.Store) exceeded Render's proxy limits. Splitting into browser-side PNGs and server-side raw data resolved this.
- **Browser callback timeout**: Cached dates now return instantly. Cache misses still take 30–90s but data gets cached for subsequent requests.
- **Deploy hanging on Render**: Pre-warm thread was doing heavy ERDDAP fetches during startup, starving gunicorn so health checks failed. Changed to disk-cache-only pre-warm with 15s delay.
- **Inconsistent POI/SST click readings**: Caused by `--workers 2` in Render start command (worker 2 had empty memory cache). Fixed by enforcing 1 worker + adding disk cache fallback in `_get_raw_data()`.
- **Overlay not rendering after fetch**: Clientside callback fired before dcc.Store data propagated. Fixed by having server callback set initial overlay, with clientside callback only handling frame changes.
- **Click callbacks returning no data on Render**: `_raw_data_cache` was populated AFTER the disk cache write. If the disk write OOMed (`.tolist()` triples memory), the worker crashed before the memory cache was set. Fixed by populating memory cache FIRST, using memory-efficient base64 numpy serialization, and writing disk cache in a background thread.

### Changed
- **Removed Dash background callbacks**: `background=True` with `DiskcacheManager` caused 502s on Render due to large polling responses. Reverted to synchronous callbacks with separate loading-overlay callback for UI feedback.
- **Render deployment**: Env var `SST_CACHE_DIR` configures cache directory path. Persistent disk added in v2.4.
- **Disk cache serialization**: Switched from `.tolist()` + JSON (3x memory overhead) to base64-encoded numpy `.npy` format (1.3x overhead). Backward-compatible with old v1 caches. Disk write now runs in a background thread.

### Removed
- `diskcache` dependency (was used for `DiskcacheManager`, no longer needed)

## 2026-03-23 — Production Deployment (v1.0)

### Added
- **Render deployment**: App live at https://offshore-sst-map.onrender.com
- Render Starter tier ($7/month): 0.5 CPU, 512MB RAM, always-on
- Gunicorn Procfile tuned for Render (1 worker, 180s timeout)

### Fixed
- Removed invalid `callback_timeout` config (not supported in Dash 4)

## 2026-03-23 — Sidebar Polish

### Changed
- **Sidebar grouped into 3 sections** (Data, Playback, Map Tools) with visible dividers (#334155) and muted uppercase labels.
- **Fetch SST button** is now brand blue (#0183fe) with white text — clearly the primary CTA. Lighter date picker text (weight 400) so it doesn't compete.
- **Section spacing** increased for clearer visual separation between control groups.
- **Gitignore**: Added `Branding:Logo/` to prevent source logo files from being committed.

## 2026-03-22 — Phase 2: GotOne Branding

### Added
- **GotOne branding**: Dark navy header and sidebar (#0a1628) with white fish logotype.
- **Brand blue (#0183fe)** throughout: slider track, spinner, measure accents, status text.
- **Custom CSS** (`assets/gotone.css`): Full styling overrides for Dash 4 component class names (date picker, dropdown, calendar popup, slider).

### Changed
- **White pill-shaped buttons** on dark sidebar (matches GotOne CTA style: white bg, dark text).
- **Dark text on white inputs**: Date picker, POI dropdown, and calendar popup all legible against dark sidebar.
- **Larger header**: 72px with 56px logotype for better brand presence.
- **Title**: "GotOne Offshore SST Analyzer" in browser tab.

## 2026-03-22 — Measure Tool & Unified Click System

### Added
- **Ruler/measure tool**: Two-click distance measurement with heading. Shows nautical miles, statute miles, bearing, and compass direction (e.g. "42.3 nm (48.7 mi) • 225° SW"). Measure button in sidebar toggles mode. Snaps to POI coordinates when clicking near a fishing spot.

### Changed
- **Unified click interactions**: All map clicks (SST reading, POI info, measure) route through one callback and render in the same click-marker layer. Only one tooltip visible at a time — no more overlapping popups.
- **POI markers are visual-only**: No more `dl.Popup` children. Click detection uses proximity threshold (~5 nm) via `find_nearest_poi()`. Clicks bubble through to the map for unified handling.
- **Play/Pause button**: Plain text labels instead of unicode icons (which rendered inconsistently across browsers).

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
