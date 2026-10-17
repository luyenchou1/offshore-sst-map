"""Tests for the sprint 2 SST data format (data/sstgrid.py), the weekly cache (data/cache.py), the week's bytes route and
the seeder's backfill (tools/sync_cache.py). No network, no pytest:

    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES .venv/bin/python tools/test_sst_grid.py

NOAA and the live app are both replaced by one scripted requests.get / requests.post (data/outbound looks requests.get
up per call). Each test gets an empty cache dir. The browser side of the format (assets/sstengine.js decoding the same
bytes, drawing them where they belong) is tools/test_sstengine.mjs.
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
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="sst-grid-test-"))
os.environ["SST_CACHE_DIR"] = str(TMP / "cache")
os.environ["LABS_STATS_DIR"] = str(TMP / "stats")
os.environ["_SST_PREWARM_STARTED"] = "1"
for var in ("RENDER", "LABS_STATS_KEY", "LABS_HOST", "LABS_HOME_URL", "GFW_API_TOKEN", "CACHE_UPLOAD_TOKEN",
            "SST_CACHE_MAX_MB"):
    os.environ.pop(var, None)

import numpy as np  # noqa: E402
import requests  # noqa: E402
import xarray as xr  # noqa: E402

from data import cache as cachemod  # noqa: E402
from data import erddap, geo, outbound, sstgrid, sstmeta  # noqa: E402

CFG = json.load(open(ROOT / "config.json"))
LATEST = date(2026, 10, 15)
_n = [0]
_real_get, _real_post = requests.get, requests.post
geo.mask_land_rasterized = lambda arr, lats, lons: arr        # no Natural Earth download in tests


def fresh():
    _n[0] += 1
    d = TMP / f"c{_n[0]}" / "sst"
    d.mkdir(parents=True)
    cachemod.CACHE_DIR = d
    cachemod._index_cache["t"] = 0.0
    return d


def field(ny, nx, seed=1, lat0=44.0, lat1=37.8, lon0=-76.0, lon1=-64.8, smooth=6):
    """A MUR-like field: smooth large-scale pattern plus small eddies, 0.1 F, with land in the north-west corner."""
    from scipy.ndimage import gaussian_filter
    rng = np.random.default_rng(seed)
    lats = np.linspace(lat0, lat1, ny)
    lons = np.linspace(lon0, lon1, nx)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    f = 50 + 30 * (44 - la) / 6.2 + 3 * np.sin(lo * 1.7) + gaussian_filter(rng.normal(0, 40, (ny, nx)), smooth)
    f = np.round(f, 1)
    f[(la > 40.6 + (lo + 74) * 0.5) & (lo < -70)] = np.nan
    return f.astype(np.float32), lats, lons


# ---------------- the format ----------------

def test_round_trip_is_exact():
    f, lats, lons = field(120, 200)
    days = [f + 0.1 * k for k in range(7)]
    days[3] = days[3].copy()
    days[3][40:50, 150:170] = np.nan                         # water with no value on one day (254)
    days[2] = days[2].copy()
    days[2][60, 100] = days[2][60, 99] + 30.0                # a 30 F jump: escaped residuals
    days[2][61, 0] = 29.0                                    # column 0 jump against the row above
    blob = sstgrid.pack_week(days, lats, lons, level=1)
    w = sstgrid.unpack_week(blob)
    h = w["header"]
    assert (h["nx"], h["ny"], h["nd"]) == (200, 120, 7) and not h["whole"]
    assert abs(h["lat_n"] - 44.0) < 1e-5 and abs(h["lon_e"] + 64.8) < 1e-5
    for k, d in enumerate(days):
        q, v = sstgrid.quantize(d)
        assert np.array_equal(v, w["valid"][k]), k
        assert np.array_equal(q[v], w["codes"][k][v]), k
        back = sstgrid.codes_to_f(w["codes"][k], w["valid"][k])
        assert np.nanmax(np.abs(back - np.round(d, 1))) < 0.051, k
    water = np.zeros(f.shape, bool)
    for d in days:
        water |= np.isfinite(d)
    assert np.array_equal(w["mask"] == sstgrid.WATER, water)
    assert np.all(w["mask"][~water] == sstgrid.LAND)
    assert not w["valid"][3][45, 160] and w["mask"][45, 160] == sstgrid.WATER   # missing today, water in the week
    (s, wb), (n, e) = sstgrid.bounds_of(h)
    dlat = (44.0 - 37.8) / 119
    assert abs(n - (44.0 + dlat / 2)) < 1e-5 and abs(s - (37.8 - dlat / 2)) < 1e-5


def test_codes_clip_and_whole_degrees():
    lats, lons = np.array([41.0, 40.99]), np.array([-71.0, -70.99, -70.98])
    a = np.array([[20.0, 28.0, 100.26], [np.nan, 71.34, 71.36]], dtype=np.float64)
    q, v = sstgrid.quantize(a)
    assert q[0, 0] == 0 and q[0, 1] == 0 and q[0, 2] == 723 and q[1, 1] == 433 and q[1, 2] == 434 and not v[1, 0]
    w = sstgrid.unpack_week(sstgrid.pack_week([a], lats, lons))
    assert sstgrid.codes_to_f(w["codes"][0], w["valid"][0])[1, 1] == np.float32(71.3)
    whole = sstgrid.unpack_week(sstgrid.pack_week([np.round(a)], lats, lons))
    assert whole["header"]["whole"] and not w["header"]["whole"]
    empty = sstgrid.unpack_week(sstgrid.pack_week([np.full((2, 3), np.nan)], lats, lons))
    assert not empty["valid"][0].any() and np.all(empty["mask"] == sstgrid.LAND)
    for bad in (np.array([40.0, 41.0]), np.array([41.0, 40.9, 40.5])):    # south first; uneven
        try:
            sstgrid.pack_week([np.zeros((len(bad), 3))], bad, lons)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_auto_range_rule():
    f, lats, lons = field(80, 120)
    days = [f + k * 0.2 for k in range(7)]
    w = sstgrid.unpack_week(sstgrid.pack_week(days, lats, lons))
    hist = sstgrid.code_histogram(w["codes"], w["valid"])
    lo, hi = sstgrid.auto_range(hist)
    vals = np.concatenate([np.round(d[np.isfinite(d)], 1) for d in days])
    p5, p95 = np.sort(vals)[int(np.ceil(0.05 * len(vals))) - 1], np.sort(vals)[int(np.ceil(0.95 * len(vals))) - 1]
    assert lo == np.floor(p5 + 1e-6) and hi == np.ceil(p95 - 1e-6), (lo, hi, p5, p95)
    assert lo == int(lo) and hi - lo >= sstgrid.AUTO_MIN_SPAN
    flat = np.full((10, 10), 50.0)                            # uniform winter water: at least 8 F
    hw = sstgrid.unpack_week(sstgrid.pack_week([flat], np.linspace(42, 41, 10), np.linspace(-71, -70, 10)))
    assert sstgrid.auto_range(sstgrid.code_histogram(hw["codes"], hw["valid"])) == (46.0, 54.0)
    assert sstgrid.auto_range(np.zeros(sstgrid.MAX_CODE + 1, np.int64)) == sstgrid.LOCKED_RANGE


def test_week_size_for_the_coverage_box():
    """A week over the whole coverage box (1121 x 621 at 0.01 deg, about 30% land) stays well under 1 MB. Measured on
    real MUR for Oct 9 to 15, 2026: 588 KB at gzip -9 (the seeder), 641 KB at -6 (built on Render)."""
    from scipy.ndimage import gaussian_filter
    f, lats, lons = field(621, 1121, seed=7, smooth=25)
    rng = np.random.default_rng(3)
    # day-to-day changes as smooth as MUR's (a few hundredths of a degree per cell): real weeks are checked below
    days = [np.round(f + gaussian_filter(rng.normal(0, 8, f.shape), 10).astype(np.float32) + 0.2 * k, 1)
            for k in range(7)]
    t0 = time.monotonic()
    blob = sstgrid.pack_week(days, lats, lons, level=6)
    enc = time.monotonic() - t0
    t0 = time.monotonic()
    sstgrid.unpack_week(blob)
    dec = time.monotonic() - t0
    print(f"      synthetic coverage-box week: {len(blob) / 1024:.0f} KB at -6 (encode {enc:.2f}s, decode {dec:.2f}s)")
    assert len(blob) < 1024 * 1024
    weeks = Path(os.environ.get("SST_REAL_CACHE", ROOT / "cache")) / "sst" / "weeks"
    for p in sorted(weeks.glob("*_week.json.gz"))[-3:] if weeks.is_dir() else []:
        e = json.load(gzip.open(p))
        print(f"      {p.name}: grid {e['grid_bytes'] / 1024:.0f} KB, file {p.stat().st_size / 1024:.0f} KB")
        assert e["grid_bytes"] < 1024 * 1024


def test_old_entries_all_encodings():
    """Entries from before sprint 2: v1 JSON lists, v2 base64 .npy, rows south first or north first; either variant."""
    import base64
    import io
    f, lats, lons = field(50, 70, lat0=43.8, lat1=38.8, lon0=-74.96, lon1=-68.8)
    whole = [np.round(f) + k for k in range(7)]

    def npy(a):
        buf = io.BytesIO()
        np.save(buf, a)
        return base64.b64encode(buf.getvalue()).decode()
    dates = [str(date(2025, 7, 6) + timedelta(days=k)) for k in range(7)]
    v2 = {"raw_days": [{"arrF": npy(d.astype(np.float64)), "date": dt} for d, dt in zip(whole, dates)],
          "lats": npy(lats), "lons": npy(lons), "dates": dates, "frames": None}
    v2_south = dict(v2, lats=npy(lats[::-1]),
                    raw_days=[{"arrF": npy(d[::-1].astype(np.float64)), "date": dt} for d, dt in zip(whole, dates)])
    v1 = {"raw_days": [{"arrF": d.tolist(), "date": dt} for d, dt in zip(whole, dates)],
          "lats": lats.tolist(), "lons": lons.tolist(), "dates": dates}
    for name, e in (("v2", v2), ("v2 south first", v2_south), ("v1", v1)):
        a = sstgrid.old_entry_arrays(e)
        assert a["lats"][0] > a["lats"][-1], name
        w = sstgrid.unpack_week(sstgrid.old_entry_blob(e, a))
        assert w["header"]["whole"] and w["header"]["ny"] == 50, name
        got = sstgrid.codes_to_f(w["codes"][4], w["valid"][4])
        assert np.array_equal(np.isnan(got), np.isnan(whole[4])) and np.nanmax(np.abs(got - whole[4])) == 0, name
        assert np.all(w["mask"][np.isnan(whole[4])] == sstgrid.NODATA), name


def test_entry_from_fetch_orients_and_keeps_meta():
    lats = np.round(np.arange(37.8, 44.0001, 0.1), 4)          # ERDDAP order: south first
    lons = np.round(np.arange(-76.0, -64.7999, 0.1), 4)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    days = [{"arrF": np.round(40 + la + 0.1 * k, 1).astype(np.float32), "date": str(LATEST - timedelta(days=6 - k))}
            for k in range(7)]
    sst = {"source": "MUR", "server": "s", "dataset_id": "jplMURSST41", "dataset_title": "t", "days": days,
           "lats": lats, "lons": lons, "requested_end": str(LATEST), "missing_days": [], "complete": True,
           "latest_available": str(LATEST)}
    e = sstgrid.entry_from_fetch(sst, CFG)
    assert e["coverage"] == CFG["coverage_id"] and e["dates"][-1] == str(LATEST) and e["meta"]["source"] == "MUR"
    w = sstgrid.unpack_week(sstgrid.entry_blob(e))
    arr = sstgrid.week_arrays(sstgrid.entry_blob(e), e["dates"], w)
    i = int(np.argmin(np.abs(arr["lats"] - 41.0)))
    assert arr["lats"][0] > arr["lats"][-1] and abs(arr["raw_days"][6]["arrF"][i, 5] - round(81.0 + 0.6, 1)) < 1e-4
    assert e["vmin"] < e["vmax"] and e["grid_bytes"] == len(sstgrid.entry_blob(e))


# ---------------- the weekly cache ----------------

def _entry(end, n=30, m=40):
    f, lats, lons = field(n, m)
    dates = [str(end - timedelta(days=6 - k)) for k in range(7)]
    meta = sstmeta.make_meta("MUR", "jplMURSST41", "s", dates, built_at="2025-01-01T00:00:00Z")
    return sstgrid.build_week([f] * 7, dates, lats, lons, meta=meta, coverage=CFG["coverage_id"], level=1)


def test_weekly_cache_index_and_old_entries():
    d = fresh()
    end = date(2024, 7, 20)
    assert cachemod.put_week(end, _entry(end))
    assert cachemod.get_week(end)["dates"][-1] == str(end)
    (d / "2024-07-13_adaptive.json.gz").write_bytes(b"x")
    (d / "2024-07-13_locked.json.gz").write_bytes(b"x")
    (d / "2024-07-20_adaptive.json.gz").write_bytes(b"x")    # superseded by the weekly file
    assert cachemod.weeks_index() == {"weeks": ["2024-07-20"], "old_only": ["2024-07-13"]}
    assert cachemod.saved_end_dates() == [date(2024, 7, 20), date(2024, 7, 13)]
    (cachemod.weeks_dir() / "2024-07-27_week.json.gz").write_bytes(b"not gzip")
    assert cachemod.get_week(date(2024, 7, 27)) is None
    rep = cachemod.disk_report()
    assert rep["weekly_files"] == 2 and rep["old_files"] == 3 and rep["budget_mb"] and rep["disk_mb"]


def test_eviction_superseded_first_then_least_recently_viewed():
    d = fresh()
    now = time.time()
    names = []
    for k in range(6):
        end = date(2024, 6, 1) + timedelta(days=7 * k)
        cachemod.put_week(end, _entry(end))
        names.append(cachemod.week_name(end))
    old_sup = d / "2024-06-01_adaptive.json.gz"              # superseded by a weekly file
    old_only = d / "2023-06-01_adaptive.json.gz"
    for p in (old_sup, old_only):
        p.write_bytes(os.urandom(20000))
    files = list(cachemod.weeks_dir().glob("*.json.gz")) + [old_sup, old_only]
    for k, p in enumerate(sorted(files)):                     # everything viewed long ago, in name order
        os.utime(p, (now - 86400 * (30 - k), now - 86400 * 40))
    viewed = cachemod.weeks_dir() / names[0]
    cachemod.touch_viewed(viewed)                             # the oldest week was just viewed
    st = viewed.stat()
    assert now - st.st_atime < 5 and now - st.st_mtime > 86400 * 39   # access time = last viewed; mtime kept
    total = sum(p.stat().st_size for p in files)
    os.environ["SST_CACHE_MAX_MB"] = str((total - 1) / 1048576)
    try:
        removed = cachemod.evict()
        assert removed == ["2024-06-01_adaptive.json.gz"], removed          # superseded first
        os.environ["SST_CACHE_MAX_MB"] = str((total - 40000 - 1) / 1048576)
        removed = cachemod.evict()
        # then the least recently viewed: the old-only entry (viewed 30 days ago), then the oldest-viewed weekly file;
        # the oldest week was just viewed, so it stays
        assert removed == ["2023-06-01_adaptive.json.gz", names[1]], removed
        os.environ["SST_CACHE_MAX_MB"] = "0.00001"
        left = cachemod.evict()
        assert viewed.exists() and names[0] not in left                   # viewed in the last 10 minutes
    finally:
        os.environ.pop("SST_CACHE_MAX_MB", None)


# ---------------- fake NOAA + fake live app (the seeder) ----------------

def _axis(lo, hi, step):
    return np.round(lo + step * np.arange(int(round((hi - lo) / step)) + 1), 4)


LATS = _axis(37.8, 44.0, 0.1)
LONS = _axis(-76.0, -64.8, 0.1)
_DATE_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})T")


def nc_day(day):
    times = np.array([np.datetime64(f"{day}T09:00:00", "ns")])
    la, lo = np.meshgrid(LATS, LONS, indexing="ij")
    data = (15 + 0.5 * (44 - la) + 0.1 * (date.fromisoformat(day).toordinal() % 7))[None].astype("float32")
    ds = xr.Dataset({"analysed_sst": (("time", "latitude", "longitude"), data, {"units": "degree_C"})},
                    coords={"time": times, "latitude": LATS, "longitude": LONS})
    return ds.to_netcdf(engine="scipy")


class Resp:
    def __init__(self, status=200, body=b"", js=None):
        self.status_code = status
        self.body = json.dumps(js).encode() if js is not None else (body.encode() if isinstance(body, str) else body)
        self.content = self.body
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body)

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]


class World:
    """NOAA (griddap) and the live app (/api/cache/*) behind one requests.get / requests.post."""

    def __init__(self, old_only=(), wanted=(), busy=None):
        self.old_only = [str(d) for d in old_only]
        self.weekly = []
        self.wanted = [str(d) for d in wanted]
        self.uploads = []
        self.days_asked = []
        self.busy = busy or (lambda day, n: False)            # (day, nth request for it) -> 503?
        self.count = {}
        self.lock = threading.Lock()

    def get(self, url, headers=None, timeout=None, stream=False, **kw):
        if "/griddap/" in url:
            if ".csv?time" in url:
                return Resp(200, f"time\nUTC\n{LATEST}T09:00:00Z\n")
            day = _DATE_RE.search(url).group(1)
            with self.lock:
                n = self.count[day] = self.count.get(day, 0) + 1
                self.days_asked.append(day)
            if self.busy(day, n):
                return Resp(503, 'Error { code=503; message="Service Unavailable"; }')
            if date.fromisoformat(day) > LATEST:
                return Resp(404, 'Error { message="Not Found: Start is greater than the axis maximum"; }')
            return Resp(200, nc_day(day))
        if url.endswith("/api/cache/wanted"):
            return Resp(200, js={"weeks": self.wanted})
        if url.endswith("/api/cache/weeks"):
            return Resp(200, js={"weeks": self.weekly, "old_only": self.old_only, "coverage": "x", "disk": {}})
        raise requests.exceptions.ConnectionError(f"no network in tests: {url}")

    def post(self, url, data=None, headers=None, timeout=None, **kw):
        assert url.endswith("/api/cache/upload") and headers["X-Upload-Token"] == "tok"
        fname = headers["X-Cache-Filename"]
        entry = json.loads(gzip.decompress(data))
        assert sstgrid.is_grid_entry(entry) and fname.endswith("_week.json.gz")
        end = fname[:10]
        with self.lock:
            self.uploads.append(end)
            if end in self.old_only:
                self.old_only.remove(end)
            if end not in self.weekly:
                self.weekly.append(end)
            if end in self.wanted:
                self.wanted.remove(end)
        return Resp(200, js={"ok": True, "file": fname})


def run_seeder(world, *argv):
    import tools.sync_cache as sc
    slept = []
    requests.get, requests.post = world.get, world.post
    sc.sleep = slept.append
    erddap._reset_latest_cache()
    outbound.reset_all()
    try:
        code = sc.main(["--token", "tok", "--base-url", "https://live.test", "--level", "1",
                        "--upload-delay", "2", *argv])
    finally:
        requests.get, requests.post = _real_get, _real_post
    return code, slept


def test_seeder_recent_wanted_then_backfill_resumable():
    old = [date(2025, 7, 12) - timedelta(days=7 * k) for k in range(5)]        # newest first: 07-12, 07-05, ...
    world = World(old_only=old, wanted=[date(2019, 2, 10)])
    code, slept = run_seeder(world, "--count", "3", "--backfill", "2", "--backfill-delay", "20")
    assert code == 0
    assert world.uploads == ["2026-10-15", "2026-10-14", "2026-10-13", "2019-02-10", "2025-07-12", "2025-07-05"]
    assert world.old_only == [str(d) for d in old[2:]] and world.wanted == []
    assert slept.count(20) == 1 and slept.count(2) == 6      # rate limit between backfill weeks; upload pauses
    # the three recent weeks share their days: 9 distinct days asked once each, not 21
    recent = [d for d in world.days_asked if d >= "2026-10-07"]
    assert len(recent) == len(set(recent)) == 9, recent
    # the next run takes up where this one stopped
    code, _ = run_seeder(world, "--count", "1", "--backfill", "2", "--no-wanted")
    assert code == 0 and world.uploads[-2:] == ["2025-06-28", "2025-06-21"]
    code, _ = run_seeder(world, "--count", "1", "--backfill", "5", "--no-wanted")
    assert code == 0 and world.old_only == [] and world.uploads[-1] == "2025-06-14"
    code, _ = run_seeder(world, "--count", "0", "--backfill", "5", "--no-wanted")
    assert code == 0 and world.uploads[-1] == "2025-06-14"   # nothing left: nothing fetched


def test_seeder_tolerates_503s_and_resumes():
    import tools.sync_cache as sc
    old = [date(2025, 7, 12), date(2025, 7, 5)]
    erddap.RETRY_BACKOFF = (0.01, 0.02)                     # the retry pauses themselves aren't under test
    try:
        # NOAA answers 503 twice for every day before succeeding: retried within the week's budget
        world = World(old_only=old, busy=lambda day, n: n <= 2)
        code, _ = run_seeder(world, "--count", "1", "--backfill", "2", "--no-wanted")
        assert code == 0 and world.old_only == [], world.old_only
        # A week NOAA keeps refusing: tried twice (with a pause), left for the next run; the run still succeeds
        world = World(old_only=old, busy=lambda day, n: "2025-07-06" <= day <= "2025-07-12")
        code, slept = run_seeder(world, "--count", "1", "--backfill", "2", "--no-wanted")
        assert code == 0 and world.old_only == ["2025-07-12"] and "2025-07-05" in world.uploads
        assert sc.RETRY_PAUSE_S in slept
        world.busy = lambda day, n: False
        code, _ = run_seeder(world, "--count", "0", "--backfill", "2", "--no-wanted")
        assert code == 0 and world.old_only == []
        # a recent week NOAA refuses does fail the run (the page's default week matters)
        world = World(busy=lambda day, n: day == "2026-10-15")
        code, _ = run_seeder(world, "--count", "1", "--no-wanted")
        assert code == 1 and world.uploads == []
    finally:
        erddap.RETRY_BACKOFF = (1.0, 2.5)


def test_seeder_time_cap_and_local_out():
    world = World(old_only=[date(2025, 7, 12), date(2025, 7, 5)])
    code, _ = run_seeder(world, "--count", "1", "--backfill", "2", "--no-wanted", "--max-minutes", "0.00001")
    assert code == 0 and world.uploads == ["2026-10-15"] and len(world.old_only) == 2
    out = TMP / "seed-out"
    code, _ = run_seeder(World(), "--dates", "2026-10-15", "--out", str(out))
    p = out / "sst" / "weeks" / "2026-10-15_week.json.gz"
    assert code == 0 and p.exists() and sstgrid.is_grid_entry(json.load(gzip.open(p)))


# ---------------- the app: the week's bytes never wait on NOAA ----------------

def test_week_route_never_asks_noaa():
    import app as appmod
    d = fresh()
    appmod._raw_data_cache.clear()
    appmod._blob_cache.clear()
    calls = []

    def nowhere(*a, **k):
        calls.append(a)
        raise requests.exceptions.ConnectionError("no network")
    requests.get = nowhere
    try:
        c = appmod.server.test_client()
        t0 = time.monotonic()
        assert c.get("/api/sst/week/2024-07-20.bin").status_code == 404
        end = date(2024, 7, 20)
        cachemod.put_week(end, _entry(end))
        r = c.get("/api/sst/week/2024-07-20.bin")
        assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip"
        assert r.headers["Cache-Control"].startswith("public") and r.headers["ETag"]
        assert sstgrid.unpack_week(r.data)["header"]["nd"] == 7
        assert time.monotonic() - t0 < 2 and not calls
        # an old entry of a week (no weekly file) is converted for the route, on its own grid
        (d / "2024-07-13_adaptive.json.gz").write_bytes(gzip.compress(json.dumps(
            {"dates": [str(date(2024, 7, 7) + timedelta(days=k)) for k in range(7)],
             "lats": [41.0, 40.99], "lons": [-71.0, -70.99],
             "raw_days": [{"arrF": [[60.0 + k, 61.0], [62.0, None]], "date": str(date(2024, 7, 7) + timedelta(days=k))}
                          for k in range(7)]}).encode()))
        r = c.get("/api/sst/week/2024-07-13.bin")
        wk = sstgrid.unpack_week(r.data)
        assert r.status_code == 200 and wk["header"]["whole"] and wk["header"]["nx"] == 2 and not calls
        assert sstgrid.codes_to_f(wk["codes"][3], wk["valid"][3])[0, 0] == 63.0 and not wk["valid"][3][1, 1]
    finally:
        requests.get = _real_get


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
    requests.get, requests.post = _real_get, _real_post
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
