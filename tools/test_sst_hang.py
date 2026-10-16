"""Regression tests for the Oct 16, 2026 outage (docs/incident-2026-10-16.md): an outside server that can't be
reached, drops packets, never answers or drips bytes must never hold a request thread past its budget, a busy or
broken upstream must answer at once instead of piling up threads, and nothing may be left blocked afterwards.

    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES .venv/bin/python tools/test_sst_hang.py

Real sockets, no fake `requests`: local servers that drip or never answer, a closed port (refused), and
tools/netsim (errno 101, dropped IPv4 then errno 101, no DNS) for made-up host names. Each test times every call.
No pytest; the whole file runs in about a minute.
"""

import importlib.util
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="sst-hang-test-"))
os.environ["SST_CACHE_DIR"] = str(TMP / "cache")
os.environ["LABS_STATS_DIR"] = str(TMP / "stats")
os.environ["_SST_PREWARM_STARTED"] = "1"   # no startup pre-warm thread
os.environ["_SST_WATCHDOG_STARTED"] = "1"  # tests call the watchdog check directly
for var in ("RENDER", "LABS_STATS_KEY", "LABS_HOST", "LABS_HOME_URL", "GFW_API_TOKEN", "CACHE_UPLOAD_TOKEN"):
    os.environ.pop(var, None)

# tools/netsim for made-up host names only (real hosts are untouched)
os.environ["NETSIM"] = ("noaa-unreachable.test=unreachable,noaa-v4drop.test=v4drop,noaa-nodns.test=dnsfail,"
                        "gfw-unreachable.test=unreachable")
os.environ["NETSIM_QUIET"] = "1"
_spec = importlib.util.spec_from_file_location("netsim", ROOT / "tools" / "netsim" / "sitecustomize.py")
netsim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(netsim)

import geopandas  # noqa: E402,F401 - imported up front so the land-mask test times the download, not the import
import numpy as np  # noqa: E402

from data import cache as cachemod  # noqa: E402
from data import erddap  # noqa: E402
from data import geo  # noqa: E402
from data import outbound  # noqa: E402

CFG = json.load(open(ROOT / "config.json"))
LATEST = date(2026, 10, 15)
SLACK = 0.6   # s allowed past a deadline (thread hand-off, a loaded laptop)
_n = [0]


# ---------------- local servers ----------------

class Server:
    """A local HTTP server. mode: drip (200 + one byte every 0.5 s), tarpit (never answers), json (body after
    `delay` s). `url` is its base URL."""

    def __init__(self, mode, body=b"", delay=0.0):
        self.mode, self.body, self.delay = mode, body, delay
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.port = self.sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.hits = 0
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.hits += 1
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c):
        try:
            c.settimeout(60)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = c.recv(65536)
                if not chunk:
                    return
                buf += chunk
            if self.mode == "tarpit":
                while c.recv(65536):
                    pass
            elif self.mode == "drip":
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10000000\r\nConnection: close\r\n\r\n")
                while True:
                    time.sleep(0.5)
                    c.sendall(b"x")
            else:
                time.sleep(self.delay)
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
                          b"Connection: close\r\n\r\n" % len(self.body) + self.body)
        except OSError:
            pass
        finally:
            c.close()

    def close(self):
        self.sock.close()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def noaa_cfg(server):
    """config.json with every ERDDAP source on `server` (base URL ending in /erddap)."""
    cfg = json.loads(json.dumps(CFG))
    e = cfg.setdefault("erddap", {})
    for key in ("mur", "oisst"):
        e[key] = [dict(src, server=server) for src in (e.get(key) or erddap.DEFAULT_SOURCES[key])]
    return cfg


# ---------------- helpers ----------------

def fresh():
    """Breakers closed, caches empty, a new cache dir."""
    _n[0] += 1
    d = TMP / f"c{_n[0]}" / "sst"
    d.mkdir(parents=True)
    cachemod.CACHE_DIR = d
    cachemod._index_cache["t"] = 0.0
    outbound.reset_all()
    erddap._reset_latest_cache()
    appmod = _app()
    appmod._raw_data_cache.clear()
    appmod._refresh_tried.clear()
    appmod._failed_recently.clear()
    appmod._gfw_style_cache.clear()
    appmod._gfw_style_failed.clear()
    appmod._gfw_tile_cache.clear()
    return appmod


def _app():
    import app as appmod
    appmod.mask_land_rasterized = lambda arr, lats, lons: arr  # no Natural Earth download
    return appmod


def timed(fn, *a, **kw):
    """(seconds, result or exception)."""
    t0 = time.monotonic()
    try:
        out = fn(*a, **kw)
    except Exception as e:  # noqa: BLE001
        out = e
    return time.monotonic() - t0, out


def run_parallel(fns):
    """Run the callables at once; [(seconds, result or exception)] in order."""
    out = [None] * len(fns)
    start = threading.Barrier(len(fns))

    def go(i, f):
        start.wait()
        out[i] = timed(f)

    ts = [threading.Thread(target=go, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in ts]
    [t.join(120) for t in ts]
    assert not any(t.is_alive() for t in ts), "a call never returned"
    return out


def outbound_calls_running():
    """Threads still inside an outside call (data/outbound._call)."""
    hits = []
    for ident, frame in sys._current_frames().items():
        f = frame
        while f is not None:
            if f.f_code.co_name == "_call" and f.f_code.co_filename.endswith("outbound.py"):
                hits.append(ident)
                break
            f = f.f_back
    return hits


def assert_nothing_left_blocked(grace=3.0):
    """Every outside call has ended (its deadline holds on its own thread too)."""
    t_end = time.monotonic() + grace
    while outbound_calls_running() and time.monotonic() < t_end:
        time.sleep(0.05)
    left = outbound_calls_running()
    assert not left, f"{len(left)} outside calls still running {grace}s after their callers returned"


def synthetic_week(end):
    lats = np.round(np.arange(38.80, 43.80001, 0.05), 4)
    lons = np.round(np.arange(-74.96, -68.80001, 0.05), 4)
    days = [{"arrF": np.full((len(lats), len(lons)), 60.0), "date": str(end - timedelta(days=i))}
            for i in range(6, -1, -1)]
    return {"source": "MUR", "server": "https://coastwatch.pfeg.noaa.gov/erddap", "dataset_id": "jplMURSST41",
            "dataset_title": erddap.TITLES["MUR"], "var": "analysed_sst", "units": "degree_F", "days": days,
            "lats": lats, "lons": lons, "requested_end": str(end), "missing_days": [], "complete": True,
            "latest_available": str(LATEST), "fetch_seconds": 0.1, "errors": {}}


# ---------------- outbound / erddap ----------------

def test_breaker_open_probe_close():
    b = outbound.Breaker("t", open_s=0.2, threshold=1, trip_on=())
    assert b.admit() is False
    b.report(outbound.UNREACHABLE, "no route")
    for call in (b.check, b.admit):
        try:
            call()
            raise AssertionError("breaker should be open")
        except outbound.OutboundError as e:
            assert e.kind == outbound.DOWN
    time.sleep(0.25)
    assert b.admit() is True            # the one probe
    try:
        b.admit()
        raise AssertionError("only one probe at a time")
    except outbound.OutboundError as e:
        assert e.kind == outbound.DOWN
    b.report(outbound.UNREACHABLE, "still no route", probe=True)   # probe failed: open again
    assert b.is_down()
    time.sleep(0.25)
    assert b.admit() is True
    b.report(None, probe=True)          # probe answered: closed
    assert b.admit() is False and not b.is_down()
    g = outbound.Breaker("gfw", open_s=60, threshold=3, trip_on=(outbound.TIMEOUT,))
    g.report(outbound.TIMEOUT)
    g.report(outbound.TIMEOUT)
    assert not g.is_down()
    g.report(outbound.TIMEOUT)
    assert g.is_down()


def test_http_get_drip_and_tarpit_return_at_the_deadline():
    fresh()
    for mode in ("drip", "tarpit"):
        srv = Server(mode)
        try:
            el, out = timed(erddap.http_get, f"{srv.url}/erddap/x.nc", erddap.Deadline(2.0), read_timeout=1.5)
            assert isinstance(out, erddap.FetchError) and out.kind == erddap.TIMEOUT, (mode, out)
            assert el < 2.0 + SLACK, (mode, el)
            # before: iter_content(64 KiB) waited for a whole chunk, so a drip held the thread for hours
            assert_nothing_left_blocked(grace=2.0)
        finally:
            srv.close()


def test_unreachable_refused_no_dns_fail_at_once_and_open_the_breaker():
    for base in ("http://noaa-unreachable.test", f"http://127.0.0.1:{closed_port()}", "http://noaa-nodns.test"):
        fresh()
        url = f"{base}/erddap/griddap/jplMURSST41.csv?time%5Blast%5D"
        el, out = timed(erddap.http_get, url, erddap.Deadline(5.0))
        assert isinstance(out, erddap.FetchError) and out.kind == erddap.UNREACHABLE, (base, out)
        assert el < 1.0, (base, el)
        assert erddap.noaa(url).is_down(), base
        el, out = timed(erddap.http_get, url, erddap.Deadline(5.0))   # breaker open: not even tried
        assert isinstance(out, erddap.FetchError) and out.kind == erddap.UNREACHABLE and el < 0.05, (base, el)


def test_week_fails_fast_when_noaa_drops_ipv4_and_has_no_ipv6():
    """Production, Oct 16: each request waited out the connect timeout, then said errno 101. 22 s per week."""
    fresh()
    cfg = noaa_cfg("http://noaa-v4drop.test/erddap")
    el, out = timed(erddap.get_sst_multiday, date(2011, 8, 16), cfg)
    assert isinstance(out, erddap.SSTUnavailable) and out.reason == "network", out
    assert el < erddap.CONNECT_TIMEOUT + 1.0, el      # was 22.0 s
    assert erddap.noaa_down(cfg)
    el, out = timed(erddap.get_sst_multiday, date(2004, 7, 21), cfg)
    assert isinstance(out, erddap.SSTUnavailable) and out.reason == "network" and el < 0.3, (el, out)
    assert_nothing_left_blocked()


def test_newest_day_lookup_is_asked_once_for_many_callers():
    fresh()
    srv = Server("tarpit")
    try:
        src = {"server": f"{srv.url}/erddap", "dataset_id": "jplMURSST41"}
        res = run_parallel([lambda: erddap.latest_time(src, max_wait=1.0)] * 10)
        assert srv.hits == 1, srv.hits                      # one request, the other nine waited for it
        assert max(el for el, _ in res) < 1.0 + SLACK and all(out is None for _, out in res)
        el, out = timed(erddap.latest_time, src, max_wait=1.0)   # failed: not asked again for 5 minutes
        assert out is None and el < 0.05 and srv.hits == 1
        assert_nothing_left_blocked()
    finally:
        srv.close()


# ---------------- app: loading weeks ----------------

def test_load_week_unreachable_30_readers_all_answer_fast_and_queue():
    appmod = fresh()
    real = appmod.CFG.get("erddap")
    appmod.CFG["erddap"] = noaa_cfg("http://noaa-v4drop.test/erddap")["erddap"]
    try:
        # Wave 1, all at once: SST_NOAA_READERS of them try NOAA (one connect timeout), the rest are told at once
        weeks = [date(2005, 1, 1) + timedelta(days=37 * i) for i in range(30)]
        res = run_parallel([lambda d=d: appmod._load_week(d, False, LATEST) for d in weeks])
        worst = max(el for el, _ in res)
        assert worst < erddap.CONNECT_TIMEOUT + 2.0, worst                      # was 22 s each
        assert sum(1 for el, _ in res if el < 0.5) >= 30 - appmod._NOAA_READERS, [round(el, 1) for el, _ in res]
        for el, out in res:
            assert isinstance(out, erddap.SSTUnavailable), out
            assert out.reason in ("network", "busy") and out.queued is True, (out.reason, out)
        assert len(cachemod.wanted_weeks()) == cachemod.MAX_WANTED  # queued for the seeder (the list holds 30)
        assert appmod._noaa_readers._value == appmod._NOAA_READERS   # every slot given back
        assert not any(lk.locked() for lk in appmod._week_locks.values())
        # Wave 2: the breaker is open, so nobody even tries; the honest message says NOAA can't be reached
        (cachemod.CACHE_DIR / cachemod.WANTED_FILE).unlink(missing_ok=True)
        weeks = [date(2009, 2, 1) + timedelta(days=37 * i) for i in range(30)]
        res = run_parallel([lambda d=d: appmod._load_week(d, False, LATEST) for d in weeks])
        assert max(el for el, _ in res) < 0.5, [round(el, 2) for el, _ in res]
        assert all(isinstance(out, erddap.SSTUnavailable) and out.reason == "network" and out.queued
                   for _, out in res), [getattr(o, "reason", o) for _, o in res]
        msg = json.dumps(appmod._failure_message(res[-1][1], weeks[-1]).to_plotly_json(), default=str)
        assert "can't reach NOAA" in msg and "within a day" in msg and "\u2014" not in msg
        assert_nothing_left_blocked()
    finally:
        appmod.CFG["erddap"] = real


def test_load_week_slow_noaa_holds_at_most_n_readers():
    appmod = fresh()
    srv = Server("drip")
    real = appmod.CFG.get("erddap")
    appmod.CFG["erddap"] = dict(noaa_cfg(f"{srv.url}/erddap")["erddap"], budget_s=6.5)
    try:
        weeks = [date(2006, 6, 1) + timedelta(days=41 * i) for i in range(16)]
        res = run_parallel([lambda d=d: appmod._load_week(d, False, LATEST) for d in weeks])
        slow = [el for el, _ in res if el > 1.0]
        assert len(slow) <= appmod._NOAA_READERS, [round(el, 1) for el, _ in res]
        assert max(el for el, _ in res) < 6.5 + SLACK + 0.5, [round(el, 1) for el, _ in res]  # one budget at most
        assert all(isinstance(out, erddap.SSTUnavailable) for _, out in res)
        assert appmod._noaa_readers._value == appmod._NOAA_READERS
        assert_nothing_left_blocked(grace=2.5)
    finally:
        appmod.CFG["erddap"] = real
        srv.close()


def test_two_visitors_same_week_wait_is_bounded():
    """The second visitor waits for the first visitor's fetch, never past one budget."""
    appmod = fresh()
    srv = Server("tarpit")
    real = appmod.CFG.get("erddap")
    appmod.CFG["erddap"] = dict(noaa_cfg(f"{srv.url}/erddap")["erddap"], budget_s=2)
    try:
        res = run_parallel([lambda: appmod._load_week(date(2012, 7, 1), False, LATEST)] * 3)
        assert max(el for el, _ in res) < 2 + 3 + SLACK, [round(el, 1) for el, _ in res]
        assert all(isinstance(out, erddap.SSTUnavailable) for _, out in res)
        assert_nothing_left_blocked()
    finally:
        appmod.CFG["erddap"] = real
        srv.close()


def test_lock_wait_runs_out_answers_busy_without_a_second_fetch():
    appmod = fresh()
    srv = Server("tarpit")
    real = appmod.CFG.get("erddap")
    appmod.CFG["erddap"] = dict(noaa_cfg(f"{srv.url}/erddap")["erddap"], budget_s=0.5)
    end = date(2014, 5, 5)
    lk = appmod._week_lock(end)
    lk.acquire()                       # another visitor's fetch of this week that never ends
    try:
        el, out = timed(appmod._load_week, end, False, LATEST)
        assert isinstance(out, erddap.SSTUnavailable) and out.reason == "busy", out
        assert el < 0.5 + 3 + SLACK, el                              # waited one budget...
        assert srv.hits == 0, srv.hits                               # ...and asked NOAA nothing
        assert appmod._noaa_readers._value == appmod._NOAA_READERS
    finally:
        lk.release()
        appmod.CFG["erddap"] = real
        srv.close()


def test_week_without_land_mask_is_served_not_saved():
    appmod = fresh()
    real_get, real_gdf = appmod.get_sst_multiday, geo._LAND_GDF
    appmod.get_sst_multiday = lambda end, cfg, **kw: synthetic_week(end)
    try:
        geo._LAND_GDF = None
        w = appmod._load_week(date(2013, 9, 9), False, LATEST)
        assert w["write"] == "fresh" and w["save"] is False
        geo._LAND_GDF = object()        # loaded
        w = appmod._load_week(date(2013, 10, 9), False, LATEST)
        assert w["save"] is True
    finally:
        appmod.get_sst_multiday, geo._LAND_GDF = real_get, real_gdf


def test_land_mask_wait_is_bounded_and_no_lock_held_during_the_download():
    real_dl = geo._download_ne10m_land_zip
    saved = (geo._LAND_GDF, geo._LAND_FAILED_AT, geo._LAND_LOADING)
    started = threading.Event()

    def slow_download():
        started.set()
        time.sleep(1.5)
        raise RuntimeError("simulated: S3 too slow")

    geo._download_ne10m_land_zip = slow_download
    geo._LAND_GDF, geo._LAND_FAILED_AT, geo._LAND_LOADING = None, None, None
    try:
        el, out = timed(geo.load_land_gdf, wait_s=0.3)
        assert out is None and el < 0.3 + SLACK, el
        assert started.is_set() and not geo._LAND_LOCK.locked()   # downloading, lock free
        el, out = timed(geo.load_land_gdf, wait_s=0.2)            # a second caller waits on the same load
        assert out is None and el < 0.2 + SLACK
        real_wait, geo.LAND_WAIT_S = geo.LAND_WAIT_S, 0.3
        try:   # default waits (the per-day mask calls of one week): only the first one waits
            el1, _ = timed(geo.load_land_gdf)
            el2, out = timed(geo.load_land_gdf)
            assert 0.25 < el1 < 0.3 + SLACK and el2 < 0.05 and out is None, (el1, el2)
        finally:
            geo.LAND_WAIT_S = real_wait
        time.sleep(1.0)
        assert geo._LAND_LOADING is None and geo._LAND_FAILED_AT is not None
        el, out = timed(geo.load_land_gdf, wait_s=5)               # failed recently: not tried again
        assert out is None and el < 0.05
    finally:
        geo._download_ne10m_land_zip = real_dl
        geo._LAND_GDF, geo._LAND_FAILED_AT, geo._LAND_LOADING = saved


# ---------------- app: GFW tile proxy ----------------

def _tiles(appmod, n, dr="2026-10-09,2026-10-15", xs=None):
    def one(i):
        c = appmod.server.test_client()
        x = (xs or [37])[i % len(xs or [37])]
        r = c.get(f"/api/gfw/7/{x}/{45 + i % 6}.png?dr={dr}")
        return r.status_code, r.headers.get("Cache-Control"), r.data
    return run_parallel([lambda i=i: one(i) for i in range(n)])


def test_gfw_style_slow_20_tiles_answer_within_its_limit():
    """Before: one generate-png at a time under a lock, so 20 tiles took 20 x 15 s and every thread."""
    appmod = fresh()
    srv = Server("drip")
    os.environ["GFW_API_TOKEN"] = "test"
    real = (appmod._GFW_API, appmod.GFW_STYLE_S, appmod.GFW_STYLE_WAIT_S)
    appmod._GFW_API, appmod.GFW_STYLE_S, appmod.GFW_STYLE_WAIT_S = srv.url, 1.5, 1.0
    try:
        res = _tiles(appmod, 20)
        assert max(el for el, _ in res) < 1.5 + SLACK, [round(el, 1) for el, _ in res]
        for el, (status, cc, data) in res:   # empty: 200 (it failed) or 503 (still running when the wait ran out)
            assert status in (200, 503) and cc == "no-store", (status, cc)
            assert data == (appmod._EMPTY_PNG if status == 200 else b"busy")   # 503: no image, so the page retries
        assert not appmod._gfw_style_pending and srv.hits == 1   # one generate-png call for the range
        assert sum(1 for _, (st, _, _) in res if st == 503) >= 20 - 1 - appmod.GFW_STYLE_WAITERS  # the rest: at once
        assert appmod._gfw_style_waiting[0] == 0
        res = _tiles(appmod, 20)                                  # failed a moment ago: not asked again
        assert max(el for el, _ in res) < 0.3 and srv.hits == 1
        assert all(st == 200 for _, (st, _, _) in res)            # a failure: empty, and not worth a retry
        assert_nothing_left_blocked()
    finally:
        appmod._GFW_API, appmod.GFW_STYLE_S, appmod.GFW_STYLE_WAIT_S = real
        os.environ.pop("GFW_API_TOKEN", None)
        srv.close()


def test_gfw_tiles_slow_answer_bounded_and_breaker_opens():
    appmod = fresh()
    tiles = Server("drip")
    style = Server("json", body=json.dumps({"url": tiles.url + "/t/{z}/{x}/{y}"}).encode())
    os.environ["GFW_API_TOKEN"] = "test"
    real = (appmod._GFW_API, appmod.GFW_TILE_S)
    appmod._GFW_API, appmod.GFW_TILE_S = style.url, 1.0
    try:
        res = _tiles(appmod, 14, xs=list(range(30, 44)))
        gfw = outbound.upstream("gfw")
        # 6 at a time, 4 more wait for a slot (<= 3 s), the rest at once; every one bounded
        assert max(el for el, _ in res) < gfw.queue_s + 1.0 + SLACK + 0.5, [round(el, 1) for el, _ in res]
        assert all(st in (200, 503) and cc == "no-store" for _, (st, cc, _) in res)
        assert any(st == 503 for _, (st, _, _) in res)            # too many at once: "busy", the page retries
        assert gfw.is_down()                                      # timeouts in a row opened the breaker
        el, out = timed(lambda: _tiles(appmod, 1, xs=[50]))
        assert el < 0.3
        assert_nothing_left_blocked()
    finally:
        appmod._GFW_API, appmod.GFW_TILE_S = real
        os.environ.pop("GFW_API_TOKEN", None)
        tiles.close()
        style.close()


def test_gfw_unreachable_answers_at_once():
    appmod = fresh()
    os.environ["GFW_API_TOKEN"] = "test"
    real = appmod._GFW_API
    appmod._GFW_API = "http://gfw-unreachable.test"
    try:
        for i in range(5):
            el, res = timed(lambda i=i: _tiles(appmod, 1, dr=f"2026-09-{10 + i:02d},2026-09-{16 + i:02d}"))
            assert el < 0.3, (i, el)
        assert outbound.upstream("gfw").is_down()
    finally:
        appmod._GFW_API = real
        os.environ.pop("GFW_API_TOKEN", None)


# ---------------- executors, diagnostics ----------------

def test_call_with_timeout_returns_at_the_timeout():
    appmod = _app()
    el, out = timed(appmod._call_with_timeout, time.sleep, 0.3, 2.0)
    assert isinstance(out, FuturesTimeout) and el < 0.3 + SLACK, (el, out)   # `with` waited the whole 2 s


def test_debug_threads_endpoint():
    appmod = _app()
    c = appmod.server.test_client()
    assert c.get("/api/debug/threads").status_code == 403
    os.environ["CACHE_UPLOAD_TOKEN"] = "tok"
    try:
        assert c.get("/api/debug/threads", headers={"X-Upload-Token": "nope"}).status_code == 401
        r = c.get("/api/debug/threads", headers={"X-Upload-Token": "tok"})
        text = r.get_data(as_text=True)
        assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
        assert "MainThread" in text and "requests running" in text and "NOAA readers" in text
        assert "debug_threads" in text   # its own stack is listed
    finally:
        os.environ.pop("CACHE_UPLOAD_TOKEN", None)


def test_watchdog_logs_a_slow_request():
    appmod = _app()
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, daemon=True)
    t.start()
    records = []
    h = logging.Handler()
    h.emit = lambda rec: records.append(rec.getMessage())
    logging.getLogger("app").addHandler(h)
    real = appmod.SLOW_REQUEST_S
    level = logging.getLogger("app").level
    logging.getLogger("app").setLevel(logging.WARNING)
    try:
        appmod.SLOW_REQUEST_S = 0.5
        appmod._inflight[t.ident] = (time.monotonic() - 2, "GET /api/gfw/7/37/48.png")
        assert appmod._watchdog_check() == ["GET /api/gfw/7/37/48.png"]
        assert any("Slow request: GET /api/gfw/7/37/48.png" in m for m in records), records
        n = len(records)
        appmod._watchdog_check()           # logged once per request
        assert len(records) == n
    finally:
        appmod.SLOW_REQUEST_S = real
        appmod._inflight.pop(t.ident, None)
        appmod._inflight_reported.discard(t.ident)
        logging.getLogger("app").removeHandler(h)
        logging.getLogger("app").setLevel(level)
        stop.set()


def test_sigusr1_dumps_every_thread():
    if not hasattr(signal, "SIGUSR1"):
        return
    code = ("import os, signal, time, threading; import app; "
            "threading.Thread(target=time.sleep, args=(5,), name='sleeper', daemon=True).start(); "
            "os.kill(os.getpid(), signal.SIGUSR1); time.sleep(0.5); print('alive')")
    env = dict(os.environ, NETSIM="")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
    assert p.returncode == 0 and "alive" in p.stdout, p.stderr[-2000:]
    assert "most recent call first" in p.stderr and "Current thread 0x" in p.stderr, p.stderr[-2000:]
    assert p.stderr.count("Thread 0x") >= 1, p.stderr[-2000:]   # the other threads too


def main():
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    logging.basicConfig(level=logging.ERROR)
    failed = 0
    for name, fn in tests:
        t0 = time.monotonic()
        try:
            fn()
            print(f"ok    {name}  ({time.monotonic() - t0:.1f}s)")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
