# SST sprint 1, lane U ("explain and read the map"): progress log

Branch sst1-lane-u, worktree /Users/lchou/Code/sst-sprint1/lane-u, dev server port 8062.

## 2026-10-16
- Read BRIEF.md, docs/sst-review.md (sections 1 to 4), CLAUDE.md, the Currents page (layout/currentsview.py,
  assets/currents.js, assets/currents.css) and the SST code (app.py SST callbacks, layout/sidebar.py,
  layout/mapview.py, map/*, assets/gotone.css, assets/tooltips.css, assets/labs.js, the analytics contract).
- Found while reading: every map tap and every frame change uploads the whole sst-store (the week's PNG frames)
  to the server, because handle_map_click has it as State and render_click_marker as Input. A tap is also two
  sequential server round trips (classify, then read the temperature). Plan: classify taps in the browser
  (assets/sst.js, pixel hit test) and give the readout callback a small key store instead of the frames.
- [CHECK] verification for How it works: MUR sensors and history from the ERDDAP metadata (jplMURSST41
  index.json: MODIS Terra/Aqua, AMSR2 GCOM-W, AVHRR MetOp-B, in situ; "nominal 4-day latency; replaced nrt
  (1-day latency)"; "most recent 7 days usually revised"); warm-core rings drift SW about 5 to 6 km a day and
  last weeks to months (URI statistical study, NAFO reports); MUR agrees with the GHRSST ensemble to 0.36 C on
  average (Chin et al. 2017 abstract); diurnal warming about 3 C in favorable conditions, more in extremes
  (Gentemann 2003/2008); Global Fishing Watch fishing effort runs about 72 hours behind (GFW data
  availability page). All 15 source links return 200 with curl -sIL.
- Magnetic variation: tools/build_declination.py implements WMM-2025 (coefficients from the public WMM.COF;
  this Mac has a copy in CoreLocation). It reproduces NOAA's calculator (Hudson Canyon 13.09 W vs 13.1,
  Stellwagen 14.17 W vs 14.2). map/declination.py is a 0.5 degree grid over the area with yearly change;
  bilinear error 0.001 degree. Variation over the water runs about 11.9 W (off Cape May) to 14.7 W (Cashes).
- Built (commit c8b0e74): How it works modal (layout/sstabout.py) + header/sidebar links and the first-visit rule;
  status strip (assets/sst.js SST.status); tap routing in the browser (SST.classify, pixel hit test) and
  render_selection (readings / spot cards / measurements redrawn per day, week strip in the card); measure
  Cancel / Measure again / Clear, readable panel, true + magnetic; legend copy; plain copy; flex page; drawer
  under 1200 px and on touch; floating playbar; 44 px targets; first view; tooltip selectors; slider brand +
  weekday marks; dashed outline dropped; emoji gone. Tap readout: ~50 to 90 ms locally (was two round trips
  uploading ~180 KB of frames each).
- Smoke test tools/smoke_sst.mjs (35 checks per size) and tools/test_sst_ui.py; tools/test_labstats.py passes
  (the whitelist test greps for ev("name") literals, so the SST calls are written that way).
- Fixed along the way: stale Tooltip className when layers changed order (split measure-layer / click-marker);
  fractional zoom showed basemap tile seams (whole zooms only); phone strip lost freshness to ellipsis (short
  forms, priority order); failed loads were invisible on phones (strip shows them).
- Lane D finished mid-sprint. Reverted my edits inside fetch_sst_data (lane D rewrote it): weekday marks moved
  to a clientside callback on sst-key, sidebar status is lane D's _status_line, and the button label needs
  FETCH_LABEL = "Load week" at merge. The strip reads lane D's extras (missing_days, complete,
  preliminary_days, week_match, queued). Fishing-activity note uses lane D's GFW_NOTE when merged.
- Trial merge (git merge-tree sst1-lane-u sst1-lane-d): clean except CHANGELOG.md (both entries at the top).
  Ran the merged tree from a scratch copy (isolated cache copy, FETCH_LABEL patched) on 8062: opens on Oct 15
  fetched live in 3.6 s with real meta; smoke test passes at all three sizes.
