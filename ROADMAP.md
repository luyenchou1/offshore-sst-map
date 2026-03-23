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

## Phase 3: Responsive / Mobile
*Status: Planned — next up*

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
