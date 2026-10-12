# GotOne Offshore SST Analyzer — Project Context

## What This Is
**GotOne Labs** (labs.gotoneapp.com): free, experimental tools for saltwater anglers from [gotoneapp.com](https://www.gotoneapp.com), one Dash 4.0 app. The home page has a tile per tool; the tools are the **Offshore SST** map (`/sst`) and **Currents** (`/currents`). The SST map shows daily sea-surface temperatures for the Cape May, NJ to Portland, ME offshore corridor. Users pick any 7-day date window (back to June 2002) and animate through the week's SST evolution to spot fishing-relevant trends (warm eddies, cold upwelling, thermoclines).

## Tech Stack
- **Current version**: v1.0 (displayed in sidebar UI; next release: v1.01)
- **Dash 4.0** + **dash-leaflet** + **dash-bootstrap-components** (Flatly theme + GotOne CSS overrides)
- **Basemap**: Esri World Light Gray Canvas — keyless raster tiles, `{z}/{y}/{x}` scheme. Replaced CARTO Positron (`basemaps.cartocdn.com/light_all`), which began requiring an API key and served "API KEY REQUIRED" watermarks. Defined in `layout/mapview.py` (SST map) and `layout/catchview.py` (catch map). `/currents` uses the dark counterpart (`World_Dark_Gray_Base` + `World_Dark_Gray_Reference` labels), also keyless.
- **Pages** (one Dash app; hand-written router `route_page` in `app.py`): `/` GotOne Labs home (tiles; `layout/labsview.py`) · `/sst` SST map · `/currents` tidal currents — Today / Tide cycle / Example weeks, plus estimated currents in six harbor inlets · `/catches` catch map (gated by `CATCHES_ACCESS_KEY`, no public link). Both tools link back with "← GotOne Labs". **On the old host `sst.gotoneapp.com`, `/` is still the SST map** (the Squarespace embed points there) and the Labs home is `/labs`; set `LABS_HOST` to redirect it (see Custom Domain).
- **Visits**: an anonymous counter for Labs / SST / Currents (`data/labstats.py`, `assets/labs.js`); private stats at `/labs/stats?key=$LABS_STATS_KEY`.
- **NOAA CoastWatch ERDDAP** — MUR GHRSST (1km, preferred) with OISST (25km) fallback
- Data available from 2002-06-01 to ~2 days ago (MUR has ~2-day latency)
- Python 3.12, venv at `.venv/`
- Dev server: `python app.py` (port 8050)
- Production: **Render Starter** ($7/month) at https://offshore-sst-map.onrender.com
  - Custom domain: `sst.gotoneapp.com` (CNAME → Render, SSL auto-provisioned)
  - Start command: `gunicorn app:server --bind 0.0.0.0:$PORT --workers 1 --threads 16 --timeout 180`
  - Must be 1 worker (raw data cache is in-memory per process)
  - 16 threads allows concurrent I/O (GFW tile proxy, click callbacks) without separate processes
  - Auto-deploy is ON — every push to `main` goes live in ~2–3 min (observed Oct 2026). Put `[skip render]` in a commit message to skip that deploy
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

### SST Data Sourcing — cache first, seeded off-Render as insurance

**History:** from June 2026 NOAA CoastWatch ERDDAP blocked Render's datacenter IP at the network layer (`Network is unreachable`, `Connection reset by peer`, connect timeouts; `coastwatch.noaa.gov` additionally tarpits with `ReadTimeout` from every IP), so cache misses on Render hung the full 90s and showed the red "SST fetch timed out after 90s" error. **As of 2026-10-10 the block is lifted** — Render fetched an uncached Oct 8 window live in ~10 s. The off-Render seeding stays as insurance in case it returns.

- **Render (production)** serves from its disk cache first; on a miss it fetches ERDDAP itself via `get_sst_multiday()` (works again).
- **Off-Render seeder**: a daily GitHub Action (`.github/workflows/seed-sst-cache.yml`) runs `tools/sync_cache.py`, which fetches recent 7-day windows from ERDDAP and POSTs raw-only cache files to `POST /api/cache/upload` (auth: `CACHE_UPLOAD_TOKEN` env var + GitHub repo secret). Seeds end-dates today-2..today-11; the ±3-day fuzzy lookup covers the rest. Uploads retry on transient Render 502s. **GitHub silently disabled this job for inactivity on 2026-08-30**; `.github/workflows/keepalive.yml` now prevents that (see Lessons).
- **localhost (dev)** fetches ERDDAP live, exactly as the Data Flow below describes.

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

### Currents Page (`/currents`)
Animated tidal-current flow for eastern Long Island (Little Peconic → east of Block Island; south of Montauk → Watch Hill / Fishers), with a time-synced tide + current chart. Built to show why currents don't follow the tide table — e.g. at The Race slack comes ~2h40m *after* Montauk high/low water, at Plum Gut ~1h35m after.

Three views (sidebar "View" control; a "How it works" modal opens on a first visit):
- **Today** (default): typical currents for any day 2024–2030 from that day's real Montauk tide times and ranges — the **tide-stage atlas** evaluated client-side from Montauk high/lows bundled in the atlas meta (no live model, no runtime dependency except an optional NOAA fetch of the selected spot's own prediction for the chart/badge, with fallback). Opens paused on "now"; "Now" button returns there.
- **Tide cycle**: one typical Montauk cycle LW → HW → LW at Neap / Average / Spring strength.
- **Example weeks**: hourly model output incl. weather — supermoon, summer spring + neap, Hurricane Sandy (Oct 2012; replaced Blizzard Jonas: in fishing season, biggest modeled surge at Montauk, +3.1 ft).
- Until the atlas exists in the manifest (deploy before upload), the page opens on Example weeks with the other views disabled.

```
Atlas build (off-Render: build-currents.yml source=atlas, or locally)
  tools/build_tide_atlas.py
    hindcast May 1 – Nov 1 2016 (4,416 h), surface + depth-avg, streamed week by week
      (gridded int8 frames + NOAA JSON cached under build/atlas-work; reruns download nothing)
    phase phi: two segments from Montauk high/lows (HW→LW = 0→0.5, LW→HW = 0.5→1), 24 bins,
      bin centres (b+0.5)/24; range R(t): each half-cycle's |H−L| at its mid-time, linear
      between mid-times (no step at HW/LW), clamped to the season's range
    per bin: least squares of 3 "tent" basis fields at the 10/50/90th R percentiles
      (linear interpolation, continued past the ends) → 72 frames (basis·24 + bin), GOCUR1
    per-cell skill (share of variance explained) + residual RMS → haze + "Confidence here"
    NOAA: 30-day interval=30 curve per station fitted the same way → typ[3][24] + median
      lags; Montauk 6-min tide shape; high/lows 2024–2030 bundled; Neap/Average/Spring
      presets = 10/50/90th R percentiles of 2024–30 (not 2016: lunar-node peak)
    guards: fit coverage, The Race vs NOAA typical, 2026 slack check, Race-area skill,
      sizes; report: out-of-sample replay of example weeks, day/night aliasing check
    → atlas-2016_{surface,davg}.bin.gz (~0.9 MB each) + _meta.json.gz (~0.1 MB);
      manifest "atlas" entry (incl. presets, dates); uploaded only with --upload / publish

Week build (off-Render: local, or .github/workflows/build-currents.yml)
  tools/build_currents.py
    NECOFS FVCOM GOM3 (UMass Dartmouth) over OPeNDAP:
      hourly hindcast 1978→2016 for the curated weeks in data/currents_weeks.json,
      or the live GOM3 forecast (offline since ~Oct 2026; daily job exits 0 until it returns)
    → surface u,v (siglay 0) + depth-averaged ua,va for the region's mesh elements
    → element values averaged to nodes → barycentric onto a 221×126 grid (~400 m)
    → int8 (0.025 m/s per unit) → GOCUR1 binary (format in data/currents.py), gzip
    + NOAA CO-OPS predictions: Montauk tide 8510560 (6-min + H/L) and ~86 current
      stations (30-min curves + MAX_SLACK events) + model water level → <week>_meta.json.gz
    → POST /api/currents/upload (CACHE_UPLOAD_TOKEN); manifest.json uploaded LAST

Serve (Render; no request-time processing)
  GET /api/currents/manifest.json                     max-age 60
  GET /api/currents/<week>_<surface|davg>.bin.gz      Content-Encoding: gzip,
  GET /api/currents/<week>_meta.json.gz               max-age 1 day, ETag/304
  files in CURRENTS_DIR = $CURRENTS_DATA_DIR or $SST_CACHE_DIR/currents
  (/var/data/cache/currents on Render); names checked against FILE_RE

Client (assets/currents.js → window.Currents)
  L.Map.addInitHook grabs the dash-leaflet map whose container id is "currents-map"
  views are "timelines": S.t in [t0, t1) looping; weights(t) → frame weights
    (week: 2 hourly frames; date/cycle: 2 basis × 2 phase bins from tideAt()/tent());
    x mapping for the chart (wall time for date/week, seconds for cycle)
  once per animation frame the weighted frames are blended into S.cur (water cells
    only); the particle layer (pane z 450) samples it bilinearly; trails = destination-
    in fade + 'lighter' strokes, coloured by speed
  low-confidence haze (pane 455): canvas from skill + residual RMS → L.imageOverlay
  NOAA station dots (pane 470) coloured flood/ebb/slack (typical curve in date/cycle,
    live NOAA for the selected spot once fetched); click → select (+ set_props)
  Plotly chart drawn into #currents-chart (purged when the axis type changes):
    Montauk tide, station current (NOAA / typical solid, model dotted), slack + max
    markers, "now" line; Plotly hover OFF — one overlay-div time line with our own
    readout (time, tide, currents); mouse hover previews that moment on the map
    (playback holds, leaving returns); click/tap/drag sets the time + pauses
  badge: week = median lags this week; cycle = typical lags; date = that day's NOAA
    slack/max times; + "Confidence here" from the skill map
  setters only record state; one coalesced apply() loads what the view needs (a cold
    Today load fetches only manifest + atlas meta + one atlas field + one NOAA call)
  one requestAnimationFrame loop (~30 fps); nothing per frame touches Dash
```

Each week ≈ 2–2.5 MB per depth + ~70 KB meta (under Render's ~7 MB response limit). Validated against NOAA: model surface current at The Race / Plum Gut / Montauk correlates 0.94–0.99 with predictions and gets slack timing within ~10–35 min; peak speed in the narrowest gaps runs ~25–30% low (the ~1 km mesh smooths ~1.5 km-wide jets). The sidebar says so.

Atlas accuracy (full-season build, Oct 2026): share of the model's flow the typical cycle reproduces in weeks it wasn't built from — Race/Plum Gut/Fishers 87–96%, Gardiners/Peconics 78–89%, Block Island Sound 65–83%, ocean side S of Montauk 28–58% (hazed). Slack timing vs NOAA over all of 2026: The Race median 26 min, **biased late** (+26 min mean — the model's own timing at The Race; 90% within 46 min); Plum Gut median 13 min (90% within 33). The "How it works" modal (`layout/currentsview.py:_about_body`) quotes these — update it if the atlas is rebuilt.

#### Harbor-inlet estimates (Lake Montauk, Napeague, Accabonac, Three Mile, Northwest Creek, Sag Harbor Cove)
NECOFS's ~400 m grid has no water inside these harbors (Accabonac: 3 cells), so their inlet currents are computed from the tide, client-side, in every view:
```
Build (local; tools/build_inlets.py → data/inlets.json, committed — ships with the code)
  per harbor (INLETS): DEM tile, seed point inside, cut line land→land across the narrowest point
  harbor area by water level (NAVD88 −1.2…+0.8 m): NCEI DEM_all ImageServer (CUDEM 1/9″),
    flood-filled from the seed with the inlet cut off
  inlet: survey dimensions where they exist (Napeague: PEP 2012 — 160 m², 55 m wide, 670 m),
    else the DEM along the cut, floored by NOAA chart depths ("min_depth_m") where lidar misses
    the channel; channel length by hand (the narrow reach); marker = deepest point on the cut;
    flood/ebb = square across the cut, flood toward the seed (Napeague set by hand: it bends)
  bay tide outside: Montauk 8510560 high/lows + a NOAA station's median offsets and range ratio
    over a year (8511171 Threemile entrance, 8510502 Montauk Harbor entrance, 8511629 Sag
    Harbor), or interpolated between two stations by straight-line distance
  datum: Montauk MLLW/MSL vs NAVD88 (mdapi) + sea-level rise since the 1983–2001 epoch (0.12 m)
  report (typical timing vs Montauk → the "How it works" table), Lake Montauk check vs NOAA
    inside/entrance ("check"), selftest; harbors sorted east → west (the Spot list order)
Page (dcc.Store currents-inlets → Currents.setInlets)
  inletRun(): (L/g) dv/dt = Δη − r v|v|,  d(harbor)/dt = Ac v / A(harbor level);
    backward Euler (quadratic in v), dt 60 s, 24 h spin-up (cycle view: 3 cycles);
    tide from each view's tl.hilo() (bundled / week / synthetic cycle high-lows, cosine rule)
  inlet spots join S.stations (estimate: true); arrow marker in S.inletLayer (always shown);
  chart = view's tide traces + harbor tide (dashed) + inlet current; badge tagged "Rough estimate"
  "How it works" table (layout/currentsview.py:_inlet_table) from each inlet's report
```
Short, wide inlets (Lake Montauk, Accabonac, Northwest Creek, Sag Harbor Cove) don't choke: slack comes within minutes of high/low water just outside, which itself runs later westward (Sag Harbor +95/+75 min vs Montauk). Napeague and Three Mile Harbor have long, narrow channels that hold the harbor's tide back (slack 1–2 h after Montauk HW). To add a harbor: an `INLETS` entry, rerun the builder, check the report and the DEM-vs-chart throat line it logs, commit `data/inlets.json`.

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
| `data/currents.py` | Currents data dir, GOCUR1 frame format (pack / parse / validate), filename allow-list, manifest reader |
| `data/currents_weeks.json` | Curated hindcast weeks (+ lesson text) and featured NOAA stations for /currents |
| `tools/build_currents.py` | Off-Render builder for the example weeks: NECOFS → gridded frames + NOAA predictions → upload |
| `tools/build_tide_atlas.py` | Off-Render builder for the tide-stage atlas behind Today / Tide cycle (see Architecture → Currents Page) |
| `tools/build_inlets.py` | Builder for the harbor-inlet estimates (DEM hypsometry, inlet survey, outside-tide offsets, report, Lake Montauk check, selftest) → `data/inlets.json` |
| `data/inlets.json` | Harbor-inlet model inputs (committed; read by `data/currents.read_inlets()`, run in the browser) |
| `layout/currentsview.py` | /currents layout: view switch, date / strength / week panels, controls, dark map, chart, "How it works" modal (accuracy text) |
| `assets/currents.js` | /currents engine: view timelines, atlas math (mirrors build_tide_atlas.py), harbor-inlet model (mirrors build_inlets.py), particles, haze, stations, chart, cursor, badge, live NOAA fetch |
| `assets/currents.css` | /currents styles + the shared header nav link |
| `layout/labsview.py` | GotOne Labs home: hero + one tile per tool (`TOOLS`) + about/footer |
| `assets/labs.css` | Labs home styles (gotoneapp.com look: Helvetica Neue, navy #091d50, yellow #ffe000), scoped to `.labs-page` |
| `assets/labs.js` | Page titles + the anonymous page-view beacon (`/api/hit`) |
| `assets/labs/` | Tile previews + share card (`tools/build_labs_previews.py`, from the tools' own data) and the white horizontal wordmark (from GotOne/Graphics) |
| `data/labstats.py` | Visit counter: SQLite on the persistent disk, daily-salted visitor hash, referrer hosts |
| `requirements-tools.txt` | Build-only deps (`netCDF4`, Pillow for DEM TIFFs, etc.) — never installed on Render |
| `.github/workflows/` | `seed-sst-cache.yml` (daily SST seed), `build-currents.yml` (currents), `keepalive.yml` (heartbeat), `sync-catches.yml` (catch sync — not wired up yet) |

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
18. **route_page** — Router: `/catches` (gated), `/currents`, `/sst`, `/labs`; `/` and unknown paths = Labs home (on the old `sst.` host: the SST map). A clientside callback on the same `url.pathname` calls `GotOneLabs.page()` (assets/labs.js): tab title + anonymous count (output `page-sink`). `update_title=None`, so Dash doesn't flash "Updating..." over those titles.
19. **Currents (clientside only)** — week / depth / rate / station / options / strength / date forward to `window.Currents.*` (each writes `''` to a `currents-sink-*` div; initial calls hand the engine its defaults; setters only record state and one coalesced apply() loads data once the map exists); the view switch (`currents-mode`) also returns the three panels' `style`; "Now" calls `goNow()` and returns today (ET) to the date picker; the `currents-inlets` store (data/inlets.json) goes to `setInlets()`; "How it works" opens the modal (auto-opens once per browser via a localStorage flag); play/pause toggles and returns the button label; step ±1 h; lesson text comes from the `currents-weeks` store. All per-frame work is in `assets/currents.js`, which pushes back into Dash only via `dash_clientside.set_props` (station click → dropdown, auto-play → button label).

## Critical Lessons Learned

### NOAA Blocks Render's Datacenter IP — Data Must Be Fetched Off-Render
*(Lifted as of 2026-10-10 — see Architecture → SST Data Sourcing. Kept for the diagnosis technique and in case it returns.)*
- **The symptom**: recent dates on the live site show a red "SST fetch timed out after 90s" (or "No compatible SST dataset found") box, while localhost works perfectly. **The cause is not in the code** — NOAA's firewall blocks Render's datacenter IP at the network layer. Confirmed in the live Render logs: `ConnectionError: Network is unreachable` (coastwatch.pfeg), `ConnectTimeout` / `Connection reset by peer` (upwell), `ReadTimeout` (coastwatch.noaa.gov tarpit). It worked from Render months ago; NOAA tightened anti-cloud filtering since.
- **`erddap_search` swallowed exceptions silently**, so the failures looked like a generic 90s timeout with zero log breadcrumbs. It now logs the exception type per server — that's how the block was confirmed (`ConnectTimeout`=firewall drop, `ReadTimeout`=tarpit, `403`=WAF/User-Agent).
- **A User-Agent header is necessary but not sufficient**: NOAA's WAF 403s the default `python-requests` UA, so `data/erddap._HEADERS` sends a real one. But that only addresses the 403 variant — the network-level IP block is the real problem, and **no fetch-code change can fix it**.
- **The fix is architectural**: fetch where ERDDAP is reachable (localhost / GitHub runners — both verified not blocked) and push cache files to Render via `POST /api/cache/upload`. See *Architecture → SST Data Sourcing*. The public AWS MUR Zarr mirror (`s3://mur-sst`) was evaluated and rejected — its time axis is frozen at ~Jan 2020.
- **If the live map breaks for recent dates**: check the seed workflow first (`gh run list --workflow=seed-sst-cache.yml`), not Render's ERDDAP path. Re-seed manually by running `tools/sync_cache.py` from a non-blocked IP with `CACHE_UPLOAD_TOKEN` set.

### GitHub Actions Schedules Die After 60 Idle Days
- In a public repo, scheduled workflows are **silently disabled** after 60 days without repository activity. That stopped "Seed SST cache" on 2026-08-30 (last commit was July 1) and nothing alerted — the SST map only kept working because NOAA had lifted its block. Check with `gh workflow list --all` (state `disabled_inactivity`); re-enable with `gh workflow enable "<name>"`.
- **`.github/workflows/keepalive.yml`** runs weekly and pushes a heartbeat commit (`.github/keepalive.txt`) only when `main` has been idle 45+ days. A real commit is the documented activity signal; whether an API re-enable resets the timer is undocumented, so we don't rely on it. Render auto-deploys `main`, so a heartbeat commit also redeploys the app (a harmless restart: the disk cache survives).

### NECOFS / FVCOM Gotchas (currents pipeline)
- **The live GOM3 forecast is listed but missing**: THREDDS `forecasts.xml` advertises `FVCOM/NECOFS/Forecasts/NECOFS_GOM3_FORECAST.nc`, but the file 404s (Oct 2026). The `NECOFS_FVCOM_OCEAN_GLOBAL_FORECAST.nc` that *is* live is **daily-mean** — tides average out, useless for currents. The hourly 30-year hindcast (`fvcom/hindcasts/30yr_gom3`, 1978-01-01 → 2017-01-01) works.
- **Nothing on UMassD's THREDDS is newer than 2016**: `catalog/archives.xml` holds only 2010–2014 forecast archives (GOM3v11/v12/v13, MassBay, waves). So storms like Henri (Aug 2021, Westerly landfall) can't be example weeks; in-season storms in the hindcast, by modeled surge at Montauk: Sandy Oct 2012 +3.1 ft, Bob Aug 1991 +2.2, Hermine Sep 2016 +2.0, Irene Aug 2011 +1.8.
- **2016 sits near a lunar-node peak**: M2 is ~7% stronger than in 2026, so tide-range presets taken from 2016 overstate today's tides. The atlas takes Neap/Average/Spring from the 2024–30 range distribution; Today mode is immune because it blends by the day's actual range in feet.
- **The model's slack at The Race runs ~25 min late vs NOAA** (all of 2026: mean +26 min, 90% within 46) while Plum Gut is close (median 13 min). It's the model, not the atlas method — the hourly weeks show the same 16–36 min. Don't promise "slack within 20 min" at The Race; the chart/badge show NOAA's own times.
- **NOAA *subordinate* current stations have no curve** — only slack and max-current events, whatever `interval` you ask for (72 of the region's 86, all the `ACT*` ones; e.g. Montauk Point = The Race with per-phase time shifts of −74 to −124 min and ×0.7 flood / ×0.5 ebb, from mdapi `?expand=currentpredictionoffsets`). Treated as a curve, the chart drew a zigzag (7 points a day) and the atlas fit left most phase bins empty → 0 kn → those dots read "slack" most of the time. Detect "events only" (median spacing > 45 min) and rebuild with NOAA's own rule — quarter sine slack ↔ max, half cosine between maxima (`densify` in `tools/build_tide_atlas.py` and `assets/currents.js`). Zero-crossing code must count exact zeros (NOAA's slack points are 0.0).
- **NOAA current predictions are capped at 31 days per request** (`interval=30`), but `MAX_SLACK` events and Montauk high/lows come back for a whole year in one call, and 6-min tide heights for 6 months. The atlas builder fits each station from one 30-day curve (Aug 2016 covers two spring/neap cycles).
- **Don't read `Itime`/`Itime2` on the hindcast**: it's an aggregation of ~470 monthly files, and those variables make THREDDS open every one (hung 10+ min). The aggregated `time` coordinate is cached and fast; it's float32 MJD (~6-min resolution at modern dates), so round to the hour.
- **Month boundaries repeat an hour** in the aggregation (467 duplicate steps). A week crossing one has 170 raw steps for 169 hours; `window_steps()` keeps the first copy. Without it every later frame is shifted an hour — fatal for slack timing.
- **Velocities are element-centred** (`lonc/latc`; `nv` is 1-based); `siglay` index 0 is the surface; `ua/va` are depth-averaged. Average elements → nodes, then barycentric interpolation inside mesh triangles; grid points outside the mesh are land.
- **Model water level (`zeta`) isn't on the gauge's datum** — a MSL→MLLW shift left it 0.7–1.2 ft low every week. Align by the week's median difference so only weather (surge) separates it from NOAA's prediction.
- **~1 MB/s from smast.umassd.edu**; the region's elements span 47% of the index range, so a week (4 variables × 169 h) is ~115 MB, ~2–3 min.
- **NOAA "no predictions" errors are deterministic** — don't retry them (`NoRetry`); retrying cost ~18 s per station. `LIS1001` The Race and `LIS1012` Plum Gut are harmonic (pass `bin=<currbin>`); most `ACT*` stations are subordinate. 86 current-prediction stations fall in the region.
- **NOAA CO-OPS rate-limits per IP (~160 calls, then `403 Forbidden`)**: the first GitHub Actions build fetched a week's ~175 prediction calls with 4 threads (~21 req/s) and the runner was 403'd on the next week; a sequential 2 req/s test from home internet also got one 403, at call #162. Calls are now sequential, `NOAA_MIN_INTERVAL` = 1 s apart (~3 min/week), the spacing doubles (max 4 s) after each 403/429, and throttled calls back off 60/120/240 s (`Throttled`). Only "no data" responses skip a station — a network error or throttle fails the week, so a throttled run can't publish a week with stations silently missing. A failed week is skipped (its old version stays live), the rest still publish, and the run exits 1.
- **NOAA can return an empty list for a station it covers**: in the first production build ACT2776 (Mulford Point) came back with zero rows in one week, beside a 504 for another station, and was dropped without a log line. `station_cp()` retries an empty answer once, and every dropped station is now logged. The page tolerates a station missing from one week (selection is ignored; switching weeks falls back to The Race), but the dropdown label won't match, so rebuild that week.

### Harbor Inlets (estimates)
- **Lidar DEMs miss inlet channels.** NCEI's 2015 CUDEM (topobathy lidar) shows Napeague's west inlet as a 21 m², 0.4 m-deep gap; the 2012 bathymetric survey measured 160 m² (8–10 ft deep, 180 ft wide). Fast, turbid inlet water defeats lidar and the gap gets interpolated from the shoulders. With the DEM throat the model "chokes" the harbor to a tide of a few inches — a red flag that sent us looking for the survey. Use surveys or charts for the throat; the DEM only for the harbor's area. (Chart soundings agree with the DEM within ~0.35 m elsewhere, but none sit in the throat.)
- **Napeague has one inlet now.** The east inlet by Goff Point closed around 2007 (Peconic Estuary Program report), so all exchange goes through the west inlet at Lazy Point, which widened from 64 ft (1970) to 180 ft (2010); the county dredged it in Oct 2014 and homeowners report more widening since. A bigger inlet means less lag (≈30–40 min after Montauk instead of ≈70) — the first thing to recalibrate from on-the-water observations.
- **NOAA "Goff Point, 0.4 mile NW of" (ACT2471) is not the harbor inlet**: it sits in the bay at the head of the 10–12 m channel west of Hicks Island and floods toward 225° (along that channel). Its slack (~1h15–1h40 after Montauk HW) is the bay's.
- **The tide arrives later westward through Gardiners Bay** (NOAA vs Montauk Fort Pond Bay): Threemile Harbor entrance HW +65 / LW +39 min, Sag Harbor +93 / +74, Little Gull +39 / −2. NECOFS agrees in direction but runs ~20–35 min later than NOAA west of Napeague, and its Napeague Bay amplitude looks wrong (coarse mesh) — so the bay tide outside each inlet is interpolated from NOAA stations, not taken from the model.
- **Momentum matters for slack**: a friction-only (Keulegan) model puts slack at the moment bay and harbor levels cross; with the 670 m channel's inertia the water keeps running and slack comes ~13 min later. The slack window (< 0.5 kn) is ~30 min either way. Bigger tides choke more (friction ∝ v²): ~1h30 lag on supermoon tides vs ~1h10 typical.
- **Lake Montauk is the check case** (NOAA predicts inside 8510448 and at the entrance 8510502): NOAA ±5 min, the method 0 / +2 min with the DEM's channel (deep and dredged, so lidar sees it). It shows the method doesn't invent lag; it can't validate Napeague's lag — only on-the-water slack times can.
- **NOAA ENC "Dredged_Area" polygons give each maintained channel's depth and width**: `encdirect.noaa.gov/.../enc_harbour/MapServer/228` (DRVAL1 = m below MLLW; intersect the polygon with the cut for the width; soundings are layer 76). Lake Montauk Reach A 4.9–5.0 m × ~44 m (USACE survey May 2026), Three Mile 1.8 m × ~31 m (1996), Northwest Creek 1.8 m × ~45 m (1995), Accabonac 2.4 m (1996; on the approach, not in the gap). Lidar saw the deep dredged channels (Lake Montauk, Three Mile) but missed the shallower ones (Napeague, Northwest Creek ~0.5 m vs 2.4 m soundings, Sag Harbor bridge ~1 m vs 1.8–2.7 m).
- **Three Mile Harbor's lag is the most sensitive**: a 4.2 km² harbor behind a ~450 m jetty reach of ~163 m². Across plausible channel sizes and lengths, slack runs ~1h10–2h20 after Montauk HW (163 m² × 450 m → 1h55). An on-the-water slack time at the jetties would pin it down.
- **The U.S. Coast Pilot 2 (ch. 7) gives the only published inlet speeds**: Lake Montauk entrance 1.2 kn flood / ~0.5 kn ebb, Three Mile Harbor ~3 kn. They run 30–40% above the model's cross-inlet averages (typical per-tide peak, `typical_flood_kn` in the report), as expected mid-channel. The model can't reproduce Lake Montauk's weak ebb (flood/ebb asymmetry). Town / county figures for harbor areas (East Hampton LWRP, Town GIS 2022) match the DEM hypsometry within a few percent. Dredging history: Accabonac (county, 8 ft MLW, 2013/2020/2023), Northwest Creek (county, Nov 2023), Lake Montauk deepened to 17 ft MLLW (USACE, done July 2026).
- **NOAA has no tide station inside any of these harbors**; "Threemile Harbor entrance" (8511171, with historic data and harmonics) sits ~200 m out in the bay, so it is the outside tide.
- **NOAA's sea-level trend API answers in `trendUnits: "inches/decade"`** (Montauk 1.42 → 3.6 mm/yr). NOAA tide predictions exclude the ~0.12 m rise since the 1983–2001 epoch; it matters when tide heights meet a NAVD88 DEM.
- **Keep `inletRun()` in `assets/currents.js` in lockstep with `tools/build_inlets.py`**: `window.Currents._inletSelftest()` should report `maxErr` ≈ 5e-5 (the stored values' rounding). The builder computes its selftest from the *live* atlas (3-decimal heights); an older local atlas copy (2 decimals) drifts ~0.006 kn.

### Tide Height ≠ Tidal Current (the point of /currents)
- NOAA publishes current predictions separately from tide predictions, and in the Race / Plum Gut / Montauk system slack and max flow are phase-shifted from local high/low water. Supermoon week 2015: The Race slack ≈ 2h40m after Montauk HW/LW, max flood ≈ 15 min–1 h *before* HW; Plum Gut slack ≈ 1h40m after. So "fish the slack at high tide" by a Montauk tide table is off by hours, and two passages a few miles apart turn about an hour apart.
- Wind and surge push real flow off the tide-only predictions: during Blizzard Jonas (Jan 2016) the model's Montauk current peaked 3.2 kn vs NOAA's 2.7 with water ~2.3 ft above its usual offset; during Hurricane Sandy (now the storm example week) the model puts Montauk +3.1 ft above the predicted tide on the evening of Oct 29, 2012.
- **The currents are locked to the tide cycle in the passes, so a typical cycle works there**: 2026 NOAA predictions put The Race's slack 2h38 after Montauk HW (80% within about ±20 min) and its strength varies ~±20% with the range; that's why the default view is a tide-stage atlas, not a forecast. It fails on the ocean side south of Montauk (wind and shelf currents dominate) — hazed on the map.

### Dash + Custom JS Gotchas (/currents)
- **Leaflet is a global (`window.L`) loaded by dash-leaflet before `/assets/*.js`**, but `window.dash_clientside` is created *after* assets — look it up lazily. Grab a dash-leaflet map with `L.Map.addInitHook` (match `getContainer().id`); dash-leaflet's `eventHandlers.load` misses the initial load. react-leaflet calls `map.remove()` on page swap, which fires `unload` — clean up there.
- **Plotly isn't on the page until a `dcc.Graph` mounts** (lazy-loaded). /currents mounts a hidden 1-px `dcc.Graph` (`responsive=False`) just to load it, then draws its own chart with `Plotly.react` into a plain div.
- **Don't drive a `dcc.Graph` at animation rate**: it pushes `relayoutData` back into Dash on every relayout. The time cursor is an absolutely-positioned div placed from the known axis range.
- **Never use the class name `row` in custom HTML**: Bootstrap's `.row` (negative margins + flex-wrap) clipped and wrapped the map clock. Prefix custom classes (`cur-row`).
- **`document.hidden` pauses `requestAnimationFrame`**: the loop stops when the tab is hidden and restarts on `visibilitychange`. A hidden browser pane shows a frozen animation — to test, drive `window.Currents._state.particles.step()` manually, or shim it: override `document.hidden` to `false`, replace `requestAnimationFrame` with a 33 ms `setTimeout`, and dispatch `visibilitychange` (then real mouse hovers/drags work).
- **Plotly reads numeric dates in the browser's local timezone**: the chart got ET wall-clock times as millisecond numbers, so Plotly shifted every tick and hover time by the viewer's UTC offset (4 h in EDT) while the map clock was right — scrubbing by the hover readout landed hours off. Pass timezone-naive date strings (`px()` in currents.js) for x data and range. Easy to miss: the curves and cursor stay mutually consistent, only the labels are wrong, and only when the browser isn't on UTC.
- **Users scrub with the hover readout, not by dragging**: Plotly's x-unified hover line follows the mouse, so a hover that didn't move the map looked like broken scrubbing (and after a real drag, playback resumed and ran away). Now mouse hover previews the map (`S.preview`, playback holds), leaving the chart returns, and click/tap/drag jumps there and pauses.
- **Then: one time line, one readout — Plotly's hover is off** (`hovermode: false`). With it on, a desktop click left a second (dashed "set time") marker that nobody could read, and on iPad Plotly pinned its hover box and dotted spike where the finger first landed while our line followed the drag — two lines showing two times. Now the white line is the only time indicator and our `.currents-readout` rides on it (time, tide, NOAA / typical current, model current), shown while pointing or touching (lingers ~2 s after a touch). A "Click to set this time" hint appears only while previewing a time other than the set one. Legend names say what each line is ("Montauk tide, ft (NOAA)", "<spot> current, kn (NOAA)", "<spot> current, model") and list the slack / strongest-flood markers when the chart is ≥ 1000 px wide.
- **Dash 4 `DatePickerSingle` `display_format` doesn't take moment's `ddd`** (weekday): it rendered "Su11, Oct 1…". Use `"MMM D, YYYY"` like the SST page.
- **Dash 4 dropdown menus render inline**, inside the dark sidebar, so they inherit its light text (white on white). `#sidebar-col .dash-dropdown-content` needs the navy override too, not just `.dash-dropdown-value`.
- **The date input has a fixed width** and overflowed its flex slot in the narrow sidebar, covering the "Now" button (clicks hit the calendar). The date row wraps and the input is `width: 100%`.
- **Leaflet 1.9 tiles use `mix-blend-mode: plus-lighter`** (its fix for seams in opaque basemaps). On a semi-transparent overlay tile layer (the Esri place-name pane) it brightens every tile edge into faint lines across the map — on iPad and in Chromium, even at whole zoom levels. Give overlay tile layers `mix-blend-mode: normal`. Don't "fix" seams by overlapping tiles 1 px: with plus-lighter the overlap itself draws bright lines.
- **Plotly overlaying axes can tick in sync with the main axis** (non-round values like 5.25 / 1.81 kn on a tall chart): set `tickmode: "auto"` on `yaxis2`.
- **Keep the JS atlas math in lockstep with `tools/build_tide_atlas.py`** (phase, range interpolation + clamp, tent weights, bin centres). The builder writes a `selftest` block; `await window.Currents._selftest()` in the browser console should report `maxErr` ≈ 0 (store full-precision centres/clamp and NOAA's 3-decimal heights, or it drifts by ~0.002 m/s).

### GotOne Labs home page
- **`gotone.css` locks `html, body` (`overflow: hidden; height: 100%`)** so the map pages never scroll. So `.labs-page` is its own scroller: `height: 100dvh; overflow-y: auto`. The first fix — unlocking the page with `html:has(.labs-page), body:has(.labs-page)` — worked in Chromium but not in the user's browser (the page swaps in after load and `:has` on the root wasn't re-checked), leaving the page cut off at the bottom. Don't rely on `:has` for root styles that depend on SPA-inserted content.
- **Dash 4's `html.Img` has no `loading` prop** (TypeError → the router callback 500s and the page renders empty). Same family as `dbc.Spinner`'s `spinner_class_name`: check the component's allowed args.
- **Bootstrap turns on smooth scrolling**, so in browser tests `window.scrollTo()` then `scrollY` reads the old value (and the hidden preview pane pauses the animation). Set `document.documentElement.style.scrollBehavior = "auto"` first.
- **The counter only reports from the live hosts** (`*.gotoneapp.com`, `*.onrender.com`); set `window.GotOneLabsCountHere = true` in the console to test locally. Stats read the same SQLite file (`$SST_CACHE_DIR/stats/labs.sqlite`); `LABS_STATS_KEY` unset = open locally, 404 on Render.

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
- **/currents on tablets**: the drawer also applies under `(max-width: 1199.98px), (hover: none) and (pointer: coarse)` (any touch screen — iPad landscape is 1133–1376 px wide — plus narrow windows), scoped to `.currents-page` so the SST page is untouched. The playback controls float on the map (`.currents-playbar`, created by currents.js) because the sidebar is hidden while you watch, and picking a view / date / week / strength / spot closes the drawer (clientside callback with `allow_duplicate` outputs). gotone.css hides `.sidebar-backdrop` with `!important` above phone width, which beats the toggle callback's inline style, so the backdrop is shown with `.currents-page:has(.gotone-sidebar.drawer-open)`.
- **/currents is a flex column exactly as tall as the visible viewport** (`.currents-page { height: 100dvh }`, header `flex: none`, content `flex: 1; min-height: 0`, columns `height: 100%`). The old `calc(100vh - 72px)` was wrong twice: the header is 80 px (56 px logo + padding), so the page overflowed by 8 px even on desktop, and iPad/iPhone Safari's `100vh` includes the area under the toolbars, which pushed the tide chart off-screen. The SST page still has the 72 px assumption.
- **Full screen (/currents)**: a map button calls `requestFullscreen` (or `webkitRequestFullscreen`) on the whole page; `html.currents-fs` shrinks the header to a 44 px branded bar (logo, title, "How it works"; no subtitle or SST link). In iPadOS full screen the **status bar (time, battery) stays drawn over the top of the page** and Safari adds its own **exit "X" (~56 pt square, ~20 pt in, ~32 pt down)** that a page can't hide — measured from an iPad screenshot. So on touch screens the bar is `max(24px, env(safe-area-inset-top))` of status-bar strip + a 60 px row, with 92 px left padding: the X lands in the bar instead of on the map's zoom buttons, and the status text sits on navy instead of the logo. iPadOS Safari supports it, iPhone Safari doesn't for non-video elements (no button there), and inside an iframe without `allow="fullscreen"` (the Squarespace embed) the button opens the page in a new tab instead. The Claude preview pane refuses fullscreen requests — test the class/layout by faking `document.fullscreenElement` and dispatching `fullscreenchange`.

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
- **Auto-deploy is ON**: pushes to `main` went live within ~2.5 min in Oct 2026 with no Manual Deploy (it used to be off). Treat every push to `main` as a production deploy — test locally first — and check the live asset afterwards (e.g. grep the served `/assets/currents.js`). `[skip render]` or `[render skip]` in a commit message skips that commit's deploy (docs-only changes); Manual Deploy in the dashboard still works.
- **Pre-warm thread** runs 15s after startup. Only loads from disk cache (no ERDDAP, uses `raw_only=True` to skip PNG rendering). Heavy ERDDAP fetches during startup starve the gunicorn worker and cause Render's health check to fail, hanging the deploy indefinitely.
- **Persistent Disk**: 1 GB disk mounted at `/var/data/cache`. Set `SST_CACHE_DIR=/var/data/cache` in Render Environment. Cache survives worker restarts and deploys. Without this, Render's ephemeral filesystem wipes cache on every container restart — the pre-cache system is useless without persistent storage.
- **`GFW_API_TOKEN` env var** must be set in Render Environment for fishing activity layer. Without it, the layer degrades gracefully (transparent tiles).
- **Health check failures**: Caused by ERDDAP stalls blocking the single worker. The pre-cache 120s timeout and GFW tile `updateWhenZooming=False` mitigate this. If the worker restarts, persistent disk preserves cache files.

### Custom Domain & Squarespace Integration
- **Home: `labs.gotoneapp.com`** — CNAME `labs` in Squarespace DNS → `offshore-sst-map.onrender.com`, added as a custom domain in Render (SSL automatic). Tools are paths on it (`/sst`, `/currents`), not subdomains: one address to share, one DNS record and certificate, one counter, and the tools share browser storage.
- **Old address: `sst.gotoneapp.com`** (same CNAME target) keeps working. Until `LABS_HOST=labs.gotoneapp.com` is set in Render, its `/` serves the SST map (and `/labs` the home). With `LABS_HOST` set, `redirect_old_host()` 301s page loads to the same path on labs (`/` → `/sst`); `/api/*`, `/_dash*` and `/assets/*` are never redirected (the GitHub workflows still upload to `sst.`, and a redirected POST would turn into a GET).
- **Squarespace embed**: iframe on `gotoneapp.com/offshore-sst` page. Uses Squarespace Embed Block with `<iframe src="https://sst.gotoneapp.com">` (point it at `https://labs.gotoneapp.com/sst` once labs is live).
- **iframe headers**: `@server.after_request` hook sets `Content-Security-Policy: frame-ancestors` to allow embedding from `gotoneapp.com`, `labs.` and `sst.gotoneapp.com`, and the Render domain. Also removes `X-Frame-Options` if set by middleware.
- **Link previews**: `meta_tags` in `app.py` carry Open Graph / Twitter tags with `https://labs.gotoneapp.com/assets/labs/og.jpg` (the same card for every page).
- **To update the embed URL**: change `src` in the Squarespace Embed Block. To change allowed embedding origins, update `set_iframe_headers()` in `app.py`.

### ERDDAP Behavior
- **MUR data latency**: ~2 days behind. Always try `end_date - 0`, then `-1`, then `-2`.
- **Rate limiting (429)**: ERDDAP servers return 429 if hit with too many concurrent requests. Max 2 parallel workers with exponential backoff (2s, 4s, 6s retry).
- **All 3 configured servers redirect to the same backend** (coastwatch.pfeg.noaa.gov). Trying different "servers" doesn't help for rate limits.
- **Single multi-day NetCDF requests time out** for 7+ days of MUR data. Parallel individual-day fetches (2 at a time) are more reliable.
- **Time budget**: 25s for single-day, 90s for multi-day. Must finish before browser-side callback timeout.

### Leaflet Z-Index
Map layer panes and their z-index values matter enormously:
- **tilePane**: 200 (Esri Light Gray basemap)
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
- **Cache miss path**: 7 parallel ERDDAP fetches, ~60-120s on localhost (residential internet). **On Render this path is dead** — NOAA blocks the IP, so a cache miss returns the red "timed out after 90s" error. Recent dates must be pre-seeded into the cache by the off-Render GitHub Action (see *Architecture → SST Data Sourcing*).
- **Localhost timeout issue**: Expanded AOI ERDDAP fetches can exceed Dash's browser callback timeout, causing "server did not respond" and a stuck loading overlay. Data may still finish fetching server-side. (No longer applies on Render — it never fetches ERDDAP; it only serves cache.)
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

# /currents data (build-only deps; the app itself doesn't need them).
# Use `python -m pip`: the venv's pip launcher is broken from a past move.
.venv/bin/python -m pip install -r requirements-tools.txt
.venv/bin/python tools/build_currents.py --weeks 2015-09-supermoon   # → ./cache/currents
.venv/bin/python tools/build_tide_atlas.py --start 2016-08-01 --end 2016-08-15 --force  # quick dev atlas
.venv/bin/python tools/build_inlets.py      # → data/inlets.json (commit it; ships with the code)
# → http://localhost:8050/currents
# Production: dispatch the workflow (uses the GitHub secret; no local token needed)
gh workflow run build-currents.yml -f source=hindcast -f weeks=all
gh workflow run build-currents.yml -f source=atlas -f publish=false   # build + artifact only
gh run download <run-id> -n tide-atlas -D build/atlas-ci              # test locally first
gh workflow run build-currents.yml -f source=atlas -f publish=true    # rebuild from cache + upload
```

## Environment Variables
| Variable | Required | Purpose |
|----------|----------|---------|
| `GFW_API_TOKEN` | No | Global Fishing Watch API token (JWT). Enables fishing activity overlay. Without it, layer degrades gracefully (transparent tiles). |
| `SST_CACHE_DIR` | Yes (Render) | Cache directory path. Set to `/var/data/cache` on Render (persistent disk mount). Default `./cache` for local dev. |
| `CACHE_UPLOAD_TOKEN` | Yes (Render) | Shared secret for `POST /api/cache/upload` and `POST /api/currents/upload`; also a GitHub repo secret for the seed and currents workflows. Unset → uploads disabled (403). |
| `CURRENTS_DATA_DIR` | No | Override for the /currents data dir. Default `$SST_CACHE_DIR/currents` (→ `/var/data/cache/currents` on Render). |
| `CATCH_DATA_DIR` | Yes (Render) | Directory holding the catch CSVs (`/var/data/cache` on Render). Default: the repo's `data/`. |
| `CATCHES_ACCESS_KEY` | Yes (Render) | Access key for `/catches` (`?key=…`). Unset on Render → page closed. |
| `CATCHES_UPLOAD_TOKEN` | No | Secret for `POST /api/catches/upload` (catch sync — not automated yet; CSVs are uploaded by hand). |
| `LABS_HOST` | No | `labs.gotoneapp.com` → page loads on `sst.gotoneapp.com` redirect there (set only once that domain works). Unset → the old host serves the SST map at `/`. |
| `LABS_STATS_KEY` | Yes (Render) | Key for the private visit stats (`/labs/stats?key=…`, `/api/labs/stats?key=…`). Unset on Render → 404. |
| `PORT` | Render only | Set automatically by Render for gunicorn binding. |
