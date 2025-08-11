# app.py — Offshore SST map (NJ→MA AOI)
# Static Folium render (no st_folium): pan/zoom won't rerun Streamlit
# - Hover SST via Leaflet tooltips (pre-baked CircleMarkers)
# - Mouse lat/lon via Leaflet MousePosition
# - High-res coastline mask (Natural Earth 10m) rasterized to SST grid
# - AOI mask, correct raster orientation, adaptive/fixed color scale
# - 1x/2x/3x visual upsample
# - POIs: green dots + unobtrusive text labels
# - Wide, responsive layout (fills main pane)

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
import streamlit.components.v1 as components


# ---------- Page ----------
st.set_page_config(page_title="Offshore SST (NJ→MA)", layout="wide")
st.markdown(
    """
    <style>
      /* widen main area */
      .block-container {padding-top: 1.5rem; padding-bottom: 0.5rem; max-width: 1800px;}
      /* give the folium container full width */
      .folium-container {width: 100%; height: 100%;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Offshore SST — NJ→MA AOI")
st.caption("Daily SST from NOAA CoastWatch ERDDAP (MUR preferred; OISST fallback). Temperatures in °F (whole degrees).")
st.write("AOI defined by your polygon; map is zoomable. Pan/zoom won't trigger reruns.")


# ---------- Session state ----------
for k, v in {
    "sst_payload": None,  # dict with array + meta
}.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ---------- Config ----------
@st.cache_resource
def load_config() -> Dict:
    with open("config.json", "r") as f:
        return json.load(f)

CFG = load_config()


def get_aoi_lonlat() -> List[Tuple[float, float]]:
    if "aoi_polygon_lonlat" in CFG:
        return [tuple(pt) for pt in CFG["aoi_polygon_lonlat"]]
    if "aoi_polygon" in CFG:
        return [tuple(pt) for pt in CFG["aoi_polygon"]]
    raise RuntimeError("AOI polygon not found in config.json (need 'aoi_polygon_lonlat').")


def polygon_bounds(lonlat_pts: List[Tuple[float, float]]):
    lons = [p[0] for p in lonlat_pts]
    lats = [p[1] for p in lonlat_pts]
    return min(lons), min(lats), max(lons), max(lats)


def today_utc():
    return datetime.now(timezone.utc).date()


# ---------- ERDDAP search/fetch ----------
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


@st.cache_data(ttl=3600)
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
            # approx grid spacing
            if len(lats) > 1 and len(lons) > 1:
                dy_km = abs(lats[1] - lats[0]) * 111.32
                dx_km = abs(lons[1] - lons[0]) * 111.32 * np.cos(np.deg2rad(np.mean(lats)))
                cell_km = float((dy_km + dx_km) / 2.0)
            else:
                cell_km = float("nan")
            return {
                "server": server,
                "dataset_id": dsid,
                "dataset_title": title,
                "var": varname,
                "units": units,
                "arrF": arrF,
                "lats": lats,
                "lons": lons,
                "cell_km": cell_km,
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


# ---------- High‑res land (Natural Earth 10m) + rasterized mask ----------
@st.cache_data(show_spinner=False)
def _download_ne10m_land_zip() -> bytes:
    url = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


@st.cache_resource(show_spinner=False)
def load_land_gdf():
    """Load Natural Earth 10m land polygons (EPSG:4326). Fallback to lowres if download fails."""
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

        # Small inward buffer to ensure shoreline cells are masked (adjust if over-masking)
        try:
            land_clip["geometry"] = land_clip.buffer(-0.0007)  # ~75 m
        except Exception:
            pass

        height, width = arrF.shape
        transform = from_bounds(minlon, minlat, maxlon, maxlat, width, height)
        shapes = [(geom, 1) for geom in land_clip.geometry if geom and not geom.is_empty]
        land_mask = rasterize(
            shapes=shapes,
            out_shape=(height, width),
            transform=transform,
            fill=0,
            all_touched=True,
            dtype="uint8",
        ).astype(bool)

        out = arrF.copy()
        out[land_mask] = np.nan
        return out
    except Exception:
        return arrF


# ---------- Hover helpers ----------
def add_tooltip_grid(
    m: folium.Map,
    arrF: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    zoom_level_hint: int = 8,
    mode: str = "Normal",
):
    """Tiny markers at decimated grid cell centers with sticky °F tooltips."""
    base = {"Sparse": 40, "Normal": 60, "Dense": 90}.get(mode, 60)
    target = min(base + max(0, zoom_level_hint - 6) * 20, 160)
    stride_lat = max(1, len(lats) // target)
    stride_lon = max(1, len(lons) // target)
    radius = 3 if zoom_level_hint <= 7 else (4 if zoom_level_hint <= 9 else 5)

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
                fill_opacity=0.06,  # tiny fill helps hover reliability
                color="#ffffff",
                fill_color="#ffffff",
                tooltip=folium.Tooltip(f"{v:.0f} °F", sticky=True, direction="top"),
            ).add_to(m)


# ---------- POIs ----------
POIS = [
    ("Haabs Ledge",          40 + 52.095/60.0,  -(71 + 50.292/60.0)),
    ("Butterfish Hole",      40 + 50.188/60.0,  -(71 + 40.494/60.0)),
    ("July2025",             40 + 53.733/60.0,  -(71 + 49.849/60.0)),
    ("CIA",                  40 + 56.006/60.0,  -(71 + 43.000/60.0)),
    ("Gully",                41 +  1.229/60.0,  -(71 + 25.017/60.0)),
    ("Wind Farm SW Corner",  40 + 58.439/60.0,  -(71 + 16.398/60.0)),
    ("Tuna Ridge",           40 + 55.000/60.0,  -(71 + 16 + 45/60.0)),
]


def add_pois(m: folium.Map):
    for name, lat, lon in POIS:
        # green dot
        folium.CircleMarker(
            location=[lat, lon],
            radius=5,
            color="#0ea5e9",     # outline (cyan-ish)
            weight=2,
            fill=True,
            fill_color="#16a34a", # green fill
            fill_opacity=0.95,
            opacity=1.0,
        ).add_to(m)
        # label (no white icon background)
        folium.Marker(
            location=[lat, lon],
            icon=DivIcon(
                html=f"""
                <div style="
                    font-size:12px;
                    color:#e5e7eb;
                    text-shadow: 0 1px 2px rgba(0,0,0,0.7);
                    transform: translate(8px, -8px);
                    white-space: nowrap;">
                    {name}
                </div>
                """
            ),
        ).add_to(m)


# ---------- Map builder (returns HTML) ----------
def build_map_html(
    sst,
    lock_fixed_scale=False,
    up_factor=2,
    tip_mode="Normal",
    map_height_px=820,
):
    aoi = get_aoi_lonlat()
    lon0 = float(np.mean([p[0] for p in aoi])); lat0 = float(np.mean([p[1] for p in aoi]))

    m = Map(
        location=[lat0, lon0],
        zoom_start=7,
        tiles="CartoDB positron",
        control_scale=True,
        max_zoom=12,
        min_zoom=5,
        width="100%",          # responsive width
        height=map_height_px,  # fixed height (Streamlit needs a number)
    )

    # AOI polygon
    poly_geo = {"type": "Feature", "properties": {"name": "AOI"},
                "geometry": {"type": "Polygon", "coordinates": [[list(pt) for pt in aoi]]}}
    GeoJson(poly_geo, name="AOI",
            style_function=lambda x: {"color": "#222", "weight": 2, "fillOpacity": 0}).add_to(m)

    # Base grid, orientation, AOI mask
    arrF = sst["arrF"]; lats = sst["lats"]; lons = sst["lons"]
    arrF, lats, lons = orient_to_leaflet(arrF, lats, lons)
    aoi_poly = Polygon(aoi)
    Lon, Lat = np.meshgrid(lons, lats)
    mask = np.vectorize(lambda x, y: aoi_poly.contains(Point(x, y)))(Lon, Lat)
    arrF = np.where(mask, arrF, np.nan)

    # High‑res land mask
    arrF = mask_land_rasterized(arrF, lats, lons)

    # Color scale
    finite = arrF[np.isfinite(arrF)]
    if lock_fixed_scale or finite.size < 50:
        vmin, vmax = 48, 90
    else:
        vmin = max(float(np.nanpercentile(finite, 5)), 48)
        vmax = min(float(np.nanpercentile(finite, 95)), 90)
        if vmin >= vmax: vmin, vmax = 48, 90

    # Visual upsample + render
    arrF_vis = upsample_visual(arrF, up_factor)
    cmap = build_colormap(vmin, vmax)
    rgba = array_to_rgba(arrF_vis, cmap)

    lat_min, lat_max = float(np.min(lats)), float(np.max(lats))
    lon_min, lon_max = float(np.min(lons)), float(np.max(lons))
    bounds = [[lat_min, lon_min], [lat_max, lon_max]]

    ImageOverlay(image=rgba, bounds=bounds, opacity=0.78,
                 name=f"SST (°F) [{int(vmin)}–{int(vmax)}]").add_to(m)
    cmap.caption = "SST (°F)"; cmap.add_to(m)

    # Fit to data bounds once
    m.fit_bounds(bounds)

    # Mouse lat/lon readout (in-map UI)
    MousePosition(position='topright', separator=' | ',
                  prefix='Lat | Lon:', num_digits=4).add_to(m)

    # Adaptive tooltip grid (use a mid-zoom hint)
    add_tooltip_grid(m, arrF, lats, lons, zoom_level_hint=8, mode=tip_mode)

    # POIs
    add_pois(m)

    # Render to HTML string
    html = m.get_root().render()
    return html


# ---------- Sidebar ----------
with st.sidebar:
    days_back = st.slider("Days back (select date)", 1, 7, 3)
    date_sel = today_utc() - timedelta(days=days_back - 1)
    lock_fixed = st.checkbox("Lock color scale to 48–90°F", value=False)
    up_choice = st.selectbox("Visual resolution", ["1x (native)", "2x upsample", "3x upsample"], index=1)
    up_factor = {"1x (native)": 1, "2x upsample": 2, "3x upsample": 3}[up_choice]
    tip_density = st.selectbox("Tooltip density", ["Sparse", "Normal", "Dense"], index=1)
    fetch_btn = st.button("Fetch SST", type="primary")


# ---------- Fetch ----------
if fetch_btn:
    with st.spinner("Fetching SST subset from ERDDAP…"):
        try:
            sst = get_sst(date_sel)
            st.session_state["sst_payload"] = sst
            st.success(f"Dataset: {sst['dataset_id']} — {sst['dataset_title']} | Var: {sst['var']} ({sst['units']})")
        except Exception as e:
            st.error(f"Failed to fetch or render SST: {e}")


# ---------- Render (static HTML; pan/zoom won't rerun) ----------
sst = st.session_state.get("sst_payload")
if sst is None:
    st.warning("Click **Fetch SST** to load data.")
else:
    if np.isfinite(sst.get("cell_km", np.nan)):
        st.caption(f"Approx grid spacing: ~{sst['cell_km']:.1f} km")

    # Choose a generous height; width is responsive (100% of main pane)
    MAP_HEIGHT_PX = 900

    html = build_map_html(
        sst,
        lock_fixed_scale=lock_fixed,
        up_factor=up_factor,
        tip_mode=tip_density,
        map_height_px=MAP_HEIGHT_PX,
    )

    # Embed as a static component (no Streamlit reruns on pan/zoom)
    components.html(html, height=MAP_HEIGHT_PX, scrolling=False)