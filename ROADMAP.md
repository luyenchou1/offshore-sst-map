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
*Status: Complete*

### Deployed
- Live at https://sst.gotoneapp.com (custom domain) and https://offshore-sst-map.onrender.com
- Embedded on Squarespace at https://www.gotoneapp.com/offshore-sst (iframe)
- Render Starter ($7/month): 0.5 CPU, 512 MB RAM, 1 GB persistent disk ($0.30/month)
- Gunicorn: 1 worker, 180s timeout (must be 1 worker — in-memory cache is per-process)
- Custom domain: CNAME `sst` → Render in Squarespace DNS, SSL auto-provisioned

### Disk Cache
- `data/cache.py`: gzip-compressed JSON cache (~500 KB–1 MB per 7-day window)
- Cache key: `{end_date}_{adaptive|locked}` — locked vs adaptive produce different PNGs
- Invalidation: permanent for dates >3 days old (MUR finalized), 12-hour TTL for recent dates
- LRU eviction at 200 entries (~200 MB max)
- Render persistent disk mounted at `/opt/render/project/src/cache`
- v2 serialization: base64-encoded numpy `.npy` format (1.3x memory vs 3x for `.tolist()`)
- Disk write runs in background thread (non-blocking)

### Two-Part Storage Architecture
- **Browser (dcc.Store)**: ~7 MB — 7 pre-rendered base64 PNG frames + dates + metadata
- **Server (`_raw_data_cache` dict)**: ~18 MB — raw float arrays for click-to-read temperature lookups
- Memory cache populated **before** disk write (memory-first for reliability on 512 MB Render)
- Disk cache fallback: if memory cache misses (restart, eviction), raw data is rebuilt from disk cache
- Solved: original ~26 MB single payload caused Render 502 errors and browser timeouts

### Pre-Warm
- Daemon thread on startup (15s delay) loads current week from disk cache only
- No ERDDAP fetches during startup — avoids starving gunicorn and failing Render health checks

## Phase 3: Responsive / Mobile
*Status: Planned*

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
