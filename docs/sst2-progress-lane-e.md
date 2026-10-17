# SST sprint 2, lane E (engine and data): progress log

Branch `sst2-lane-e`, worktree `/Users/lchou/Code/sst-sprint2/lane-e`, dev port 8081. Newest entries at the bottom.

## 2026-10-16

- Read BRIEF.md, SPRINT2-DECISIONS.md, design-notes.md + design/src, CLAUDE.md, docs/sst-review.md (E1, R1, T4, T6, R4,
  D3) and the SST code paths (app.py load/serve/cache, data/*, map/overlay.py, assets/sst.js, the seeder, the tests).
- `cache` is a symlink into the main checkout: every dev server and test here runs with `SST_CACHE_DIR` pointing at a
  temp copy, never at `./cache`.
- Real data for the extended box (37.8 to 44.0 N, 76.0 to 64.8 W), week of Oct 9 to 15, 2026, from this Mac:
  one MUR day is 1121 x 621 cells, 5.6 MB as .nc, 1.18 MB on the wire, 1.3 to 1.6 s. 28.9% of the box is land in MUR.
- Encoding measured on that week (0.1 F codes, uint16 base 28 F):
  plain gzip 1002 KB/week; byte planes of deltas 855 to 939 KB; zigzag bytes of a 2D residual 575 to 601 KB
  (left neighbour, gradient a+b-c, MED and (a+b)/2 predictors all within 4%: the 0.1 F quantization noise is the floor).
  Chosen: left-neighbour residual, zigzag in one byte, escapes for big jumps, one mask per week (masks are identical
  across the days), gzip -9. Temporal deltas made it worse (1080 to 1216 KB).
- MUR `analysis_error` (the haze source): checked 12 dates 2005 to 2026. Over the whole box it is 0.36 to 0.40 C on
  every date, 4 or 5 distinct values (ERDDAP gives 0.01 C), never above 0.40. It has smooth spatial structure (higher
  near the coasts and in the northeast, lower offshore) but almost no range, so the haze can only be relative (the top
  of that range). A strided request (every 5th cell, 0.05 deg) is 7 KB on the wire and 0.5 s.
- Lead's additions (mid-task): one cache entry per week (the color scale is a browser choice now), a disk-based cap
  sized from the real disk with least-recently-viewed eviction, old two-variant entries still load, and plain retention
  rules in CLAUDE.md.
- NOTE: pyflakes 4.0.3 got installed into the shared venv at 22:09 by a lint fallback; uninstall before finishing.
- Haze dropped (Luyen, via the lead): no analysis_error fetch, no error grid in GOSST1 (header bytes 40 to 63
  reserved), `setOptions` ignores "haze", `valueAt().estimate` is always null.
- Built: data/sstgrid.py (GOSST1, weekly entry, Auto range, old-entry conversion, store payload), weekly cache in
  sst/weeks/ with /api/cache/weeks and disk-budget LRU eviction (data/cache.py), /api/sst/week/<end>.bin, tenths and a
  per-run day cache in data/erddap.py, the coverage box in config.json, assets/sstengine.js, the readout card in
  assets/sst.js, the seeder's backfill (tools/sync_cache.py) and workflow, the preview builder reading weekly files.
- Seeder run against NOAA from this Mac: 2026-10-15 and 2026-10-14 weeks, 588 / 590 KB files (grid 583 / 585 KB),
  10.4 s and 5.2 s (the second week reused six days from the run's cache).
- Lane V's requests (mid-task): getRange(), colorFor(f) -> [r, g, b] printing as rgb(...), the engine attaches itself,
  opacity through the engine, lock-scale and the server legend out of every callback (both elements kept in the layout
  because lane V's CSS and smoke test read them by id), AREA and AOI_TILE_BOUNDS on the coverage box, the smoke test on
  the canvas.
- Bugs found by the suites and fixed: the worker never got the week when breaks/isotherms were turned on after load;
  returning to /sst crashed the page (attach added the canvas layer before the map was loaded); a second identical tap
  didn't close the card (identical clickData doesn't refire Dash: the marker closes it itself); Leaflet's 200 ms
  tooltip fade left two cards for a moment; the test stubs patched app.mask_land_rasterized, which no longer exists
  there (the fetch and hang suites were downloading Natural Earth).
- Frame times (tools/perf_sstengine.mjs, 390x664 @2x, CPU 4x): decode 63 ms, histogram 54 ms (once per week);
  palette/range/stepped change ~5 ms; crossfade frames median 33 ms, worst 50 ms (idle page 17 ms), same with
  isotherms + breaks; isotherms + breaks 160 to 270 ms per day in the worker.
- Suites: test_sst_ui 9/9, test_sst_fetch 27/27, test_sst_hang 18/18, test_sst_grid 12/12, test_labstats 24/24,
  load_sst_outage (3 scenarios ok), test_sstengine.mjs 20/20, smoke_sst.mjs all checks at 3 sizes (legend skipped
  on this branch alone), boot_sst_pages.mjs all checks (24/24 pages + init paths).
