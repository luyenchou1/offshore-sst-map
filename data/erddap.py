"""ERDDAP search, dataset selection, grid fetch, and SST retrieval.

Bug fixes applied:
  1. MUR preference: reject non-MUR datasets when primary terms include "MUR"
  3. try/except around fetch_grid so failures fall through to next server
  4. Auto-retry with older dates if target date has no data (MUR latency)
"""

import io
import logging
import re
import tempfile
import time
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import xarray as xr

from data.convert import to_fahrenheit_whole

logger = logging.getLogger(__name__)


def erddap_search(server: str, terms: List[str]) -> Optional[pd.DataFrame]:
    """Search an ERDDAP server for SST datasets."""
    try:
        r = requests.get(
            f"{server}/search/index.csv",
            params={"searchFor": " ".join(terms)},
            timeout=8,
        )
        r.raise_for_status()
        return pd.read_csv(io.StringIO(r.text))
    except Exception:
        return None


def pick_dataset(
    df: Optional[pd.DataFrame], require_mur: bool = False
) -> Optional[Dict[str, str]]:
    """Score and pick the best SST dataset from search results.

    When *require_mur* is True (used with primary search terms), candidates
    that don't mention "mur" in their metadata are rejected so that coarser
    BLENDED datasets on other servers are skipped.  (Bug 1 fix)
    """
    if df is None or df.empty or "Dataset ID" not in df.columns:
        return None

    candidates = []
    for _, row in df.iterrows():
        dsid = str(row.get("Dataset ID", ""))
        title = str(row.get("Title", ""))
        sumry = str(row.get("Summary", ""))
        combined = (title + " " + sumry + " " + dsid).lower()
        if any(
            k in combined
            for k in [
                "analysed_sst",
                "sea surface temperature",
                "sst",
                "mur",
                "ghrsst",
                "oisst",
                "blended",
            ]
        ):
            candidates.append((dsid, title, sumry))

    # Bug 1 fix: when looking for MUR specifically, drop non-MUR results
    if require_mur:
        mur_only = [
            c for c in candidates if "mur" in (c[0] + " " + c[1] + " " + c[2]).lower()
        ]
        if mur_only:
            candidates = mur_only
        else:
            return None  # no MUR on this server — try the next one

    def score(item):
        dsid, title, sumry = item
        t = (title + " " + sumry + " " + dsid).lower()
        s = 0
        if "mur" in t:
            s += 10
        if "ghrsst" in t:
            s += 8
        if "analysed_sst" in t:
            s += 7
        if "oisst" in t or "blended" in t:
            s += 5
        if "daily" in t:
            s += 2
        if "l4" in t:
            s += 2
        return s

    candidates.sort(key=score, reverse=True)
    if candidates:
        dsid, title, _ = candidates[0]
        return {"id": dsid, "title": title}
    return None


def guess_var_from_das(das_text: str) -> Optional[str]:
    """Match actual variable definitions in ERDDAP DAS (e.g. '  sst {')."""
    for v in ["analysed_sst", "sst", "sea_surface_temperature", "temperature"]:
        if re.search(rf'^\s+{v}\s*\{{', das_text, re.MULTILINE):
            return v
    return None


def fetch_grid(
    server: str, dsid: str, date, bbox: Tuple[float, float, float, float]
):
    """Download a NetCDF grid slice from ERDDAP and return (xarray.Dataset, var_name)."""
    das_url = f"{server}/griddap/{dsid}.das"
    r = requests.get(das_url, timeout=10)
    r.raise_for_status()
    varname = guess_var_from_das(r.text)
    if not varname:
        raise RuntimeError("Could not identify SST variable in dataset DAS.")

    minlon, minlat, maxlon, maxlat = bbox
    t0 = f"{date}T00:00:00Z"
    t1 = f"{date}T23:59:59Z"
    query = (
        f"{varname}[({t0}):1:({t1})]"
        f"[({minlat}):1:({maxlat})]"
        f"[({minlon}):1:({maxlon})]"
    )
    nc_url = f"{server}/griddap/{dsid}.nc?{query}"
    rr = requests.get(nc_url, timeout=20)
    rr.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tf:
        tf.write(rr.content)
        path = tf.name
    ds = xr.open_dataset(path)
    return ds, varname


def fetch_grid_multiday(
    server: str,
    dsid: str,
    date_start,
    date_end,
    bbox: Tuple[float, float, float, float],
):
    """Download a multi-day NetCDF grid from ERDDAP.

    Returns (xarray.Dataset, var_name) with a time dimension spanning
    date_start through date_end.
    """
    das_url = f"{server}/griddap/{dsid}.das"
    r = requests.get(das_url, timeout=10)
    r.raise_for_status()
    varname = guess_var_from_das(r.text)
    if not varname:
        raise RuntimeError("Could not identify SST variable in dataset DAS.")

    minlon, minlat, maxlon, maxlat = bbox
    t0 = f"{date_start}T00:00:00Z"
    t1 = f"{date_end}T23:59:59Z"
    query = (
        f"{varname}[({t0}):1:({t1})]"
        f"[({minlat}):1:({maxlat})]"
        f"[({minlon}):1:({maxlon})]"
    )
    nc_url = f"{server}/griddap/{dsid}.nc?{query}"
    # Larger timeout for multi-day downloads (~10x single-day data)
    rr = requests.get(nc_url, timeout=75)
    rr.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tf:
        tf.write(rr.content)
        path = tf.name
    ds = xr.open_dataset(path)
    return ds, varname


def get_sst(target_date, config: Dict) -> Dict:
    """Fetch SST data, returning a dict with arrF, lats, lons, metadata.

    Improvements over original:
      - Tries target_date, then date-1, date-2 for MUR latency (Bug 4 fix)
      - Requires MUR when using primary search terms (Bug 1 fix)
      - Wraps fetch_grid in try/except per server (Bug 3 fix)
    """
    MAX_SECONDS = 25  # total time budget — must finish within Dash's timeout

    aoi = [tuple(pt) for pt in config["aoi_polygon_lonlat"]]
    lons_aoi = [p[0] for p in aoi]
    lats_aoi = [p[1] for p in aoi]
    bbox = (min(lons_aoi), min(lats_aoi), max(lons_aoi), max(lats_aoi))

    t0 = time.monotonic()

    # Bug 4 fix: try up to 3 dates to handle MUR data latency
    for date_offset in range(3):
        date = target_date - timedelta(days=date_offset)

        for terms in [config["primary_search_terms"], config["fallback_search_terms"]]:
            is_primary = terms == config["primary_search_terms"]

            for server in config["servers"]:
                if time.monotonic() - t0 > MAX_SECONDS:
                    raise RuntimeError(
                        f"SST fetch timed out after {MAX_SECONDS}s. "
                        f"Data for this date range may not be available yet."
                    )

                df = erddap_search(server, terms)
                choice = pick_dataset(df, require_mur=is_primary)  # Bug 1 fix
                if not choice:
                    continue

                dsid, title = choice["id"], choice["title"]

                try:  # Bug 3 fix
                    ds, varname = fetch_grid(server, dsid, date, bbox)
                except Exception as e:
                    logger.warning(
                        "fetch_grid failed for %s/%s on %s: %s",
                        server, dsid, date, e,
                    )
                    continue

                da = ds[varname].squeeze()
                lat_name = next(
                    (d for d in ds.dims if "lat" in d.lower()), "lat"
                )
                lon_name = next(
                    (d for d in ds.dims if "lon" in d.lower()), "lon"
                )
                data2 = (
                    da.values[0, :, :]
                    if ("time" in da.dims and da.ndim == 3)
                    else da.values
                )
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
                    "date_used": str(date),
                }

    raise RuntimeError(
        "No compatible SST dataset found on any configured ERDDAP server."
    )


def _fetch_single_day(server, dsid, varname, date, bbox):
    """Fetch one day — helper for parallel execution.

    Skips the DAS lookup (already done by caller) and fetches the grid
    directly.  Retries once on 429 rate-limit errors.
    """
    minlon, minlat, maxlon, maxlat = bbox
    t0 = f"{date}T00:00:00Z"
    t1 = f"{date}T23:59:59Z"
    query = (
        f"{varname}[({t0}):1:({t1})]"
        f"[({minlat}):1:({maxlat})]"
        f"[({minlon}):1:({maxlon})]"
    )
    nc_url = f"{server}/griddap/{dsid}.nc?{query}"

    for attempt in range(3):
        rr = requests.get(nc_url, timeout=45)
        if rr.status_code == 429:
            time.sleep(2 * (attempt + 1))  # back off: 2s, 4s, 6s
            continue
        rr.raise_for_status()
        break
    else:
        raise RuntimeError(f"Rate limited after 3 retries for {date}")

    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tf:
        tf.write(rr.content)
        path = tf.name
    ds = xr.open_dataset(path)
    da = ds[varname].squeeze()
    lat_name = next((d for d in ds.dims if "lat" in d.lower()), "lat")
    lon_name = next((d for d in ds.dims if "lon" in d.lower()), "lon")
    data2 = (
        da.values[0, :, :] if ("time" in da.dims and da.ndim == 3) else da.values
    )
    lats = ds[lat_name].values
    lons = ds[lon_name].values
    units = da.attrs.get("units", "kelvin")
    arrF = to_fahrenheit_whole(data2, units)
    return {"arrF": arrF, "date": str(date), "lats": lats, "lons": lons, "units": units}


def get_sst_multiday(end_date, config: Dict, num_days: int = 7) -> Dict:
    """Fetch multiple days of SST using parallel individual-day requests.

    Returns a dict with per-day 2D arrays, shared lats/lons, and metadata.
    Falls back to older date ranges if the requested end_date is not yet
    available (MUR has ~2-day latency).

    Uses concurrent.futures to fetch all days in parallel for speed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    MAX_SECONDS = 90

    aoi = [tuple(pt) for pt in config["aoi_polygon_lonlat"]]
    lons_aoi = [p[0] for p in aoi]
    lats_aoi = [p[1] for p in aoi]
    bbox = (min(lons_aoi), min(lats_aoi), max(lons_aoi), max(lats_aoi))

    t0 = time.monotonic()

    # Try the requested window, then shift back 1-2 days for MUR latency
    for date_offset in range(3):
        adj_end = end_date - timedelta(days=date_offset)
        dates = [adj_end - timedelta(days=i) for i in range(num_days - 1, -1, -1)]

        for terms in [config["primary_search_terms"], config["fallback_search_terms"]]:
            is_primary = terms == config["primary_search_terms"]

            for server in config["servers"]:
                if time.monotonic() - t0 > MAX_SECONDS:
                    raise RuntimeError(
                        f"SST fetch timed out after {MAX_SECONDS}s. "
                        f"Data for this date range may not be available yet."
                    )

                df = erddap_search(server, terms)
                choice = pick_dataset(df, require_mur=is_primary)
                if not choice:
                    continue

                dsid, title = choice["id"], choice["title"]

                # Look up the variable name once (shared across all days)
                try:
                    das_url = f"{server}/griddap/{dsid}.das"
                    r = requests.get(das_url, timeout=10)
                    r.raise_for_status()
                    varname = guess_var_from_das(r.text)
                    if not varname:
                        continue
                except Exception:
                    continue

                # Fetch all days in parallel (max 2 to avoid rate limits)
                try:
                    results = {}
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = {
                            pool.submit(
                                _fetch_single_day, server, dsid, varname, d, bbox
                            ): d
                            for d in dates
                        }
                        for fut in as_completed(futures):
                            d = futures[fut]
                            results[d] = fut.result()  # raises on failure
                except Exception as e:
                    logger.warning(
                        "Parallel fetch failed for %s/%s on %s to %s: %s",
                        server, dsid, dates[0], dates[-1], e,
                    )
                    continue

                # Assemble results in chronological order
                days = []
                for d in dates:
                    r = results[d]
                    days.append({"arrF": r["arrF"], "date": r["date"]})

                first = results[dates[0]]
                return {
                    "server": server,
                    "dataset_id": dsid,
                    "dataset_title": title,
                    "var": "analysed_sst",
                    "units": first["units"],
                    "days": days,
                    "lats": first["lats"],
                    "lons": first["lons"],
                }

    raise RuntimeError(
        "No compatible SST dataset found on any configured ERDDAP server."
    )
