# SST sprint 2, lane V (look and controls): progress log

Branch `sst2-lane-v`, worktree `/Users/lchou/Code/sst-sprint2/lane-v`, dev server port 8082. Base: main 0ab18f0.

## What was built

- **Colors panel** (`assets/sstlook.js`, `assets/sstlook.css`): built into an empty Dash div `#sst-color-panel`
  (layout/sidebar.py, after the day controls), so it lives in the sidebar on laptops and in the drawer on phones and
  tablets (never on the map there). Chips Auto / Tuna 62 to 76 F / Marlin 70 to 84 F / Gulf Stream (week's 99.5th
  percentile rounded up, 18 F span) / Custom (1 F steps, 28 to 90, + and - buttons, spin buttons), Fit to view,
  Stepped, Breaks (isotherms + highlight, off by default), Depth lines (on). Numbered color bar with end triangles,
  ticks every degree, numbers spaced at least 22 px, the week's histogram above (lightly smoothed), and a line that
  says auto / preset / your range / fitted to the view. Radio-group chips with arrow keys, aria-live range,
  role=img bar with a text label, 44 px targets on touch. Choices remembered in `gotone-sst-colors-v1`.
- **Live legend** `#sst-legend-live` (layout/mapview.py): compact bar + numbers + which range + the OISST warning.
- **Engine seam**: everything color-related goes through `window.SSTEngine` (brief API). Until an engine is on the
  page, `html.sst-engine-on` is off: the color part of the panel and the live legend stay hidden and the page looks
  as before (server legend, "Same colors every week"). The depth lines, spot labels and dark basemap work either way.
- **Dark basemap** for /sst only: Esri World_Dark_Gray_Base + World_Dark_Gray_Reference (pane z 440). Keyless
  (checked: 200 image/jpeg without a key), attribution from the services' copyrightText plus "Powered by Esri".
- **Depth contours**: `tools/build_contours.py` (ETOPO 2022 15 arc-sec from CoastWatch ERDDAP) ->
  `assets/sst/contours.json.gz` (GOCONT1), served by `/api/sst/contours.json` (map/depth.py, gzip + ETag + a week of
  cache, versioned URL). Drawn on our own canvas in `sst-depth-pane` (z 430): above the SST (410) and fishing
  activity (420), below the place names (440) and spots (450). Labels along straight stretches, cut into the line,
  text dark or light by the SST color under it, never over spots, spot names or controls.
- **Spot labels** (R6): canvas in `sst-spotlabel-pane` (z 455); canyons (short names) from zoom 8, all from zoom 9.
- **Hudson Canyon** to the canyon head, 39.642 N, 72.440 W (see Verification).
- **How it works** rewritten for the new colors (why the spectrum, Auto, presets, Fit to view, the color bar,
  Stepped, Breaks, depth lines, spot names, mapped area, sources).
- **Analytics**: `preset_auto`, `preset_tuna`, `preset_marlin`, `preset_gulfstream`, `preset_custom`,
  `custom_range`, `fit_view`, `stepped`, `breaks`, `contours` in `labstats.EVENTS["sst"]` and the contract; sent by
  sstlook.js handlers only (not defaults, not restored choices).
- **Haze dropped** (Luyen, Oct 16): no toggle, event or copy; the clouds paragraph stays.

## Verification

- Hudson Canyon head: ETOPO 2022 15 arc-sec, the 300 m isobath's most inshore point (shelf-normal 325 degrees) is
  39.6421 N, 72.4396 W (292.6 m there), recomputed by the builder into the asset's `checks`. NOAA ENC general-band
  depth contours (cells US2EC03M / US2EC04M, the 12300 / 13003 scale band, from NOAA ENC Direct
  `enc_general/MapServer/64`) wrap the head with the 50-fathom line at about 39.66 N, 72.46 W; their 100-fathom line
  runs up the canyon's west wall to 39.61 N, 72.42 W. The old POI (39.54, -72.05) is 35 km (19 nm) ESE on the slope.
- Contours checked by eye at zoom 10 and 11 over Hudson Canyon against an ETOPO hillshade and the ENC lines.
- Esri terms: the legacy server.arcgisonline.com tiles answer without a key; Esri's current docs steer production
  apps to the keyed basemap service and require "Powered by Esri" + data attribution (both shown). Same endpoints the
  live app already uses for /sst (Light Gray) and /currents (Dark Gray).

## Measurements

- Contours asset: 58,765 bytes gzipped (57 KB; budget 300), 178 KB JSON, 24,393 vertices, 6 levels
  (30 fa 4935, 40 fa 4543, 100 fa 7988, 500 m 2152, 1000 m 2323, 2000 m 2452). Build: 7 s download (16.9 MB,
  cached), 1.3 s processing.
- Phone profile (390x664 at 3x, CDP CPU throttle 4x): contour + spot-label redraw 5.3 to 7.9 ms per moveend over six
  views at zoom 7 to 11; panel + legend render 1.3 to 5.0 ms. (The SST recolor itself is lane E's.)
- Week payload: unchanged by lane V.

## Tests (all on this branch, with the test-only engine stub injected where the page needs an engine)

- tools/test_sst_look.py (new): 11 passed
- tools/test_labstats.py: 24 passed (whitelist test now covers sstlook.js and the contract)
- tools/test_sst_ui.py: 9 passed; tools/test_sst_fetch.py: 27 passed; tools/test_sst_hang.py: 18 passed
- tools/load_sst_outage.py --port 8082: unreachable ok, render ok, slow ok
- node tools/boot_sst_pages.mjs --port 8082: all checks passed (24/24 pages)
- node tools/smoke_sst.mjs --base http://127.0.0.1:8082: all checks passed at 1440x900, 390x664, 820x1180
- Page changes /sst -> /currents -> /sst: the look re-attaches to the new map, no errors.

## Edits in shared files

- app.py: one import and the `/api/sst/contours.json` route (block before "Currents page data"). Nothing else.
- layout/mapview.py: dark basemap, `sst-labels-pane`, `#sst-legend-live`, `#sst-look-data` (contours URL + spots).
- layout/sidebar.py: `#sst-color-panel`; Color strength default 1.0 (was 0.7).
- tools/smoke_sst.mjs, tools/test_labstats.py, CLAUDE.md, CHANGELOG.md.

## For lane E and the lead at merge

See the sprint report; kept here in short:
1. Engine: fire `ready` when a week's data is in (each week); `histogram()` over the whole coverage, all days;
   `setOptions({stepped, isotherms, breaks})` (no haze); `fitToView()` sets and returns the fixed range. Optional
   but better: `getRange()` -> `{mode, min, max}` and `colorFor(f)` -> `[r, g, b]` (the bar then matches the map
   exactly; without them Stepped bands are drawn at the band's middle, `floor(f) + 0.5`). sstlook.js doesn't call
   `attach(map)`.
2. Opacity slider -> `SSTEngine.setOptions({opacity})` (replaces update_sst_opacity); default is now 1.0.
3. Retire `lock-scale` (hidden when the engine is on; still an Input of fetch_sst_data) and the server legend
   (`legend-container`, hidden when the engine is on) when the PNG path goes.
4. sst.js first view (`AREA`, `GROUNDS`) and mapview `AOI_TILE_BOUNDS` -> the new coverage box.
5. smoke_sst.mjs "SST image on its bounds" and "colors under 10 taps" read the PNG overlay; retarget to the engine.
6. How it works assumes 0.1 F data and the larger box for new weeks, whole degrees / the old box for weeks saved
   before Oct 2026 until refreshed: confirm against lane E's behavior.
7. Nothing to run or set on Render or GitHub for lane V (asset committed, no env vars, no seeder change).
