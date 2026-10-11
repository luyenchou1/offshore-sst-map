#!/usr/bin/env python3
"""Estimated harbor-inlet currents for /currents -> data/inlets.json.

NECOFS's ~1 km mesh has no water inside the small harbors (Napeague, Three
Mile, Accabonac, Lake Montauk), so the map can't show the strong currents in
their inlets. This estimates them from the tide instead, with the classic
"pond + inlet" model (Keulegan 1967; King 1974; with the channel's inertia as
in Mehta & Ozsoy 1978): the harbor fills and drains through its inlet, the
head difference between bay and harbor driving the water through the channel
against friction.

    (L / g) dv/dt = (eta_bay - eta_harbor) - r v|v|
    r = k_loss / (2 g) + n^2 L / R^(4/3)          (entrance/exit + Manning)
    d(eta_harbor)/dt = Ac v / A_harbor(eta_harbor)

Momentum matters: the water keeps running after the levels even out, which
puts slack at Napeague (670 m channel) ~13 min later than friction alone.

Inputs per harbor:
  * harbor water area at each level: NOAA NCEI 1/9" (~3 m) topobathy DEM,
    flood-filled from a point inside the harbor with the inlet cut off;
  * inlet cross-section, width and channel length: a survey where one exists
    (lidar misses the channel in fast, turbid inlets: at Napeague the DEM gave
    21 m2 where the 2012 survey measured 160 m2);
  * the bay tide outside: NOAA's Montauk (8510560) high/lows with time
    offsets and a range ratio toward the nearest subordinate station.

The page runs the same model in the browser (assets/currents.js) from the
Montauk high/lows it already has, so every view works with no extra data or
live calls. This script writes data/inlets.json with the inputs, a report
(typical slack / peak timing, a Lake Montauk check against NOAA's inside and
entrance predictions) and a "selftest" block that the browser reproduces.

  .venv/bin/python tools/build_inlets.py              # -> data/inlets.json
  .venv/bin/python tools/build_inlets.py --check      # report only
"""

import argparse
import gzip
import io
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "data", "inlets.json")
WORK = os.path.join(REPO, "build", "inlets-work")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GotOneSST/1.0; +https://sst.gotoneapp.com)"}
COOPS = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
DEM_URL = ("https://gis.ngdc.noaa.gov/arcgis/rest/services/DEM_mosaics/DEM_all/"
           "ImageServer/exportImage")
DEM_PX = 1 / 9 / 3600                     # 1/9 arc-second, the CUDEM cell size
LIVE = "https://sst.gotoneapp.com/api/currents/"
ET = ZoneInfo("America/New_York")

TIDE_STATION = "8510560"                  # Montauk, Fort Pond Bay: the page's tide
G = 9.81
FT = 0.3048
KNOTS = 1.943844
DT = 60.0                                 # model step (s); the browser uses the same
SPINUP = 86400.0                          # harbor starts level with the bay a day early
MANNING_N = 0.025                         # sandy inlet channel
K_LOSS = 1.1                              # entrance (~0.1) + exit (~1.0) losses
LEVELS = [round(-1.2 + 0.05 * i, 2) for i in range(41)]     # NAVD88 m

INLETS = [
    {
        "id": "est-napeague",
        "label": "Napeague Harbor inlet",
        "harbor_name": "Napeague Harbor",
        "name": "Napeague Harbor's west inlet, between Hicks Island and Lazy Point",
        "show": True,
        "lat": 41.0121, "lon": -72.0614,          # the throat
        "flood_dir": 105, "ebb_dir": 285,         # direction of travel through it
        "dem_bbox": (-72.100, 40.990, -72.020, 41.045),       # W, S, E, N
        "seed": (41.0100, -72.0500),              # inside the harbor
        "cut": ((41.01421, -72.06162), (41.01063, -72.06091)),  # land to land across it
        "throat": {
            "area_m2": 160.0, "width_m": 55.0, "length_m": 670.0,
            "source": ("2012 bathymetric survey for the Peconic Estuary Program (Napeague "
                       "Harbor Water Circulation Enhancement): minimum cross-section 1,720 sq "
                       "ft; a 2,200 ft long, 180 ft wide channel. The east inlet near Goff "
                       "Point closed around 2007, so all of the harbor's water now moves "
                       "through this one."),
        },
        # Napeague Bay lies 44% of the way (along the shore) from Fort Pond Bay
        # to NOAA's Threemile Harbor entrance station (8511171: high water +65 min,
        # low water +39 min, range x1.18 vs Montauk). NECOFS agrees: its M2 tide
        # off the inlet runs ~+20 min, scaled by how it does at Threemile.
        "outside": {"hw_min": 29, "lw_min": 17, "ratio": 1.08,
                    "source": ("NOAA Montauk 8510560 high/low predictions, shifted 44% of "
                               "the way toward NOAA Threemile Harbor entrance 8511171.")},
        "notes": ("Estimate from the harbor's size, the inlet's 2012 dimensions and the tide "
                  "outside; not a NOAA prediction. Speeds are averages across the inlet: the "
                  "outside (south side) of the channel's bend runs faster. The inlet has "
                  "reportedly widened since 2012, which would make slack come sooner."),
        # NOAA's ACT2471 "Goff Point, 0.4 mile NW of" is out in the bay at the
        # head of the deep channel west of Hicks Island, not in this inlet.
    },
]

# Checks the method where NOAA predicts the tide both inside and outside:
# Lake Montauk's jettied channel (geometry straight from the DEM; the
# dredged channel is deep and wide enough for lidar to see).
VALIDATION = {
    "id": "lake-montauk", "label": "Lake Montauk",
    "dem_bbox": (-71.960, 41.035, -71.905, 41.085),
    "seed": (41.0600, -71.9250),
    "cut": ((41.07779, -71.93863), (41.07835, -71.93498)),
    "throat_from_dem": True, "length_m": 400.0,
    "outside_station": "8510502",    # Montauk Harbor entrance (subordinate)
    "inside_station": "8510448",     # Lake Montauk (harmonic)
}


def log(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------ NOAA --

def cached_json(name, url, params):
    path = os.path.join(WORK, "noaa", name + ".json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    time.sleep(1.0)                          # NOAA's firewall 403s bursts
    r = requests.get(url, params=params, headers=HEADERS, timeout=90)
    r.raise_for_status()
    j = r.json()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(j, f)
    return j


def datums(station):
    j = cached_json(f"datums_{station}", f"{MDAPI}/stations/{station}/datums.json", {})
    d = {x["name"]: x["value"] for x in j.get("datums") or []}
    if not all(k in d for k in ("MLLW", "MSL", "NAVD88")):
        raise RuntimeError(f"{station}: datums incomplete")
    return {"mllw_navd88_m": round((d["MLLW"] - d["NAVD88"]) * FT, 4),
            "msl_navd88_m": round((d["MSL"] - d["NAVD88"]) * FT, 4),
            "mean_ft": round(d["MSL"] - d["MLLW"], 3), "epoch": j.get("epoch")}


def sea_level_rise(epoch):
    """Rise since the tidal epoch's midpoint (NOAA's predictions don't include it)."""
    rate = 3.4                               # mm/yr, NOAA's trend for Montauk
    try:
        j = cached_json("slr_8510560",
                        "https://api.tidesandcurrents.noaa.gov/dpapi/prod/webapi/product/sealvltrends.json",
                        {"station": TIDE_STATION})
        s = j["SeaLvlTrends"][0]
        per_mm = {"inches/decade": 2.54, "mm/yr": 1.0, "feet/century": 3.048}[s["trendUnits"]]
        rate = round(float(s["trend"]) * per_mm, 2)
    except Exception as e:                   # trend service is optional
        log(f"  sea-level trend unavailable ({type(e).__name__}); using {rate} mm/yr")
    y0, y1 = (int(x) for x in (epoch or "1983-2001").split("-"))
    years = datetime.now(timezone.utc).year - (y0 + y1) / 2
    return round(rate * years / 1000, 3), rate


def hilo(station, begin, end):
    """High/low predictions, GMT, ft above MLLW -> list of (epoch, ft, 'H'|'L')."""
    j = cached_json(f"hilo_{station}_{begin}_{end}", COOPS, {
        "product": "predictions", "station": station, "begin_date": begin, "end_date": end,
        "time_zone": "gmt", "units": "english", "interval": "hilo", "datum": "MLLW",
        "format": "json", "application": "gotone_inlets"})
    if "predictions" not in j:
        raise RuntimeError(f"{station}: {j.get('error')}")
    out = []
    for p in j["predictions"]:
        t = datetime.strptime(p["t"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        out.append((t.timestamp(), float(p["v"]), "H" if p["type"].startswith("H") else "L"))
    return out


def atlas_hilo():
    """The Montauk high/lows the live page bundles (atlas meta), for the
    selftest; a local copy only if the site is unreachable (an older local
    build can differ in the last decimal, which the selftest would flag)."""
    try:
        man = requests.get(LIVE + "manifest.json", headers=HEADERS, timeout=60).json()
        raw = requests.get(LIVE + man["atlas"]["files"]["meta"], headers=HEADERS, timeout=120).content
        meta = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    except (requests.RequestException, KeyError, ValueError) as e:
        log(f"  live atlas unavailable ({type(e).__name__}); using the local copy")
        local = os.path.join(REPO, "cache", "currents")
        with open(os.path.join(local, "manifest.json")) as f:
            name = json.load(f)["atlas"]["files"]["meta"]
        with gzip.open(os.path.join(local, name)) as f:
            meta = json.load(f)
    h = meta["tide"]["hilo"]
    return [(float(t), float(v), ty) for t, v, ty in zip(h["t"], h["v"], h["type"])]


# ------------------------------------------------------------------- DEM --

def dem(name, bbox):
    """NCEI's best-available DEM mosaic (CUDEM 1/9" here), NAVD88 metres."""
    path = os.path.join(WORK, f"dem_{name}.npz")
    w, s, e, n = bbox
    if not os.path.exists(path):
        from PIL import Image
        W, H = round((e - w) / DEM_PX), round((n - s) / DEM_PX)
        log(f"  downloading DEM {name} ({W} x {H})")
        r = requests.get(DEM_URL, params={
            "bbox": f"{w},{s},{e},{n}", "bboxSR": 4326, "imageSR": 4326, "size": f"{W},{H}",
            "format": "tiff", "pixelType": "F32", "noData": -99999,
            "interpolation": "RSP_NearestNeighbor", "f": "image"}, headers=HEADERS, timeout=600)
        r.raise_for_status()
        z = np.array(Image.open(io.BytesIO(r.content)), dtype=np.float32)
        if (z < -9000).any():
            raise RuntimeError(f"DEM {name} has gaps")
        os.makedirs(WORK, exist_ok=True)
        np.savez_compressed(path, z=z)
    z = np.load(path)["z"].astype(np.float64)
    ny, nx = z.shape
    lat0 = (s + n) / 2
    return {"z": z, "w": w, "n": n, "dlon": (e - w) / nx, "dlat": (n - s) / ny,
            "mx": (e - w) / nx * 111320 * math.cos(math.radians(lat0)),
            "my": (n - s) / ny * 110950}


def ij(g, lat, lon):
    return int((g["n"] - lat) / g["dlat"]), int((lon - g["w"]) / g["dlon"])


def harbor_area(g, seed, cut):
    """Water area (m2) connected to `seed` at each level, the inlet cut off."""
    from scipy import ndimage
    z = g["z"]
    block = np.zeros(z.shape, bool)
    p, q = ij(g, *cut[0]), ij(g, *cut[1])
    steps = int(math.hypot(q[0] - p[0], q[1] - p[1]) * 3) + 4
    for f in np.linspace(0, 1, steps):
        i = int(round(p[0] + (q[0] - p[0]) * f))
        j = int(round(p[1] + (q[1] - p[1]) * f))
        block[i - 2:i + 3, j - 2:j + 3] = True
    sd = ij(g, *seed)
    out = []
    for eta in LEVELS:
        lab, _ = ndimage.label((z < eta) & ~block)
        if lab[sd] == 0:
            out.append(0.0)
            continue
        comp = lab == lab[sd]
        if comp[0].any() or comp[-1].any() or comp[:, 0].any() or comp[:, -1].any():
            # Spills over low land at the DEM edge: far above any normal tide.
            out += [out[-1]] * (len(LEVELS) - len(out))
            break
        out.append(round(float(comp.sum() * g["mx"] * g["my"]), -2))
    first = next((a for a in out if a > 0), None)
    if first is None:
        raise RuntimeError("the harbor seed point is never under water")
    return [a if a > 0 else first for a in out]      # dry seed at the lowest levels


def dem_section(g, cut, eta):
    """Cross-section (m2) and wet width (m) along the cut line at level eta."""
    from scipy.ndimage import map_coordinates
    p, q = ij(g, *cut[0]), ij(g, *cut[1])
    dist = math.hypot((q[0] - p[0]) * g["my"], (q[1] - p[1]) * g["mx"])
    m = max(int(dist), 2)
    zz = map_coordinates(g["z"], [np.linspace(p[0], q[0], m), np.linspace(p[1], q[1], m)], order=1)
    d = np.clip(eta - zz, 0, None)
    dx = dist / (m - 1)
    return float(np.trapezoid(d, dx=dx)), float((d > 0).sum() * dx)


# ----------------------------------------------------------------- model --
# assets/currents.js mirrors everything below (inletRun); keep them in step.

def shifted(events, out, mean_ft):
    """Bay-side high/lows: Montauk's, shifted in time and scaled about MSL."""
    T, V = [], []
    for t, v, ty in events:
        T.append(t + (out["hw_min"] if ty == "H" else out["lw_min"]) * 60.0)
        V.append(mean_ft + (v - mean_ft) * out["ratio"])
    return T, V


def cos_tide(T, V, t):
    """NOAA's cosine rule between consecutive high/lows (clamped at the ends)."""
    n = len(T)
    if t <= T[0]:
        return V[0]
    if t >= T[n - 1]:
        return V[n - 1]
    lo, hi = 0, n
    while lo < hi:                           # largest k with T[k] <= t
        mid = (lo + hi) >> 1
        if T[mid] <= t:
            lo = mid + 1
        else:
            hi = mid
    k = min(lo - 1, n - 2)
    f = (t - T[k]) / (T[k + 1] - T[k])
    return V[k] + (V[k + 1] - V[k]) * (1 - math.cos(math.pi * f)) / 2


def interp_clamped(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    k = int((x - xs[0]) / (xs[1] - xs[0]))
    k = min(max(k, 0), len(xs) - 2)
    return ys[k] + (ys[k + 1] - ys[k]) * (x - xs[k]) / (xs[k + 1] - xs[k])


def inlet_run(inlet, datum, events, t_start, t_end):
    """Harbor level and inlet current from t_start to t_end every DT seconds.

    Backward Euler for both the channel velocity and the harbor level,
    solved exactly each step (a quadratic in v), so the stiff friction law
    stays stable at any step. Geometry and friction come from the previous
    step's levels. Returns times, current (m/s, + = flood, into the harbor)
    and harbor level (ft above Montauk MLLW).
    """
    th, H = inlet["throat"], inlet["harbor"]
    base = datum["mllw_navd88_m"] + datum["slr_m"]          # MLLW ft -> NAVD88 m
    msl = datum["msl_navd88_m"] + datum["slr_m"]
    T, V = shifted(events, inlet["outside"], datum["mean_ft"])
    A0, W, L = th["area_m2"], th["width_m"], th["length_m"]
    n2L, kl = th["manning_n"] ** 2 * L, th["k_loss"] / (2 * G)
    lv, ar = H["levels_m"], H["area_m2"]
    a = DT * G / L                                          # inertia of the channel
    steps = int((t_end - t_start) // DT) + 1
    ts = [t_start + i * DT for i in range(steps)]
    eo = cos_tide(T, V, ts[0]) * FT + base
    eb, v = eo, 0.0
    vel, lvl = [0.0], [(eb - base) / FT]
    for i in range(1, steps):
        et = 0.5 * (eo + eb)
        Ac = max(A0 + W * (et - msl), 0.05 * A0)
        r = kl + n2L / (Ac / W) ** (4 / 3)
        Ab = interp_clamped(eb, lv, ar)
        eo = cos_tide(T, V, ts[i]) * FT + base
        # v' (1 + c) + b v'|v'| = v + a (eo' - eb), eb' = eb + DT Ac v' / Ab
        b, c = a * r, a * DT * Ac / Ab
        rhs = v + a * (eo - eb)
        v = math.copysign((-(1 + c) + math.sqrt((1 + c) ** 2 + 4 * b * abs(rhs))) / (2 * b), rhs)
        eb += DT * Ac * v / Ab
        vel.append(v)
        lvl.append((eb - base) / FT)
    return ts, vel, lvl


# ---------------------------------------------------------------- report --

def zero_crossings(ts, vs):
    out = []
    for k in range(len(vs) - 1):
        a, b = vs[k], vs[k + 1]
        if (a < 0) != (b < 0) and a != b:
            out.append((ts[k] + (ts[k + 1] - ts[k]) * a / (a - b), 1 if b > 0 else -1))
    return out


def nearest(t, refs, within=5 * 3600):
    best = min(refs, key=lambda r: abs(t - r))
    return (t - best) if abs(t - best) <= within else None


def summarize(inlet, datum, events, t0, t1):
    """Typical timing vs Montauk high/low water over [t0, t1]."""
    ts, v, hb = inlet_run(inlet, datum, events, t0 - SPINUP, t1)
    k0 = int(SPINUP // DT)
    ts, v, hb = ts[k0:], np.array(v[k0:]), np.array(hb[k0:])
    HW = [t for t, _, ty in events if ty == "H" and t0 <= t <= t1]
    LW = [t for t, _, ty in events if ty == "L" and t0 <= t <= t1]
    zc = zero_crossings(ts, v)
    to_ebb = [nearest(t, HW) for t, sg in zc if sg < 0]
    to_flood = [nearest(t, LW) for t, sg in zc if sg > 0]
    peaks_f = [ts[i] for i in range(1, len(v) - 1) if v[i] > 0.1 and v[i] >= v[i - 1] and v[i] > v[i + 1]]
    peaks_e = [ts[i] for i in range(1, len(v) - 1) if v[i] < -0.1 and v[i] <= v[i - 1] and v[i] < v[i + 1]]
    # harbor high/low vs Montauk (the lag the inlet adds, plus the bay's own)
    hH = [ts[i] for i in range(1, len(hb) - 1) if hb[i] >= hb[i - 1] and hb[i] > hb[i + 1]]
    hL = [ts[i] for i in range(1, len(hb) - 1) if hb[i] <= hb[i - 1] and hb[i] < hb[i + 1]]
    T, V = shifted(events, inlet["outside"], datum["mean_ft"])
    bay = np.array([cos_tide(T, V, t) for t in ts])

    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(float(np.median(xs)) / 60) if xs else None

    return {
        "slack_after_hw_min": med(to_ebb), "slack_after_lw_min": med(to_flood),
        "max_flood_vs_hw_min": med([nearest(t, HW) for t in peaks_f]),
        "max_ebb_vs_lw_min": med([nearest(t, LW) for t in peaks_e]),
        "harbor_hw_after_montauk_min": med([nearest(t, HW) for t in hH]),
        "harbor_lw_after_montauk_min": med([nearest(t, LW) for t in hL]),
        "harbor_range_vs_bay": round(float((np.percentile(hb, 99) - np.percentile(hb, 1))
                                           / (np.percentile(bay, 99) - np.percentile(bay, 1))), 3),
        "peak_flood_kn": round(float(np.percentile(v, 99.5)) * KNOTS, 2),
        "peak_ebb_kn": round(float(-np.percentile(v, 0.5)) * KNOTS, 2),
    }


def lake_montauk_check(datum, begin, end):
    """Model Lake Montauk's tide behind its inlet; compare with NOAA's own
    inside (8510448) vs entrance (8510502) predictions."""
    v = VALIDATION
    g = dem(v["id"], v["dem_bbox"])
    area = harbor_area(g, v["seed"], v["cut"])
    msl = datum["msl_navd88_m"]
    A0, Wd = dem_section(g, v["cut"], msl)
    mont, ent, ins = hilo(TIDE_STATION, begin, end), hilo(v["outside_station"], begin, end), \
        hilo(v["inside_station"], begin, end)

    def lags(ev, ref, kind):
        r = [t for t, _, ty in ref if ty == kind]
        return [nearest(t, r, 3 * 3600) for t, _, ty in ev if ty == kind]

    def mrange(ev):
        return float(np.mean([abs(ev[i + 1][1] - ev[i][1]) for i in range(len(ev) - 1)]))

    eH = [x for x in lags(ent, mont, "H") if x is not None]
    eL = [x for x in lags(ent, mont, "L") if x is not None]
    out = {"hw_min": round(float(np.median(eH)) / 60), "lw_min": round(float(np.median(eL)) / 60),
           "ratio": round(mrange(ent) / mrange(mont), 3)}
    inlet = {"throat": {"area_m2": A0, "width_m": Wd, "length_m": v["length_m"],
                        "manning_n": MANNING_N, "k_loss": K_LOSS},
             "harbor": {"levels_m": LEVELS, "area_m2": area}, "outside": out}
    t0, t1 = mont[2][0] + SPINUP, mont[-3][0]
    s = summarize(inlet, datum, mont, t0, t1)
    noaa_h = float(np.median([x for x in lags(ins, ent, "H") if x is not None])) / 60
    noaa_l = float(np.median([x for x in lags(ins, ent, "L") if x is not None])) / 60
    return {
        "throat_m2": round(A0), "width_m": round(Wd), "harbor_km2": round(area[LEVELS.index(-0.1)] / 1e6, 2),
        "model_lag_hw_min": s["harbor_hw_after_montauk_min"] - out["hw_min"],
        "model_lag_lw_min": s["harbor_lw_after_montauk_min"] - out["lw_min"],
        "noaa_lag_hw_min": round(noaa_h, 1), "noaa_lag_lw_min": round(noaa_l, 1),
        "model_range_ratio": s["harbor_range_vs_bay"],
        "noaa_range_ratio": round(mrange(ins) / mrange(ent), 2),
        "model_peak_kn": [s["peak_flood_kn"], s["peak_ebb_kn"]],
    }


def selftest(inlet, datum, events, day):
    """Values the browser must reproduce for the date view of `day` (ET)."""
    d = datetime.strptime(day, "%Y-%m-%d")
    t0 = datetime(d.year, d.month, d.day, tzinfo=ET).timestamp()
    t1 = (datetime(d.year, d.month, d.day, tzinfo=ET) + timedelta(days=1)).timestamp()
    ts, v, hb = inlet_run(inlet, datum, events, t0 - SPINUP, t1 + 2 * 3600)
    pts = []
    for h in range(0, 25, 2):
        k = int(round((t0 + h * 3600 - ts[0]) / DT))
        pts.append({"t": ts[k], "kn": round(v[k] * KNOTS, 4), "harbor_ft": round(hb[k], 4)})
    return {"day": day, "points": pts}


# ------------------------------------------------------------------ main --

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="print the report; don't write")
    ap.add_argument("--report-from", default="20260901")
    ap.add_argument("--report-to", default="20261031")
    ap.add_argument("--selftest-day", default="2026-10-11")
    a = ap.parse_args()

    datum = datums(TIDE_STATION)
    datum["slr_m"], rate = sea_level_rise(datum.get("epoch"))
    datum["slr_note"] = (f"NOAA's predictions are referenced to the {datum.get('epoch')} epoch; "
                         f"Montauk's sea level has risen ~{datum['slr_m']} m since "
                         f"({rate} mm/yr).")
    log(f"Montauk datums (NAVD88): MLLW {datum['mllw_navd88_m']} m, MSL {datum['msl_navd88_m']} m; "
        f"sea level +{datum['slr_m']} m since the epoch")
    mont = hilo(TIDE_STATION, a.report_from, a.report_to)
    bundled = atlas_hilo()
    out = []
    for spec in INLETS:
        log(f"\n{spec['label']}")
        g = dem(spec["id"], spec["dem_bbox"])
        area = harbor_area(g, spec["seed"], spec["cut"])
        k = LEVELS.index(-0.1)
        dem_a, dem_w = dem_section(g, spec["cut"], datum["msl_navd88_m"])
        log(f"  harbor {area[k] / 1e6:.2f} km2 at MSL ({min(a_ for a_ in area if a_) / 1e6:.2f}"
            f"-{area[-1] / 1e6:.2f} over {LEVELS[0]}..{LEVELS[-1]} m NAVD88)")
        log(f"  inlet: {spec['throat']['area_m2']:.0f} m2, {spec['throat']['width_m']:.0f} m wide "
            f"(survey); the DEM shows {dem_a:.0f} m2, {dem_w:.0f} m wide")
        inlet = {k2: spec[k2] for k2 in ("id", "label", "harbor_name", "name", "lat", "lon",
                                          "flood_dir", "ebb_dir", "outside", "notes")}
        inlet["throat"] = {**spec["throat"], "manning_n": MANNING_N, "k_loss": K_LOSS,
                           "dem_area_m2": round(dem_a, 1)}
        inlet["harbor"] = {"levels_m": LEVELS, "area_m2": area,
                           "source": "NOAA NCEI 1/9 arc-second topobathy DEM (CUDEM), NAVD88"}
        inlet["confidence"] = "Rough"
        t0, t1 = mont[2][0] + SPINUP, mont[-3][0]
        inlet["report"] = summarize(inlet, datum, mont, t0, t1)
        log("  " + json.dumps(inlet["report"]))
        inlet["selftest"] = selftest(inlet, datum, bundled, a.selftest_day)
        if spec.get("show", True):
            out.append(inlet)

    log("\nLake Montauk check (model vs NOAA inside/entrance predictions):")
    lm = lake_montauk_check(datum, a.report_from, a.report_to)
    log("  " + json.dumps(lm))
    if abs(lm["model_lag_hw_min"] - lm["noaa_lag_hw_min"]) > 20 or \
            abs(lm["model_lag_lw_min"] - lm["noaa_lag_lw_min"]) > 20:
        log("  WARNING: the method's harbor lag disagrees with NOAA by > 20 min at Lake Montauk")

    doc = {
        "version": 1,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": ("Pond-and-inlet model (Keulegan 1967; King 1974) driven by NOAA's Montauk "
                   "high/low predictions: the harbor fills and drains through its inlet, the "
                   "flow limited by the inlet's size and friction. Built by "
                   "tools/build_inlets.py; run in the browser by assets/currents.js."),
        "constants": {"g": G, "dt_s": DT, "spinup_s": SPINUP},
        "datum": datum,
        "validation": {"lake_montauk": lm},
        "inlets": out,
    }
    if a.check:
        return 0
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    log(f"\nwrote {os.path.relpath(OUT, REPO)} ({os.path.getsize(OUT) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
