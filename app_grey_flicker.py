# app.py — Offshore SST map (NJ→MA AOI)
# - Static Folium render (no jumpy reruns on pan/zoom)
# - Leaflet-native hover tooltips with °F (no clicks needed)
# - Live lat/lon readout via MousePosition (client-side)
# - High-res coastline mask (Natural Earth 10m) + AOI mask
# - Green POI dots with clean always-visible labels
# - Fixed/adaptive color scale; 1x/2x/3x visual upsample

import io
import os
import json
import tempfile
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple, Dict

import numpy as np
import pandas as pd
import requests
import streamlit as st
import xarray as xr

from shapely.geometry import Polygon, Point, box
import folium
from folium import Map
from folium.features import GeoJson, DivIcon
from folium.raster_layers import ImageOverlay
from folium.plugins import MousePosition
from branca.colormap import LinearColormap
from scipy.ndimage import zoom
from streamlit.components.v1 import html as st_html

# ---------- Page ----------
st.set_page_config(page_title="Offshore SST (NJ→MA)", layout="wide")
st.title("Offshore SST — NJ→MA AOI")
st.caption("Daily SST from NOAA CoastWatch ERDDAP (MUR preferred; OISST fallback). Temperatures in °F (whole degrees).")

# ---------- Session ----------
if "sst_payload" not in st.session_state:
    st.session_state["sst_payload"] = None
if "tip_density_mode" not in st.session_state:
    st.session_state["tip_density_mode"] = "Normal"

# ---------- Config ----------
@st.cache_resource
def load_config() -> Dict:
    with open("config.json", "r") as f:
        return json.load(f)

CFG = load_config()

def get_aoi_lonlat() -> List[Tuple[float, float]]:
    if "aoi_polygon_lonlat" in CFG:
        return [tuple(pt) for pt in CFG["aoi_polygon_lonlat"]]
    if "aoi_polygon" in CFG:  # backward compat
        return [tuple(pt) for pt in CFG["aoi_polygon"]]
    raise RuntimeError("AOI polygon not found in config.json (need 'aoi_polygon_lonlat').")

def polygon_bounds(lonlat_pts: List[Tuple[float, float]]):
    lons = [p[0] for p in lonlat_pts]
    lats = [p[1] for p in lonlat_pts]
    return min(lons), min(lats), max(lons), max(lats)

def today_utc():
    return datetime.now(timezone.utc).date()

# ---------- ERDDAP ----------
def erddap_search(server: str, terms: List[str]) -> Optional[pd.DataFrame]:
    try:
        r = requests.get(f"{server}/search/index.csv", params={"searchFor": " ".join(terms)}, timeout=25)
        r.raise_for_status()
        return pd.read_csv(io.StringIO(r.text))
    except Exception:
        return None

def pick_dataset(df: pd.DataFrame) -> Optional[Dict[str, str]]:
    if df is None or df.empty or "Dataset ID" not in df.columns:
        return None
    candidates = []
    for _, row in df.iterrows():
        dsid = str(row.get("Dataset ID", ""))
        title = str(row.get("Title", ""))
        sumry = str(row.get("Summary", ""))
        t = (title + " " + sumry + " " + dsid).lower()
        if any(k in t for k in ["analysed_sst", "sea surface temperature", "sst", "mur", "ghrsst", "oisst", "blended"]):
            candidates.append((dsid, title, sumry))
    def score(item):
        dsid, title, sumry = item
        t = (title + " " + sumry + " " + dsid).lower()
        s = 0
        if "mur" in t: s += 10
        if "ghrsst" in t: s += 8
        if "analysed_sst" in t: s += 7
        if "oisst" in t or "blended" in t: s += 5
        if "daily" in t: s += 2
        if "l4" in t: s += 2
        return s
    candidates.sort(key=score, reverse=True)
    if candidates:
        dsid, title, _ = candidates[0]
        return {"id": dsid, "title": title}
    return None

def guess_var_from_das(das_text: str) -> Optional[str]:
    for v in ["analysed_sst", "sst", "sea_surface_temperature", "temperature"]:
        if v in das_text:
            return v
    return None

def fetch_grid(server: str, dsid: str, date, bbox: Tuple[float, float, float, float]):
    das_url = f"{server}/griddap/{dsid}.das"
    r = requests.get(das_url, timeout=25)
    r.raise_for_status()
    varname = guess_var_from_das(r.text)
    if not varname:
        raise RuntimeError("Could not identify SST variable in dataset DAS.")
    minlon, minlat, maxlon, maxlat = bbox
    t0 = f"{date}T00:00:00Z"
    t1 = f"{date}T23:59:59Z"
    query = f"{varname}[({t0}):1:({t1})][({minlat}):1:({maxlat})][({minlon}):1:({maxlon})]"
    nc_url = f"{server}/griddap/{dsid}.nc?{query}"
    rr = requests.get(nc_url, timeout=120)
    rr.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tf:
        tf.write(rr.content)
        path = tf.name
    ds = xr.open_dataset(path)
    return ds, varname

def to_fahrenheit_whole(arr: np.ndarray, units_hint: str) -> np.ndarray:
    data = arr.astype("float32")
    u = (units_hint or "").lower()
    if u.startswith("k"):
        f = (data - 273.15) * 9/5 + 32
    elif u.startswith("c") or "degree_c" in u:
        f = data * 9/5 + 32
    elif u.startswith("f"):
        f = data
    else:
        f = (data - 273.15) * 9/5 + 32
    f = np.where(np.isfinite(f), f, np.nan)
    return np.round(f, 0)

@st.cache_data(ttl=3600, show_spinner=False)
def get_sst(date):
    aoi = get_aoi_lonlat()
    minlon, minlat, maxlon, maxlat = polygon_bounds(aoi)
    for terms in [CFG["primary_search_terms"], CFG["fallback_search_terms"]]:
        for server in CFG["servers"]:
            df = erddap_search(server, terms)
            choice = pick_dataset(df) if df is not None else None
            if not choice:
                continue
            dsid, title = choice["id"], choice["title"]
            ds, varname = fetch_grid(server, dsid, date, (minlon, minlat, maxlon, maxlat))
            da = ds[varname].squeeze()
            lat_name = next((d for d in ds.dims if "lat" in d.lower()), "lat")
            lon_name = next((d for d in ds.dims if "lon" in d.lower()), "lon")
            data2 = da.values[0, :, :] if ("time" in da.dims and da.ndim == 3) else da.values
            lats = ds[lat_name].values
            lons = ds[lon_name].values
            units = da.attrs.get("units", "kelvin")
            arrF = to_fahrenheit_whole(data2, units)
            return {
                "server": server,
                "dataset_id": dsid,
                "dataset_title": title,
                "var": varname,
                "units": units,
                "arrF": arrF,
                "lats": lats,
                "lons": lons,
            }
    raise RuntimeError("No compatible SST dataset found on configured ERDDAP servers.")

# ---------- Rendering helpers ----------
def build_colormap(vmin, vmax):
    return LinearColormap(
        colors=["#2c7fb8","#41b6c4","#7fcdbb","#c7e9b4","#ffffcc",
                "#fde68a","#fca35d","#fb6a4a","#ef3b2c","#cb181d","#99000d"],
        vmin=vmin, vmax=vmax
    )

def array_to_rgba(arrF: np.ndarray, cmap: LinearColormap) -> np.ndarray:
    rgba = np.zeros((arrF.shape[0], arrF.shape[1], 4), dtype=np.uint8)
    for i in range(arrF.shape[0]):
        for j in range(arrF.shape[1]):
            v = arrF[i, j]
            if np.isnan(v):
                rgba[i, j] = [0, 0, 0, 0]
            else:
                h = cmap(v).lstrip("#")
                r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
                rgba[i, j] = [r, g, b, 255]
    return rgba

def orient_to_leaflet(arrF, lats, lons):
    # Leaflet expects row 0 = NORTH, col 0 = WEST
    if lats[0] < lats[-1]:
        arrF = np.flipud(arrF); lats = lats[::-1]
    if lons[0] > lons[-1]:
        arrF = np.fliplr(arrF); lons = lons[::-1]
    if arrF.shape == (len(lons), len(lats)):
        arrF = arrF.T
    return arrF, lats, lons

def upsample_visual(arr, factor: int):
    if factor == 1:
        return arr
    return zoom(arr, (factor, factor), order=1)

# ---------- High‑res land mask ----------
@st.cache_data(show_spinner=False)
def _download_ne10m_land_zip() -> bytes:
    url = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content

@st.cache_resource(show_spinner=False)
def load_land_gdf():
    try:
        data = _download_ne10m_land_zip()
        tmp = tempfile.mkdtemp()
        zip_path = os.path.join(tmp, "ne10m_land.zip")
        with open(zip_path, "wb") as f:
            f.write(data)
        import geopandas as gpd
        gdf = gpd.read_file(f"zip://{zip_path}").to_crs("EPSG:4326")
        return gdf
    except Exception:
        try:
            import geopandas as gpd
            gdf = gpd.read_file(gpd.datasets.get_path("naturalearth_lowres")).to_crs("EPSG:4326")
            return gdf
        except Exception:
            return None

def mask_land_rasterized(arrF: np.ndarray, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Rasterize high‑res land polygons to the SST grid and mask (set to NaN)."""
    try:
        import geopandas as gpd
        from rasterio.features import rasterize
        from rasterio.transform import from_bounds

        land = load_land_gdf()
        if land is None or land.empty:
            return arrF

        minlon, maxlon = float(np.min(lons)), float(np.max(lons))
        minlat, maxlat = float(np.min(lats)), float(np.max(lats))
        bbox = gpd.GeoDataFrame(geometry=[box(minlon, minlat, maxlon, maxlat)], crs="EPSG:4326")
        land_clip = gpd.overlay(land, bbox, how="intersection")
        if land_clip.empty:
            return arrF

        # Slight inward buffer to ensure shoreline pixels are masked
        try:
            land_clip["geometry"] = land_clip.buffer(-0.0007)  # ~75 m
        except Exception:
            pass

        h, w = arrF.shape
        transform = from_bounds(minlon, minlat, maxlon, maxlat, w, h)
        shapes = [(geom, 1) for geom in land_clip.geometry if geom and not geom.is_empty]

        land_mask = rasterize(
            shapes=shapes,
            out_shape=(h, w),
            transform=transform,
            fill=0,
            all_touched=True,   # any pixel touching land becomes land
            dtype="uint8",
        ).astype(bool)

        out = arrF.copy()
        out[land_mask] = np.nan
        return out
    except Exception:
        return arrF

def add_land_overlay_highres(m: folium.Map, lats: np.ndarray, lons: np.ndarray):
    """Draw high‑res land polygons above the SST for a clean coastline edge."""
    try:
        import geopandas as gpd
        land = load_land_gdf()
        if land is None or land.empty:
            return
        minlon, maxlon = float(np.min(lons)), float(np.max(lons))
        minlat, maxlat = float(np.min(lats)), float(np.max(lats))
        bbox = gpd.GeoDataFrame(geometry=[box(minlon, minlat, maxlon, maxlat)], crs="EPSG:4326")
        land_clip = gpd.overlay(land, bbox, how="intersection")
        if land_clip.empty:
            return
        folium.GeoJson(
            land_clip.__geo_interface__,
            name="Land (10m)",
            style_function=lambda feat: {
                "fillColor": "#f8f9fa",
                "color": "#adb5bd",
                "weight": 1,
                "fillOpacity": 0.96,
            },
        ).add_to(m)
    except Exception:
        pass

# ---------- Hover helpers ----------
def add_tooltip_grid(m: folium.Map, arrF: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                     zoom_level: int, mode: str = "Normal"):
    """Invisible tiny markers with sticky °F tooltips. Density adapts with zoom."""
    base = {"Sparse": 40, "Normal": 60, "Dense": 90}.get(mode, 60)
    target = min(base + max(0, zoom_level - 6) * 20, 160)
    stride_lat = max(1, len(lats) // target)
    stride_lon = max(1, len(lons) // target)
    radius = 3 if zoom_level <= 7 else (4 if zoom_level <= 9 else 5)
    for i in range(0, len(lats), stride_lat):
        for j in range(0, len(lons), stride_lon):
            v = arrF[i, j]
            if not np.isfinite(v):
                continue
            folium.CircleMarker(
                location=[float(lats[i]), float(lons[j])],
                radius=radius,
                stroke=False,
                fill=True,
                fill_opacity=0.05,
                tooltip=folium.Tooltip(f"{v:.0f} °F", sticky=True, direction="top"),
            ).add_to(m)

# ---------- POIs ----------
POIS = [
    ("Haabs Ledge",  40 + 52.095/60, -(71 + 50.292/60)),
    ("Butterfish Hole", 40 + 50.188/60, -(71 + 40.494/60)),
    ("July2025", 40 + 53.733/60, -(71 + 49.849/60)),
    ("CIA", 40 + 56.006/60, -(71 + 43.000/60)),
    ("Gully", 41 + 1.229/60, -(71 + 25.017/60)),
    ("Wind Farm SW Corner", 40 + 58.439/60, -(71 + 16.398/60)),
    ("Tuna Ridge", 40 + 55.000/60, -(71 + 16.750/60)),
]

def add_pois(m: folium.Map):
    for name, lat, lon in POIS:
        # green dot
        folium.CircleMarker(
            location=[lat, lon],
            radius=6,
            color="#0f5132",
            weight=2,
            fill=True,
            fill_color="#22c55e",
            fill_opacity=0.9,
        ).add_to(m)
        # label (always visible, no white rectangle)
        folium.Marker(
            location=[lat, lon],
            icon=DivIcon(
                icon_size=(150, 24),
                icon_anchor=(0, -6),
                html=f'<div style="font: 600 12px/1.2 Inter,system-ui,Arial; '
                     f'color:#0f5132; background:transparent; text-shadow:0 0 4px rgba(255,255,255,.75);">'
                     f'{name}</div>'
            )
        ).add_to(m)

# ---------- Map builder ----------
def build_map(sst, lock_fixed_scale=False, up_factor=2, tip_mode="Normal"):
    aoi = get_aoi_lonlat()
    lon0 = float(np.mean([p[0] for p in aoi])); lat0 = float(np.mean([p[1] for p in aoi]))

    m = Map(
        location=[lat0, lon0],
        zoom_start=6,
        tiles="CartoDB positron",
        control_scale=True,
        max_zoom=12,
        min_zoom=5,
    )

    # AOI outline
    poly_geo = {"type": "Feature", "properties": {"name": "AOI"},
                "geometry": {"type": "Polygon", "coordinates": [[list(pt) for pt in aoi]]}}
    GeoJson(poly_geo, name="AOI", style_function=lambda x: {"color": "#222", "weight": 2, "fillOpacity": 0}).add_to(m)

    # Prepare grid + masks
    arrF = sst["arrF"]; lats = sst["lats"]; lons = sst["lons"]
    arrF, lats, lons = orient_to_leaflet(arrF, lats, lons)

    aoi_poly = Polygon(aoi)
    Lon, Lat = np.meshgrid(lons, lats)
    mask = np.vectorize(lambda x, y: aoi_poly.contains(Point(x, y)))(Lon, Lat)
    arrF = np.where(mask, arrF, np.nan)

    # Land mask
    arrF = mask_land_rasterized(arrF, lats, lons)

    # Color scale
    finite = arrF[np.isfinite(arrF)]
    if lock_fixed_scale or finite.size < 50:
        vmin, vmax = 48, 90
    else:
        vmin = max(float(np.nanpercentile(finite, 5)), 48)
        vmax = min(float(np.nanpercentile(finite, 95)), 90)
        if vmin >= vmax: vmin, vmax = 48, 90

    # Render overlay
    arrF_vis = upsample_visual(arrF, up_factor)
    cmap = build_colormap(vmin, vmax)
    rgba = array_to_rgba(arrF_vis, cmap)

    lat_min, lat_max = float(np.min(lats)), float(np.max(lats))
    lon_min, lon_max = float(np.min(lons)), float(np.max(lons))
    bounds = [[lat_min, lon_min], [lat_max, lon_max]]

    ImageOverlay(image=rgba, bounds=bounds, opacity=0.78, name=f"SST (°F) [{int(vmin)}–{int(vmax)}]").add_to(m)
    cmap.caption = "SST (°F)"; cmap.add_to(m)
    m.fit_bounds(bounds)

    # Lat/Lon readout
    MousePosition(position='topright', separator=' | ', prefix='Lat | Lon:', num_digits=4).add_to(m)

    # Hover tooltips (pick a nominal zoom for density calculation; 8 works well)
    add_tooltip_grid(m, arrF, lats, lons, zoom_level=8, mode=tip_mode)

    # Land overlay above raster
    add_land_overlay_highres(m, lats, lons)

    # POIs
    add_pois(m)

    return m

# ---------- Sidebar ----------
with st.sidebar:
    days_back = st.slider("Days back (select date)", 1, 7, 3)
    date_sel = today_utc() - timedelta(days=days_back - 1)
    lock_fixed = st.checkbox("Lock color scale to 48–90°F", value=False)
    up_choice = st.selectbox("Visual resolution", ["1x (native)", "2x upsample", "3x upsample"], index=1)
    up_factor = {"1x (native)": 1, "2x upsample": 2, "3x upsample": 3}[up_choice]
    tip_density = st.selectbox("Tooltip density", ["Sparse", "Normal", "Dense"], index=1)
    st.session_state["tip_density_mode"] = tip_density
    fetch_btn = st.button("Fetch SST", type="primary")

st.write("AOI defined by your polygon; map is zoomable. Pan/zoom won’t trigger reruns.")

# ---------- Fetch & Render ----------
if fetch_btn:
    with st.spinner("Fetching SST subset from ERDDAP…"):
        try:
            sst = get_sst(date_sel)
            st.session_state["sst_payload"] = sst
            st.success(f"Dataset: {sst['dataset_id']} — {sst['dataset_title']} | Var: {sst['var']} ({sst['units']})")
        except Exception as e:
            st.error(f"Failed to fetch SST: {e}")

sst = st.session_state.get("sst_payload")
if sst is None:
    st.warning("Click **Fetch SST** to load data.")
else:
    m = build_map(
        sst,
        lock_fixed_scale=lock_fixed,
        up_factor=up_factor,
        tip_mode=st.session_state.get("tip_density_mode", "Normal"),
    )
    # Static HTML render → pan/zoom don’t cause Streamlit to rerun
    _map_html = m.get_root().render()
    st_html(_map_html, height=1000, scrolling=False)