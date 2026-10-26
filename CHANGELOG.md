# Changelog

## 2026-10-26 - Labs: Your Catches on catches.gotoneapp.com

### Changed
- The Your Catches tile and the `/catches` forwards (`labs.gotoneapp.com/catches`, and `gotoneapp.com/catches` through its Squarespace URL mapping) now go to catches.gotoneapp.com, the GotOne web app's own Labs address (a Firebase Hosting custom domain Tom Fuda set up; CNAME `catches` -> got-one-362618.web.app in Squarespace DNS). The Firebase address keeps working. The web app's GotOne logo now links back to gotoneapp.com/labs.

## 2026-10-21 - Labs: the GotOne web app

### Added
- A Labs tile for the GotOne web app (the first of the four), Tom Fuda's dashboard where anglers sign in with their GotOne account to see their catches on a map, charts by month and species, and filters for tide, moon, wind and water temperature. Here and on gotoneapp.com/labs (rerun `tools/build_squarespace_labs.py` and repaste). The tile picture (`assets/labs/catches.jpg`) is the dashboard's catch map with its monthly and species charts, from Luyen's own account at Gulf-wide zoom.
- `labs.gotoneapp.com/catches` (and `sst.gotoneapp.com/catches`) forwards to it, like `/striper-recount`; the short link is `gotoneapp.com/catches` (a Squarespace URL mapping). It points at got-one-362618.web.app until catches.gotoneapp.com is set up in Firebase.

### Removed
- The old gated catch map at `/catches` is no longer reachable (the forward comes first). Its code, `CATCHES_ACCESS_KEY` and `POST /api/catches/upload` are unchanged.

## 2026-10-17 - Labs previews show the new SST look

### Changed
- The Offshore SST tile (Labs home, here and on gotoneapp.com/labs), its link-preview card and the Labs card are now pictures of the live map: the spectrum colors with the auto range, depth lines and canyon names, dark land, New Jersey to Nantucket with the canyons and the Gulf Stream (Oct 15, 2026). The SST card's band moved up to include Long Island.
- The Labs card's link (`og:image`) carries a content hash like the tool cards, so link previews fetch a rebuilt card instead of a cached one.

### Added
- `tools/shoot_sst_preview.mjs`: one load of the SST page in headless Chrome, basemap tiles blocked (the dark basemap's land color fills in), controls, legend and spot dots hidden, the map saved at 2x to `build/sst-shot.png`; `tools/build_labs_previews.py` crops it to the 16:9 tile (`--sst-shot`). The old matplotlib SST preview (classic colors) is gone.
- `tools/build_social.py`: Instagram images in `build/social/` (a 3-slide 4:5 carousel, 1:1 squares for SST and Currents, 9:16 stories with room for a link sticker) and caption drafts. SST maps come from `shoot_sst_preview.mjs --size` (the map alone at a fixed size, zoom 7 at 2x so depth labels read on a phone) with canyon names added from `map/pois.py`; Currents from `currents_preview()`, which now takes a size, width and centre. Its notes said the Currents preview was mid-flood; it is The Race's strongest ebb (2.8 kn, 1.5 h before Montauk low water).

## 2026-10-17 - SST: smooth coastlines and smooth water when zoomed in

### Changed
- **The coast is a smooth line at every zoom.** The colors used to end in a staircase of MUR's 1 km squares over the basemap's coastline (Hampton Bays at zoom 11 looked like a bar chart). They now stop at a detailed OpenStreetMap coastline, antialiased, with the basemap showing on land as it always did (its ponds, rivers, roads and built-up areas stay). Small islands and ledges that the basemap leaves out (many in Casco Bay) are drawn in the basemap's land color, and from zoom 11 a thin rim of land color hides any sliver where the basemap's coast and OpenStreetMap's differ.
- **The water reaches the shore.** MUR's grid stops short of the shoreline along beaches and in bays; the map now carries the color of the nearest water up to about 3 km toward shore (3 cells, by repeated neighbour averages) so the colors meet the coastline. This is drawing only: a tap there says "No satellite reading this close to shore", and the color bar's histogram, Fit to view, isotherms, breaks and the measure tool never use these values. Rivers far up from the sea (the upper Delaware, the Hudson) stay uncolored, as before.
- **Zoomed in, the water is smooth.** From the zoom where a MUR cell is 3 pixels wide (zoom 9), the map blends the temperature between cell centres instead of drawing flat 1 km squares, so the edge of the data is a smooth curve; with Stepped on, the bands are cut from the same lightly smoothed field the Breaks lines come from, so each band's edge is a temperature line instead of a staircase.
- Each day is drawn once per view (colors, isotherms and coastline together), so stepping or playing through the week only blends two finished pictures: on a phone-speed test profile the day crossfade now runs at 60 frames a second (frame gaps median 17 ms, was 33 ms with slow frames up to 170 ms).
- How it works: a "Near the coast" paragraph, the coastline in the sources, the near-shore row of the reliability table.

### Added
- `tools/build_land.py`: builds the coastline from OpenStreetMap's land polygons (osmdata.openstreetmap.de, ODbL; downloaded once, 928 MB, into `build/land`) into four levels: zoom 5 to 9 in one file loaded with the page (116 KB), and zoom 10, 11 and 12 in one file per 1-degree tile, loaded only when a view needs them (1.3 MB for the whole box). Served from `assets/sst/land/` at `/api/sst/land/<name>.json` (gzip, cached a week, no outside call). OpenStreetMap is already credited in the map's attribution.
- `assets/sstland.js` (`window.SSTLand`): loads and prepares the coastline, picks the level and the pieces in view.
- Tests: the fill never reaches a reading, filled water drawn and filled land cleared, no isotherm on land, the rim, the panes' order, interpolation off at low zoom and on (bilinear) at high zoom (`tools/test_sstengine.mjs`); the coastline assets, budgets, land and water points at every level and the route (`tools/test_sst_look.py`); coast timings in `tools/perf_sstengine.mjs --only coast`.

## 2026-10-17 - SST sprint 2 (lane V): new colors and controls, a dark map, depth lines and spot names

### Added
- **Colors panel** (sidebar on laptops, inside the drawer on phones and tablets): range chips Auto, Tuna (62 to 76 °F), Marlin (70 to 84 °F), Gulf Stream (the week's warmest water and 18 °F below) and Custom (coldest and warmest ends in 1 °F steps with + and -, 28 to 90 °F); Fit to view (the colors spread over the water on screen); Stepped (1 °F bands) and Breaks (isotherms and the break highlight, off at first) passed to the SST engine; Depth lines on or off. A numbered color bar with triangles for "and beyond" and the week's water as a small histogram above it, saying whether the range is auto, a preset, your own or fitted to the view. Keyboard and screen readers: the chips are a radio group (arrow keys), the ends are spin buttons, the range is announced; 44 px targets on touch. Choices are remembered on the device (`gotone-sst-colors-v1`). The color part shows once the SST engine (lane E) is on the page; until then the page looks as before.
- **A live legend on the map** that follows the panel (range in use, numbers, the OISST warning), compact at the top centre on phones.
- **Depth lines above the colors**: 30, 40 and 100 fathoms and 500, 1000 and 2000 m from NOAA ETOPO 2022 (15 arc-seconds), drawn as thin dark lines with a faint light edge, the 100-fathom shelf edge heavier, with small labels along straight stretches (fathoms on the shelf, meters beyond) that break the line and never sit on a spot, a spot name or the map's controls. Crisp at every zoom; canyon walls hold their shape at zoom 9 to 11. Built offline by `tools/build_contours.py` into `assets/sst/contours.json.gz` (57 KB; 24,393 points), served at `/api/sst/contours.json`.
- **Spot names** beside the circles: the canyons (short names) from zoom 8, every spot from zoom 9, The Dump over its box; a name that would cover another, a spot or a control waits for the next zoom.
- Analytics events (only when the reader changes something): `preset_auto`, `preset_tuna`, `preset_marlin`, `preset_gulfstream`, `preset_custom`, `custom_range`, `fit_view`, `stepped`, `breaks`, `contours`.
- `tools/test_sst_look.py`; `tools/smoke_sst.mjs` now checks the panel at the three sizes, the presets, the keyboard, the toggles, remembered choices (no events on a reload), the contours and their toggle, spot labels and Hudson Canyon's position. `tools/sst_engine_stub.js`: a test-only stand-in for the engine (never served).

### Changed
- **Dark basemap on the SST map** (Esri Dark Gray Canvas, keyless, with its place names above the colors): land is dark gray so the water stands out. Currents and the catch map are unchanged.
- **Hudson Canyon's spot moved to the canyon head** (39.642 N, 72.440 W; was 39.54 N, 72.05 W, about 19 nm east-southeast on the slope), where ETOPO 2022's 300 m isobath reaches furthest inshore and NOAA's chart contours wrap the head.
- Color strength starts at 100% (the colors are solid; lower it to see the chart or bathymetry underneath).
- How it works: the new colors and presets, Fit to view, the color bar, Stepped, Breaks, the depth lines, spot names, the wider area, the sources. With the engine on, "Same colors every week" steps aside (Tuna, Marlin and Custom are fixed ranges).
- The cloud haze planned for this sprint was dropped: MUR's analysis_error barely varies (0.36 to 0.40 °C everywhere), so it can't show where clouds were filled in. How it works still explains that clouds are filled in with estimates.
## 2026-10-16 - SST sprint 2 (engine and data): the map is drawn in the browser, at 0.1 °F, over a larger area

### Changed
- **The map covers more water**: 37.8 to 44.0 N and 76.0 to 64.8 W, east past Oceanographer Canyon and south far enough to show the Gulf Stream (it stopped at 38.8 N and about 69 W). Land is still masked. Fishing-activity tiles and the first view use the same area.
- **Temperatures to a tenth of a degree** ("71.3 °F") instead of whole degrees, so breaks under a degree show and the colors are smooth instead of terraced.
- **The browser draws the map** (`assets/sstengine.js`): the server sends each week once, about 0.6 MB, and the page colors it, so a tap reads the temperature at once (no trip to the server), the day changes with a short crossfade, and colors and ranges change without reloading the week. The colors sit where their readings are at every zoom.
- **Weeks saved before this release** still open, in whole degrees over the old area, until the twice-daily seeder rebuilds them: the 10 newest weeks on its first run, the rest newest first over about a week.
- **One saved file per week** (no separate "same colors every week" copy), in `cache/sst/weeks/`; the cache now keeps within a share of its disk and lets go of the weeks nobody has opened for the longest first.
- "Same colors every week" and the server-drawn legend are gone from the page's workings; the Colors panel's presets and live legend replace them. Color strength starts at full.

### Added
- `GET /api/sst/week/<end>.bin` (the week for the page, from memory or disk, never NOAA) and `GET /api/cache/weeks` (token: which weeks are saved in which format, and the disk numbers).
- The seeder rebuilds old-format weeks (`tools/sync_cache.py --backfill`, 24 a run in the workflow, resumable, gentle with NOAA, tolerant of its 503s), fetches each day once per run, and won't upload a week without the land mask.
- Tests: `tools/test_sst_grid.py`, `tools/test_sstengine.mjs`, `tools/perf_sstengine.mjs`; the smoke and boot tests read the engine's canvas.

### Not done
- **No cloud haze**: NOAA's error estimate for MUR is 0.36 to 0.40 °C everywhere on every date checked, so it can't show where clouds were filled in. A cloud layer would need a different source.

## 2026-10-16 - SST relaunch: the map is drawn where its readings are, and works on every load after a deploy

### Fixed
- **The sea-surface temperatures were drawn up to 5.3 km (2.9 nm) north of where they are**, worst along 41.3 N (Rhode Island Sound, just south of Martha's Vineyard) and about 3.6 km on average. The map stretches each day's image in Web Mercator while the data's rows are evenly spaced in latitude, so the colors sat north of their true position while a tap read the right spot: the color under your finger could be a degree off its own reading. Each image is now drawn on Mercator rows: a cell's edge lands within 0.28 km of true. Saved weeks redraw themselves from their own data the first time they're opened (no new download from NOAA).
- **Right after a deploy or restart, the SST page sometimes came up without its helpers**: taps did nothing and fishing-activity tiles weren't retried (and the page could miss its styles and visit counter). The server sent some of the first pages before it had finished listing the page's scripts (8 of 32 such loads in testing). It now finishes that before it serves anything. The page's own script also no longer depends on load order: it waits for the map library if it isn't there yet, picks up a map that's already on the page, and sets itself up only once.

### Changed
- **gunicorn pinned to 23.0.0** (was "21.2 or newer", which installed 26.2.0). From version 24 a connection that sends nothing holds one of the 16 request threads (indefinitely in 24 and 25, 5 s in 26); 23.0.0 doesn't. The load test now checks this.
- Each day's image is 1041 rows instead of 501 (a week's images about 190 KB instead of 152 KB).

### Added
- `tools/boot_sst_pages.mjs`: boots the server as on Render again and again and opens four SST pages at once right after each boot; checks the page script's setup paths.
- Checks that the color drawn at a map position matches the reading there: `tools/test_sst_ui.py` (pixel under 600 random points, a saved week), `tools/test_sst_fetch.py` (old saved weeks redrawn without NOAA), `tools/smoke_sst.mjs` (in Chrome, the color under 10 taps).

## 2026-10-16 - The server no longer stops answering when NOAA or Global Fishing Watch is down or slow

### Fixed
- **A NOAA or fishing-activity outage could stop the whole site** (all pages, Currents too) until a restart, with nothing in the log. All pages share 16 request threads, and requests waiting on an outside server held them: fishing-activity tiles queued behind one call to Global Fishing Watch at a time, and a slow answer from NOAA or GFW could hold a thread for hours. Every outside call now has a hard time limit, no lock is held while one runs, a busy or failing server is answered at once instead of waited on, and at most a few requests wait on NOAA or GFW at any moment. Details: `docs/incident-2026-10-16.md`.
- **When NOAA can't be reached, a week answers at once** with "We can't reach NOAA's data server right now", the nearest saved weeks, and "We'll have this week ready within a day" (it goes to the seeder). After a failed connection NOAA isn't asked again for a minute; it used to cost every reader 22 seconds.
- **Fishing activity**: when GFW fails, tiles come back empty at once and aren't cached for a day; when the server is busy, the page asks for the tile again a moment later. GFW failures are now logged.
- A week that couldn't be sent wasn't asked for again for a minute after the *last* request, so a popular week could stay unfetched for as long as people kept asking. Now the minute counts from the failure.
- `/api/precache`'s 120-second limit per week never applied (it waited for the fetch anyway); it does now, and precache never saves a week without the land mask.

### Added
- **Diagnostics for a stuck server**: `kill -USR1 <pid>` in the Render shell writes every thread's stack to the log; `GET /api/debug/threads` (X-Upload-Token) lists what's running, each outside server's state and every stack; any request running over 60 seconds is logged with its stack. Runbook in CLAUDE.md (Incident 2026-10-16).
- `tools/test_sst_hang.py` (18 outage tests with real sockets) and `tools/load_sst_outage.py` (gunicorn as on Render, NOAA and GFW failing three ways, 2 x 50 concurrent requests, every answer within 30 s); `tools/netsim/` simulates the failures.
- Settings (optional): `SST_NOAA_READERS`, `NOAA_MAX_CALLS`, `GFW_MAX_CALLS`, `SLOW_REQUEST_S`, `REQUEST_THREADS`.

### Changed
- The fetch no longer waits for NOAA's newest-day lookup (one lookup at a time, in the background); the connect timeout to NOAA is 3 s (was 4).
- A week fetched before the land mask finished downloading is shown but not saved.

## 2026-10-16 - Offshore SST: explained, dated, and readable on phones and tablets

### Added
- **How it works** (header link and a sidebar link; opens once on a first visit, like Currents): what breaks, warm eddies, the shelf edge and cold water near the beach look like, how to use each control, how the adaptive colors work, where the data comes from (MUR's satellites, clouds, how fresh it is, the OISST backup), a "How reliable is it?" table and the sources. Facts checked against MUR's ERDDAP metadata, Chin et al. (2017), Global Fishing Watch and WMM-2025. Opening it counts as `how_it_works` (not the automatic first-visit opening).
- **A status strip under the map**: the day on the map with "day 7 of 7", the source actually served ("MUR 1 km (NASA JPL, via NOAA CoastWatch)" or "OISST 25 km, broad pattern only"), "updated 3 hours ago", "newest available Oct 15", missing days, "preliminary" on the newest days, and which week this is when a nearby saved week or an older copy was served. On a phone the date is always on screen (strip and play bar), so a screenshot carries its date. A failed load shows there too.
- **Floating day controls** on phones and tablets (back, Play, forward and the date), as on Currents.
- **Magnetic course** in the measure tool: "187° true, 201° magnetic", from a WMM-2025 grid over the area (about 12° W off Cape May to 15° W in the Gulf of Maine), plus the temperature at each end.
- `tools/smoke_sst.mjs` (the page at laptop, phone and iPad sizes: errors, layout, How it works, the strip with a fake meta, readings across days, spots, The Dump, measure, analytics events) and `tools/test_sst_ui.py`.

### Changed
- **Readings, spot cards and measurements stay when the day changes** and show that day's temperature; a card also lists the week's seven values at that point. A second tap on a card closes it.
- **Taps go where you tap**: a spot opens only from its circle (about 16 px for a finger, 9 for a mouse, the same at every zoom), so the water right next to a spot or anywhere inside The Dump can be read. Taps are sorted in the browser, and the readout no longer uploads the week's images with every tap: about 0.05 s locally instead of two server round trips.
- **The legend** says "this week's range" (or "fixed scale"), with round ticks and what the end colors mean; it moves to the top on phones. The fishing-activity layer gets a one-line note.
- **iPad and touch screens** get the slide-in drawer instead of a 141 px sidebar; **the page fits the screen** (no 8 px overhang, no scroll when the map is clicked, nothing hanging off the right edge); 44 px tap targets; brand-blue day slider with weekday marks.
- **First view** on the fishing grounds where the whole area doesn't fit (phones), then the reader's last view.
- **Plain copy**: "Offshore SST", "Load week", "Same colors every week", "Color strength", "Show all / Hide all"; the version moved into How it works; no emoji on the Measure button. The measure readout is readable on the navy sidebar, and Measure / Cancel / Measure again / Clear say what will happen.
- The dashed area outline (which ran over land from New York Harbor to Maine) is gone. Tooltip styles now apply (their CSS never matched Leaflet's markup).
## 2026-10-16 - Offshore SST: every week loads, the newest day, and the page knows what it served

### Fixed
- **Weeks that weren't saved now load in seconds or fail honestly.** The app no longer searches NOAA for datasets on every load (those pages refuse cloud servers); it asks the MUR dataset directly, a few days at a time, newest first, retries NOAA's frequent "busy" answers after a second or two, and gives up at 22 seconds. Uncached weeks from 2005 to 2018 took 5 to 7 seconds in testing; before, they hung for over three minutes and showed "may not be available yet".
- **The newest day can be opened, and the page opens on it.** The request for a day ran to 23:59 and NOAA rejects any time past its newest data, so yesterday's map could never load. Days are now asked for at their exact time, the newest day comes from NOAA itself (checked hourly), and the date box opens on it and allows it.
- **The OISST backup works.** It pointed at a dataset that ended in 2020. It now uses OISST v2.1 (the final series where it reaches, the near-real-time one for the last two weeks), only when MUR sends nothing in time, and the page says it is the 25 km broad-pattern map.
- **Fishing activity shows the week you're looking at.** The tiles used a date range shared by every visitor (set by whoever loaded last); each tile request now carries its own week. Empty tiles no longer log errors.
- **Hidden layers download nothing.** The chart, bathymetry and fishing activity layers are added to the map only while their box is ticked (they used to load every tile at zero opacity).

### Added
- **What was served, in the data.** Every week the page receives carries a `meta` record: MUR or OISST, its resolution, the days actually served, the week asked for and the week served, NOAA's newest day, when it was fetched and whether it came from the saved copy. Old saved weeks get one filled in. The date box now shows the week served.
- **When NOAA can't send a week**, the sidebar says why in plain words and offers the three nearest saved weeks as buttons; on a phone the drawer opens so the message is seen. A week that came back partial, as OISST, or not at all goes on a list the daily seeder fetches ("We'll have this week ready within a day").
- **Partial weeks** are shown with the days NOAA sent, and the sidebar names the missing days.
- **Saved weeks stay fresh**: a week fetched while its newest days were still preliminary is refetched after 12 hours (24 inside MUR's 7-day revision window), an OISST or partial week tries MUR again after 6; if NOAA doesn't answer, the saved copy is shown.
- `tools/test_sst_fetch.py`: 26 checks of the data path against a fake NOAA (busy then fine, refused, a dead dataset, timeouts, gap days, the newest day, partial weeks, the honest failure, two visitors on one week, fishing tiles, layers, the seeder's list).

### Changed
- The seeder starts from NOAA's newest day and runs twice a day (07:00 and 17:00 ET); it also fetches the weeks readers asked for. It uploads only complete MUR weeks.
- `config.json` lists the datasets (MUR, OISST final and near-real-time) instead of three servers and search words; the two servers that only redirected or had no MUR are gone.
- The land mask is computed once per grid instead of once per day, and the Natural Earth download can no longer stall a load.
- Deploying this changes the SST callbacks: open pages need a reload ("Callback function not found" otherwise).

## 2026-10-16 - Labs analytics: feature use, "Was this helpful?", one counter for every Labs tool

### Added
- **One counting service for all Labs tools**, shared with The Striper Recount (striperrecount.gotoneapp.com posts to the same endpoints cross-origin). The rules are written down in `docs/labs-analytics-contract.md`. Page ids now include `recount` and `recount/<page>`.
- **Feature use** (`POST /api/event`): each tool reports what people actually do, at most once per page view per feature, so the numbers read as "page views that did X". SST: loading a week, playing the animation, changing the day, tapping for a temperature or a fishing spot, measuring, opening the spot list, turning on the chart / bathymetry / fishing activity layers, locking the scale, changing opacity. Currents: switching views, picking a day / strength / week, Now, picking a NOAA station or a harbor-inlet estimate, opening How it works, setting the time on the chart, play, step, speed, depth, full screen. Only real changes count, never the defaults a page starts with, and the server ignores anything not on its whitelist.
- **"Was this helpful?"** at the bottom of the SST and Currents sidebars (and their phone drawers): thumbs up or down, then "Thanks!" and an optional private note. Votes and notes are never published; they appear only on the private stats page. Spam guards: a hidden honeypot field, at least 3 seconds on the page, 500 characters, 5 posts per visitor per tool per day and 300 a day in all; our sites only.
- **Stats page rebuilt** (`/labs/stats`): per tool, views and daily visitors (The Striper Recount by page), where visits came from, feature use, Helpful votes; the latest notes; the by-day table per tool; 7 / 30 / 90 / 365-day periods; readable on a phone.
- `tools/test_labstats.py`: 24 checks of the counting and feedback rules, the dashboard and the redirects.
- `LABS_STATS_DIR` (optional) moves the stats database; tests use a temp dir.

### Changed
- Visitor hashes are still deleted after 90 days, but each day's unique counts are kept first, so long periods keep their visitor numbers. Existing counts carry over unchanged.
- The host and home redirects now skip `/api/*`, `/_dash*`, `/assets/*` and `/labs/stats` explicitly, on every host.
- Labs home footer: visits and feature use are counted anonymously, no cookies, nothing about you is stored, Do Not Track respected; feedback notes are private and never published.

## 2026-10-16 — The Striper Recount joins Labs; the Labs home moves to gotoneapp.com/labs

### Added
- **The Striper Recount** tile (the song + interactives on NOAA's 2026 striped bass recount, hosted at luyenchou1.github.io/striper-recount) with its own preview (`assets/labs/recount.jpg`, from the film's poster). External tools render as plain links.
- **`tools/build_squarespace_labs.py`** writes the Labs home as a Squarespace Code Block (`squarespace/labs-code-block.html`, scoped to `#gotone-labs`, no scripts) plus a preview page, from the same `TOOLS` list.
- **`LABS_HOME_URL`** (Render env): once set to `https://www.gotoneapp.com/labs`, the "← GotOne Labs" links go to the Squarespace page and the app's own Labs home redirects there (302). Unset, nothing changes.

### Changed
- Labs hero: "Data-driven tools and explainers for saltwater anglers, built on public data."

## 2026-10-14 — Currents: the map's time box moves under the map

### Changed
- The time / tide / current box no longer floats over the map's top-left corner, where it hid the water on phones (beta feedback from iPhone). It's now a slim strip between the map and the chart: time and tide stage, then the selected spot's current and Montauk's tide. One line on wide screens, two on phones, with a fixed height so the map never jumps; a long spot name trims the tide text, not the current.

## 2026-10-11 — GotOne Labs home page and visit counter

### Added
- **GotOne Labs home** (`/` on labs.gotoneapp.com): the marketing site's look (Helvetica Neue, navy, yellow buttons) with one tile per tool — Offshore SST and Currents — each with a real preview drawn from the tool's own data (`tools/build_labs_previews.py`), the area it covers, one line on what it's for, and a big button. Plain wording for anglers, phone-first, scrolls like a normal page.
- Tools moved to `/sst` and `/currents`; both link back with "← GotOne Labs" (replacing "Currents →" and "← SST Map"). Each page sets its own tab title, and every page carries link-preview tags with a share card (`assets/labs/og.jpg`).
- **Anonymous visit counter** for Labs, SST and Currents, modelled on Plotline's: no cookies, Do Not Track / Global Privacy Control respected, daily unique visitors from a salted hash that's never stored raw, referrer hosts (embedded visits marked), bots and other sites ignored, capped per visitor. SQLite on the persistent disk (`data/labstats.py`); private stats page at `/labs/stats?key=…` (`LABS_STATS_KEY`).

### Domains
- `sst.gotoneapp.com` keeps working: its `/` still opens the SST map (the Squarespace embed), and its Labs link goes to `/labs`. Setting `LABS_HOST=labs.gotoneapp.com` in Render (once that domain is live) redirects its page loads to labs (`/` → `/sst`); APIs are never redirected, so the data workflows keep uploading.

## 2026-10-11 — Currents: five more harbor inlets

### Added
- **Estimated inlet currents for Lake Montauk, Accabonac Harbor, Three Mile Harbor, Northwest Creek and Sag Harbor Cove** (bridge channel), alongside Napeague: arrows at each harbor mouth, "(estimate)" spots in the Spot list (east to west), and a table in "How it works" with each harbor's typical slack and peak timing vs Montauk.
- `tools/build_inlets.py` handles several harbors: harbor areas from NOAA's 3 m elevation model; inlet geometry from a survey (Napeague), or the elevation model across the inlet floored by NOAA chart depths where lidar missed the channel (Northwest Creek, Sag Harbor Cove); the bay tide outside from NOAA's own stations (Threemile Harbor entrance, Montauk Harbor entrance, Sag Harbor), computed from a year of their predictions, or interpolated between two by distance.

### Findings (typical, Sep–Oct 2026)
- Short, wide inlets keep up with the tide outside: Lake Montauk, Accabonac, Northwest Creek and Sag Harbor Cove go slack within minutes of high and low water just outside them. That local tide itself runs later westward — about +50 min at Accabonac, +80 at Northwest Creek and +95 at Sag Harbor vs Montauk — so slack at Sag Harbor Cove's bridge comes about 1½ h after Montauk high water.
- Long, narrow channels hold the harbor back: Napeague (slack ~1h15 after Montauk high water) and especially Three Mile Harbor, whose 4.2 km² harbor drains through a ~450 m jetty channel (slack ~1h55 after Montauk high water, ~50–75 min after the tide at its entrance, peaks ~2.6 kn). Three Mile's timing is the most sensitive to the channel's real size (≈1h10–2h20 across plausible dimensions).
- Lake Montauk still matches NOAA's inside-vs-entrance timing within ±5 min.
- **Checked against the U.S. Coast Pilot**, the only published inlet speeds: Lake Montauk's entrance ~1.2 kn flood (model typical 0.9 kn across the inlet) and Three Mile Harbor's ~3 kn (model 2.1 kn) — the published mid-channel speeds run 30–40% above the cross-inlet averages, as expected, which supports Three Mile's choked result. Lake Montauk's real ebb (~0.5 kn) is weaker than the model's 0.8 kn. Harbor areas agree with the towns' published figures within a few percent. Sag Harbor Cove's inlet was narrowed to the Coast Pilot's 8 ft channel through the bridge's 80 ft opening (~160 m²). "How it works" now lists typical flood / ebb speeds per tide.

## 2026-10-11 — Currents: Napeague Harbor inlet (estimate, prototype)

### Added
- **Estimated harbor-inlet currents**, starting with Napeague Harbor's inlet at Lazy Point. The ocean model's ~1 km mesh has no water inside the small harbors, so their inlet currents are computed from the tide instead: a pond-and-inlet model (the harbor fills and drains through its inlet, held back by the inlet's friction and the water's momentum) driven by the same Montauk tide the page already bundles. Works in all three views with no extra data or live calls.
- On the map: an arrow at the inlet pointing the way the water runs (teal in, orange out), larger when faster, a dot at slack; it stays up when the NOAA dots are hidden. In the Spot list as "Napeague Harbor inlet (estimate)". The chart adds the estimated tide inside the harbor (dashed) and the inlet current with its slack and peak markers; the line above it gives the day's slack and peak times, tagged "Rough estimate". "How it works" explains the method and its limits.
- `tools/build_inlets.py` → `data/inlets.json`: harbor area at each water level from NOAA NCEI's 3 m topobathy DEM; inlet size from the 2012 Peconic Estuary Program survey (minimum section 1,720 sq ft, channel 2,200 ft × 180 ft); the bay tide outside from Montauk's high/lows shifted toward NOAA's Threemile Harbor entrance station (+29 min at high water, +17 at low, ×1.08). Writes a report and a selftest the browser reproduces (`Currents._inletSelftest()`, max error ~5e-5 kn).

### Findings
- **Slack in the Napeague inlet comes about 1 h 10 min after Montauk high water and 1 h 05 min after low water**, longer on big tides (1 h 30 m in the 2015 supermoon week); the current runs hardest about 1½ h before high water (flood) and 1¾ h before low water (ebb), peaking around 2.3 kn averaged across the inlet. The harbor's own tide is ~6% smaller than the bay's and turns 40–50 min after it.
- NOAA's "Goff Point, 0.4 mile NW of" current station is out in the bay at the head of the deep channel west of Hicks Island (floods toward 225°), not in the harbor's inlet.
- The lidar DEM misses the inlet channel: it shows a 21 m² throat where the survey measured 160 m². With the DEM's throat the harbor would barely have a tide; the survey dimensions are used, and the DEM only for the harbor's area.
- Check against NOAA where both sides are predicted: the same method puts Lake Montauk's tide 0–2 min behind its entrance; NOAA says within ±5 min.

## 2026-10-11 — Currents: "Today" and "Tide cycle" views

### Added
- **Tide-stage atlas** (`tools/build_tide_atlas.py`) — every hour of May–Oct 2016 from the NECOFS model, fitted by Montauk tide phase (two segments: high → low → high, 24 bins) and tidal range (three basis patterns at the 10/50/90th range percentiles, least squares per bin). Currents in the passes are locked to the tide cycle, so this gives typical currents for any date from tide predictions alone, with no live model. Includes per-cell confidence maps, NOAA station curves fitted the same way, Montauk high/lows for 2024–2030, and Neap/Average/Spring presets from 2024–30 tides (2016 was a lunar-node peak). Built and published by the `build-currents` workflow (`source=atlas`, `publish` off by default; work cache + artifact).
- **Today view (new default)** — typical currents for any day 2024–2030 from that day's real Montauk high/low times and ranges, opening paused on "now" (with a **Now** button). The selected spot's chart line and the day's slack/max times come live from NOAA, falling back to the typical curve if NOAA can't be reached.
- **Tide cycle view** — one average Montauk cycle, low → high → low, at Neap / Average / Spring strength, with the spot's typical lag behind Montauk high/low water.
- **Low-confidence haze** over waters where the tide explains less than ~70% of the flow (mainly the ocean side south of Montauk), and **"Confidence here: High / Good / Moderate / Low"** for the selected spot.
- **"How it works" panel** — purpose, the three views, accuracy by area and sources; opens on a first visit.
- **Hurricane Sandy (Oct 26 – Nov 2, 2012) example week**, replacing Blizzard Jonas: in fishing season, and the biggest modeled surge at Montauk in the archive (+3.1 ft). Weeks removed from `data/currents_weeks.json` now drop out of the manifest.

### Changed
- `assets/currents.js` reorganised around per-view timelines; frames are blended once per animation frame and the particles sample the result. A cold Today load fetches ~1 MB (manifest, atlas, one NOAA call) instead of a 2.5 MB week.
- The page falls back to Example weeks until the atlas has been uploaded.
- Supermoon lesson text: slack comes about 2½ hours (not 2) after Montauk high/low water.

### Fixed
- Sidebar dropdown menus showed light text on white (Dash 4 renders them inline in the dark sidebar).
- **iPad: the tide chart sat below the bottom of the screen.** The page assumed a 72 px header (it's 80) and used `100vh`, which on iPad/iPhone Safari includes the area under the toolbars. /currents is now a flex column sized to the visible viewport (`100dvh`).

### Tablet & full screen
- On iPads (any touch screen) and windows narrower than 1200 px the sidebar is a slide-in drawer, as on phones, so the map and chart get the full width; it closes itself after you pick a view, date, week, strength or spot.
- Floating Play / step buttons on the map (tablets and phones), kept in sync with the sidebar's Play button.
- Full-screen button on the map; in full screen the header shrinks to a slim branded bar (logo, "Currents", "How it works"). On iPad the bar also spans the status bar iPadOS keeps on top and leaves room for Safari's own exit "X", so neither overlaps the logo or the map's zoom buttons. Opens the page in a new tab when embedded without fullscreen permission; not shown on iPhone, which can't full-screen a web page.
- Faint lines across the map (iPad and desktop): Leaflet's additive tile blending on the place-name layer; it now blends normally.
- The current axis shows round numbers (it had synced to the tide axis: 5.25 / 1.81 / −1.62 kn), and the low-confidence haze fades out at the model's edge instead of ending in a hard rectangle.

### Subordinate NOAA stations (72 of 86)
- NOAA publishes only slack / max-current times for subordinate stations such as Montauk Point (The Race's prediction shifted and scaled). The page drew those few points as a zigzag, and the atlas fitted them as if they were a curve, leaving most tide-phase bins at 0 kn, so those dots showed "slack" most of the time in Today and Tide cycle and their typical slack timing was missing. Both now rebuild the curve between events with NOAA's quarter-sine rule; the atlas was republished (all 86 stations have curves and typical timing).

### Chart interaction
- **One time line, one readout.** Plotly's own hover box and dotted spike are off. On iPad they stayed where the finger first landed while the time line followed a drag, so the chart showed two different times. On desktop, clicking left a second dashed "set time" marker. Now the white line is the only time indicator, with a readout beside it (time, Montauk tide, NOAA / typical current at the spot, model current) while you point at, tap or drag on the chart. Desktop adds a "Click to set this time" hint while previewing.
- Clearer legend: "Montauk tide, ft (NOAA)", "<spot> current, kn (NOAA)", "<spot> current, model", plus the slack and strongest-flood/ebb markers on wide screens; the sidebar's "How to read this" explains each line.

### Accuracy (full-season build)
- Out-of-sample share of the model's flow reproduced: Race/Plum Gut/Fishers 87–96%, Gardiners/Peconics 78–89%, Block Island Sound 65–83%, ocean side 28–58%.
- Slack vs NOAA over all of 2026: Plum Gut median 13 min. The Race median 26 min, biased late (the model's own timing at The Race), which the "How it works" text says plainly.

---

## 2026-10-10 — Currents page (`/currents`)

### Added
- **`/currents` page** — animated tidal-current flow for eastern Long Island (Montauk, The Race, Plum Gut, Gardiners Bay, the Peconics, Fishers, Watch Hill, Block Island Sound) on a dark Esri basemap, linked from the SST header. Canvas particle animation (brightness/colour by speed), ~86 NOAA current-prediction stations coloured flood/ebb/slack, and a time-synced Plotly chart of Montauk's tide against the selected station's current (NOAA solid, model dotted) with slack/max markers and a draggable time cursor.
- **Lag badge** — for the selected station, the week's median offset of slack water and max flood from Montauk high/low water (e.g. The Race: slack ≈ 2h40m after high and low water). This is the core lesson: the tide table's high/low/slack doesn't tell you when the current turns.
- **Four curated weeks** (`data/currents_weeks.json`) from the NECOFS GOM3 hourly hindcast: 2015 supermoon spring tide, summer-2016 spring and neap (same season, opposite extremes), and Blizzard Jonas (Jan 2016) where wind and surge pull the model off NOAA's tide-only predictions.
- **Surface / depth-averaged toggle** and 15 min–2 h per second playback speeds.
- **`tools/build_currents.py`** — off-Render builder: reads NECOFS over OPeNDAP, interpolates the triangle mesh onto a ~400 m grid, packs int8 `GOCUR1` frames (~2–2.5 MB per week per depth), fetches NOAA tide + current predictions, uploads with the manifest last. Sanity guards refuse implausible builds; a physics check compares model vs NOAA at The Race / Plum Gut / Montauk (correlation 0.94–0.99, slack timing within ~10–35 min).
- **`/api/currents/manifest.json`, `/api/currents/<file>`, `POST /api/currents/upload`** — serve prebuilt files from the persistent disk (gzip `Content-Encoding`, 1-day cache, ETag/304) and accept token-authenticated uploads (size checked before buffering, format-validated, atomic write).
- **`.github/workflows/build-currents.yml`** — manual rebuilds of the curated weeks, plus a daily run that switches on "live" currents automatically if UMassD republishes the NECOFS forecast (exits quietly while it's offline).
- **`.github/workflows/keepalive.yml`** — weekly heartbeat commit when `main` has been idle 45+ days, so GitHub can't silently disable scheduled workflows again.
- **`requirements-tools.txt`** — build-only dependencies (`netCDF4` etc.), never installed on Render.

### Changed
- `requirements.txt` now lists `plotly` and `dash-extensions` explicitly (previously only transitive) and bounds `dash-leaflet<2` (the currents engine relies on its global Leaflet).
- README: documents `/currents`, corrects the persistent-disk path (`/var/data/cache`) and adds `--threads 16` to the start command.

### Fixed / Ops
- **"Seed SST cache" had been silently disabled by GitHub since 2026-08-30** (60 days without commits). Re-enabled; the keepalive workflow prevents a repeat. The SST map kept working only because **NOAA has lifted its block on Render's IP** — Render now fetches uncached windows from ERDDAP itself (Oct 8 window loaded live in ~10 s). Docs updated.
- **Chart times were off by the viewer's timezone offset** — Plotly reads numeric dates in browser-local time, so the chart's day ticks and hover readout ran 4 h behind the map clock in Eastern time (7 h in Pacific). The chart now gets timezone-naive Eastern times.
- **Scrubbing the chart now drives the map the way people use it** — moving the mouse along the chart previews that moment on the map (playback holds; a dashed line marks where it resumes), and click/tap/drag jumps there and pauses until Play. Previously only a click-drag moved the map, hovering moved just Plotly's readout, and playback resumed right after a drag.
- **First production build hit NOAA rate limiting** — 4 parallel threads (~21 req/s) from the GitHub runner got `403 Forbidden` from CO-OPS after one week (~175 calls). The builder now makes NOAA calls one at a time, 1 s apart (spacing doubles after each 403), backs off minutes when throttled, never silently drops stations on network errors, and publishes the weeks that did build (exit 1 still flags the failure).

---

## 2026-10-10 — Keyless Basemap (CARTO → Esri Light Gray)

### Fixed
- **Basemap showed "API KEY REQUIRED" watermarks**: CARTO began gating its previously-keyless Positron tiles (`basemaps.cartocdn.com/light_all`) behind an API key. Both the SST map (`layout/mapview.py`) and the catch map (`layout/catchview.py`) used that URL, so both rendered watermark tiles instead of a basemap.

### Changed
- **Switched both maps to Esri World Light Gray Canvas** (`server.arcgisonline.com/.../World_Light_Gray_Base`) — a keyless, no-account, near-identical light-grey basemap. Note the Esri tile scheme is `{z}/{y}/{x}` (not `{z}/{x}/{y}`), with no `{s}` subdomain or `{r}` retina token. Attribution updated to Esri. The SST data pipeline was unaffected — only the background tiles changed.

---

## 2026-06-22 — Off-Render SST Fetching (NOAA IP-block workaround)

### Context
NOAA CoastWatch ERDDAP began blocking Render's datacenter IP at the network layer — connections from the deployed app are refused/unreachable (`Network is unreachable`, `Connection reset by peer`, connect timeouts) across all three servers; `coastwatch.noaa.gov` additionally tarpits (`ReadTimeout`) from every IP. Cache-miss fetches on Render hung the full 90s budget and surfaced as the red "SST fetch timed out after 90s" / "No compatible SST dataset found" error. localhost and GitHub-hosted runners are **not** blocked, so SST fetching was moved off Render entirely. (It worked from Render months ago; NOAA tightened anti-cloud filtering since.)

### Added
- **`POST /api/cache/upload` endpoint** — receives a pre-built gzip cache file from an off-Render fetcher. Auth via the `CACHE_UPLOAD_TOKEN` env var (403 if unset, 401 on mismatch, constant-time compare); filename validated against the `cache_key` pattern (no path traversal); body validated as gzip-JSON; 8 MB cap; atomic write.
- **`tools/sync_cache.py`** — runs where ERDDAP is reachable; fetches recent 7-day windows via the normal pipeline, builds the same raw-only payload the pre-cache writes, and POSTs them up. Retries on transient Render 500/502/503/504 with backoff; paces uploads (`--upload-delay`, default 2s).
- **`.github/workflows/seed-sst-cache.yml`** — daily GitHub Action (11:00 UTC) that runs the sync script with `CACHE_UPLOAD_TOKEN` from a repo secret. Seeds end-dates today-2..today-11; the app's ±3-day fuzzy lookup covers the rest. Public repo → free Actions minutes.
- **`erddap_search` failure logging** — previously swallowed every exception silently (making a blocked search look like a generic 90s timeout). Now logs the exception type per server, which is how the IP block was confirmed.

### Changed
- **All ERDDAP requests send a descriptive User-Agent** (`data/erddap._HEADERS`). NOAA's WAF 403s the default `python-requests` UA. (Necessary but not sufficient — the network-level IP block is the underlying issue.)

### Summary
Render no longer fetches ERDDAP directly; it serves recent dates from cache files pushed up by a daily GitHub Action. localhost still fetches ERDDAP live. The public AWS MUR Zarr mirror was evaluated and rejected (frozen at ~2020).

---

## 2026-04-05 — Official Release (v1.0)

### Added
- **Version indicator** in sidebar UI — shows current version (bottom-right of sidebar body).
- **Fuzzy cache lookup** (`find_nearest_cached()`): When the exact end date isn't cached, checks ±3 nearby days. Pre-cached entries are every 7 days, so most dates within tuna season now hit a cache entry that's at most 3 days off — avoiding slow ERDDAP fetches entirely. Status message shows offset when serving from a nearby cache (e.g., "nearest cache: +1d").

### Fixed
- **Loading overlay stuck after fetch error**: The spinner was never hidden when ERDDAP timed out. Added `map-loading-overlay` as an output of the fetch callback so both success and error paths hide it.

### Summary
First official release. All core features stable: 7-day SST animation, 32 POI fishing spots, GFW fishing activity overlay, measure tool, mobile-responsive drawer, pre-cached tuna season data (2020–2025), persistent disk cache, multi-threaded gunicorn with server-side tile caching.

---

## 2026-04-04 — Pre-Cache Optimization & Persistent Storage

### Added
- **Multi-threaded gunicorn** (`--threads 16`): GFW tile proxy requests release the GIL during network I/O, allowing click callbacks to run concurrently. With 16 threads, ~48 tile requests process in ~3 rounds instead of ~12, keeping threads free for click callbacks.
- **Server-side GFW tile cache** (`_gfw_tile_cache`): Caches both 200 and 404 tile responses in memory. ~Half of tile requests return 404 (empty ocean), each saving a ~1s GFW API round-trip. After first zoom to an area, subsequent visits are instant from cache. Cleared on date range change.
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
- **Raw data cache OOM**: `_raw_data_cache` was unbounded — each date window adds ~25 MB of numpy arrays, and entries were never evicted. After fetching several dates, memory accumulated past 512 MB. Now limited to 2 entries via `_put_raw_cache()` with oldest-first eviction. Falls back to disk cache on miss.
- **Pre-cache OOM on Render 512 MB**: Old `_build_payload` held all 7 days + PNGs + serialized arrays simultaneously (~65-70 MB peak). New `_precache_single_date` processes one day at a time (~30-35 MB peak).
- **Pre-cache losing progress on restart**: Render's ephemeral filesystem wiped cache files on every container restart. Persistent disk solves this permanently.
- **Tooltip delay after zoom**: GFW tile proxy requests (~48 after zoom) flooding the worker prevented click callbacks from responding. Multi-threaded gunicorn (`--threads 16`) + server-side tile cache eliminates the delay. First zoom to a new area: ~3s. Subsequent zooms to same area: instant from cache.

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
