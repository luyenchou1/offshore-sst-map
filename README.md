# Offshore SST Map — NJ→MA AOI

Daily sea-surface temperature (SST) for the offshore corridor from New York Harbor to Massachusetts (coastline → ~80 nm).
Data comes from NOAA CoastWatch ERDDAP (prefers **MUR GHRSST 1km**; falls back to **OISST**). Temperatures are shown in **°F (whole degrees)**.

## What's new (v1.0 — Dash Migration)
- **Migrated from Streamlit to Dash + Dash Leaflet** — smooth pan/zoom with no page reloads
- **Fixed MUR dataset selection** — now correctly fetches 1km MUR data instead of 5km BLENDED
- **Fixed winter/spring color scale** — temperatures below 48°F now show proper color differentiation
- **Improved error handling** — server failures fall through to next server instead of crashing
- **Auto date retry** — if latest date has no data (MUR latency), automatically tries older dates
- **Faster rendering** — vectorized color mapping (~100x faster than per-pixel loop)
- **Deployable** — ready for Render, Railway, or any WSGI host

## Setup (Local)
```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Open http://localhost:8050

## Deploy to Render
1. Push this repo to GitHub
2. Connect the repo on [render.com](https://render.com)
3. Render auto-detects the `Procfile` and deploys

## Controls
- **Days back**: Select 1-7 days back from today (UTC)
- **Lock color scale**: Toggle between adaptive (percentile-based) or fixed (30-90°F) scale
- **Visual resolution**: 1x native, 2x or 3x upsampled for smoother gradients
- **Tooltip density**: Sparse/Normal/Dense hover grid
- **Fetch SST**: Click to load data for the selected date
