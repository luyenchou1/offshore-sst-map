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
