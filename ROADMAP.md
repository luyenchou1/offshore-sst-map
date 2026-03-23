# Roadmap

## Phase 1: UI Polish & POIs
*Status: Complete*

- Renamed to "Offshore SST Analyzer"
- Compact status text, subtle AOI boundary, clean Play/Pause labels
- 20 fishing spots + The Dump rectangle added
- Multi-select POI picker dropdown with count label
- Expanded AOI south to 39.5°N for full POI coverage
- Unified click system (POI info, SST readings, measure tool — one tooltip at a time)
- Ruler/measure tool with distance (nm/mi) and heading

## Phase 2: GotOne Branding
*Status: Complete*

- Dark navy header (#0a1628) with white GotOne fish logotype
- Dark navy sidebar matching header, light text throughout
- Brand blue (#0183fe) for slider track, spinner, measure accents
- White pill-shaped buttons with dark text (matches GotOne CTA style)
- Dark text on white inputs (date picker, POI dropdown, calendar popup)
- Custom CSS overrides for Dash 4 component class names

## Phase 2b: Production Deployment & Performance
*Status: Deployed on Render Starter ($7/month) — performance optimization in progress*

- Live at https://offshore-sst-map.onrender.com
- Gunicorn with 1 worker, 180s timeout
- **Known issue**: Browser callback timeout (~30s) causes re-fetches to fail on Render's slower CPU
- **Next**: Disk-based cache + Dash long_callback to fix timeout and make repeat fetches instant
  - `data/cache.py`: gzip-compressed JSON cache on Render persistent disk
  - `@app.long_callback` with DiskcacheManager for timeout-proof fetching
  - Startup pre-warm thread for instant first-visitor experience
  - Cache invalidation: permanent for dates >3 days old, 12hr TTL for recent dates

## Phase 3: Responsive / Mobile
*Status: Planned

- Stacked layout on mobile (controls above map, or collapsible drawer)
- Full-width map on small screens
- Touch-friendly animation controls (larger tap targets)
- Legend and day indicator repositioned for mobile
- Date picker mobile UX
- Test on iOS Safari and Android Chrome

## Phase 4: AI Fishing Analysis
*Status: Planned — most complex, highest user value*

- Analyze each 7-day SST window for fishing-relevant insights
- Temperature break line detection (where species congregate)
- Warm/cold eddy identification
- SST trend analysis (warming vs cooling over the 7 days)
- Species-specific temperature zone mapping (tuna, mahi, stripers, etc.)
- Plain-language summary panel, e.g.:
  > "Strong 4°F temperature break along the 100-fathom line trending NE.
  > Good conditions for yellowfin/bluefin staging. Inshore temps (37-39°F)
  > too cold for pelagics — focus efforts 60+ miles out."
- Likely uses Claude API to analyze the actual grid data per fetch
- Consider overlay visualization of detected breaks/eddies on the map
