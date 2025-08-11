# Offshore SST Map — NJ→MA AOI

Daily sea-surface temperature (SST) for the offshore corridor from New York Harbor to Massachusetts (coastline → ~80 nm).  
Data comes from NOAA CoastWatch ERDDAP (prefers **MUR GHRSST**; falls back to **OISST**). Temperatures are shown in **°F (whole degrees)**.

## What’s new (2025‑08‑10)
- **Hover‑only SST probe**: Leaflet‑native tooltip grid shows °F when you move the mouse over the colored overlay (no clicks required).
- **Lock map view**: Prevents any re‑centering/zoom changes while you interact. (We only auto‑fit on the very first render.)
- **Orientation & AOI mask solidified**: Raster is correctly oriented north‑up, west‑left; pixels outside the polygon are masked to transparent.
- **Adaptive/fixed color scale**: 5th–95th percentile by default (guard‑railed 48–90 °F), or lock to 48–90 °F.
- **Visual upsample**: 1×/2×/3× display‑only smoothing for cleaner gradients.

> If your map ever “jumped” before, turn on **Lock map view** in the sidebar. That keeps your current center/zoom stable.

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt