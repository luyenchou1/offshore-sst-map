"""ERDDAP access for the SST map: known datasets, one hard time budget, OISST fallback.

Sources come from config.json ("erddap"): MUR (jplMURSST41, 1 km) first; OISST v2.1 (25 km)
only when MUR sends nothing in time. There is no dataset search at request time (the search
pages 403/404 from cloud IPs and cost seconds per server).

Every network call runs against one Deadline, so get_sst_multiday() returns within its
budget: the days it got (a partial week says which days are missing), or an SSTUnavailable
that says why. Per-request connect/read timeouts are capped by the time left, 429/5xx are
retried after a short backoff while there is time, and days are fetched a few at a time
(newest first) so an overloaded server isn't hit with a whole week at once.

Requests go through data/outbound (incident 2026-10-16): the deadline also holds while a body
arrives slowly, at most NOAA_MAX_CALLS requests are in flight, and after a connect failure
(unreachable, refused, no DNS, connect timeout) NOAA isn't asked again for a minute: every
fetch in that minute fails at once with kind "unreachable" (reason "network").
"""

import io
import logging
import os
import random
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests  # noqa: F401 - tests replace requests.get (data/outbound looks it up per call)
import xarray as xr

from data import outbound
from data.convert import to_fahrenheit
from data.outbound import Deadline  # noqa: F401 - re-exported: erddap.Deadline

logger = logging.getLogger(__name__)

# NOAA CoastWatch sits behind a WAF that returns HTTP 403 to the default
# `python-requests/<ver>` User-Agent, most aggressively for datacenter/cloud IPs.
# Any non-`python-requests` UA clears the WAF; we send a descriptive one.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GotOneSST/1.0; +https://sst.gotoneapp.com)"
}

MUR_START = date(2002, 6, 1)

# Defaults when config.json has no "erddap" block (keep in sync with config.json).
_PFEG = "https://coastwatch.pfeg.noaa.gov/erddap"
DEFAULT_SOURCES = {
    "budget_s": 22,
    "concurrency": 3,
    "mur": [{"server": _PFEG, "dataset_id": "jplMURSST41", "var": "analysed_sst", "hour": 9}],
    "oisst": [
        {"server": _PFEG, "dataset_id": "ncdcOisst21Agg_LonPM180", "var": "sst", "zlev": 0.0,
         "hour": 12, "first": "1981-09-01"},
        {"server": _PFEG, "dataset_id": "ncdcOisst21NrtAgg_LonPM180", "var": "sst", "zlev": 0.0,
         "hour": 12, "first": "2020-05-04"},
    ],
}
TITLES = {
    "MUR": "Multi-scale Ultra-high Resolution (MUR) SST Analysis v4.1, NASA JPL (1 km)",
    "OISST": "NOAA Daily Optimum Interpolation SST (OISST) v2.1, NCEI (25 km)",
}

CONNECT_TIMEOUT = 3.0   # s; connection set-up, capped by the time left (from GitHub/home it takes < 0.2 s)
READ_TIMEOUT = 8.0      # s; longest wait for the next bytes of a response
RETRY_BACKOFF = (1.0, 2.5)  # s before the 2nd and 3rd try of a 429/5xx/network error
OISST_RESERVE_S = 5.0   # s of the budget kept back for the OISST fallback
LATEST_TTL_S = 3600     # the newest-day lookup is cached for an hour...
LATEST_FAIL_TTL_S = 300  # ...and a failed lookup isn't retried for 5 minutes

# The kinds of failure a request can end in (FetchError.kind). unreachable: no connection could be made, or NOAA
# failed that way in the last minute and wasn't asked (outbound's breaker); never retried within a fetch.
BUSY, TIMEOUT, NETWORK, BLOCKED, DEAD, MISSING, BAD, UNREACHABLE = (
    "busy", "timeout", "network", "blocked", "dead", "missing", "bad", "unreachable")
_RETRYABLE = (BUSY, NETWORK, TIMEOUT)
_OUTBOUND_KIND = {outbound.DOWN: UNREACHABLE, outbound.UNREACHABLE: UNREACHABLE, outbound.BUSY: BUSY,
                  outbound.TIMEOUT: TIMEOUT, outbound.NETWORK: NETWORK, outbound.TOO_BIG: BAD}


class FetchError(Exception):
    """One request failed. kind: busy (429/5xx, or no free connection), timeout, network, unreachable (no
    connection), blocked (403), dead (unknown dataset id), missing (the date isn't in the dataset), bad."""

    def __init__(self, kind: str, detail: str = "", status: Optional[int] = None):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail
        self.status = status


class SSTUnavailable(RuntimeError):
    """No SST could be had within the budget.

    reason: "busy" (NOAA overloaded or too slow), "blocked" (403), "network", "no_data" (the
    dates aren't in any dataset, e.g. before June 2002 or in the future), "dead" (dataset gone).
    """

    def __init__(self, message: str, reason: str, requested_end=None, errors=None,
                 elapsed_s: float = 0.0):
        super().__init__(message)
        self.reason = reason
        self.requested_end = requested_end
        self.errors = errors or {}
        self.elapsed_s = elapsed_s


def sources(config: Optional[Dict]) -> Dict:
    src = dict(DEFAULT_SOURCES)
    src.update((config or {}).get("erddap") or {})
    return src


def aoi_bbox(config: Dict) -> Tuple[float, float, float, float]:
    aoi = [tuple(pt) for pt in config["aoi_polygon_lonlat"]]
    lons = [p[0] for p in aoi]
    lats = [p[1] for p in aoi]
    return (min(lons), min(lats), max(lons), max(lats))


# ---------------- HTTP ----------------

def _classify(status: int, body: str) -> FetchError:
    b = (body or "").lower()
    if status in (429, 500, 502, 503, 504):
        return FetchError(BUSY, f"HTTP {status}", status)
    if status in (401, 403):
        return FetchError(BLOCKED, f"HTTP {status}", status)
    if status in (400, 404):
        if "unknown datasetid" in b:
            return FetchError(DEAD, f"HTTP {status} unknown dataset", status)
        if ("axis maximum" in b or "axis minimum" in b or "no matching results" in b
                or "outside of the" in b):
            return FetchError(MISSING, f"HTTP {status} date not in dataset", status)
        return FetchError(DEAD, f"HTTP {status}", status)
    return FetchError(NETWORK, f"HTTP {status}", status)


def noaa(url_or_server: str) -> outbound.Upstream:
    """The outbound Upstream (threads, breaker) for a NOAA ERDDAP server or URL."""
    return outbound.for_url(url_or_server, "noaa")


def noaa_down(config: Optional[Dict] = None) -> bool:
    """True while the MUR server failed to connect in the last minute (requests to it fail at once)."""
    return noaa(sources(config)["mur"][0]["server"]).is_down()


def http_get(url: str, deadline: Deadline, read_timeout: float = READ_TIMEOUT) -> bytes:
    """GET url and return the (decoded) body, or raise FetchError. Returns by the deadline.

    The call runs on the NOAA upstream's threads (data/outbound): the caller waits only until the
    deadline, the body is read in pieces against it (a server that drips bytes can't hold anything
    past it), and a server that couldn't be reached in the last minute isn't asked (UNREACHABLE).
    """
    if deadline.left() < 0.3:
        raise FetchError(TIMEOUT, "no time left")
    try:
        rep = noaa(url).request("GET", url, deadline, connect_s=CONNECT_TIMEOUT, read_s=read_timeout,
                                headers=_HEADERS)
    except outbound.OutboundError as e:
        raise FetchError(_OUTBOUND_KIND.get(e.kind, NETWORK), e.detail) from None
    if rep.status != 200:
        raise _classify(rep.status, rep.text(2000))
    return rep.body


def with_retry(call, deadline: Deadline, label: str = ""):
    """Run call(); retry busy/network/timeout failures after a short backoff while time allows."""
    for attempt in range(len(RETRY_BACKOFF) + 1):
        try:
            return call()
        except FetchError as e:
            if e.kind not in _RETRYABLE or attempt == len(RETRY_BACKOFF):
                raise
            pause = RETRY_BACKOFF[attempt] * (0.8 + 0.4 * random.random())
            if deadline.left() < pause + 2.0:
                raise
            logger.info("ERDDAP %s: %s, retrying in %.1fs", label, e, pause)
            time.sleep(pause)
    raise FetchError(TIMEOUT, "retries exhausted")  # not reached


# ---------------- Newest day (time[last]) ----------------

_latest = {}  # (server, dataset_id) -> {"checked": monotonic, "date": date|None, "good": date|None}
_latest_lock = threading.Lock()
_latest_pending = {}  # key -> threading.Event of the lookup in flight (one lookup per dataset at a time)


def _lookup_latest(src: Dict, deadline: Deadline) -> date:
    url = f"{src['server']}/griddap/{src['dataset_id']}.csv?time%5Blast%5D"
    body = http_get(url, deadline, read_timeout=4.0).decode("utf-8", "replace")
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    try:
        return datetime.strptime(lines[-1][:10], "%Y-%m-%d").date()
    except (IndexError, ValueError):
        raise FetchError(BAD, f"unexpected time[last] answer: {body[:80]!r}") from None


def _known(key):
    with _latest_lock:
        hit = _latest.get(key)
    return (hit["date"] or hit["good"]) if hit else None


def latest_time(src: Dict, deadline: Optional[Deadline] = None, max_wait: float = 4.0,
                block: bool = True) -> Optional[date]:
    """Newest day a dataset has (its ERDDAP time axis), cached for an hour.

    A failed lookup returns the last good answer (if any) and isn't retried for 5 minutes.
    block=False never waits: it returns what is known and refreshes in a background thread.
    One lookup per dataset is in flight at a time: other callers wait for it (at most max_wait /
    the deadline) instead of asking NOAA again.
    """
    key = (src["server"], src["dataset_id"])
    now = time.monotonic()
    with _latest_lock:
        hit = _latest.get(key)
        if hit:
            ttl = LATEST_TTL_S if hit["date"] else LATEST_FAIL_TTL_S
            if now - hit["checked"] < ttl:
                return hit["date"] or hit["good"]
        ev = _latest_pending.get(key)
        mine = ev is None
        if mine:
            ev = _latest_pending[key] = threading.Event()
    wait_s = max_wait if deadline is None else max(0.0, min(max_wait, deadline.left()))
    if not mine:  # someone is already asking: wait for that answer (or not at all)
        if block:
            ev.wait(wait_s)
        return _known(key)
    if not block:
        try:  # started outside the lock (Thread.start waits for the new thread)
            threading.Thread(target=_refresh_latest, args=(src, key, ev, 4.0), daemon=True,
                             name="erddap-latest").start()
        except RuntimeError:
            _finish_latest(key, ev)
        return hit["good"] if hit else None
    return _refresh_latest(src, key, ev, wait_s)


def _finish_latest(key, ev):
    with _latest_lock:
        if _latest_pending.get(key) is ev:
            _latest_pending.pop(key, None)
    ev.set()


def _refresh_latest(src, key, ev, wait_s=4.0):
    """Look up time[last] once, within wait_s. Never raises; always releases the waiters (ev)."""
    try:
        good_before = (_latest.get(key) or {}).get("good")
        try:
            dl = Deadline(wait_s)
            d = with_retry(lambda: _lookup_latest(src, dl), dl, label=f"{src['dataset_id']} time[last]")
        except FetchError as e:
            logger.warning("ERDDAP time[last] lookup failed for %s: %s", src["dataset_id"], e)
            with _latest_lock:
                _latest[key] = {"checked": time.monotonic(), "date": None, "good": good_before}
            return good_before
        with _latest_lock:
            _latest[key] = {"checked": time.monotonic(), "date": d, "good": d}
        return d
    finally:
        _finish_latest(key, ev)


def latest_mur_date(config: Optional[Dict] = None, block: bool = True,
                    deadline: Optional[Deadline] = None, max_wait: float = 4.0) -> Optional[date]:
    """The newest MUR day NOAA has (cached for an hour), or None if never learned."""
    mur = sources(config)["mur"]
    return latest_time(mur[0], deadline=deadline, max_wait=max_wait, block=block)


def _reset_latest_cache():
    """Tests only."""
    with _latest_lock:
        _latest.clear()
        _latest_pending.clear()


# ---------------- Grids ----------------

def _grid_url(src: Dict, d0: date, d1: date, bbox) -> str:
    """griddap .nc URL for days d0..d1 at the dataset's time of day ("hour": MUR 09Z, OISST 12Z).

    Exact time stamps, not d0T00:00 to d1T23:59: ERDDAP rejects a stop past the axis maximum, so
    a whole-day range always 404'd on the newest day (the page could never show it). A single
    day is one value, which ERDDAP snaps to the nearest stamp: callers check the date returned.
    """
    minlon, minlat, maxlon, maxlat = bbox
    hh = int(src.get("hour", 12))
    if d0 == d1:
        q = f"{src['var']}[({d0}T{hh:02d}:00:00Z)]"
    else:
        q = f"{src['var']}[({d0}T{hh:02d}:00:00Z):1:({d1}T{hh:02d}:00:00Z)]"
    if src.get("zlev") is not None:
        q += f"[({src['zlev']})]"
    q += f"[({minlat}):1:({maxlat})][({minlon}):1:({maxlon})]"
    return f"{src['server']}/griddap/{src['dataset_id']}.nc?{q}"


def _open_nc(content: bytes) -> xr.Dataset:
    """Open an ERDDAP .nc (NetCDF-3) from memory; fall back to a temp file that is removed."""
    try:
        with xr.open_dataset(io.BytesIO(content), engine="scipy") as ds:
            return ds.load()
    except Exception:
        fd, path = tempfile.mkstemp(suffix=".nc")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            with xr.open_dataset(path) as ds:
                return ds.load()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def parse_grids(content: bytes, var: str):
    """ERDDAP .nc bytes -> ([(iso_date, arrF[lat, lon]), ...], lats, lons, units).

    Handles MUR [time][lat][lon] and OISST [time][zlev][lat][lon]; values become °F to 0.1 (sprint 2: data/sstgrid
    keeps tenths; whole degrees before).
    """
    try:
        ds = _open_nc(content)
    except Exception as e:
        raise FetchError(BAD, f"unreadable NetCDF: {type(e).__name__}: {e}") from None
    if var not in ds:
        raise FetchError(BAD, f"variable {var!r} not in the response")
    da = ds[var]
    lat_name = next((d for d in da.dims if "lat" in d.lower()), None)
    lon_name = next((d for d in da.dims if "lon" in d.lower()), None)
    if not lat_name or not lon_name:
        raise FetchError(BAD, f"no lat/lon axes in {da.dims}")
    for dim in list(da.dims):  # drop zlev / altitude (single level)
        if dim not in ("time", lat_name, lon_name):
            da = da.isel({dim: 0})
    units = da.attrs.get("units", "kelvin")
    lats = np.asarray(ds[lat_name].values, dtype=np.float64)
    lons = np.asarray(ds[lon_name].values, dtype=np.float64)
    out = []
    if "time" in da.dims:
        times = ds["time"].values
        for i in range(da.sizes["time"]):
            day = str(np.datetime_as_string(np.asarray(times[i], dtype="datetime64[ns]"), unit="D"))
            arr = da.isel(time=i).transpose(lat_name, lon_name).values
            out.append((day, to_fahrenheit(arr, units)))
    else:
        out.append((None, to_fahrenheit(da.transpose(lat_name, lon_name).values, units)))
    return out, lats, lons, units


def _fetch_mur_day(src, d: date, bbox, deadline: Deadline):
    content = http_get(_grid_url(src, d, d, bbox), deadline)
    grids, lats, lons, units = parse_grids(content, src["var"])
    if not grids:
        raise FetchError(MISSING, "empty response")
    day, arr = grids[0]
    if day is not None and day != str(d):  # a gap day: ERDDAP snapped to a neighbour
        raise FetchError(MISSING, f"{d} not in dataset (nearest is {day})")
    return {"date": str(d), "arrF": arr, "lats": lats, "lons": lons, "units": units}


def fetch_days(src, dates: List[date], bbox, deadline: Deadline, concurrency: int = 3,
               cache: Optional[Dict] = None):
    """Fetch one day per request, `concurrency` at a time, newest first.

    Returns (results {date: day}, errors {date: kind}). Returns at the deadline even if
    requests are still running; those finish (or give up) on their own and are discarded.
    cache (the seeder's, for one run): days already fetched are taken from it, new ones added.
    """
    results, errors = {}, {}
    stop = {}  # set when the dataset is gone or the server refuses us: no point going on
    if cache is not None:
        for d in list(dates):
            hit = cache.get(("sst", src["dataset_id"], d))
            if hit is not None:
                results[d] = hit
        dates = [d for d in dates if d not in results]
        if not dates:
            return results, errors

    def one(d):
        if stop:
            raise FetchError(stop["kind"], "skipped after an earlier refusal")
        try:
            return with_retry(lambda: _fetch_mur_day(src, d, bbox, deadline), deadline,
                              label=f"{src['dataset_id']} {d}")
        except FetchError as e:
            if e.kind in (BLOCKED, DEAD, UNREACHABLE):
                stop.setdefault("kind", e.kind)
            raise

    order = sorted(dates, reverse=True)
    pool = ThreadPoolExecutor(max_workers=max(1, concurrency))
    try:
        futs = {pool.submit(one, d): d for d in order}
        done, not_done = wait(futs, timeout=max(0.0, deadline.left()))
        unreachable = []
        for f in done:
            d = futs[f]
            try:
                results[d] = f.result()
                if cache is not None:
                    cache[("sst", src["dataset_id"], d)] = results[d]
            except FetchError as e:
                errors[d] = e.kind
                if e.kind == UNREACHABLE:
                    unreachable.append((d, e))
                else:
                    logger.warning("ERDDAP %s %s: %s", src["dataset_id"], d, e)
            except Exception as e:  # noqa: BLE001 - a parse bug must not sink the week
                errors[d] = BAD
                logger.warning("ERDDAP %s %s: %s: %s", src["dataset_id"], d, type(e).__name__, e)
        if unreachable:  # one line, not one per day
            logger.warning("ERDDAP %s: %d days not fetched, NOAA unreachable: %s", src["dataset_id"],
                           len(unreachable), min(unreachable)[1])
        for f in not_done:
            errors[futs[f]] = TIMEOUT
            f.cancel()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results, errors


def fetch_oisst(cfg_sources: Dict, dates: List[date], bbox, deadline: Deadline):
    """OISST v2.1 for `dates`: the final dataset where it reaches, near-real-time for the rest.

    A whole week is ~20 KB, so each dataset is one request. Returns (results, errors, used)
    where used is the list of sources that served days.
    """
    results, errors, used = {}, {}, []
    for src in cfg_sources.get("oisst") or []:
        todo = [d for d in sorted(dates) if str(d) not in {r["date"] for r in results.values()}]
        first = src.get("first")
        if first:
            todo = [d for d in todo if d >= date.fromisoformat(first)]
        if not todo or deadline.left() < 1.0:
            continue
        last = latest_time(src, deadline=deadline, max_wait=min(3.0, deadline.left() / 3))
        if last:
            todo = [d for d in todo if d <= last]
        if not todo:
            continue
        try:
            content = with_retry(lambda: http_get(_grid_url(src, todo[0], todo[-1], bbox), deadline),
                                 deadline, label=f"{src['dataset_id']} {todo[0]}..{todo[-1]}")
            grids, lats, lons, units = parse_grids(content, src["var"])
        except FetchError as e:
            logger.warning("OISST %s %s..%s: %s", src["dataset_id"], todo[0], todo[-1], e)
            for d in todo:
                errors[d] = e.kind
            continue
        wanted = {str(d): d for d in todo}
        served = False
        for day, arr in grids:
            if day in wanted:
                results[wanted[day]] = {"date": day, "arrF": arr, "lats": lats, "lons": lons,
                                        "units": units, "src": src}
                errors.pop(wanted[day], None)
                served = True
        for d in todo:
            if d not in results:
                errors.setdefault(d, MISSING)
        if served:
            used.append(src)
    return results, errors, used


# ---------------- The week ----------------

def _reason(errors: Dict) -> str:
    kinds = set(errors.values())
    if not kinds:
        return "no_data"
    if kinds <= {MISSING}:
        return "no_data"
    if UNREACHABLE in kinds:
        return "network"
    for k in (BUSY, TIMEOUT):
        if k in kinds:
            return "busy"
    if BLOCKED in kinds:
        return "blocked"
    if NETWORK in kinds:
        return "network"
    if DEAD in kinds:
        return "dead"
    return "busy"


def get_sst_multiday(end_date, config: Dict, num_days: int = 7, budget_s: Optional[float] = None,
                     allow_fallback: bool = True, require_full: bool = False,
                     concurrency: Optional[int] = None, cache: Optional[Dict] = None) -> Dict:
    """Fetch the week ending `end_date` (or the newest MUR day, if that is earlier).

    Returns within `budget_s` (config "budget_s", default 22) with:
      days            [{"arrF", "date"}] in date order (fewer than num_days if some are missing)
      lats, lons, units, server, dataset_id, dataset_title, var
      source          "MUR" or "OISST" (OISST only when MUR sent no day in time)
      requested_end   the end date asked for (ISO); served_end is days[-1]["date"]
      missing_days    ISO dates of the window that weren't served
      complete        False if a missing day may still come (NOAA busy/slow), True if the
                      missing days don't exist in the data
      latest_available  newest MUR day (ISO) or None
      fetch_seconds   time spent
    Raises SSTUnavailable when nothing could be had. require_full=True (seeder, precache) raises
    unless every day that exists was fetched, and allow_fallback=False never uses OISST.
    cache: the seeder's per-run cache of days already fetched (overlapping weeks share their days).
    """
    src_cfg = sources(config)
    budget = float(budget_s if budget_s is not None else src_cfg.get("budget_s", 22))
    conc = int(concurrency or src_cfg.get("concurrency", 3))
    t0 = time.monotonic()
    deadline = Deadline(budget)
    bbox = aoi_bbox(config)
    mur_list = src_cfg["mur"]
    requested_end = end_date

    latest = latest_time(mur_list[0], deadline=deadline, max_wait=min(4.0, budget / 5))
    end = min(end_date, latest) if latest else end_date
    dates = [end - timedelta(days=i) for i in range(num_days - 1, -1, -1)]
    dates = [d for d in dates if d >= MUR_START]
    if not dates:
        raise SSTUnavailable(f"No satellite data for the week ending {end_date} (MUR starts "
                             f"{MUR_START}).", "no_data", requested_end=end_date)

    mur_deadline = deadline.minus(OISST_RESERVE_S) if allow_fallback else deadline
    got, errors, served_by = {}, {}, None
    for src in mur_list:
        todo = [d for d in dates if d not in got]
        if not todo or mur_deadline.left() < 1.0:
            break
        res, errs = fetch_days(src, todo, bbox, mur_deadline, concurrency=conc, cache=cache)
        if res and served_by is None:
            served_by = src
        if served_by is src:
            got.update(res)
        for d, k in errs.items():
            if d not in got:
                errors[d] = k
        for d in res:
            errors.pop(d, None)

    def _maybe_later(d, errs):
        """A missing day that may still come: NOAA failed, or a recent day reported absent."""
        return errs.get(d) != MISSING or bool(latest and (latest - d).days < 7)

    def _result(source, src, results, errs):
        keys = sorted(results)
        first = results[keys[0]]
        missing = [str(d) for d in dates if d not in results]
        transient = [d for d in dates if d not in results and _maybe_later(d, errs)]
        return {
            "source": source,
            "server": src["server"],
            "dataset_id": src["dataset_id"],
            "dataset_title": TITLES[source],
            "var": src["var"],
            "units": first["units"],
            "days": [{"arrF": results[d]["arrF"], "date": results[d]["date"]} for d in keys],
            "lats": first["lats"],
            "lons": first["lons"],
            "requested_end": str(requested_end),
            "missing_days": missing,
            "complete": not transient,
            "latest_available": str(latest) if latest else None,
            "fetch_seconds": round(time.monotonic() - t0, 2),
            "errors": {str(d): k for d, k in errs.items()},
        }

    def _unavailable(errs, prefix="No SST"):
        reason = _reason(errs)
        return SSTUnavailable(
            f"{prefix} for {dates[0]} to {dates[-1]} within {budget:.0f}s ({reason}).",
            reason, requested_end=requested_end, errors={str(d): k for d, k in errs.items()},
            elapsed_s=time.monotonic() - t0)

    if got:
        transient = [d for d in dates if d not in got and _maybe_later(d, errors)]
        if require_full and transient:
            raise _unavailable(errors, prefix=f"Only {len(got)} of {len(dates)} MUR days")
        if len(got) < len(dates):
            logger.info("MUR partial week %s..%s: %d of %d days (%s)", dates[0], dates[-1],
                        len(got), len(dates), {str(d): errors.get(d) for d in dates if d not in got})
        return _result("MUR", served_by, got, errors)

    if require_full or not allow_fallback:
        raise _unavailable(errors, prefix="No MUR")

    if errors and set(errors.values()) <= {UNREACHABLE} and \
            all(noaa(s["server"]).is_down() for s in src_cfg.get("oisst") or []):
        logger.warning("MUR unavailable for %s..%s: NOAA unreachable, not trying OISST (same server)",
                       dates[0], dates[-1])
    elif deadline.left() >= 1.0:
        logger.warning("MUR unavailable for %s..%s (%s), trying OISST", dates[0], dates[-1],
                       _reason(errors))
        oi, oerr, used = fetch_oisst(src_cfg, dates, bbox, deadline)
        if oi:
            # The payload names one dataset: the one that served the most days (NRT for recent weeks).
            src = max(used, key=lambda s: sum(1 for r in oi.values() if r["src"] is s))
            all_errs = dict(errors)
            all_errs.update(oerr)
            return _result("OISST", src, oi, all_errs)
        for d, k in oerr.items():  # MUR's failure explains the miss; OISST's only fills gaps
            errors.setdefault(d, k)
    raise _unavailable(errors)

