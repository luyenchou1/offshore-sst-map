#!/usr/bin/env python3
"""Build the tide-stage current atlas behind the /currents "Today" and
"Tide cycle" views.

Around Montauk, The Race and Plum Gut the currents are locked to the tide: the
same pattern recurs every Montauk tide cycle, stronger or weaker with the
tidal range. This tool averages a season of the NECOFS FVCOM hindcast into
that pattern, so the page can show typical currents for any date from
Montauk's tide predictions alone - no live model.

The model the page evaluates (keep in sync with assets/currents.js):
  phase phi(t)  two segments from Montauk high/low predictions:
                HW -> LW maps 0 -> 0.5, LW -> next HW maps 0.5 -> 1.
                Bins b = floor(phi * NB); bin centres (b + 0.5) / NB.
  range R(t)    each half-cycle's range |H - L| placed at its mid-time and
                interpolated linearly between mid-times (no step at HW/LW),
                clamped to the season's observed range.
  field         V = sum_j w_j(R) * C[j, phi], j = 0..2. w are "tent" weights
                interpolating linearly between range centres r0 < r1 < r2
                (10th/50th/90th percentiles of R), continued linearly past the
                ends. C is least-squares fitted per phase bin; between bin
                centres the page interpolates linearly (circularly).

Runs off-Render (locally or in GitHub Actions). Gridded model frames and NOAA
responses are cached under --work-dir, so a rerun downloads nothing.

Usage (repo root, project venv with requirements-tools.txt installed):
  python tools/build_tide_atlas.py                                       # full season
  python tools/build_tide_atlas.py --start 2016-08-01 --end 2016-08-15   # quick dev build
  CACHE_UPLOAD_TOKEN=... python tools/build_tide_atlas.py --upload
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import time

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_currents as B  # noqa: E402  (also puts the repo root on sys.path)
from data import currents as C  # noqa: E402

log = B.log
NB = 24                          # phase bins per tide cycle
RACE = "LIS1001"
PLUM_GUT = "LIS1012"
LIVE = "https://sst.gotoneapp.com"
UTC = dt.timezone.utc
# Report regions (lon0, lon1, lat0, lat1), same boxes as the design analysis.
REGIONS = {
    "Race / Plum Gut / Fishers": (-72.25, -71.85, 41.15, 41.40),
    "Block Island Sound": (-71.85, -71.40, 41.05, 41.40),
    "Gardiners Bay / Peconics": (-72.45, -72.05, 40.90, 41.15),
    "Ocean side, S of Montauk": (-72.10, -71.40, 40.90, 41.04),
}


# ------------------------------------------------------------ NOAA (cached) ---

class Cache:
    def __init__(self, root):
        self.dir = os.path.join(root, "noaa")
        os.makedirs(self.dir, exist_ok=True)

    def get(self, url, params, what):
        key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:24]
        path = os.path.join(self.dir, key + ".json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        j = B.get_json(url, params, what)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(j, f)
        os.replace(path + ".tmp", path)
        return j

    def coops(self, params, what):
        p = {"units": "english", "time_zone": "gmt", "format": "json",
             "application": "gotone_currents", **params}
        return self.get(B.COOPS, p, what)


def fetch_hilo(cache, first, last):
    """Montauk high/low predictions for [first, last] (dates, inclusive),
    one request per calendar year; returns (t, v, isH) with strict H/L
    alternation."""
    rows, d = [], first
    while d <= last:
        e = min(dt.date(d.year, 12, 31), last)
        j = cache.coops({"station": B.TIDE_STATION, "product": "predictions", "datum": "MLLW",
                         "interval": "hilo", "begin_date": d.strftime("%Y%m%d"),
                         "end_date": e.strftime("%Y%m%d") + " 23:59"}, f"Montauk high/low {d.year}")
        rows += j["predictions"]
        d = e + dt.timedelta(days=1)
    rows.sort(key=lambda r: r["t"])
    t, v, h = [], [], []
    fixes = 0
    for r in rows:
        tt, vv, hh = B.parse_t(r["t"]), float(r["v"]), r["type"] == "H"
        if t and tt == t[-1]:
            continue
        if h and hh == h[-1]:                     # two highs (or lows) in a row
            fixes += 1
            if (hh and vv > v[-1]) or (not hh and vv < v[-1]):
                t[-1], v[-1] = tt, vv             # keep the more extreme one
            continue
        t.append(tt), v.append(vv), h.append(hh)
    if fixes:
        log(f"  note: merged {fixes} repeated high/low event(s)")
    t, v, h = np.array(t, np.int64), np.array(v), np.array(h)
    if not np.all(h[1:] != h[:-1]):
        raise RuntimeError("high/low predictions do not alternate")
    return t, v, h


class TideState:
    """Phase and range from alternating high/low events (see module doc)."""

    def __init__(self, t, v, is_high, clamp=None):
        self.t, self.v, self.h = np.asarray(t, np.float64), np.asarray(v), np.asarray(is_high)
        self.mid = (self.t[:-1] + self.t[1:]) / 2
        self.rng = np.abs(self.v[1:] - self.v[:-1])
        self.clamp = clamp

    def __call__(self, times):
        times = np.asarray(times, np.float64)
        k = np.searchsorted(self.t, times, side="right") - 1
        if (k < 0).any() or (k >= self.t.size - 1).any():
            raise ValueError("time outside the high/low coverage")
        frac = (times - self.t[k]) / (self.t[k + 1] - self.t[k])
        phi = np.where(self.h[k], 0.5 * frac, 0.5 + 0.5 * frac)
        rng = np.interp(times, self.mid, self.rng)
        if self.clamp is not None:
            rng = np.clip(rng, *self.clamp)
        return phi, rng


def tent(rng, c):
    """Weights (..., 3) interpolating linearly between centres c0 < c1 < c2,
    continued linearly beyond the ends."""
    rng = np.asarray(rng, np.float64)
    lo = rng <= c[1]
    a = np.where(lo, (rng - c[0]) / (c[1] - c[0]), (rng - c[1]) / (c[2] - c[1]))
    w = np.zeros(rng.shape + (3,))
    w[..., 0] = np.where(lo, 1 - a, 0.0)
    w[..., 1] = np.where(lo, a, 1 - a)
    w[..., 2] = np.where(lo, 0.0, a)
    return w


class Fit:
    """Per-phase-bin least squares  y ~ sum_j w_j(R) C_j  for many cells at
    once. All cells share each bin's 3x3 normal matrix, so this only keeps
    running sums: A = sum w w', B = sum w y, S2 = sum y^2 (per cell)."""

    def __init__(self, ncomp, ncells):
        self.A = np.zeros((NB, 3, 3))
        self.B = np.zeros((NB, ncomp, 3, ncells))
        self.S2 = np.zeros((NB, ncells))
        self.n = np.zeros(NB, np.int64)

    def add(self, phi, w, y):
        """phi (n,), w (n, 3), y (ncomp, n, ncells)."""
        b = np.minimum((np.asarray(phi) * NB).astype(int), NB - 1)
        for bb in np.unique(b):
            s = b == bb
            wb = w[s]
            self.A[bb] += wb.T @ wb
            for c in range(y.shape[0]):
                ys = y[c][s].astype(np.float64)
                self.B[bb, c] += wb.T @ ys
                self.S2[bb] += (ys * ys).sum(0)
            self.n[bb] += int(s.sum())

    def solve(self):
        """Returns coef (NB, ncomp, 3, ncells), SSE per bin and cell, cond (NB,)."""
        coef = np.zeros_like(self.B)
        cond = np.array([np.linalg.cond(a) for a in self.A])
        for bb in range(NB):
            for c in range(self.B.shape[1]):
                # lstsq: a sparse diagnostic subset (day-only, say) may be singular.
                coef[bb, c] = np.linalg.lstsq(self.A[bb], self.B[bb, c], rcond=None)[0]
        sse = self.S2 - np.einsum("bcjn,bcjn->bn", coef, self.B)
        return coef, np.maximum(sse, 0.0), cond


def evaluate(fields, phi, rng, c):
    """The page's interpolation. fields (3, NB, ...) -> values at (phi, rng)."""
    w = tent(rng, c)
    x = np.asarray(phi) * NB - 0.5
    f = np.floor(x)
    a = x - f
    b0 = f.astype(int) % NB
    b1 = (b0 + 1) % NB
    out = 0.0
    for j in range(3):
        wj = w[..., j].reshape(w.shape[:-1] + (1,) * (fields.ndim - 2))
        aj = a.reshape(a.shape + (1,) * (fields.ndim - 2))
        out = out + wj * ((1 - aj) * fields[j, b0] + aj * fields[j, b1])
    return out


# ------------------------------------------------------------- model frames ---

def season_frames(ds, times, g, k_start, k_end, work, depths):
    """Yield (times, {depth: (u, v)}) per model week over water cells, as int8
    (SCALE m/s per unit), caching each week under work/frames."""
    sig = hashlib.sha1(json.dumps([g["bbox"], g["nx"], g["ny"], int(g["sel"].size)]).encode()
                       ).hexdigest()[:10]
    water = np.flatnonzero(g["mask"].ravel())
    folder = os.path.join(work, "frames")
    os.makedirs(folder, exist_ok=True)
    weeks = list(range(k_start, k_end, 168))
    for n, k0 in enumerate(weeks):
        k1 = min(k0 + 168, k_end)
        path = os.path.join(folder, f"{sig}_{k0}_{k1}.npz")
        t0 = time.monotonic()
        if os.path.exists(path):
            z = dict(np.load(path))
            how = "cached"
        else:
            z = {"times": times[k0:k1]}
            for depth in ("surface", "davg"):
                names, layer = (("u", "v"), 0) if depth == "surface" else (("ua", "va"), None)
                for comp, name in zip(("u", "v"), names):
                    el = B.fetch_elements(ds, name, k0, k1, g["i0"], g["i1"], layer=layer, verbose=False)
                    grid = B.mesh_to_grid(el, g).reshape(k1 - k0, -1)[:, water]
                    z[f"{depth}_{comp}"] = B.quantize(grid)
            np.savez_compressed(path + ".tmp.npz", **z)
            os.replace(path + ".tmp.npz", path)
            how = "fetched"
        day = dt.datetime.fromtimestamp(int(z["times"][0]), UTC)
        log(f"  week {n + 1:2d}/{len(weeks)}  {day:%Y-%m-%d}  {k1 - k0} steps  "
            f"({how}, {time.monotonic() - t0:.0f}s)")
        yield z["times"], {d: (z[f"{d}_u"], z[f"{d}_v"]) for d in depths}


# ------------------------------------------------------------------ stations ---

def region_stations(cache, bbox):
    j = cache.get(f"{B.MDAPI}/stations.json", {"type": "currentpredictions", "units": "english"},
                  "NOAA station list")
    lon0, lat0, lon1, lat1 = bbox
    seen = {}
    for s in j["stations"]:
        if lat0 <= s["lat"] <= lat1 and lon0 <= s["lng"] <= lon1:
            seen.setdefault(s["id"], s)
    return list(seen.values())


def crossings(t, v):
    """Linear zero crossings of a signed series: [(time, rising?)]."""
    out = []
    for i in range(len(v) - 1):
        a, b = v[i], v[i + 1]
        if a != 0 and (a < 0) != (b < 0):
            out.append((t[i] + (t[i + 1] - t[i]) * a / (a - b), b > a))
    return out


def extremes(t, v, xs):
    """Peak of each signed segment between consecutive crossings:
    [(time, value)], refined by a parabola through the top three samples."""
    out = []
    bounds = [x for x, _ in xs]
    for a, b in zip(bounds, bounds[1:]):
        idx = np.where((t > a) & (t < b))[0]
        if idx.size < 3:
            continue
        k = idx[np.argmax(np.abs(v[idx]))]
        if 0 < k < len(v) - 1:
            y0, y1, y2 = v[k - 1], v[k], v[k + 1]
            den = y0 - 2 * y1 + y2
            off = 0.5 * (y0 - y2) / den if den else 0.0
            off = max(-1.0, min(1.0, off))
            out.append((t[k] + off * (t[k + 1] - t[k]), y1 - 0.25 * (y0 - y2) * off))
    return out


def nearest_offsets(times, refs):
    """Signed offset (s) from each ref to the nearest time within 5 h."""
    times = np.sort(np.asarray(times, np.float64))
    out = []
    if not times.size:
        return out
    for r in refs:
        k = np.searchsorted(times, r)
        cands = [times[i] for i in (k - 1, k) if 0 <= i < times.size]
        best = min(cands, key=lambda x: abs(x - r))
        if abs(best - r) <= 5 * 3600:
            out.append(best - r)
    return out


def curve_lags(t, v, hl_t, hl_h):
    """Median timing of slack / max current relative to Montauk HW / LW (s)."""
    xs = crossings(t, v)
    ex = extremes(t, v, xs)
    hw, lw = hl_t[hl_h], hl_t[~hl_h]
    pick = (lambda vals: int(np.median(vals)) if len(vals) >= 5 else None)
    return {
        "slack_ebb_vs_hw": pick(nearest_offsets([x for x, rise in xs if not rise], hw)),
        "slack_flood_vs_lw": pick(nearest_offsets([x for x, rise in xs if rise], lw)),
        "max_flood_vs_hw": pick(nearest_offsets([x for x, val in ex if val > 0], hw)),
        "max_ebb_vs_lw": pick(nearest_offsets([x for x, val in ex if val < 0], lw)),
    }


def station_records(cache, g, tide, centres, clamp, labels, begin, end):
    lon0, lat0, lon1, lat1 = C.REGION_BBOX
    ones = np.ones((1, g["ny"], g["nx"]))
    out = []
    for st in region_stations(cache, C.REGION_BBOX):
        params = {"product": "currents_predictions", "station": st["id"], "interval": "30",
                  "begin_date": begin, "end_date": end}
        if st.get("type") == "H" and st.get("currbin"):
            params["bin"] = st["currbin"]
        try:
            rows = cache.coops(params, f"{st['id']} curve")["current_predictions"]["cp"]
        except (B.NoRetry, KeyError, TypeError, ValueError) as e:
            log(f"    skip {st['id']} ({st['name']}): {e}")
            continue
        if len(rows) < 200:
            log(f"    skip {st['id']} ({st['name']}): only {len(rows)} predictions")
            continue
        t = np.array([B.parse_t(r["Time"]) for r in rows], np.float64)
        v = np.array([float(r["Velocity_Major"]) for r in rows])
        phi, rng = tide(t)
        fit = Fit(1, 1)
        fit.add(phi, tent(rng, centres), v[None, :, None])
        coef, _, _ = fit.solve()
        typ = coef[:, 0, :, 0].T                     # (3, NB) knots, + = flood
        in_model = B.sample_point(g, ones, st["lat"], st["lng"]) is not None
        featured = st["id"] in labels
        if not (in_model or featured):
            continue
        out.append({
            "id": st["id"], "name": st["name"], "label": labels.get(st["id"], st["name"]),
            "featured": featured, "type": st.get("type"),
            "bin": params.get("bin"), "lat": st["lat"], "lon": st["lng"],
            "depth_ft": st.get("depth"),
            "flood_dir": rows[0].get("meanFloodDir"), "ebb_dir": rows[0].get("meanEbbDir"),
            "in_model": in_model,
            "typ": [[round(float(x), 2) for x in row] for row in typ],
            "lags": curve_lags(t, v, tide.t, tide.h),
        })
    out.sort(key=lambda s: (not s["featured"], s["label"]))
    return out


# ------------------------------------------------------------------ helpers ---

def b64(a):
    return base64.b64encode(np.asarray(a, np.uint8).tobytes()).decode("ascii")


def nearest_water(g, lat, lon):
    lon0, lat0, lon1, lat1 = g["bbox"]
    jj, ii = np.nonzero(g["mask"])
    lats = lat1 - jj * (lat1 - lat0) / (g["ny"] - 1)
    lons = lon0 + ii * (lon1 - lon0) / (g["nx"] - 1)
    k = int(np.argmin((lats - lat) ** 2 + ((lons - lon) * np.cos(np.radians(lat))) ** 2))
    return int(jj[k]), int(ii[k])


def region_masks(g):
    lon0, lat0, lon1, lat1 = g["bbox"]
    lats = lat1 - np.arange(g["ny"]) * (lat1 - lat0) / (g["ny"] - 1)
    lons = lon0 + np.arange(g["nx"]) * (lon1 - lon0) / (g["nx"] - 1)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    m = g["mask"].astype(bool)
    out = {"All water": m}
    for name, (x0, x1, y0, y1) in REGIONS.items():
        out[name] = m & (lo >= x0) & (lo < x1) & (la >= y0) & (la < y1)
    return out


def load_week(week, depth):
    """A published example week's frames + meta: local cache, else the live site."""
    def read(fname):
        p = os.path.join(str(C.CURRENTS_DIR), fname)
        if os.path.exists(p):
            with open(p, "rb") as f:
                return gzip.decompress(f.read())
        r = requests.get(f"{LIVE}/api/currents/{fname}", headers=B.HEADERS, timeout=120)
        r.raise_for_status()
        raw = r.content
        return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    h, mask, u, v = C.unpack_frames(read(f"{week}_{depth}.bin.gz"))
    meta = json.loads(read(f"{week}_meta.json.gz"))
    return h, u.astype(np.float32) * h["scale"], v.astype(np.float32) * h["scale"], meta


# --------------------------------------------------------------------- main ---

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--atlas-id", default="atlas-2016")
    ap.add_argument("--start", default="2016-05-01", help="season start (UTC date)")
    ap.add_argument("--end", default="2016-11-01", help="season end (UTC date, exclusive)")
    ap.add_argument("--station-month", default="2016-08-01",
                    help="first day of the 30-day NOAA window for station curves")
    ap.add_argument("--years", default="2024-2030", help="Montauk high/lows bundled for date mode")
    ap.add_argument("--grid-m", type=float, default=400.0)
    ap.add_argument("--work-dir", default=os.path.join(B.REPO, "build", "atlas-work"))
    ap.add_argument("--out-dir", default=str(C.CURRENTS_DIR))
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--base-url", default=os.environ.get("SYNC_BASE_URL", LIVE))
    ap.add_argument("--token", default=os.environ.get("CACHE_UPLOAD_TOKEN"))
    ap.add_argument("--force", action="store_true", help="publish despite failed checks")
    args = ap.parse_args()
    if args.upload and not args.token:
        log("ERROR: --upload needs CACHE_UPLOAD_TOKEN (or --token)")
        return 2
    if not C.WEEK_ID_RE.fullmatch(args.atlas_id):
        log(f"ERROR: bad --atlas-id {args.atlas_id!r}")
        return 2

    os.makedirs(args.work_dir, exist_ok=True)
    cache = Cache(args.work_dir)
    with open(B.WEEKS_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    labels = cfg.get("featured_stations", {})
    default_station = cfg.get("default_station", RACE)
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    sm = dt.date.fromisoformat(args.station_month)
    se = sm + dt.timedelta(days=29)                 # NOAA serves <= 31 days per request
    y0, y1 = (int(x) for x in args.years.split("-"))
    depths = ["surface", "davg"]

    # -- tide state ----------------------------------------------------------
    log("Montauk tide predictions ...")
    season_hl = fetch_hilo(cache, min(start, sm) - dt.timedelta(days=2),
                           max(end, se) + dt.timedelta(days=2))
    bundle_hl = fetch_hilo(cache, dt.date(y0 - 1, 12, 30), dt.date(y1 + 1, 1, 2))
    tide_info = cache.get(f"{B.MDAPI}/stations/{B.TIDE_STATION}.json", None, "tide station")
    tide_info = tide_info["stations"][0]

    # -- model season ----------------------------------------------------------
    log("Model mesh + times ...")
    ds = B.open_dataset(B.NECOFS_HINDCAST)
    mesh = B.retry(lambda: B.load_mesh(ds), "mesh")
    times = B.retry(lambda: B.model_times(ds), "times")
    g = B.build_geometry(mesh, C.REGION_BBOX, args.grid_m)
    water = np.flatnonzero(g["mask"].ravel())
    ts0 = int(dt.datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
    ts1 = int(dt.datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp())
    k_start, k_end = int(np.searchsorted(times, ts0)), int(np.searchsorted(times, ts1))
    hours = np.unique(times[k_start:k_end])
    if hours.size < 24 * 14:
        log(f"ERROR: only {hours.size} model hours in {start}..{end}")
        return 1
    tide_raw = TideState(*season_hl)
    _, r_all = tide_raw(hours)
    centres = [float(x) for x in np.percentile(r_all, [10, 50, 90])]
    clamp = (float(r_all.min()), float(r_all.max()))
    tide = TideState(*season_hl, clamp=clamp)
    log(f"  grid {g['nx']} x {g['ny']}, {water.size} water cells; {hours.size} model hours; "
        f"range centres {centres[0]:.2f} / {centres[1]:.2f} / {centres[2]:.2f} ft "
        f"(season {clamp[0]:.2f}-{clamp[1]:.2f})")
    if not (centres[1] - centres[0] > 0.05 and centres[2] - centres[1] > 0.05):
        log("ERROR: tidal range barely varies over the season")
        return 1

    fits = {d: Fit(2, water.size) for d in depths}
    daynight = {"day": Fit(2, water.size), "night": Fit(2, water.size)}
    last = None
    for wt, fields in season_frames(ds, times, g, k_start, k_end, args.work_dir, depths):
        keep = np.r_[True, np.diff(wt) != 0]
        if last is not None and wt[0] == last:
            keep[0] = False
        last = wt[-1]
        wt = wt[keep]
        phi, rng = tide(wt)
        w = tent(rng, centres)
        for d in depths:
            u, v = (a[keep].astype(np.float32) * B.SCALE for a in fields[d])
            fits[d].add(phi, w, np.stack([u, v]))
            if d == "surface":
                hour = (wt // 3600) % 24
                day = (hour >= 11) & (hour < 23)          # ~07-19 EDT
                for key, sel in (("day", day), ("night", ~day)):
                    if sel.any():
                        daynight[key].add(phi[sel], w[sel], np.stack([u[sel], v[sel]]))
    ds.close()

    # -- solve -------------------------------------------------------------------
    nx, ny, cells = g["nx"], g["ny"], g["nx"] * g["ny"]
    problems, report = [], {"atlas_id": args.atlas_id}
    frames, skill, resid, coefs = {}, {}, {}, {}
    for d in depths:
        coef, sse, cond = fits[d].solve()
        coefs[d] = coef
        if fits[d].n.min() < 50 or cond.max() > 1e4:
            problems.append(f"{d}: weak fit (min samples/bin {fits[d].n.min()}, "
                            f"max cond {cond.max():.0f})")
        sst = fits[d].S2.sum(0)
        sk = np.where(sst > 0, 1 - sse.sum(0) / np.maximum(sst, 1e-12), 0.0)
        rms = np.sqrt(sse.sum(0) / max(1, fits[d].n.sum()))
        skill[d] = np.zeros(cells, np.uint8)
        skill[d][water] = np.clip(np.round(sk * 100), 0, 100)
        resid[d] = np.zeros(cells, np.uint8)
        resid[d][water] = np.clip(np.round(rms / 0.005), 0, 255)   # 0.005 m/s units
        q = np.zeros((3 * NB, 2, cells), np.int8)
        big = np.abs(coef).max()
        if big > 3.1:
            log(f"  note: {d} basis peak {big:.2f} m/s clipped to int8 range")
        for j in range(3):
            for bb in range(NB):
                q[j * NB + bb][:, water] = B.quantize(coef[bb, :, j, :])
        frames[d] = q
        log(f"  {d}: samples/bin {fits[d].n.min()}-{fits[d].n.max()}, max cond {cond.max():.0f}, "
            f"peak basis speed {big:.2f} m/s")

    # -- stations + Montauk tide shape ---------------------------------------
    log("NOAA station curves ...")
    stations = station_records(cache, g, tide, centres, clamp, labels,
                               sm.strftime("%Y%m%d"), se.strftime("%Y%m%d") + " 23:30")
    log(f"  {len(stations)} stations ({sum(s['featured'] for s in stations)} featured)")
    six = cache.coops({"station": B.TIDE_STATION, "product": "predictions", "datum": "MLLW",
                       "interval": "6", "begin_date": start.strftime("%Y%m%d"),
                       "end_date": (end - dt.timedelta(days=1)).strftime("%Y%m%d") + " 23:59"},
                      "Montauk 6-min tide")["predictions"]
    st_t = np.array([B.parse_t(r["t"]) for r in six], np.float64)
    st_v = np.array([float(r["v"]) for r in six])
    phi, rng = tide(st_t)
    tfit = Fit(1, 1)
    tfit.add(phi, tent(rng, centres), st_v[None, :, None])
    tcoef, _, _ = tfit.solve()
    tide_typ = tcoef[:, 0, :, 0].T                        # (3, NB) ft MLLW

    # -- presets from the bundled years (2016 sat at a lunar-node peak) ---------
    bundle = TideState(*bundle_hl, clamp=clamp)
    yh = np.arange(int(dt.datetime(y0, 1, 1, tzinfo=UTC).timestamp()),
                   int(dt.datetime(y1 + 1, 1, 1, tzinfo=UTC).timestamp()), 3600)
    _, ry = bundle(yh)
    pr = [float(x) for x in np.percentile(ry, [10, 50, 90])]
    bt, _, bh = bundle_hl
    t_ebb = float(np.mean(np.diff(bt)[bh[:-1]]))           # HW -> LW
    t_flood = float(np.mean(np.diff(bt)[~bh[:-1]]))        # LW -> HW
    presets = [{"key": k, "label": lab, "range_ft": round(r, 2)}
               for k, lab, r in zip(("neap", "average", "spring"),
                                    ("Neap", "Average", "Spring"), pr)]
    log(f"  {y0}-{y1} range presets: neap {pr[0]:.2f}, average {pr[1]:.2f}, spring {pr[2]:.2f} ft "
        f"(2016 centres {centres[0]:.2f}/{centres[1]:.2f}/{centres[2]:.2f}); "
        f"HW->LW {t_ebb / 3600:.2f} h, LW->HW {t_flood / 3600:.2f} h")

    # -- checks ----------------------------------------------------------------
    regions = region_masks(g)
    fq = {d: frames[d].astype(np.float32).reshape(3, NB, 2, ny, nx) * B.SCALE for d in depths}
    by_id = {s["id"]: s for s in stations}
    race = by_id.get(RACE)
    if race is None:
        problems.append("The Race (LIS1001) missing from stations")
    else:
        rj, ri = nearest_water(g, race["lat"], race["lon"])
        th = np.radians(race["flood_dir"] or 0)
        ph = (np.arange(480) + 0.5) / 480
        mid = np.full(ph.shape, pr[1])
        uv = evaluate(fq["surface"][:, :, :, rj, ri], ph, mid, centres)       # (480, 2)
        model_kn = (uv[:, 0] * np.sin(th) + uv[:, 1] * np.cos(th)) * B.KNOTS
        noaa_kn = evaluate(np.array(race["typ"])[:, :, None], ph, mid, centres)[:, 0]
        corr = float(np.corrcoef(model_kn, noaa_kn)[0, 1])
        hrs = np.where(ph < 0.5, ph / 0.5 * t_ebb, -(1 - ph) / 0.5 * t_flood) / 3600

        def slack_hours(series):
            out = {}
            for i in range(len(series)):
                a, b = series[i - 1], series[i]
                if (a < 0) != (b < 0):
                    out["ebb" if a > 0 else "flood"] = hrs[i]
            return out
        sm_, sn_ = slack_hours(model_kn), slack_hours(noaa_kn)
        dts = [abs(sm_[k] - sn_[k]) * 60 for k in sm_ if k in sn_]
        report["race_typical"] = {"corr": round(corr, 3),
                                  "slack_diff_min": [round(x) for x in dts],
                                  "peak_model_kn": round(float(np.abs(model_kn).max()), 2),
                                  "peak_noaa_kn": round(float(np.abs(noaa_kn).max()), 2)}
        log(f"  The Race (average tide): model vs NOAA corr {corr:+.2f}, slack timing off "
            f"{', '.join(f'{x:.0f}' for x in dts)} min, peak {np.abs(model_kn).max():.1f} vs "
            f"{np.abs(noaa_kn).max():.1f} kn")
        if corr < 0.9 or len(dts) < 2 or max(dts) > 45:
            problems.append(f"The Race typical current disagrees with NOAA (corr {corr:.2f}, "
                            f"slack diffs {dts})")

        # 10 days of 2026 at The Race: atlas slack times vs NOAA's.
        ev = cache.coops({"product": "currents_predictions", "station": RACE, "bin": race["bin"] or "13",
                          "interval": "MAX_SLACK", "begin_date": "20261001",
                          "end_date": "20261010 23:59"}, "Race 2026 slacks")["current_predictions"]["cp"]
        noaa_slack = [B.parse_t(r["Time"]) for r in ev if str(r.get("Type", "")).lower() == "slack"]
        tt = np.arange(int(dt.datetime(2026, 9, 30, 18, tzinfo=UTC).timestamp()),
                       int(dt.datetime(2026, 10, 11, 6, tzinfo=UTC).timestamp()), 360)
        p26, r26 = bundle(tt)
        uv = evaluate(fq["surface"][:, :, :, rj, ri], p26, r26, centres)
        along = uv[:, 0] * np.sin(th) + uv[:, 1] * np.cos(th)
        atlas_slack = [x for x, _ in crossings(tt.astype(np.float64), along)]
        off = [abs(x) / 60 for x in nearest_offsets(atlas_slack, noaa_slack)]
        if off:
            report["race_2026_slack_min"] = {"median": round(float(np.median(off))),
                                             "p90": round(float(np.percentile(off, 90))),
                                             "n": len(off)}
            log(f"  The Race, Oct 1-10 2026: atlas slack vs NOAA median {np.median(off):.0f} min, "
                f"90% within {np.percentile(off, 90):.0f} min ({len(off)} slacks)")
        if len(off) < 20 or np.median(off) > 45:
            problems.append(f"2026 slack check failed ({len(off)} matched)")

    box = regions["Race / Plum Gut / Fishers"].ravel()
    race_skill = float(np.median(skill["surface"][box])) / 100
    report["skill_median"] = {d: {r: round(float(np.median(skill[d][m.ravel()])) / 100, 2)
                                  for r, m in regions.items()} for d in depths}
    log("  in-season skill (median share of current variance explained):")
    for r in regions:
        log(f"    {r:28s} surface {report['skill_median']['surface'][r]:.0%}   "
            f"depth-avg {report['skill_median']['davg'][r]:.0%}")
    if race_skill < 0.7:
        problems.append(f"Race-area skill only {race_skill:.0%}")

    # Day vs night composites (sea breeze can alias into tide phase).
    cd = {k: f.solve()[0][:, :, 1, :] for k, f in daynight.items()}   # basis 1 (average)
    call = coefs["surface"][:, :, 1, :]
    report["day_night_diff_pct"] = {}
    for r, m in regions.items():
        sel = m.ravel()[water]
        den = np.sqrt(np.mean(call[:, :, sel] ** 2))
        num = np.sqrt(np.mean((cd["day"][:, :, sel] - cd["night"][:, :, sel]) ** 2))
        report["day_night_diff_pct"][r] = round(float(num / max(den, 1e-9)) * 100)
    log("  day-vs-night composite difference: " + ", ".join(
        f"{r.split(',')[0]} {p}%" for r, p in report["day_night_diff_pct"].items()))

    # Replay the published example weeks (2015 and 2012 are outside the fit season).
    report["replay"] = {}
    for week in ("2015-09-supermoon", "2012-10-sandy", "2016-08-spring", "2016-08-neap"):
        try:
            h, u, v, meta = load_week(week, "surface")
        except Exception as e:  # noqa: BLE001 - report only
            log(f"  replay {week}: unavailable ({type(e).__name__})")
            continue
        hl = meta["tide"]["hilo"]
        wk = TideState([x["t"] for x in hl], [x["v"] for x in hl],
                       [x["type"] == "H" for x in hl], clamp=clamp)
        wt = h["t0"] + h["dt"] * np.arange(h["nframes"])
        try:
            p, r = wk(wt)
        except ValueError:
            log(f"  replay {week}: tide coverage too short")
            continue
        pred = evaluate(fq["surface"], p, r, centres)          # (nf, 2, ny, nx)
        row = {}
        for name, m in regions.items():
            uu, vv = u[:, m], v[:, m]
            pu, pv = pred[:, 0][:, m], pred[:, 1][:, m]
            row[name] = round(float(1 - ((uu - pu) ** 2 + (vv - pv) ** 2).sum()
                                    / ((uu ** 2 + vv ** 2).sum() + 1e-12)), 2)
        report["replay"][week] = row
        log(f"  replay {week:18s} " + "  ".join(f"{k.split(',')[0][:14]} {x:.0%}" for k, x in row.items()))

    # -- outputs -----------------------------------------------------------------
    cycle_s = t_ebb + t_flood
    blobs = {}
    for d in depths:
        q = frames[d]
        raw = C.pack_frames(q[:, 0].reshape(3 * NB, ny, nx), q[:, 1].reshape(3 * NB, ny, nx),
                            g["mask"], t0=0, dt=cycle_s / NB, bbox=g["bbox"],
                            scale=B.SCALE, depth=d)
        blobs[f"{args.atlas_id}_{d}.bin.gz"] = gzip.compress(raw, 9)

    selftest = []
    for sid in (RACE, PLUM_GUT):
        s = by_id.get(sid)
        if not s:
            continue
        j, i = nearest_water(g, s["lat"], s["lon"])
        for when in ("2026-07-04T16:00:00", "2026-10-11T15:30:00", "2026-12-25T03:10:00"):
            t = int(dt.datetime.fromisoformat(when).replace(tzinfo=UTC).timestamp())
            p, r = bundle(np.array([t]))
            for d in depths:
                val = evaluate(fq[d][:, :, :, j, i], p, r, centres)[0]
                selftest.append({"t": t, "j": j, "i": i, "depth": d,
                                 "u": round(float(val[0]), 4), "v": round(float(val[1]), 4)})

    built_at = dt.datetime.now(UTC).isoformat(timespec="seconds")
    meta = {
        "atlas": {
            "id": args.atlas_id, "built_at": built_at,
            "title": "Typical tide cycle",
            "source": "NECOFS FVCOM GOM3 hindcast (UMass Dartmouth), "
                      f"{start:%b %-d %Y} - {end - dt.timedelta(days=1):%b %-d %Y}",
            "bins": NB, "bin_centre": "(b + 0.5) / bins",
            "phase": "two segments: 0 = Montauk high water, 0.5 = low water",
            "frames": "basis * bins + bin",
            # Full precision: the page must evaluate exactly the fitted model.
            "centres_ft": centres,
            "clamp_ft": list(clamp),
            "presets": presets,
            "t_ebb_s": round(t_ebb), "t_flood_s": round(t_flood),
            "skill": {d: b64(skill[d]) for d in depths},
            "resid": {d: b64(resid[d]) for d in depths}, "resid_unit": 0.005,
            "selftest": selftest,
            "dates": {"min": f"{y0}-01-01", "max": f"{y1}-12-31"},
        },
        "tide": {
            "station": B.TIDE_STATION, "name": tide_info.get("name", "Montauk"),
            "lat": tide_info["lat"], "lon": tide_info["lng"], "datum": "MLLW", "units": "ft",
            "typ": [[round(float(x), 3) for x in row] for row in tide_typ],
            "hilo": {"t": [int(x) for x in bt], "v": [round(float(x), 3) for x in bundle_hl[1]],
                     "type": "".join("H" if x else "L" for x in bh)},
        },
        "stations": stations,
        "default_station": default_station,
        "credits": "Currents: NECOFS/FVCOM, UMass Dartmouth (Chen lab). "
                   "Tide and current predictions: NOAA CO-OPS.",
    }
    blobs[f"{args.atlas_id}_meta.json.gz"] = gzip.compress(
        json.dumps(meta, separators=(",", ":")).encode("utf-8"), 9)
    for name, blob in blobs.items():
        mb = len(blob) / 1e6
        log(f"  {name}: {mb:.2f} MB")
        if len(blob) > B.MAX_SERVED_BYTES:
            problems.append(f"{name} is {mb:.1f} MB (limit {B.MAX_SERVED_BYTES / 1e6:.1f})")
        err = C.validate_upload(name, blob)
        if err:
            problems.append(f"{name}: {err}")

    report["problems"] = problems
    entry = {"id": args.atlas_id, "title": "Typical tide cycle", "built_at": built_at,
             "season": f"{start} to {end - dt.timedelta(days=1)}",
             "files": {"surface": f"{args.atlas_id}_surface.bin.gz",
                       "davg": f"{args.atlas_id}_davg.bin.gz",
                       "meta": f"{args.atlas_id}_meta.json.gz"},
             "dates": meta["atlas"]["dates"], "presets": presets}
    index = [{"id": s["id"], "label": s["label"], "featured": s["featured"]} for s in stations]

    def merged(existing):
        m = dict(existing or {})
        m.setdefault("version", 1)
        m.setdefault("weeks", [])
        known = {s["id"]: s for s in m.get("stations", [])}
        for s in index:
            known.setdefault(s["id"], s)
        m["stations"] = sorted(known.values(), key=lambda s: (not s["featured"], s["label"]))
        m.setdefault("default_station", default_station)
        m["atlas"] = entry
        m["generated_at"] = built_at
        return m

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, f"{args.atlas_id}_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    if problems:
        log("\nCHECKS FAILED:\n  " + "\n  ".join(problems))
        if not args.force:
            log("Nothing written or uploaded (use --force to override).")
            return 1
    for name, blob in blobs.items():
        B.write_local(args.out_dir, name, blob)
    local = None
    try:
        with open(os.path.join(args.out_dir, "manifest.json"), encoding="utf-8") as f:
            local = json.load(f)
    except (OSError, ValueError):
        pass
    B.write_local(args.out_dir, "manifest.json", json.dumps(merged(local), indent=1).encode())
    log(f"\nWrote {len(blobs) + 1} files to {args.out_dir}")

    if not args.upload:
        return 0
    log(f"Uploading to {args.base_url} ...")
    try:
        for name, blob in blobs.items():
            res = B.upload(args.base_url, args.token, name, blob)
            log(f"  uploaded {name} ({res.get('size_kb')} KB)")
            time.sleep(2)
        r = requests.get(f"{args.base_url.rstrip('/')}/api/currents/manifest.json",
                         headers=B.HEADERS, timeout=30)
        remote = r.json() if r.ok else None
        B.upload(args.base_url, args.token, "manifest.json",
                 json.dumps(merged(remote), indent=1).encode())
        log("  uploaded manifest.json (last)")
    except Exception as e:  # noqa: BLE001
        log(f"ERROR uploading: {type(e).__name__}: {e} (manifest not updated)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
