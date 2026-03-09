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


def _download_ne10m_land_zip() -> bytes:
    import requests

    url = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def load_land_gdf():
    """Load and cache the Natural Earth 10m land GeoDataFrame."""
    global _LAND_GDF
    if _LAND_GDF is not None:
        return _LAND_GDF
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

        minlon, maxlon = float(np.min(lons)), float(np.max(lons))
        minlat, maxlat = float(np.min(lats)), float(np.max(lats))
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

        # Flip mask vertically because rasterize produces south-up
        aoi_mask = np.flipud(aoi_mask)

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


def mask_land_rasterized(
    arrF: np.ndarray, lats: np.ndarray, lons: np.ndarray
) -> np.ndarray:
    """Mask land pixels using Natural Earth 10m rasterized to the SST grid."""
    try:
        import geopandas as gpd
        from rasterio.features import rasterize
        from rasterio.transform import from_bounds

        land = load_land_gdf()
        if land is None or land.empty:
            return arrF

        minlon, maxlon = float(np.min(lons)), float(np.max(lons))
        minlat, maxlat = float(np.min(lats)), float(np.max(lats))
        bbox_geom = gpd.GeoDataFrame(
            geometry=[box(minlon, minlat, maxlon, maxlat)], crs="EPSG:4326"
        )
        land_clip = gpd.overlay(land, bbox_geom, how="intersection")
        if land_clip.empty:
            return arrF

        # Slight inward buffer to sharpen coastline edge
        try:
            land_clip["geometry"] = land_clip.buffer(-0.0007)  # ~75 m
        except Exception:
            pass

        height, width = arrF.shape
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

        # Flip mask vertically because rasterize produces south-up
        land_mask = np.flipud(land_mask)

        out = arrF.copy()
        out[land_mask] = np.nan
        return out
    except Exception:
        return arrF
