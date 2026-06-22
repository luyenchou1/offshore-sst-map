#!/usr/bin/env python3
"""Build recent SST cache windows locally and push them to the live app.

Why this exists: NOAA's firewall blocks ERDDAP from Render's datacenter IP
(connections are refused / unreachable), so the deployed app can't fetch SST
on a cache miss. This script runs somewhere ERDDAP IS reachable (a residential
IP or a CI runner that isn't blocked), builds the same raw-only cache files the
app's pre-cache writes, and POSTs them to /api/cache/upload.

Seed finalized end-dates (today-4 and older never go "stale"); together with the
app's ±3-day fuzzy cache lookup, that covers every selectable recent date.

Usage (from repo root, with the project venv):
  CACHE_UPLOAD_TOKEN=secret \
  python tools/sync_cache.py --base-url https://sst.gotoneapp.com

  python tools/sync_cache.py --dry-run            # build only, no upload
"""
from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
import os
import sys
import time
from datetime import date, timedelta

import numpy as np
import requests

# Make `data.*` importable regardless of where this is launched from
# (cron / CI put the script's own dir on sys.path, not the repo root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Clean imports only — deliberately NOT importing app.py (it starts a
# pre-warm thread at import and, in some branches, loads extra data).
from data.cache import cache_key  # noqa: E402
from data.erddap import get_sst_multiday  # noqa: E402
from data.geo import mask_aoi_rasterized, mask_land_rasterized, orient_to_leaflet  # noqa: E402


def _serialize_array(arr: np.ndarray) -> str:
    """base64-encoded numpy .npy — identical to app._serialize_array."""
    buf = io.BytesIO()
    np.save(buf, arr)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_raw_payload(sst: dict, cfg: dict, locked: bool = False) -> dict:
    """Raw-only disk-cache payload (frames=None).

    Mirrors app._precache_single_date exactly so the uploaded file is
    byte-compatible with what the app reads. Only the adaptive payload is
    needed: the app rebuilds the locked variant from the same raw arrays.
    """
    lats_raw = sst["lats"]
    lons_raw = sst["lons"]
    _, lats, lons = orient_to_leaflet(
        sst["days"][0]["arrF"], lats_raw.copy(), lons_raw.copy()
    )
    serialized_lats = _serialize_array(lats)
    serialized_lons = _serialize_array(lons)
    res_km = abs(float(lats[1] - lats[0])) * 111.0 if len(lats) > 1 else None
    bounds = [
        [float(np.min(lats)), float(np.min(lons))],
        [float(np.max(lats)), float(np.max(lons))],
    ]

    serialized_days = []
    dates = []
    running_p5_min = float("inf")
    running_p95_max = float("-inf")
    for day_data in sst["days"]:
        arrF = day_data["arrF"]
        arrF, _, _ = orient_to_leaflet(arrF, lats_raw.copy(), lons_raw.copy())
        arrF = mask_aoi_rasterized(arrF, lats, lons, cfg)
        arrF = mask_land_rasterized(arrF, lats, lons)
        finite = arrF[np.isfinite(arrF)]
        if finite.size >= 50:
            running_p5_min = min(running_p5_min, float(np.nanpercentile(finite, 5)))
            running_p95_max = max(running_p95_max, float(np.nanpercentile(finite, 95)))
        serialized_days.append(
            {"arrF": _serialize_array(arrF), "date": day_data["date"]}
        )
        dates.append(day_data["date"])

    if locked:
        vmin, vmax = 30.0, 90.0
    elif running_p5_min < float("inf"):
        vmin = max(running_p5_min, 28.0)
        vmax = min(running_p95_max, 95.0)
        if vmax - vmin < 5.0:
            mid = (vmin + vmax) / 2
            vmin, vmax = mid - 3.0, mid + 3.0
    else:
        vmin, vmax = 30.0, 90.0

    return {
        "frames": None,  # raw-only; app renders PNGs on first load
        "dates": dates,
        "bounds": bounds,
        "vmin": float(vmin),
        "vmax": float(vmax),
        "res_km": res_km,
        "server": sst["server"],
        "dataset_id": sst["dataset_id"],
        "dataset_title": sst["dataset_title"],
        "raw_days": serialized_days,
        "lats": serialized_lats,
        "lons": serialized_lons,
    }


def upload_cache_file(base_url, token, fname, blob, attempts=5):
    """POST one cache file, retrying transient Render 5xx / network errors.

    Render runs a single worker; rapid uploads can momentarily saturate it and
    its proxy returns 502/503. Those are transient — back off and retry rather
    than dropping the file.
    """
    url = f"{base_url.rstrip('/')}/api/cache/upload"
    headers = {
        "X-Upload-Token": token,
        "X-Cache-Filename": fname,
        "Content-Type": "application/octet-stream",
    }
    backoff = [3, 8, 15, 25]
    for i in range(attempts):
        try:
            r = requests.post(url, data=blob, headers=headers, timeout=90)
            if r.status_code == 200:
                return True, r.json()
            if r.status_code in (500, 502, 503, 504) and i < attempts - 1:
                wait = backoff[min(i, len(backoff) - 1)]
                print(f"  {fname}: HTTP {r.status_code} (worker busy) — retry in {wait}s",
                      flush=True)
                time.sleep(wait)
                continue
            return False, f"HTTP {r.status_code} {r.text[:120]}"
        except requests.RequestException as e:
            if i < attempts - 1:
                wait = backoff[min(i, len(backoff) - 1)]
                print(f"  {fname}: {type(e).__name__} — retry in {wait}s", flush=True)
                time.sleep(wait)
                continue
            return False, f"{type(e).__name__}: {e}"
    return False, "exhausted retries"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base-url",
        default=os.environ.get("SYNC_BASE_URL", "https://sst.gotoneapp.com"),
        help="Live app base URL",
    )
    ap.add_argument("--token", default=os.environ.get("CACHE_UPLOAD_TOKEN"))
    ap.add_argument(
        "--start-back", type=int, default=2,
        help="Most recent end-date to seed = today - start_back (default 2)",
    )
    ap.add_argument(
        "--count", type=int, default=9,
        help="Number of consecutive end-dates to seed (default 9)",
    )
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--dry-run", action="store_true", help="Build but do not upload")
    ap.add_argument(
        "--upload-delay", type=float, default=2.0,
        help="Seconds to pause between uploads (avoids saturating the single "
             "Render worker; default 2.0)",
    )
    args = ap.parse_args()

    if not args.token and not args.dry_run:
        print("ERROR: no token (set CACHE_UPLOAD_TOKEN or pass --token)", file=sys.stderr)
        return 2

    with open(args.config) as f:
        cfg = json.load(f)

    today = date.today()
    end_dates = [today - timedelta(days=args.start_back + i) for i in range(args.count)]
    print(f"seeding {len(end_dates)} dates: {end_dates[0]} .. {end_dates[-1]} "
          f"-> {args.base_url}")

    ok = fail = 0
    for ed in end_dates:
        fname = cache_key(ed, False) + ".json.gz"
        try:
            t = time.monotonic()
            sst = get_sst_multiday(ed, cfg)
            payload = build_raw_payload(sst, cfg, locked=False)
            blob = gzip.compress(json.dumps(payload).encode("utf-8"))
            print(f"built {fname}  {len(blob) / 1024:.0f} KB  in "
                  f"{time.monotonic() - t:.1f}s", flush=True)
            if args.dry_run:
                ok += 1
                continue
            success, info = upload_cache_file(args.base_url, args.token, fname, blob)
            if success:
                print(f"  uploaded: {info}", flush=True)
                ok += 1
            else:
                print(f"  UPLOAD FAILED {fname}: {info}", file=sys.stderr, flush=True)
                fail += 1
            time.sleep(args.upload_delay)
        except Exception as e:  # noqa: BLE001 — report and continue to next date
            print(f"  FAILED {fname}: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
            fail += 1

    print(f"done: {ok} ok, {fail} failed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
