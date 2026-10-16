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
