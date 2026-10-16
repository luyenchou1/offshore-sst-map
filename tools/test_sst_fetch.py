"""Tests for the SST data path (data/erddap.py, data/cache.py, data/sstmeta.py and the fetch,
layer and cache routes in app.py) against a fake ERDDAP. No network, no pytest:

    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES .venv/bin/python tools/test_sst_fetch.py

ERDDAP is replaced by a scripted requests.get that serves real NetCDF-3 bytes, 503s, 403s,
404s (dead dataset / date not in dataset) and timeouts. Each test gets an empty cache dir.
"""

import gzip
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="sst-fetch-test-"))
os.environ["SST_CACHE_DIR"] = str(TMP / "cache")
os.environ["LABS_STATS_DIR"] = str(TMP / "stats")
os.environ["_SST_PREWARM_STARTED"] = "1"  # no startup pre-warm thread
for var in ("RENDER", "LABS_STATS_KEY", "LABS_HOST", "LABS_HOME_URL", "GFW_API_TOKEN",
            "CACHE_UPLOAD_TOKEN"):
    os.environ.pop(var, None)

import numpy as np  # noqa: E402
import requests  # noqa: E402
import xarray as xr  # noqa: E402

from data import cache as cachemod  # noqa: E402
from data import erddap  # noqa: E402
from data import sstmeta  # noqa: E402

CFG = json.load(open(ROOT / "config.json"))
LATEST = date(2026, 10, 15)
_n = [0]


# ---------------- fake ERDDAP ----------------

def _axis(lo, hi, step):
    n = int(round((hi - lo) / step)) + 1
    return np.round(lo + step * np.arange(n), 4)


MUR_LATS = _axis(38.80, 43.80, 0.05)   # coarser than MUR's 0.01 so tests stay fast
MUR_LONS = _axis(-74.96, -68.81, 0.05)
OI_LATS = _axis(38.875, 43.625, 0.25)
OI_LONS = _axis(-74.875, -68.875, 0.25)


def nc_bytes(var, days, hour, lats, lons, zlev=False, celsius=20.0):
    times = np.array([np.datetime64(f"{d}T{hour:02d}:00:00", "ns") for d in days])
    shape = (len(days), len(lats), len(lons))
    data = np.full(shape, celsius, dtype="float32") + np.linspace(0, 3, len(lons), dtype="float32")
    dims = ("time", "latitude", "longitude")
    coords = {"time": times, "latitude": lats, "longitude": lons}
    if zlev:
        data = data[:, None, :, :]
        dims = ("time", "zlev", "latitude", "longitude")
        coords["zlev"] = np.array([0.0])
    ds = xr.Dataset({var: (dims, data, {"units": "degree_C"})}, coords=coords)
    return ds.to_netcdf(engine="scipy")


class FakeResp:
    def __init__(self, status=200, body=b""):
        self.status_code = status
        self.body = body.encode() if isinstance(body, str) else bytes(body)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]


_DATE_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})Z\)")


def requested_days(url):
    """Days a griddap URL asks for, and whether the stop time is a whole-day range."""
    found = _DATE_RE.findall(url)
    ds = [date.fromisoformat(f[0]) for f in found[:2]]
    if len(ds) == 1:
        return [ds[0]]
    d, out = ds[0], []
    while d <= ds[1]:
        out.append(d)
        d += timedelta(days=1)
    return out


class FakeErddap:
    """Routes requests.get by dataset id. behave[dsid] is a function(url, n) -> FakeResp or raises,
    where n counts the calls for that dataset; the default serves every day that exists."""

    def __init__(self, latest=LATEST, final_last=date(2026, 10, 1), behave=None, gaps=()):
        self.latest = latest
        self.final_last = final_last
        self.behave = behave or {}
        self.gaps = set(gaps)
        self.calls = []
        self.counts = {}
        self.lock = threading.Lock()

    def last_of(self, dsid):
        return {"jplMURSST41": self.latest, "ncdcOisst21Agg_LonPM180": self.final_last,
                "ncdcOisst21NrtAgg_LonPM180": self.latest}.get(dsid)

    def __call__(self, url, headers=None, timeout=None, stream=False, **kw):
        if "/griddap/" not in url:
            raise requests.exceptions.ConnectionError(f"no network in tests: {url}")
        dsid = url.split("/griddap/")[1].split(".")[0]
        with self.lock:
            self.calls.append(url)
            n = self.counts[dsid] = self.counts.get(dsid, 0) + 1
        if dsid in self.behave:
            r = self.behave[dsid](url, n, timeout)
            if r is not None:
                return r
        return self.serve(dsid, url)

    def serve(self, dsid, url):
        if dsid not in ("jplMURSST41", "ncdcOisst21Agg_LonPM180", "ncdcOisst21NrtAgg_LonPM180"):
            return FakeResp(404, 'Error { message="Not Found: Currently unknown datasetID=%s"; }' % dsid)
        last = self.last_of(dsid)
        hour = 9 if dsid == "jplMURSST41" else 12
        if ".csv?time" in url:
            return FakeResp(200, f"time\nUTC\n{last}T{hour:02d}:00:00Z\n")
        if "23:59:59" in url and requested_days(url)[-1] >= last:
            return FakeResp(404, 'Error { message="Not Found: ... Stop is greater than the axis maximum"; }')
        days = [d for d in requested_days(url) if d <= last and d not in self.gaps]
        if not days:
            return FakeResp(404, 'Error { message="Not Found: Your query produced no matching results. '
                                 'Start is greater than the axis maximum"; }')
        if dsid == "jplMURSST41":
            return FakeResp(200, nc_bytes("analysed_sst", days, 9, MUR_LATS, MUR_LONS))
        return FakeResp(200, nc_bytes("sst", days, 12, OI_LATS, OI_LONS, zlev=True))


def install(fake):
    erddap.requests.get = fake
    erddap._reset_latest_cache()
    return fake


_real_get = requests.get


def fresh():
    """A new, empty cache dir for one test (the modules read CACHE_DIR at call time)."""
    _n[0] += 1
    d = TMP / f"c{_n[0]}" / "sst"
    d.mkdir(parents=True)
    cachemod.CACHE_DIR = d
    cachemod._index_cache["t"] = 0.0
    import app as appmod
    appmod._raw_data_cache.clear()
    appmod._refresh_tried.clear()
    return d


def week(end):
    return [end - timedelta(days=i) for i in range(6, -1, -1)]


def busy(url, n, timeout):
    return FakeResp(503, 'Error { code=503; message="Service Unavailable"; }')


def hang(url, n, timeout):
    t = min(timeout[1] if isinstance(timeout, tuple) else (timeout or 1), 20)
    time.sleep(t)
    raise requests.exceptions.ReadTimeout("read timed out")


# ---------------- erddap ----------------

def test_503_then_success_is_retried():
    fake = install(FakeErddap(behave={"jplMURSST41": lambda u, n, t: busy(u, n, t) if n <= 3 and ".nc?" in u else None}))
    t0 = time.monotonic()
    wk = erddap.get_sst_multiday(date(2014, 8, 20), CFG, budget_s=20)
    assert wk["source"] == "MUR" and len(wk["days"]) == 7 and wk["complete"], wk["missing_days"]
    assert [d["date"] for d in wk["days"]] == [str(d) for d in week(date(2014, 8, 20))]
    assert time.monotonic() - t0 < 20
    # call 1 is the time[last] lookup, so calls 2 and 3 (two days) got a 503 and were retried
    assert sum(1 for u in fake.calls if "jplMURSST41.nc" in u) == 9


def test_newest_day_and_clamp():
    fake = install(FakeErddap())
    wk = erddap.get_sst_multiday(LATEST + timedelta(days=3), CFG)
    assert wk["days"][-1]["date"] == str(LATEST), wk["days"][-1]["date"]
    assert wk["requested_end"] == str(LATEST + timedelta(days=3))
    assert wk["latest_available"] == str(LATEST) and len(wk["days"]) == 7
    # Exact stamps: never a whole-day range, which ERDDAP rejects on the newest day
    assert not any("23:59:59" in u for u in fake.calls)
    assert any(f"({LATEST}T09:00:00Z)" in u for u in fake.calls)


def test_403_falls_back_to_oisst_quickly():
    install(FakeErddap(behave={"jplMURSST41": lambda u, n, t: FakeResp(403, "Forbidden") if ".nc?" in u else None}))
    t0 = time.monotonic()
    wk = erddap.get_sst_multiday(date(2026, 10, 12), CFG)
    assert time.monotonic() - t0 < 5
    assert wk["source"] == "OISST" and wk["dataset_id"] == "ncdcOisst21NrtAgg_LonPM180"
    assert len(wk["days"]) == 7 and wk["days"][0]["arrF"].shape == (len(OI_LATS), len(OI_LONS))
    m = sstmeta.meta_from_fetch(wk)
    assert m["resolution_km"] == 25 and m["source_label"] == "OISST 25 km (NOAA NCEI)"


def test_dead_dataset_falls_back_to_oisst_final_and_nrt():
    install(FakeErddap(behave={"jplMURSST41": lambda u, n, t: FakeResp(404, 'message="Currently unknown datasetID=jplMURSST41"') if ".nc?" in u else None}))
    wk = erddap.get_sst_multiday(date(2026, 10, 4), CFG)  # spans the final dataset's last day (Oct 1)
    assert wk["source"] == "OISST" and len(wk["days"]) == 7
    assert [d["date"] for d in wk["days"]] == [str(d) for d in week(date(2026, 10, 4))]
    wk = erddap.get_sst_multiday(date(2015, 8, 20), CFG)
    assert wk["dataset_id"] == "ncdcOisst21Agg_LonPM180"


def test_timeouts_respect_the_budget_then_oisst():
    install(FakeErddap(behave={"jplMURSST41": lambda u, n, t: hang(u, n, t) if ".nc?" in u else None}))
    t0 = time.monotonic()
    wk = erddap.get_sst_multiday(date(2019, 2, 10), CFG, budget_s=9)
    el = time.monotonic() - t0
    assert el < 9.5, el
    assert wk["source"] == "OISST" and len(wk["days"]) == 7


def test_nothing_fails_within_budget_with_reason():
    install(FakeErddap(behave={"jplMURSST41": lambda u, n, t: busy(u, n, t) if ".nc?" in u else None,
                               "ncdcOisst21Agg_LonPM180": busy, "ncdcOisst21NrtAgg_LonPM180": busy}))
    t0 = time.monotonic()
    try:
        erddap.get_sst_multiday(date(2019, 2, 10), CFG, budget_s=8)
        raise AssertionError("expected SSTUnavailable")
    except erddap.SSTUnavailable as e:
        assert e.reason == "busy", e.reason
    assert time.monotonic() - t0 < 8.5


def test_partial_week_names_missing_days():
    bad = {"2014-08-16", "2014-08-17"}
    install(FakeErddap(behave={"jplMURSST41": lambda u, n, t: busy(u, n, t)
                               if any(f"({d}T" in u for d in bad) else None}))
    wk = erddap.get_sst_multiday(date(2014, 8, 20), CFG, budget_s=8)
    assert wk["source"] == "MUR" and len(wk["days"]) == 5
    assert wk["missing_days"] == sorted(bad) and wk["complete"] is False
    # The seeder never uploads a partial week
    try:
        erddap.get_sst_multiday(date(2014, 8, 20), CFG, budget_s=8, allow_fallback=False, require_full=True)
        raise AssertionError("expected SSTUnavailable")
    except erddap.SSTUnavailable:
        pass


def test_gap_day_is_complete():
    install(FakeErddap(gaps={date(2010, 3, 3)}))
    wk = erddap.get_sst_multiday(date(2010, 3, 5), CFG)
    assert len(wk["days"]) == 6 and wk["missing_days"] == ["2010-03-03"] and wk["complete"] is True


def test_before_mur_starts():
    install(FakeErddap())
    try:
        erddap.get_sst_multiday(date(2002, 5, 20), CFG)
        raise AssertionError("expected SSTUnavailable")
    except erddap.SSTUnavailable as e:
        assert e.reason == "no_data"


def test_latest_lookup_is_cached_and_survives_failure():
    fake = install(FakeErddap())
    assert erddap.latest_mur_date(CFG) == LATEST
    assert erddap.latest_mur_date(CFG) == LATEST
    assert sum(1 for u in fake.calls if ".csv?time" in u) == 1
    # A failure later keeps the last good answer
    erddap._latest[("https://coastwatch.pfeg.noaa.gov/erddap", "jplMURSST41")]["checked"] -= 7200
    fake.behave["jplMURSST41"] = busy
    assert erddap.latest_mur_date(CFG, max_wait=1.0) == LATEST


# ---------------- meta + cache ----------------

def test_meta_contract_fields():
    m = sstmeta.make_meta("MUR", "jplMURSST41", "https://x/erddap", ["2026-10-14", "2026-10-15"],
                          requested_end="2026-10-16", latest_available=LATEST)
    for k in sstmeta.FIELDS:
        assert k in m, k
    assert m["served_end"] == "2026-10-15" and m["requested_end"] == "2026-10-16"
    assert m["resolution_km"] == 1 and m["from_cache"] is False
    datetime.fromisoformat(m["built_at"].replace("Z", "+00:00"))


def test_old_entry_without_meta_loads_with_meta():
    d = fresh()
    entry = {"dates": [str(x) for x in week(date(2025, 7, 12))], "dataset_id": "jplMURSST41",
             "server": "https://coastwatch.pfeg.noaa.gov/erddap", "vmin": 60, "vmax": 75}
    p = d / "2025-07-12_adaptive.json.gz"
    with gzip.open(p, "wt") as f:
        json.dump(entry, f)
    os.utime(p, (1760000000, 1760000000))  # 2025-10-09: written long after the week, so final
    m = cachemod.entry_meta(cachemod.get_cached(date(2025, 7, 12), False), date(2025, 7, 12), False)
    assert m["source"] == "MUR" and len(m["days"]) == 7 and m["served_end"] == "2025-07-12"
    assert m["built_at"].startswith("2025-10-09"), m["built_at"]
    assert m["dataset_id"] == "jplMURSST41" and m["resolution_km"] == 1
    assert sstmeta.refresh_due(m) is False
    # fetched the day after the week ended: its newest days were preliminary, so it is refetched
    os.utime(p, (1752400000, 1752400000))  # 2025-07-13
    m = cachemod.entry_meta(cachemod.get_cached(date(2025, 7, 12), False), date(2025, 7, 12), False)
    assert m["preliminary_days"][-1] == "2025-07-12" and sstmeta.refresh_due(m) is True


def test_refresh_rules():
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)

    def meta(source="MUR", end="2026-10-15", built="2026-10-16T00:00:00Z", complete=True):
        return sstmeta.make_meta(source, "x", "y", [end], built_at=built, complete=complete)

    assert sstmeta.refresh_due(meta(built="2026-10-16T05:00:00Z"), now) is False  # 7 h old, preliminary
    assert sstmeta.refresh_due(meta(built="2026-10-15T23:00:00Z"), now) is True   # 13 h old, preliminary
    assert sstmeta.refresh_due(meta(end="2026-10-11", built="2026-10-15T23:00:00Z"), now) is False
    assert sstmeta.refresh_due(meta(end="2026-10-11", built="2026-10-15T11:00:00Z"), now) is True
    assert sstmeta.refresh_due(meta(end="2026-09-01", built="2026-09-20T00:00:00Z"), now) is False
    assert sstmeta.refresh_due(meta(source="OISST", end="2019-02-10", built="2026-10-16T07:00:00Z"), now) is False
    assert sstmeta.refresh_due(meta(source="OISST", end="2019-02-10", built="2026-10-16T05:00:00Z"), now) is True
    assert sstmeta.refresh_due(meta(end="2019-02-10", built="2026-10-16T05:00:00Z", complete=False), now) is True


def test_nearest_saved_and_queue():
    d = fresh()
    for name in ("2019-02-01", "2019-02-14", "2019-03-30", "2018-12-01"):
        (d / f"{name}_adaptive.json.gz").write_bytes(b"x")
    (d / "2019-02-14_locked.json.gz").write_bytes(b"x")
    near = cachemod.nearest_saved(date(2019, 2, 10), n=3)
    assert near == [date(2019, 2, 14), date(2019, 2, 1), date(2019, 3, 30)], near
    assert cachemod.queue_week(date(2019, 2, 10)) and cachemod.queue_week(date(2019, 2, 10))
    assert cachemod.wanted_weeks() == ["2019-02-10"]
    cachemod.unqueue_week(date(2019, 2, 10))
    assert cachemod.wanted_weeks() == []


# ---------------- app: the load path ----------------

def _app():
    import app as appmod
    import tools.sync_cache as sc
    appmod.mask_land_rasterized = lambda arr, lats, lons: arr  # no Natural Earth download
    sc.mask_land_rasterized = lambda arr, lats, lons: arr
    return appmod


def test_app_fresh_fetch_meta_and_cache_hit():
    appmod = _app()
    fresh()
    install(FakeErddap())
    w = appmod._load_week(date(2014, 8, 20), False, LATEST)
    m = w["meta_out"]
    assert m["source"] == "MUR" and m["from_cache"] is False and m["week_match"] == "exact"
    assert m["requested_end"] == "2014-08-20" and m["served_end"] == "2014-08-20"
    assert m["latest_available"] == str(LATEST) and len(m["days"]) == 7
    assert w["store"]["meta"] is m and len(w["store"]["frames"]) == 7
    # half a cell beyond the first/last centres
    (s, wv), (n, e) = w["store"]["bounds"]
    assert abs(s - (38.80 - 0.025)) < 1e-6 and abs(e - (MUR_LONS[-1] + 0.025)) < 1e-6
    appmod._write_week(w, False)
    appmod._raw_data_cache.clear()
    w2 = appmod._load_week(date(2014, 8, 20), False, LATEST)
    m2 = w2["meta_out"]
    assert m2["from_cache"] is True and m2["built_at"] == m["built_at"] and m2["days"] == m["days"]
    # the other color mode is rebuilt from the same week, meta intact
    w3 = appmod._load_week(date(2014, 8, 20), True, LATEST)
    assert w3["meta_out"]["source"] == "MUR" and w3["write"] == "cross" and w3["store"]["vmin"] == 30.0


def test_app_requested_after_newest_day():
    appmod = _app()
    fresh()
    install(FakeErddap())
    w = appmod._load_week(LATEST + timedelta(days=1), False, LATEST)
    m = w["meta_out"]
    assert w["end"] == LATEST and m["week_match"] == "newest"
    assert m["served_end"] == str(LATEST) and m["requested_end"] == str(LATEST + timedelta(days=1))
    assert m["preliminary_days"][-1] == str(LATEST)


def test_app_closest_saved_week_for_old_dates():
    appmod = _app()
    fresh()
    fake = install(FakeErddap())
    w = appmod._load_week(date(2015, 7, 8), False, LATEST)
    appmod._write_week(w, False)
    appmod._raw_data_cache.clear()
    n_calls = len(fake.calls)
    w = appmod._load_week(date(2015, 7, 9), False, LATEST)
    assert w["end"] == date(2015, 7, 8) and w["meta_out"]["week_match"] == "closest_saved"
    assert w["meta_out"]["requested_end"] == "2015-07-09" and len(fake.calls) == n_calls


def test_app_honest_failure_offers_saved_weeks_and_queues():
    appmod = _app()
    d = fresh()
    install(FakeErddap(behave={"jplMURSST41": busy, "ncdcOisst21Agg_LonPM180": busy,
                               "ncdcOisst21NrtAgg_LonPM180": busy}))
    for name in ("2019-01-20", "2019-03-01"):
        (d / f"{name}_adaptive.json.gz").write_bytes(b"x")
    appmod.CFG["erddap"]["budget_s"] = 7
    try:
        t0 = time.monotonic()
        try:
            appmod._load_week(date(2019, 2, 10), False, LATEST)
            raise AssertionError("expected SSTUnavailable")
        except erddap.SSTUnavailable as e:
            assert time.monotonic() - t0 < 8
            assert e.reason == "busy" and e.queued is True
            msg = appmod._failure_message(e, date(2019, 2, 10))
    finally:
        appmod.CFG["erddap"]["budget_s"] = 22
    text = json.dumps(msg.to_plotly_json(), default=str)
    assert "busy" in text and "within a day" in text and "may not be available" not in text
    assert "saved-week" in text and "2019-01-20" in text and "2019-03-01" in text
    assert "—" not in text  # no em dashes in copy
    assert cachemod.wanted_weeks() == ["2019-02-10"]


def test_app_oisst_week_is_saved_retried_and_queued():
    appmod = _app()
    fresh()
    install(FakeErddap(behave={"jplMURSST41": lambda u, n, t: FakeResp(403, "no") if ".nc?" in u else None}))
    w = appmod._load_week(date(2026, 10, 12), False, LATEST)
    m = w["meta_out"]
    assert m["source"] == "OISST" and m["resolution_km"] == 25 and m["queued"] is True
    assert m["dataset_id"] == "ncdcOisst21NrtAgg_LonPM180" and len(w["store"]["frames"]) == 7
    status = json.dumps(appmod._status_line(m).to_plotly_json(), default=str)
    assert "OISST 25 km" in status and "broad pattern" in status
    appmod._write_week(w, False)
    appmod._raw_data_cache.clear()
    # Six hours later MUR is back: the saved OISST week is replaced by MUR
    p = cachemod.CACHE_DIR / "2026-10-12_adaptive.json.gz"
    entry = json.load(gzip.open(p, "rt"))
    entry["meta"]["built_at"] = sstmeta.utc_iso(time.time() - 7 * 3600)
    with gzip.open(p, "wt") as f:
        json.dump(entry, f)
    install(FakeErddap())
    w = appmod._load_week(date(2026, 10, 12), False, LATEST)
    assert w["meta_out"]["source"] == "MUR" and w["meta_out"]["from_cache"] is False


def test_app_due_refresh_failure_serves_saved_copy():
    appmod = _app()
    fresh()
    install(FakeErddap())
    w = appmod._load_week(date(2026, 10, 14), False, LATEST)
    appmod._write_week(w, False)
    appmod._raw_data_cache.clear()
    p = cachemod.CACHE_DIR / "2026-10-14_adaptive.json.gz"
    entry = json.load(gzip.open(p, "rt"))
    entry["meta"]["built_at"] = sstmeta.utc_iso(time.time() - 13 * 3600)  # preliminary, 13 h old
    with gzip.open(p, "wt") as f:
        json.dump(entry, f)
    install(FakeErddap(behave={"jplMURSST41": busy, "ncdcOisst21Agg_LonPM180": busy,
                               "ncdcOisst21NrtAgg_LonPM180": busy}))
    t0 = time.monotonic()
    w = appmod._load_week(date(2026, 10, 14), False, LATEST)
    assert time.monotonic() - t0 < appmod._REFRESH_BUDGET_S + 1
    assert w["meta_out"]["week_match"] == "older_copy" and w["meta_out"]["from_cache"] is True
    # and isn't retried again for half an hour
    fake = install(FakeErddap())
    appmod._raw_data_cache.clear()
    w = appmod._load_week(date(2026, 10, 14), False, LATEST)
    assert not any(".nc?" in u for u in fake.calls)


# ---------------- app: routes and layers ----------------

def test_gfw_proxy_reads_range_per_request():
    appmod = _app()
    os.environ["GFW_API_TOKEN"] = "test"
    seen = []

    class R:
        def __init__(self, status, content=b"", js=None):
            self.status_code, self.content, self._js = status, content, js
            self.headers = {"Content-Type": "image/png"}

        def json(self):
            return self._js

        def raise_for_status(self):
            pass

    def post(url, params=None, headers=None, timeout=None):
        seen.append(("style", params["date-range"]))
        return R(200, js={"url": "https://gateway.api.globalfishingwatch.org/t/{z}/{x}/{y}?dr=" + params["date-range"]})

    def get(url, headers=None, timeout=None):
        seen.append(("tile", url))
        return R(404) if url.endswith("2026-10-01,2026-10-07") else R(200, b"PNGDATA")

    real_post, real_get = appmod.http_requests.post, appmod.http_requests.get
    appmod.http_requests.post, appmod.http_requests.get = post, get
    appmod._gfw_style_cache.clear()
    appmod._gfw_tile_cache.clear()
    try:
        c = appmod.server.test_client()
        a = c.get("/api/gfw/7/37/48.png?dr=2026-10-09,2026-10-15")
        b = c.get("/api/gfw/7/37/48.png?dr=2026-10-01,2026-10-07")
        assert a.status_code == 200 and a.data == b"PNGDATA"
        assert b.status_code == 200 and b.data == appmod._EMPTY_PNG  # empty tile: transparent, no 404
        assert ("style", "2026-10-09,2026-10-15") in seen and ("style", "2026-10-01,2026-10-07") in seen
        a2 = c.get("/api/gfw/7/37/48.png?dr=2026-10-09,2026-10-15")
        assert a2.data == b"PNGDATA" and len([s for s in seen if s[0] == "tile"]) == 2  # cached per range
        assert c.get("/api/gfw/7/37/48.png?dr=2026-10-15,2026-10-09").status_code == 400
        assert c.get("/api/gfw/7/37/48.png?dr=../../etc").status_code == 400
        assert c.get("/api/gfw/7/37/48.png?dr=2026-01-01,2026-06-01").status_code == 400
        assert not hasattr(appmod, "_gfw_date_range")
    finally:
        appmod.http_requests.post, appmod.http_requests.get = real_post, real_get
        os.environ.pop("GFW_API_TOKEN", None)


def test_layers_exist_only_when_on():
    import types
    appmod = _app()
    fn = getattr(appmod.toggle_layers, "__wrapped__", appmod.toggle_layers)
    real_ctx = appmod.ctx
    try:
        appmod.ctx = types.SimpleNamespace(triggered_id="layer-toggles")
        assert fn([], "2026-10-09,2026-10-15") == ([], [], [])
        on = fn(["gfw", "gebco"], "2026-10-09,2026-10-15")
        assert on[0] == [] and on[1][0].id == "gebco-layer" and on[2][0].id == "gfw-layer"
        assert on[2][0].url.endswith("?dr=2026-10-09,2026-10-15")
        appmod.ctx = types.SimpleNamespace(triggered_id="gfw-range")  # a new week
        nw = fn(["gfw", "gebco"], "2026-10-02,2026-10-08")
        assert nw[0] is appmod.dash.no_update and nw[1] is appmod.dash.no_update
        assert nw[2][0].url.endswith("?dr=2026-10-02,2026-10-08")
    finally:
        appmod.ctx = real_ctx
    from layout.mapview import GFW_NOTE, build_map
    assert "Global Fishing Watch" in GFW_NOTE and "—" not in GFW_NOTE
    layout = json.dumps(build_map().to_plotly_json(), default=str)
    assert "wms.gebco.net" not in layout and "/api/gfw/" not in layout and "charttools" not in layout


def test_wanted_route_locked_and_upload_unqueues():
    appmod = _app()
    fresh()
    c = appmod.server.test_client()
    assert c.get("/api/cache/wanted").status_code == 403
    os.environ["CACHE_UPLOAD_TOKEN"] = "tok"
    try:
        assert c.get("/api/cache/wanted").status_code == 401
        cachemod.queue_week(date(2019, 2, 10))
        r = c.get("/api/cache/wanted", headers={"X-Upload-Token": "tok"})
        assert r.status_code == 200 and r.get_json() == {"weeks": ["2019-02-10"]}
        install(FakeErddap())
        sst = erddap.get_sst_multiday(date(2019, 2, 10), CFG)
        from tools.sync_cache import build_raw_payload
        payload = build_raw_payload(sst, CFG)
        assert payload["meta"]["source"] == "MUR" and payload["meta"]["days"][-1] == "2019-02-10"
        blob = gzip.compress(json.dumps(payload).encode())
        r = c.post("/api/cache/upload", data=blob, headers={
            "X-Upload-Token": "tok", "X-Cache-Filename": "2019-02-10_adaptive.json.gz"})
        assert r.status_code == 200, r.data
        assert cachemod.wanted_weeks() == []
        # precache is still locked
        assert c.get("/api/precache").status_code == 401
    finally:
        os.environ.pop("CACHE_UPLOAD_TOKEN", None)


def test_sidebar_opens_on_newest_day():
    install(FakeErddap())
    erddap.latest_mur_date(CFG)  # learned (the page never waits for it)
    from layout.sidebar import build_sidebar

    def find(node, cid):
        if getattr(node, "id", None) == cid:
            return node
        kids = getattr(node, "children", None)
        for k in (kids if isinstance(kids, (list, tuple)) else [kids] if kids is not None else []):
            hit = find(k, cid)
            if hit is not None:
                return hit
        return None

    dp = find(build_sidebar(), "end-date-picker")
    assert dp.date == LATEST and dp.max_date_allowed == LATEST, (dp.date, dp.max_date_allowed)


def main():
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
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
    erddap.requests.get = _real_get
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
