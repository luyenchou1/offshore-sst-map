# SST sprint 1 relaunch: progress log (Oct 16, 2026)

Branch `sst1-fix` (worktree `/Users/lchou/Code/sst-sprint1/lane-fix`), starting at `23fde1d` (sprint 1 plus the
production-hang fix, docs/incident-2026-10-16.md). Three fixes before sprint 1 goes back to Render: `window.SST`
missing after a boot, the SST image drawn north of true, and a pinned gunicorn. Not pushed (a push to main deploys).

Setup for every run below: gunicorn exactly as production (`app:server --workers 1 --threads 16 --timeout 180`,
gthread) on 127.0.0.1:8072 via `.venv/bin/python -m gunicorn` (the venv's launcher has a stale shebang), a private
copy of the cache (`SST_CACHE_DIR`), `LABS_STATS_DIR` a temp dir, Python 3.12.3, Dash 4.0.0, dash-leaflet 1.1.3.

## 1. window.SST missing on /sst loads right after a boot

### Reproduction
- First try: boot, poll `/api/currents/manifest.json` until it answers, then 4 /sst pages at once in headless
  Chrome, 5 boots: 20/20 pages fine. The poll was the first request the app saw.
- Then: poll only until the port accepts a TCP connection (no HTTP request), then 4 pages at once. 8 boots: **8 of
  32 pages had no `window.SST`**; a second run 6 of 16. On every failing page `document.scripts` held **no
  `/assets/*.js` at all** (no currents.js, labs.js, sst.js) and usually no `/assets/*.css` either (so no
  gotone.css). `window.L` was defined and the map rendered. sst.js had not failed: it was never in the page.

### Cause
Dash 4.0.0 finishes its setup on the first request: `Dash._setup_server`, a `before_request` hook, walks `assets/`
(`_walk_assets_directory`), applies eager loading and builds the script and stylesheet lists. It sets
`_got_first_request["setup_server"] = True` **before** doing that work, without a lock. The listening socket (opened
by gunicorn's master) queues connections while the worker is still importing app.py (about 1 s), so the first page
loads after a boot are handed to the 16 request threads together; the first thread starts the setup, and the others
see the flag, skip it and render the index from an app whose asset list is still empty. sst.js is the last JS file
in alphabetical order, so a partial walk misses it first. It needs concurrent first requests, so it happens right
after a boot (every deploy, every restart) when several pages or one page plus a beacon arrive together.

Not the cause (checked): load order. The page lists polyfill, React, dbc, `dash_leaflet.js` (synchronous; its
Leaflet module sets `window.L` when the bundle runs), dash-extensions, then the assets in alphabetical order
(currents.js, dashExtensions_default.js, labs.js, sst.js), then the Dash renderer. All synchronous, so `window.L`
exists when sst.js runs and no map exists yet. No error is thrown in sst.js; nothing in it needs the DOM at load.

### Fix
- `app._finish_dash_setup()` (end of app.py, after every callback is registered): runs `app._setup_server()` once at
  import, inside an app context, so the worker has the full asset list before it serves anything; Dash's own hook
  then returns at once. Logs a warning if Dash has no `_setup_server` or sst.js isn't among the assets.
- `assets/sst.js` made robust on its own (defence in depth; the server fix is what fixed the bug): `window.SST` is
  defined at once (classify, status, marks and fetchError don't need Leaflet at load); a second copy of the file
  keeps the first one's state and hooks (`SST._setup`); the Leaflet hooks go in as soon as `window.L` exists,
  retried every 50 ms (then every second after 30 s); a map made before the hooks is adopted the next time it fires
  an event (a `L.Map.prototype.fire` wrapper, installed only when hooks go in late or a Leaflet map is already on
  the page), and its fishing-activity layers get the 503 retry (`map.eachLayer`). The GFW retry checks the tile URL
  per error, so a layer whose URL changes keeps working. `SST.ready()` says whether the hooks are in.
- `tools/boot_sst_pages.mjs` (new): boots gunicorn as production N times, opens 4 /sst pages at once as soon as the
  port is open, and requires every /assets script, `window.SST`, `SST.ready()` and the map hooked on each; then
  sst.js before Leaflet, after the map and a fishing-activity layer exist, twice, and across page changes
  (/sst -> / -> /sst via dcc.Link's pushstate), each ending with a map tap that reads the water.

### Verification
| code | cold-boot pages (4 at once) | window.SST missing | init checks |
|---|---|---|---|
| 23fde1d (before) | 24 | 5 (each with no /assets scripts) | sst.js before Leaflet: never set up; after the map: no map, no GFW retry; twice: hooks doubled (13 -> 14) |
| fix (gunicorn 25.1.0) | 24 | 0 | all pass |
| fix (gunicorn 23.0.0, final) | 40 | 0 | all pass |

(A bare mouse move doesn't adopt a late map: Leaflet calls `fire` only for DOM events the map listens to. Pans,
zooms and taps do; the test pans.)

## 2. The SST image drawn north of true

### Cause
Leaflet places an ImageOverlay by projecting its two corners and stretching the image linearly between them in
Web Mercator, but each PNG frame had one image row per grid row, and the grid's rows are evenly spaced in latitude.
Over 38.795 to 43.805 N a row's drawn latitude and its true latitude differ by up to 5.36 km (2.9 nm), drawn north
of true, worst at 41.30 N (mean 3.58 km over the area); the designer's figure was 5.3 km at 41.3 N. Readouts were
right all along (`map/readout.week_temps` takes the nearest grid latitude), so the tap and the picture disagreed.
Entries saved before sprint 1 also carry `bounds` at the first and last cell centres instead of the cell edges.

### Fix
- `map/overlay.py`: `mercator_row_sources()` / `mercator_rows()`: output row r is centred where Leaflet draws it
  (evenly spaced in Web Mercator between the overlay's south and north bounds) and shows the source row with the
  nearest centre to that latitude (nearest, so values, NaN land and the AOI mask are exactly the raw grid's; no row
  is skipped). Output rows are at most `MAX_ROW_DEG` (0.005 deg, 0.56 km; and half a source row) tall, so a cell
  edge lands within 0.28 km of true for MUR and OISST alike. `sst_to_base64_png(arr, vmin, vmax, lats=, bounds=)`
  now requires the grid's latitudes and the overlay's bounds. `FRAME_FORMAT = 2`.
- `app.py`: one `_render_frames()` for every path (fresh fetch, the other color mode from memory, a saved entry,
  a raw-only entry), each payload marked `"frame_format": 2` (written to the cache with the frames). Bounds come
  from the grid (`geo.grid_bounds`) on every path, so old entries' centre bounds are corrected too.
- Saved weeks: `_frames_current(entry)` is true only for frames with the current format. Anything else (frames
  drawn the old way, or none) is redrawn from the entry's own raw arrays when the week is served
  (`_build_payload_from_disk_cache`) and the entry rewritten in the background (`"write": "upgrade"`, the path
  raw-only entries already used). No NOAA call, so it works on Render. Built-at and the rest of the meta are kept.
  A rollback is safe: older code serves the redrawn frames as they are, with their (edge) bounds.
- The raw arrays, the readouts and the bounds' meaning are unchanged; nothing on the page changes but the picture.

### Measurements
| | before | after |
|---|---|---|
| cell edges, MUR grid (where a boundary between two rows is drawn vs where it is) | max 5.36 km at 41.30 N, mean 3.58 km | max 0.274 km, mean 0.130 km |
| cell edges, OISST grid | max 5.33 km | max 0.263 km |
| synthetic 9 km bands, 600 random points: drawn pixel vs tap readout | 414 wrong | 0 wrong (536 exact, 64 within half an image row of a cell edge showing the cell at the pixel's centre) |
| saved week 2026-10-15, 1510 points (its saved frame vs redrawn) | 166 wrong | 0 wrong |
| browser, laptop, 10 taps at cell centres, zoom 9: legend color under the tap vs the reading | 4 of 10 a degree off | 10 of 10 match |
| image rows / frame size (2026-10-15) | 501 rows, 152 KB for 7 frames | 1041 rows, 190 KB for 7 frames |
| redraw of a saved week (load + 7 frames), this Mac | | 0.35 to 0.41 s, once per week and color mode |

What's left is Leaflet's own rounding of the image corners to whole screen pixels (0.06 km at zoom 10).

### Tests
- `tools/test_sst_ui.py`: `test_overlay_rows_follow_web_mercator` (every image row shows the cell under it, no
  row skipped, row height, before/after edge errors for MUR and OISST), `test_overlay_colors_match_the_readout_at_
  map_positions` (named spots and 600 random points on a synthetic field with land and AOI holes: the pixel Leaflet
  draws at each lat/lon has the color of `week_temps` there; the old drawing fails 414), `test_overlay_on_a_saved_
  week` (the same on the newest saved week in the cache, and its saved frame for comparison; skipped without one).
- `tools/test_sst_fetch.py`: `test_app_old_frames_redrawn_from_saved_arrays` (an entry saved the old way is
  redrawn without asking NOAA, identical to a fresh fetch's frames, with edge bounds, written back with
  `frame_format` 2 and the original built_at, then served as saved; the other color mode and raw-only entries too).
- `tools/smoke_sst.mjs` (laptop): the overlay `<img>` sits on its projected bounds (0.00 px off), and at 10 water
  points the color drawn under the tap, decoded through the legend's gradient, matches the tap's reading.

## 3. gunicorn pinned

### What changes between versions (read in each version's `gunicorn/workers/gthread.py`)
- 23.0.0: `accept()` registers the new connection with the worker's poller and hands it to a request thread only
  once it is readable. A connection that sends nothing costs no thread (it's closed at the keepalive timeout).
- 24.x and 25.x: `accept()` submits every new connection straight to the thread pool, where the thread blocks
  reading the request with no timeout. 16 connections that send nothing hold all 16 threads until they close.
- 26.x: the same, but `handle()` first waits up to `DEFAULT_WORKER_DATA_TIMEOUT` (5 s) for data and then defers
  the connection back to the poller. So 16 idle connections stall every route for about 5 s at a time.
- requirements.txt said `gunicorn>=21.2.0`, so Render ran whatever pip resolved at the build (26.2.0 today).

### Load test (`tools/load_sst_outage.py`, now with an idle-connection check)
New in every scenario: open 16 TCP connections that send nothing, then GET /api/currents/manifest.json; it must
answer within 2 s. Same code (this branch), Python 3.12.3, each version on PYTHONPATH (`--extra-path`):

| gunicorn | unreachable | render (NOAA v4drop, GFW dropped) | slow (NOAA and GFW drip) | 16 idle connections |
|---|---|---|---|---|
| 23.0.0 | waves 0.1 s / 0.1 s | 3.0 s / 0.1 s | 22.1 s / 22.1 s | answered in 0.00 to 0.01 s: **all ok** |
| 25.1.0 (the venv) | 0.1 s / 0.1 s | 3.0 s / 0.1 s | (below) | no answer within 30 s: FAIL |
| 26.2.0 (pip today) | 0.1 s / 0.1 s | 3.0 s / 0.1 s | 22.1 s / 22.1 s | answered after 4.5 s: FAIL |

Everything else (both waves within 30 s, SIGUSR1 stack dump, /api/debug/threads, nothing left running) passed on
all three. The waves are the same on every version because the request handlers are what bound them.

### Decision
- `gunicorn==23.0.0` in requirements.txt (with the reason, and "run the load test before changing this").
  It has the gthread worker and every flag of the Render start command (`app:server --bind 0.0.0.0:$PORT
  --workers 1 --threads 16 --timeout 180`); it has no `--control-socket` (25+), which the start command doesn't use
  and the load test only adds when `--help` lists it. Classifiers go up to Python 3.12; only 3.12 was tested here.
- No trade-off needed: everything passes on 23.0.0. What 24+ adds (an ASGI worker and the uWSGI protocol in 24,
  HTTP/2 in the gthread worker by 26) isn't used by this app.
- Procfile brought in line with the Render start command (it lacked `--threads 16`; Render uses the dashboard's
  start command, not the Procfile).

## 4. Final verification (this branch, Python 3.12.3, gunicorn 23.0.0 unless noted)
| suite | result |
|---|---|
| `tools/test_sst_fetch.py` | 27/27 (new: old frames redrawn without NOAA) |
| `tools/test_sst_hang.py` | 18/18 |
| `tools/test_sst_ui.py` | 9/9 (new: 3 overlay checks; the saved-week check ran on 2026-10-15) |
| `tools/test_labstats.py` | 24/24 |
| `tools/load_sst_outage.py --extra-path <gunicorn 23.0.0>` | unreachable ok, render ok, slow ok (16 idle connections: 0.00 to 0.01 s) |
| same on 26.2.0 / 25.1.0 (comparison) | each scenario fails only the idle check: 4.5 s / no answer in 30 s |
| `node tools/smoke_sst.mjs --base http://127.0.0.1:8072` (gunicorn 23.0.0, a fresh copy of the cache, so the first load redrew the saved week) | 113 checks, all passed, at 1440x900, 390x664 and 820x1180 |
| `node tools/boot_sst_pages.mjs --rounds 10 --pages 4` (gunicorn 23.0.0) | 40/40 cold-boot pages set up; all init checks pass |
| the Render start command, literally (`gunicorn app:server --bind ... --workers 1 --threads 16 --timeout 180`, 23.0.0's console script, bound to 127.0.0.1) | `--check-config` ok; "Starting gunicorn 23.0.0", "Using worker: gthread", threads 16, timeout 180; /sst 200 |

Before and after at 41.3 N, zoom 10 (Martha's Vineyard and Nantucket; the SST's land-masked holes should sit on
the islands): `docs/relaunch-41N-before-after.jpg`. Before, the SST covers the islands' south shores and the
holes sit north of them over the water.

## 5. On Render, after merging and pushing
- The boot log should say "Starting gunicorn 23.0.0" and "Using worker: gthread". The start command doesn't change.
- Check the Python version in the build log. Everything here ran on 3.12 (as the GitHub workflows do); 23.0.0's
  classifiers stop at 3.12. If Render builds with something newer, setting `PYTHON_VERSION` to a 3.12 release in
  the Render environment would match what was tested.
- Saved weeks redraw themselves on their first view after the deploy (about 0.4 s here per week and color mode,
  no NOAA) and are rewritten on the persistent disk: expect "Rendering PNGs on the fly (rerender=False, saved
  frames format 1)" then "Cache written (upgrade)" once per week. Nothing to run by hand.
- Nothing else to set. Callback signatures are unchanged (no hard refresh needed); sst.js gets a new `?m=`
  fingerprint with the deploy.
- Locally, the shared venv still has gunicorn 25.1.0; `.venv/bin/python -m pip install -r requirements.txt`
  brings it to 23.0.0 (the load test's idle check fails on 25.1.0 by design).
