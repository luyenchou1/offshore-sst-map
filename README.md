# GotOne Offshore SST Analyzer

Daily sea-surface temperature (SST) visualization for the NJ-to-MA offshore fishing corridor. Pick any 7-day window (back to June 2002) and animate through daily SST maps to spot warm eddies, temperature breaks, and thermoclines.

Data: **NOAA CoastWatch ERDDAP** — MUR GHRSST 1km (preferred), OISST 25km (fallback). Temperatures in **°F**.

**Live**: https://sst.gotoneapp.com (also at https://offshore-sst-map.onrender.com)
**Embedded**: https://www.gotoneapp.com/offshore-sst

## Features

- **7-day SST animation** — Play/pause, step, or scrub through a week of daily SST maps
- **Date picker** — Any date from June 2002 to ~2 days ago (MUR latency)
- **20 fishing spots + The Dump** — Named POIs with click-to-read SST at each location
- **Click-to-read** — Click anywhere on the map for an instant temperature reading
- **Measure tool** — Two-click distance/bearing (nautical miles, statute miles, compass heading)
- **Nautical chart overlay** — NOAA ENC charts with depth contours, soundings, and nav aids
- **Bathymetry overlay** — GEBCO ocean depth shading (continental shelf, canyons)
- **SST opacity control** — Fade SST colors to reveal chart features underneath
- **Disk cache** — Repeat fetches return instantly from gzip-compressed cache files
- **Adaptive or locked color scale** — Toggle between percentile-based or fixed 30–90°F range
- **GotOne branding** — Dark navy UI with brand blue (#0183fe) accents

## Setup (Local)

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Open http://localhost:8050

## Deploy to Render

1. Push repo to GitHub
2. Connect the repo on [render.com](https://render.com) — Render auto-detects the `Procfile`
3. Add a **persistent disk** (1 GB, mount at `/opt/render/project/src/cache`)
4. Set env var `SST_CACHE_DIR=/opt/render/project/src/cache`
5. Start command: `gunicorn app:server --bind 0.0.0.0:$PORT --workers 1 --timeout 180`

**Important**: Must use 1 worker — the in-memory raw data cache is per-process.

## Custom Domain & Squarespace Embed

The app is served at `sst.gotoneapp.com` via a CNAME pointing to Render, and embedded on the GotOne Squarespace site at `gotoneapp.com/offshore-sst`.

**Custom domain setup:**
1. Squarespace DNS: CNAME record `sst` → `offshore-sst-map.onrender.com`
2. Render: Add `sst.gotoneapp.com` as custom domain (auto-provisions SSL)

**Squarespace embed:**
- Page at `/offshore-sst` contains an Embed Block with an iframe pointing to `https://sst.gotoneapp.com`
- `app.py` sets `Content-Security-Policy: frame-ancestors` to allow embedding from `gotoneapp.com`

## Controls

- **Date picker** — Select end date for the 7-day window
- **Fetch SST** — Load SST data (cached dates return instantly)
- **Playback** — Play/Pause, step forward/back, slider to scrub through days
- **Spots dropdown** — Multi-select which fishing POIs to show on the map
- **Lock scale** — Toggle fixed 30–90°F color range vs adaptive scaling
- **Measure** — Two-click distance and bearing measurement tool
- **Nautical chart / Bathymetry** — Toggle NOAA ENC chart and GEBCO depth layers on/off
- **SST opacity** — Slider to fade SST overlay and reveal chart features underneath

## Architecture

Two-part storage keeps browser payloads small (~7 MB) while preserving full-resolution data for click readings:

- **Browser (dcc.Store)**: 7 pre-rendered base64 PNG frames + metadata (~7 MB)
- **Server (memory dict)**: Raw float arrays for temperature lookups (~18 MB, with disk cache fallback)

See `CLAUDE.md` for full architecture details, callback structure, and lessons learned.

## Tech Stack

- **Backend**: Python 3.12, Dash 4.0, NumPy, SciPy
- **Frontend**: Dash Leaflet (Leaflet.js), Dash Bootstrap Components (Flatly theme), custom CSS
- **SST data**: NOAA CoastWatch ERDDAP — MUR GHRSST 1km (preferred), OISST 25km (fallback)
- **Chart layers**: NOAA ENC nautical charts (WMS), GEBCO bathymetry (WMS), CARTO basemap
- **Hosting**: Render Starter ($7/month) — 0.5 CPU, 512 MB RAM, 1 GB persistent disk
- **Domain**: `sst.gotoneapp.com` (CNAME → Render), embedded on Squarespace via iframe
