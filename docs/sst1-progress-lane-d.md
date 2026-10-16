# SST sprint 1, lane D (data and loading): progress log

Branch sst1-lane-d, worktree /Users/lchou/Code/sst-sprint1/lane-d, dev server port 8061.

## 2026-10-16

- Read BRIEF.md, docs/sst-review.md (2.2, 2.4, 2.6, 2.7, 2.9, 3), CLAUDE.md, and the SST code paths
  (data/erddap.py, data/cache.py, data/geo.py, app.py fetch/precache/upload/GFW, layout/mapview.py,
  layout/sidebar.py, tools/sync_cache.py, seed workflow).
- Probed ERDDAP from this Mac (residential):
  - coastwatch.pfeg jplMURSST41 time[last] = 2026-10-15T09:00Z (200, 0.4 s). One MUR day for the AOI is
    4.96 MB as plain .nc but 0.84 MB with Accept-Encoding gzip (requests sends that by default), 1.0 to 1.3 s.
  - upwell.pfeg 302-redirects to coastwatch.pfeg (same backend): dropped.
  - coastwatch.noaa.gov has no jplMURSST41 (404): dropped. No other public ERDDAP I tried
    (polarwatch, cwcgom, apdrc, pacioos, ioos, ncei, marine.ie) serves that id.
  - OISST v2.1: ncdcOisst21Agg_LonPM180 (final, last 2026-10-01) and ncdcOisst21NrtAgg_LonPM180
    (NRT, 2020-05-04 to 2026-10-15). Variable sst [time][zlev][latitude][longitude], degree_C,
    _FillValue -9.99, 0.25 degree grid, lon -180..180. A whole OISST week for the AOI is 20 to 25 KB in
    one request. The NRT request got one 503 ("Wait a minute, then try again"), then 200 three times.
  - ERDDAP 404 bodies: "Currently unknown datasetID=..." for a dead dataset; "greater than the axis
    maximum" / "no matching results" when the date isn't in the dataset. ERDDAP .nc is NetCDF-3 (CDF\x01).

### Built (review codes)
- P-2: data/erddap.py rewritten. Known dataset ids from config.json (no search), one Deadline per week
  (22 s; connect 4 s / read 8 s capped by time left; chunked reads with a deadline check), 429/5xx/network
  retried after ~1 s and ~2.5 s while time allows, 3 days at a time newest first. Returns at the deadline.
  Partial weeks served with missing_days. Honest SSTUnavailable(reason) with the 3 nearest saved weeks as
  buttons (pattern ids "saved-week"), drawer opened on phones. Per-week lock (two visitors, one fetch), a
  failure isn't re-asked for a minute. Queue for the seeder (wanted.json, GET /api/cache/wanted, upload
  unqueues; sync_cache.py --max-wanted).
- D2: OISST v2.1 final (ncdcOisst21Agg_LonPM180) + NRT (ncdcOisst21NrtAgg_LonPM180), sst[time][zlev 0.0],
  one request per dataset per week, only when MUR sends no day before the last 5 s of the budget. Masks and
  overlay bounds use cell edges (geo.grid_edges), which matters at 0.25 degree.
- T1: root cause found: the day request ran T00:00 to T23:59:59 and ERDDAP rejects a stop past the axis
  maximum (09:00Z on the newest day), so the newest day always 404'd. Now exact stamps (MUR 09Z, OISST 12Z)
  and a check of the date returned. latest_mur_date() from time[last], cached 1 h, non-blocking for the
  layout; date picker default and max = newest day; first load always the newest week; seeder starts there
  and runs twice daily.
- T3: payload meta (data/sstmeta.py) for fresh fetches and cache hits, old entries filled in; the fetch
  sets the date box to the week served. Refresh rules by meta (preliminary 12 h, revision window 24 h,
  OISST/incomplete 6 h), saved copy served if a refresh fails.
- L2: chart, bathymetry, GFW layers are created only while ticked (LayerGroup holders in the panes).
- L3: GFW proxy reads ?dr= per request (validated), style and tile caches keyed by it, module global gone;
  empty tiles are a transparent PNG (no console errors).
- L4: layout/mapview.GFW_NOTE for lane U to display.
- Land mask cached per grid (0.28 s/day -> 0.003 s), Natural Earth download capped and not retried for
  10 min after a failure.

### Verified
- tools/test_sst_fetch.py: 26 passed (fake ERDDAP: 503 then success, 403, dead dataset, timeouts within
  budget, total failure within budget, partial weeks, gap days, newest-day clamp, OISST final/NRT split,
  meta, old entries, refresh rules, saved copy on refresh failure, closest saved week, two visitors one
  fetch, failure reuse, GFW per-request range, layers, wanted route + upload unqueue, sidebar dates).
- tools/test_labstats.py: 24 passed.
- Live on :8061 against real NOAA (headless Chrome, lane-local cache copy):
  newest week Oct 9 to 15 uncached 6.6 s to usable map (server 5.6 s), cached 0.2 to 1.7 s;
  uncached 2014-08-20 6.5 s, 2009-07-15 6.1 s, 2012-07-04 7.0 s (phone), 2005-08-01 5.1 s (tablet),
  2018-09-15 6.0 s, 2007-08-25 6.5 s. Meta correct each time. Hidden layers: 0 tile requests at all sizes;
  all three on: 20 GEBCO, 20 chart, 12 GFW with dr=2026-10-09,2026-10-15. Events load/step/play/chart/
  bathymetry/gfw still sent. MUR forced dead: OISST weeks served (recent across final/NRT, 2015) and
  labelled; everything dead: honest message in 4 to 7 s, saved weeks offered, clicking one loads it and
  sets the date box, no stuck overlay, phone drawer opens. 0 console errors throughout.
- Seeder dry run: newest MUR day 2026-10-15, two weeks built (7.8 s, 4.7 s).
