"""Geospatial helpers: orientation, AOI masking, land masking."""

import logging
import os
import tempfile
from functools import lru_cache
from typing import Dict, List, Tuple

import numpy as np
from shapely.geometry import Polygon, box

logger = logging.getLogger(__name__)

# --------------- Natural Earth 10m land mask ---------------

_LAND_GDF = None  # module-level cache
_LAND_FAILED_AT = None  # monotonic time of the last failed load: don't retry for 10 minutes
_LAND_LOCK = __import__("threading").Lock()


def _download_ne10m_land_zip() -> bytes:
    import requests

    url = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip"
    r = requests.get(url, timeout=(5, 20))
    r.raise_for_status()
    return r.content


def load_land_gdf():
    """Load and cache the Natural Earth 10m land GeoDataFrame (once per process; the startup
    pre-warm loads it). A failure is remembered for 10 minutes so a fetch from NOAA never waits
    on the download again within its time budget (the week is then served unmasked)."""
    global _LAND_GDF, _LAND_FAILED_AT
    import time as _time
    if _LAND_GDF is not None:
        return _LAND_GDF
    if _LAND_FAILED_AT is not None and _time.monotonic() - _LAND_FAILED_AT < 600:
        return None
    with _LAND_LOCK:
        if _LAND_GDF is not None:
            return _LAND_GDF
        gdf = _load_land_gdf()
        if gdf is None:
            _LAND_FAILED_AT = _time.monotonic()
        return gdf


def _load_land_gdf():
    global _LAND_GDF
    try:
        import geopandas as gpd

        data = _download_ne10m_land_zip()
        tmp = tempfile.mkdtemp()
        zip_path = os.path.join(tmp, "ne10m_land.zip")
        with open(zip_path, "wb") as f:
            f.write(data)
        gdf = gpd.read_file(f"zip://{zip_path}").to_crs("EPSG:4326")
        _LAND_GDF = gdf
        return gdf
    except Exception:
        try:
            import geopandas as gpd

            gdf = gpd.read_file(
                gpd.datasets.get_path("naturalearth_lowres")
            ).to_crs("EPSG:4326")
            _LAND_GDF = gdf
            return gdf
        except Exception:
            return None


# --------------- Orientation ---------------


def orient_to_leaflet(
    arrF: np.ndarray, lats: np.ndarray, lons: np.ndarray
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ensure row 0 = North, col 0 = West."""
    if lats[0] < lats[-1]:
        arrF = np.flipud(arrF)
        lats = lats[::-1]
    if lons[0] > lons[-1]:
        arrF = np.fliplr(arrF)
        lons = lons[::-1]
    if arrF.shape == (len(lons), len(lats)):
        arrF = arrF.T
    return arrF, lats, lons


# --------------- Grid edges ---------------


def _half_step(vals: np.ndarray) -> float:
    return abs(float(vals[1] - vals[0])) / 2 if len(vals) > 1 else 0.0


def grid_edges(lats: np.ndarray, lons: np.ndarray) -> Tuple[float, float, float, float]:
    """(minlon, minlat, maxlon, maxlat) of the grid's outer cell edges.

    lats/lons are cell centres; the image and the rasterized masks must span the edges, half a
    cell beyond the first and last centres. At MUR's 0.01 degree that is 500 m; at OISST's
    0.25 degree it is 14 km, enough to misplace the whole map without it.
    """
    hy, hx = _half_step(lats), _half_step(lons)
    return (float(np.min(lons)) - hx, float(np.min(lats)) - hy,
            float(np.max(lons)) + hx, float(np.max(lats)) + hy)


def grid_bounds(lats: np.ndarray, lons: np.ndarray):
    """Leaflet ImageOverlay bounds [[south, west], [north, east]] of the grid's outer edges."""
    minlon, minlat, maxlon, maxlat = grid_edges(lats, lons)
    return [[minlat, minlon], [maxlat, maxlon]]


# --------------- AOI mask (rasterized — fast) ---------------


def mask_aoi_rasterized(
    arrF: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    config: Dict,
) -> np.ndarray:
    """Mask pixels outside the AOI polygon using rasterio rasterize."""
    try:
        from rasterio.features import rasterize
        from rasterio.transform import from_bounds

        aoi_coords = [tuple(pt) for pt in config["aoi_polygon_lonlat"]]
        aoi_poly = Polygon(aoi_coords)

        minlon, minlat, maxlon, maxlat = grid_edges(lats, lons)
        height, width = arrF.shape
        transform = from_bounds(minlon, minlat, maxlon, maxlat, width, height)

        aoi_mask = rasterize(
            shapes=[(aoi_poly, 1)],
            out_shape=(height, width),
            transform=transform,
            fill=0,
            all_touched=True,
            dtype="uint8",
        ).astype(bool)

        out = arrF.copy()
        out[~aoi_mask] = np.nan
        return out
    except Exception as e:
        logger.warning("Rasterized AOI mask failed, falling back to shapely: %s", e)
        return _mask_aoi_shapely(arrF, lats, lons, config)


def _mask_aoi_shapely(
    arrF: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    config: Dict,
) -> np.ndarray:
    """Fallback: point-in-polygon test with shapely (slower)."""
    from shapely.geometry import Point

    aoi_coords = [tuple(pt) for pt in config["aoi_polygon_lonlat"]]
    aoi_poly = Polygon(aoi_coords)
    Lon, Lat = np.meshgrid(lons, lats)
    mask = np.vectorize(lambda x, y: aoi_poly.contains(Point(x, y)))(Lon, Lat)
    out = arrF.copy()
    out[~mask] = np.nan
    return out


# --------------- Land mask (rasterized) ---------------


_LAND_MASKS = {}  # (height, width, edges) -> bool mask; every day of a week shares one grid


def mask_land_rasterized(
    arrF: np.ndarray, lats: np.ndarray, lons: np.ndarray
) -> np.ndarray:
    """Mask land pixels using Natural Earth 10m rasterized to the SST grid (cached per grid)."""
    key = (arrF.shape, tuple(round(v, 6) for v in grid_edges(lats, lons)))
    land_mask = _LAND_MASKS.get(key)
    if land_mask is None:
        land_mask = _land_mask(arrF.shape, lats, lons)
        if land_mask is None:
            return arrF
        if len(_LAND_MASKS) >= 4:
            _LAND_MASKS.pop(next(iter(_LAND_MASKS)))
        _LAND_MASKS[key] = land_mask
    out = arrF.copy()
    out[land_mask] = np.nan
    return out


def _land_mask(shape, lats: np.ndarray, lons: np.ndarray):
    """The land raster for a grid, or None (no land data, nothing to mask, or an error)."""
    try:
        import geopandas as gpd
        from rasterio.features import rasterize
        from rasterio.transform import from_bounds

        land = load_land_gdf()
        if land is None or land.empty:
            return None

        minlon, minlat, maxlon, maxlat = grid_edges(lats, lons)
        bbox_geom = gpd.GeoDataFrame(
            geometry=[box(minlon, minlat, maxlon, maxlat)], crs="EPSG:4326"
        )
        land_clip = gpd.overlay(land, bbox_geom, how="intersection")
        if land_clip.empty:
            return None

        # Slight inward buffer to sharpen coastline edge (in degrees on purpose: geopandas'
        # "geographic CRS" warning is noise here)
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                land_clip["geometry"] = land_clip.buffer(-0.0007)  # ~75 m
        except Exception:
            pass

        height, width = shape
        transform = from_bounds(minlon, minlat, maxlon, maxlat, width, height)
        shapes = [
            (geom, 1)
            for geom in land_clip.geometry
            if geom and not geom.is_empty
        ]
        land_mask = rasterize(
            shapes=shapes,
            out_shape=(height, width),
            transform=transform,
            fill=0,
            all_touched=True,
            dtype="uint8",
        ).astype(bool)
        return land_mask
    except Exception:
        return None
