# Roadmap

## Phase 1: UI Polish & POIs
*Status: Complete*

### 1a. UI Scrub
- Rename title to "Offshore SST Analyzer" (drop AOI reference)
- Simplify the green "Loaded" status alert — condense date range (already in slider) and data source
- Soften AOI boundary — light gray dashed line instead of solid black
- Fix date picker month arrow direction (up → down)
- Simplify Play/Pause button — icon only, consistent styling
- Review sidebar spacing for shorter viewports
- Consider showing current frame date in the legend
- General polish pass on typography, spacing, contrast

### 1b. Add Fishing Spots
- User will provide additional POI names and coordinates
- Add to `map/pois.py` POIS list
- Trivial change, bundle with UI scrub

## Phase 2: GotOne Branding
*Status: Planned*

- Match styling to [gotoneapp.com](https://www.gotoneapp.com) — colors, fonts, visual identity
- Add GotOne logo (user to provide PNG/SVG) to header
- Rename app to "GotOne Offshore SST Analyzer"
- Swap Bootstrap theme to match GotOne palette
- Custom CSS for buttons, alerts, controls
- Pull brand colors and fonts from the live website

## Phase 3: Responsive / Mobile
*Status: Planned — do after branding to avoid double-rework*

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
