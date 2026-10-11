#!/usr/bin/env python3
"""Build the animated tidal-current data for the /currents page.

Runs off-Render (locally or in GitHub Actions):
  1. Read hourly currents from the NECOFS FVCOM model (UMass Dartmouth) over
     OPeNDAP: the 1978-2016 GOM3 hindcast for curated weeks
     (data/currents_weeks.json), or the live GOM3 forecast when UMassD
     publishes it.
  2. Interpolate the model's unstructured triangle mesh onto a regular ~400 m
     lat/lon grid over eastern Long Island, quantize to int8 and pack GOCUR1
     frame files (format: data/currents.py).
  3. Fetch NOAA CO-OPS tide + tidal-current predictions for the same window.
  4. Write to --out-dir (default: the local CURRENTS_DIR, so a local dev
     server serves it immediately) and optionally upload to the live app,
     manifest last so a half-uploaded week never goes live. A week that fails
     to build is skipped (its old version stays live) and the run exits 1.

Usage (repo root, project venv with requirements-tools.txt installed):
  python tools/build_currents.py --weeks 2015-09-supermoon
  CACHE_UPLOAD_TOKEN=... python tools/build_currents.py --weeks all --upload
  python tools/build_currents.py --source forecast --upload   # exits 0 if offline
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import time

import numpy as np
import requests

# Make `data.*` importable from any working directory (cron/CI).
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Never import app.py here: it starts a pre-warm thread and loads catch data.
from data import currents as C  # noqa: E402

NECOFS_HINDCAST = "http://www.smast.umassd.edu:8080/thredds/dodsC/fvcom/hindcasts/30yr_gom3"
NECOFS_FORECAST = ("http://www.smast.umassd.edu:8080/thredds/dodsC/"
                   "FVCOM/NECOFS/Forecasts/NECOFS_GOM3_FORECAST.nc")
COOPS = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
# NOAA's WAF rejects the default python-requests User-Agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GotOneSST/1.0; +https://sst.gotoneapp.com)"}
TIDE_STATION = "8510560"        # Montauk (Fort Pond Bay)
NOAA_MIN_INTERVAL = 1.0         # s between CO-OPS calls (~175/week); doubles per 403
MJD0_EPOCH = int(dt.datetime(1858, 11, 17, tzinfo=dt.timezone.utc).timestamp())
SCALE = 0.025                   # m/s per int8 unit -> +/-3.2 m/s (6.2 kn)
KNOTS = 1.943844
WEEKS_FILE = os.path.join(REPO, "data", "currents_weeks.json")
MAX_SERVED_BYTES = 6.5e6        # Render's proxy 502s responses above ~7-10 MB


def log(msg):
    print(msg, flush=True)


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")


class NoRetry(Exception):
    """A deterministic failure (e.g. NOAA 'no predictions') - don't retry."""


class Throttled(Exception):
    """NOAA's firewall answered 403/429: back off for minutes, not seconds."""


def retry(fn, what, tries=5, base=3):
    for i in range(tries):
        try:
            return fn()
        except NoRetry:
            raise
        except Exception as e:  # noqa: BLE001 - network/OPeNDAP errors vary
            if i == tries - 1:
                raise
            wait = 60 * 2 ** i if isinstance(e, Throttled) else base * (i + 1)
            log(f"    {what} failed ({type(e).__name__}: {e}); retry in {wait}s")
            time.sleep(wait)


# ---------------------------------------------------------------- model ---

def open_dataset(url):
    import netCDF4
    return retry(lambda: netCDF4.Dataset(url), f"open {url}", tries=3)


def model_times(ds):
    """Model step times as epoch seconds, rounded to the hour.

    Uses the `time` coordinate, which THREDDS caches for aggregations. Do NOT
    read Itime/Itime2 here: on the 39-year hindcast aggregation that forces the
    server to open every monthly file (it hung for 10+ minutes). float32 MJD
    only resolves ~6 minutes at modern dates, so rounding to the hour is exact
    for hourly output - and only hourly windows are accepted downstream.
    """
    mjd = np.asarray(ds.variables["time"][:], dtype=np.float64)
    secs = np.round(mjd * 86400 / 3600) * 3600
    return secs.astype(np.int64) + MJD0_EPOCH


def window_steps(times, k0, want):
    """Pick `want` unique hourly steps starting at raw index k0.

    The 39-year hindcast is an aggregation of monthly files that repeats each
    month-boundary hour (467 duplicate steps), so a week can span `want + 1`
    raw steps. Returns (k1_raw, keep) where keep indexes rows of the raw range
    [k0, k1_raw) - the first copy of each hour.
    """
    k1 = min(k0 + want, len(times))
    while True:
        seg = times[k0:k1]
        keep = np.where(np.r_[True, np.diff(seg) != 0])[0]
        if keep.size >= want or k1 >= len(times):
            break
        k1 = min(k1 + (want - keep.size), len(times))
    keep = keep[:want]
    if keep.size != want or not np.all(np.diff(times[k0 + keep]) == 3600):
        raise RuntimeError("model steps are not a clean hourly series")
    return k0 + int(keep[-1]) + 1, keep


def load_mesh(ds):
    v = ds.variables
    lon = (np.asarray(v["lon"][:], np.float64) + 180) % 360 - 180
    lat = np.asarray(v["lat"][:], np.float64)
    lonc = (np.asarray(v["lonc"][:], np.float64) + 180) % 360 - 180
    latc = np.asarray(v["latc"][:], np.float64)
    nv = np.asarray(v["nv"][:], np.int64) - 1          # (3, nele), 0-based
    return lon, lat, lonc, latc, nv


def build_geometry(mesh, bbox, grid_m, margin=0.05):
    """Precompute the mesh -> regular-grid mapping once per model source.

    FVCOM velocities live at triangle centroids; they are averaged onto the
    triangle vertices (sparse matrix A), then each grid point takes the
    barycentric blend of its containing triangle's vertices. Grid points
    outside the mesh are land (mask = 0).
    """
    from matplotlib.tri import Triangulation
    from scipy.sparse import csr_matrix

    lon, lat, lonc, latc, nv = mesh
    lon0, lat0, lon1, lat1 = bbox
    sel = np.where((lonc > lon0 - margin) & (lonc < lon1 + margin)
                   & (latc > lat0 - margin) & (latc < lat1 + margin))[0]
    if sel.size == 0:
        raise RuntimeError("no model elements inside the region")
    nodes, inv = np.unique(nv[:, sel].T, return_inverse=True)
    tri = inv.reshape(-1, 3)
    counts = np.bincount(tri.ravel(), minlength=nodes.size).astype(np.float64)
    A = csr_matrix(
        (1.0 / counts[tri.ravel()], (tri.ravel(), np.repeat(np.arange(sel.size), 3))),
        shape=(nodes.size, sel.size),
    )

    mid = np.radians((lat0 + lat1) / 2)
    nx = int(round((lon1 - lon0) * 111320 * np.cos(mid) / grid_m)) + 1
    ny = int(round((lat1 - lat0) * 111320 / grid_m)) + 1
    glon = np.linspace(lon0, lon1, nx)
    glat = np.linspace(lat1, lat0, ny)                 # row 0 = north
    gx, gy = (a.ravel() for a in np.meshgrid(glon, glat))

    xs, ys = lon[nodes], lat[nodes]
    finder = Triangulation(xs, ys, triangles=tri).get_trifinder()
    ti = finder(gx, gy)
    water = np.where(ti >= 0)[0]
    vtx = tri[ti[water]]
    x, y = xs[vtx], ys[vtx]
    px, py = gx[water], gy[water]
    det = (y[:, 1] - y[:, 2]) * (x[:, 0] - x[:, 2]) + (x[:, 2] - x[:, 1]) * (y[:, 0] - y[:, 2])
    w0 = ((y[:, 1] - y[:, 2]) * (px - x[:, 2]) + (x[:, 2] - x[:, 1]) * (py - y[:, 2])) / det
    w1 = ((y[:, 2] - y[:, 0]) * (px - x[:, 2]) + (x[:, 0] - x[:, 2]) * (py - y[:, 2])) / det
    mask = np.zeros(ny * nx, np.uint8)
    mask[water] = 1
    return {
        "sel": sel, "i0": int(sel.min()), "i1": int(sel.max()) + 1,
        "A": A, "nx": nx, "ny": ny, "bbox": bbox,
        "water": water, "vtx": vtx, "w": np.stack([w0, w1, 1 - w0 - w1], 1),
        "mask": mask.reshape(ny, nx), "nodes": nodes,
    }


def fetch_elements(ds, name, k0, k1, i0, i1, layer=None, chunk=24):
    """Read var[k0:k1, (layer,) i0:i1] in time chunks, with retries."""
    out = np.empty((k1 - k0, i1 - i0), np.float32)
    for a in range(k0, k1, chunk):
        b = min(a + chunk, k1)

        def get(a=a, b=b):
            var = ds.variables[name]
            arr = var[a:b, layer, i0:i1] if layer is not None else var[a:b, i0:i1]
            return np.ma.filled(np.ma.asarray(arr, dtype=np.float32), 0.0)

        t = time.monotonic()
        out[a - k0:b - k0] = retry(get, f"{name}[{a}:{b}]")
        log(f"    {name} hours {a - k0:3d}-{b - k0 - 1:3d} ({time.monotonic() - t:.1f}s)")
    return out


def mesh_to_grid(elem, g):
    """(nt, span) element values -> (nt, ny, nx) grid, 0 on land."""
    ev = elem[:, g["sel"] - g["i0"]]
    node = (g["A"] @ ev.T).T.astype(np.float32)
    vals = np.einsum("tnk,nk->tn", node[:, g["vtx"]], g["w"].astype(np.float32))
    out = np.zeros((elem.shape[0], g["ny"] * g["nx"]), np.float32)
    out[:, g["water"]] = vals
    return out.reshape(elem.shape[0], g["ny"], g["nx"])


def quantize(a):
    return np.clip(np.round(a / SCALE), -127, 127).astype(np.int8)


def sample_point(g, field, lat, lon):
    """Mask-aware bilinear sample of field (nt, ny, nx) at a point.
    Returns (nt,) values, or None if the point is mostly on land/outside."""
    lon0, lat0, lon1, lat1 = g["bbox"]
    fx = (lon - lon0) / (lon1 - lon0) * (g["nx"] - 1)
    fy = (lat1 - lat) / (lat1 - lat0) * (g["ny"] - 1)
    i, j = int(np.floor(fx)), int(np.floor(fy))
    if not (0 <= i < g["nx"] - 1 and 0 <= j < g["ny"] - 1):
        return None
    ax, ay = fx - i, fy - j
    total, wsum = 0.0, 0.0
    for dj, di, w in ((0, 0, (1 - ax) * (1 - ay)), (0, 1, ax * (1 - ay)),
                      (1, 0, (1 - ax) * ay), (1, 1, ax * ay)):
        if g["mask"][j + dj, i + di]:
            total = total + w * field[:, j + dj, i + di]
            wsum += w
    return None if wsum < 0.5 else total / wsum


# ----------------------------------------------------------------- NOAA ---

_noaa = {"last": 0.0, "gap": NOAA_MIN_INTERVAL}


def get_json(url, params=None, what=""):
    """GET a NOAA CO-OPS endpoint, one call at a time. NOAA rate-limits per IP
    (403 after ~160 calls, both as a 21 req/s burst from a GitHub runner and
    at 2 req/s from home), so calls are spaced out and the spacing doubles,
    up to 4 s, each time we're throttled."""
    def go():
        pause = _noaa["last"] + _noaa["gap"] - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        _noaa["last"] = time.monotonic()
        r = requests.get(url, params=params, headers=HEADERS, timeout=60)
        if r.status_code in (403, 429):
            _noaa["gap"] = min(_noaa["gap"] * 2, 4.0)
            raise Throttled(f"HTTP {r.status_code} from NOAA (rate limited; "
                            f"now {_noaa['gap']:g}s between calls)")
        r.raise_for_status()
        j = r.json()
        if isinstance(j, dict) and "error" in j:
            err = j["error"]
            raise NoRetry(err.get("message", err) if isinstance(err, dict) else err)
        return j
    return retry(go, what or url, tries=4)


def coops(params, what):
    p = {"units": "english", "time_zone": "gmt", "format": "json",
         "application": "gotone_currents", **params}
    return get_json(COOPS, p, what)


def ymdhm(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y%m%d %H:%M")


def parse_t(s):
    return int(dt.datetime.strptime(s, "%Y-%m-%d %H:%M")
               .replace(tzinfo=dt.timezone.utc).timestamp())


def region_current_stations(bbox):
    j = get_json(f"{MDAPI}/stations.json",
                 {"type": "currentpredictions", "units": "english"}, "NOAA station list")
    lon0, lat0, lon1, lat1 = bbox
    seen = {}
    for s in j["stations"]:
        if lat0 <= s["lat"] <= lat1 and lon0 <= s["lng"] <= lon1:
            seen.setdefault(s["id"], s)      # several bins per id: keep the first
    return list(seen.values())


def tide_station_info():
    j = get_json(f"{MDAPI}/stations/{TIDE_STATION}.json", None, "tide station")
    s = j["stations"][0]
    return {"id": TIDE_STATION, "name": s.get("name", "Montauk"),
            "lat": s["lat"], "lon": s["lng"]}


def tide_predictions(t0, t1):
    rng = {"station": TIDE_STATION, "datum": "MLLW", "product": "predictions",
           "begin_date": ymdhm(t0), "end_date": ymdhm(t1)}
    six = coops({**rng, "interval": "6"}, "tide 6-min")["predictions"]
    hilo = coops({**rng, "interval": "hilo"}, "tide hilo")["predictions"]
    return {
        "t": [parse_t(r["t"]) for r in six],
        "v": [round(float(r["v"]), 3) for r in six],
        "hilo": [{"t": parse_t(r["t"]), "type": r["type"], "v": round(float(r["v"]), 3)}
                 for r in hilo],
    }


def station_predictions(st, t0, t1, labels):
    base = {"product": "currents_predictions", "station": st["id"],
            "begin_date": ymdhm(t0), "end_date": ymdhm(t1)}
    if st.get("type") == "H" and st.get("currbin"):
        base["bin"] = st["currbin"]
    try:
        curve = coops({**base, "interval": "30"}, f"{st['id']} curve")["current_predictions"]["cp"]
        events = coops({**base, "interval": "MAX_SLACK"}, f"{st['id']} events")["current_predictions"]["cp"]
    except (NoRetry, KeyError, TypeError, ValueError) as e:
        # No predictions for this station/window. Network errors and
        # throttling propagate instead: they would silently drop stations.
        log(f"    skip {st['id']} ({st['name']}): {e}")
        return None
    if not curve:
        return None
    ts = [parse_t(r["Time"]) for r in curve]
    regular = all(b - a == 1800 for a, b in zip(ts, ts[1:]))
    rec = {
        "id": st["id"], "name": st["name"],
        "label": labels.get(st["id"], st["name"]), "featured": st["id"] in labels,
        "type": st.get("type"), "lat": st["lat"], "lon": st["lng"],
        "depth_ft": st.get("depth"),
        "flood_dir": curve[0].get("meanFloodDir") or (events[0].get("meanFloodDir") if events else None),
        "ebb_dir": curve[0].get("meanEbbDir") or (events[0].get("meanEbbDir") if events else None),
        "curve": ({"t0": ts[0], "dt": 1800} if regular else {"t": ts})
        | {"v": [round(float(r["Velocity_Major"]), 2) for r in curve]},
        "events": [{"t": parse_t(r["Time"]), "type": str(r.get("Type", "")).lower(),
                    "v": round(float(r["Velocity_Major"]), 2)} for r in events],
    }
    return rec


# ---------------------------------------------------------------- build ---

def physics_report(g, U, V, t0, stations):
    """Compare model surface current with NOAA predictions at key stations.
    Validates the whole pipeline (mesh read, interpolation, orientation)."""
    hours = t0 + np.arange(U.shape[0]) * 3600
    for st in stations:
        if st["id"] not in ("LIS1001", "LIS1012", "ACT2406") or st["flood_dir"] is None:
            continue
        mu = sample_point(g, U, st["lat"], st["lon"])
        mv = sample_point(g, V, st["lat"], st["lon"])
        if mu is None:
            log(f"    check {st['id']:8} outside model water")
            continue
        th = np.radians(float(st["flood_dir"]))
        model = (mu * np.sin(th) + mv * np.cos(th)) * KNOTS   # + = flood
        c = st["curve"]
        ct = np.asarray(c["t"]) if "t" in c else c["t0"] + 1800 * np.arange(len(c["v"]))
        noaa = np.interp(hours, ct, c["v"])
        corr = float(np.corrcoef(model, noaa)[0, 1])

        def zeros(x):
            s = np.where(np.diff(np.sign(x)) != 0)[0]
            return hours[s] + (hours[s + 1] - hours[s]) * (x[s] / (x[s] - x[s + 1]))

        zm, zn = zeros(model), zeros(noaa)
        lag = np.median([np.min(np.abs(zm - z)) for z in zn]) / 60 if zm.size and zn.size else float("nan")
        log(f"    check {st['id']:8} {st['label'][:24]:24} corr {corr:+.2f} | "
            f"peak model {np.abs(model).max():.1f} kn vs NOAA {np.abs(noaa).max():.1f} kn | "
            f"slack timing |diff| median {lag:.0f} min")


def build_window(ds, times, g, k0, k1, keep, spec, labels, stations_meta, tide_info,
                 depths, force):
    t0, nframes = int(times[k0 + keep[0]]), keep.size
    t1 = int(times[k0 + keep[-1]])
    log(f"  window {iso(t0)} -> {iso(t1)} ({nframes} frames"
        f"{f', {k1 - k0 - nframes} duplicate step(s) dropped' if k1 - k0 > nframes else ''})")

    fields = {}
    if "surface" in depths:
        log("  fetching surface u/v ...")
        u = fetch_elements(ds, "u", k0, k1, g["i0"], g["i1"], layer=0)[keep]
        v = fetch_elements(ds, "v", k0, k1, g["i0"], g["i1"], layer=0)[keep]
        fields["surface"] = (mesh_to_grid(u, g), mesh_to_grid(v, g))
    if "davg" in depths:
        log("  fetching depth-averaged ua/va ...")
        u = fetch_elements(ds, "ua", k0, k1, g["i0"], g["i1"])[keep]
        v = fetch_elements(ds, "va", k0, k1, g["i0"], g["i1"])[keep]
        fields["davg"] = (mesh_to_grid(u, g), mesh_to_grid(v, g))

    log("  NOAA predictions ...")
    pad = 12 * 3600
    tide = tide_predictions(t0 - pad, t1 + pad)
    tide["station"] = {k: tide_info[k] for k in ("id", "name", "lat", "lon")}
    tide["datum"], tide["units"] = "MLLW", "ft"

    # Model water level at the gauge, for spotting surge against predictions.
    lon, lat = g["mesh_lonlat"]
    node = int(np.argmin((lon - tide_info["lon"]) ** 2 + (lat - tide_info["lat"]) ** 2))
    zeta = retry(lambda: np.ma.filled(np.ma.asarray(ds.variables["zeta"][k0:k1, node],
                                                    dtype=np.float64), np.nan), "zeta")[keep]
    # Align to NOAA's MLLW curve by the week's MEDIAN difference. (A MSL-MLLW
    # datum shift left the model ~0.7-1.2 ft low every week: its zero isn't the
    # gauge's MSL.) The median removes that bias but keeps short surge peaks, so
    # the dotted model line only leaves NOAA's line when weather moves water.
    zeta_ft = zeta * 3.28084
    pred = np.interp(t0 + 3600 * np.arange(nframes), tide["t"], tide["v"])
    offset = float(np.nanmedian(pred - zeta_ft))
    tide["model"] = {"t0": t0, "dt": 3600,
                     "v": [round(float(z + offset), 3) if np.isfinite(z) else None
                           for z in zeta_ft]}

    recs = [station_predictions(s, t0 - pad, t1 + pad, labels) for s in stations_meta]
    ref = fields.get("surface") or fields.get("davg")
    stations = []
    for rec in recs:
        if rec is None:
            continue
        rec["in_model"] = sample_point(g, ref[0][:1], rec["lat"], rec["lon"]) is not None
        if rec["in_model"] or rec["featured"]:
            stations.append(rec)
    stations.sort(key=lambda s: (not s["featured"], s["label"]))
    log(f"  stations kept: {len(stations)} "
        f"({sum(s['featured'] for s in stations)} featured, "
        f"{sum(not s['in_model'] for s in stations)} outside model water)")

    # Sanity guards: refuse to publish a broken build.
    problems = []
    if g["mask"].mean() < 0.3:
        problems.append(f"water fraction only {g['mask'].mean():.0%}")
    for name, (U, V) in fields.items():
        spd = np.hypot(U, V)[:, g["mask"].astype(bool)]
        p99 = float(np.percentile(spd, 99))
        log(f"  {name}: p99 speed {p99:.2f} m/s, max {float(spd.max()):.2f} m/s")
        if not np.isfinite(spd).all() or not (0.2 <= p99 <= 3.5):
            problems.append(f"{name} speeds implausible (p99 {p99:.2f} m/s)")
    if len(tide["t"]) < nframes * 5:
        problems.append("tide series too short")
    if sum(1 for s in stations if s["in_model"]) < 10:
        problems.append("fewer than 10 stations inside model water")
    if not any(s["id"] == "LIS1001" for s in stations):
        problems.append("The Race (LIS1001) missing")
    if "surface" in fields:
        physics_report(g, *fields["surface"], t0, stations)
    if problems:
        msg = "; ".join(problems)
        if not force:
            raise RuntimeError(f"sanity check failed: {msg}")
        log(f"  WARNING (forced): {msg}")

    wid = spec["id"]
    blobs = {}
    for name, (U, V) in fields.items():
        raw = C.pack_frames(quantize(U), quantize(V), g["mask"], t0=t0, dt=3600,
                            bbox=g["bbox"], scale=SCALE, depth=name)
        blobs[f"{wid}_{name}.bin.gz"] = gzip.compress(raw, compresslevel=9)
    entry = {
        "id": wid, "order": spec.get("order", 50), "kind": spec.get("kind", ""),
        "title": spec["title"], "subtitle": spec.get("subtitle", ""),
        "lesson": spec.get("lesson", ""), "start": t0, "end": t1,
        "source": spec.get("source", "NECOFS GOM3 hindcast"),
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    meta = {
        "week": entry,
        "grid": {"bbox": list(g["bbox"]), "nx": g["nx"], "ny": g["ny"]},
        "tide": tide, "stations": stations, "default_station": spec.get("default_station"),
        "credits": "Model: NECOFS / FVCOM, UMass Dartmouth (Chen lab). "
                   "Predictions: NOAA CO-OPS.",
    }
    blobs[f"{wid}_meta.json.gz"] = gzip.compress(
        json.dumps(meta, separators=(",", ":")).encode("utf-8"), compresslevel=9)
    for name, b in blobs.items():
        flag = "  <-- over proxy budget!" if len(b) > MAX_SERVED_BYTES else ""
        log(f"  {name}: {len(b) / 1e6:.2f} MB{flag}")
        if len(b) > MAX_SERVED_BYTES and not force:
            raise RuntimeError(f"{name} too large to serve through Render; use a coarser --grid-m")
    entry["files"] = {k: f"{wid}_{k}.bin.gz" for k in fields} | {"meta": f"{wid}_meta.json.gz"}
    entry["bytes"] = {k: len(blobs[f]) for k, f in entry["files"].items()}
    return entry, blobs, [{"id": s["id"], "label": s["label"], "featured": s["featured"]}
                          for s in stations]


def merge_manifest(existing, entries, station_index, default_station):
    weeks = {w["id"]: w for w in (existing or {}).get("weeks", []) if "id" in w}
    for e in entries:
        weeks[e["id"]] = e
    stations = {s["id"]: s for s in (existing or {}).get("stations", [])}
    for s in station_index:
        stations[s["id"]] = s
    return {
        "version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "default_station": default_station,
        "weeks": sorted(weeks.values(), key=lambda w: (w.get("order", 50), w["id"])),
        "stations": sorted(stations.values(), key=lambda s: (not s["featured"], s["label"])),
    }


def upload(base_url, token, fname, blob, attempts=5):
    """POST one file to /api/currents/upload, retrying transient Render 5xx
    (single worker; see tools/sync_cache.py:upload_cache_file)."""
    url = f"{base_url.rstrip('/')}/api/currents/upload"
    headers = {"X-Upload-Token": token, "X-Currents-Filename": fname,
               "Content-Type": "application/octet-stream"}
    backoff = [3, 8, 15, 25]
    for i in range(attempts):
        try:
            r = requests.post(url, data=blob, headers=headers, timeout=120)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (500, 502, 503, 504) and i < attempts - 1:
                log(f"    {fname}: HTTP {r.status_code} - retry in {backoff[i]}s")
                time.sleep(backoff[i])
                continue
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            if i == attempts - 1:
                raise
            log(f"    {fname}: {type(e).__name__} - retry in {backoff[i]}s")
            time.sleep(backoff[i])


def write_local(out_dir, fname, blob):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, fname)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["hindcast", "forecast"], default="hindcast")
    ap.add_argument("--weeks", default="all", help="comma-separated week ids, or 'all'")
    ap.add_argument("--depths", default="surface,davg")
    ap.add_argument("--grid-m", type=float, default=400.0, help="grid spacing (m)")
    ap.add_argument("--out-dir", default=str(C.CURRENTS_DIR))
    ap.add_argument("--upload", action="store_true", help="also upload to the live app")
    ap.add_argument("--base-url", default=os.environ.get("SYNC_BASE_URL", "https://sst.gotoneapp.com"))
    ap.add_argument("--token", default=os.environ.get("CACHE_UPLOAD_TOKEN"))
    ap.add_argument("--upload-delay", type=float, default=2.0)
    ap.add_argument("--force", action="store_true", help="publish despite sanity warnings")
    args = ap.parse_args()

    if args.upload and not args.token:
        log("ERROR: --upload needs CACHE_UPLOAD_TOKEN (or --token)")
        return 2
    depths = [d for d in args.depths.split(",") if d]
    if not depths or any(d not in C.DEPTHS for d in depths):
        log(f"ERROR: --depths must be from {list(C.DEPTHS)}")
        return 2

    with open(WEEKS_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    labels = cfg.get("featured_stations", {})
    default_station = cfg.get("default_station", "LIS1001")

    if args.source == "forecast":
        try:
            ds = open_dataset(NECOFS_FORECAST)
        except Exception as e:  # noqa: BLE001
            log(f"NECOFS forecast unavailable ({type(e).__name__}: {e}) - nothing to do.")
            return 0
    else:
        ds = open_dataset(NECOFS_HINDCAST)
        wanted = None if args.weeks == "all" else set(args.weeks.split(","))
        specs = [w for w in cfg["weeks"] if wanted is None or w["id"] in wanted]
        missing = (wanted or set()) - {w["id"] for w in specs}
        if missing:
            log(f"ERROR: unknown week ids: {sorted(missing)}")
            return 2

    log("Loading model mesh + times ...")
    mesh = retry(lambda: load_mesh(ds), "mesh")
    times = retry(lambda: model_times(ds), "times")
    g = build_geometry(mesh, C.REGION_BBOX, args.grid_m)
    g["mesh_lonlat"] = (mesh[0], mesh[1])
    log(f"  grid {g['nx']} x {g['ny']} @ {args.grid_m:.0f} m, water {g['mask'].mean():.0%}, "
        f"{g['sel'].size} elements (index span {g['i1'] - g['i0']})")
    log("Loading NOAA station metadata ...")
    stations_meta = region_current_stations(C.REGION_BBOX)
    tide_info = tide_station_info()
    log(f"  {len(stations_meta)} current-prediction stations in region; "
        f"tide gauge {tide_info['name']}")

    if args.source == "forecast":
        k0 = max(0, len(times) - 241)                  # at most 10 days of hourly steps
        t0 = int(times[k0])
        day = dt.datetime.fromtimestamp(t0, dt.timezone.utc)
        specs = [{"id": "live", "order": 0, "kind": "live", "title": "Latest forecast",
                  "subtitle": f"from {day:%b %-d, %Y}", "source": "NECOFS GOM3 forecast",
                  "lesson": "The current NECOFS forecast. Compare it with NOAA's predictions "
                            "to see where today's wind is pushing the water off the tide table.",
                  "_window": k0}]

    # A failed week is skipped (its previously published version stays live);
    # the run still exits 1 so the failure shows up in Actions.
    entries, all_blobs, station_index, failed = [], {}, [], []
    for n, spec in enumerate(specs):
        log(f"\n== {spec['id']}: {spec['title']} ==")
        spec = {**spec, "default_station": default_station}
        if "_window" in spec:
            k0 = spec["_window"]
            want = int(np.unique(times[k0:]).size)
        else:
            start = int(dt.datetime.fromisoformat(spec["start"]).timestamp())
            k0 = int(np.searchsorted(times, start))
            if k0 >= len(times) or times[k0] != start:
                log(f"ERROR: model has no step at {iso(start)}")
                failed.append(spec["id"])
                continue
            want = int(spec.get("hours", 168)) + 1
        try:
            k1, keep = window_steps(times, k0, want)
            entry, blobs, sidx = build_window(ds, times, g, k0, k1, keep, spec, labels,
                                              stations_meta, tide_info, depths, args.force)
        except Throttled as e:
            log(f"ERROR building {spec['id']}: {e} even after backing off; "
                "skipping the remaining weeks - re-run later")
            failed += [s["id"] for s in specs[n:]]
            break
        except Exception as e:  # noqa: BLE001
            log(f"ERROR building {spec['id']}: {type(e).__name__}: {e}")
            failed.append(spec["id"])
            continue
        entries.append(entry)
        all_blobs.update(blobs)
        station_index += sidx
    ds.close()
    if not entries:
        log(f"\nNothing built; failed: {', '.join(failed)}")
        return 1

    for fname, blob in all_blobs.items():
        write_local(args.out_dir, fname, blob)
    local_manifest = None
    try:
        with open(os.path.join(args.out_dir, "manifest.json"), encoding="utf-8") as f:
            local_manifest = json.load(f)
    except (OSError, ValueError):
        pass
    write_local(args.out_dir, "manifest.json", json.dumps(
        merge_manifest(local_manifest, entries, station_index, default_station),
        indent=1).encode("utf-8"))
    log(f"\nWrote {len(all_blobs) + 1} files to {args.out_dir}")

    status = 0
    if failed:
        log(f"FAILED (not built): {', '.join(failed)}")
        status = 1
    if not args.upload:
        return status
    log(f"Uploading to {args.base_url} ...")
    try:
        for fname, blob in all_blobs.items():
            res = upload(args.base_url, args.token, fname, blob)
            log(f"  uploaded {fname} ({res.get('size_kb')} KB)")
            time.sleep(args.upload_delay)
        remote = requests.get(f"{args.base_url.rstrip('/')}/api/currents/manifest.json",
                              headers=HEADERS, timeout=30)
        remote_manifest = remote.json() if remote.ok else None
        manifest = merge_manifest(remote_manifest, entries, station_index, default_station)
        upload(args.base_url, args.token, "manifest.json",
               json.dumps(manifest, indent=1).encode("utf-8"))
        log("  uploaded manifest.json (last)")
    except Exception as e:  # noqa: BLE001
        log(f"ERROR uploading: {type(e).__name__}: {e} (manifest not updated)")
        return 1
    return status


if __name__ == "__main__":
    sys.exit(main())
