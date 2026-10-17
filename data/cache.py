"""Disk-based cache for SST weeks, plus the list of weeks the seeder should fetch.

Since sprint 2 each week is ONE file, SST_CACHE_DIR/sst/weeks/<end>_week.json.gz (data/sstgrid: the 0.1 F grid over
the coverage box and the haze grid, "grid_format" 1). The color scale is chosen in the browser, so there is no
"adaptive" / "locked" pair any more. Entries saved before sprint 2 (SST_CACHE_DIR/sst/<end>_<adaptive|locked>.json.gz:
whole degrees over the old, smaller area) still load, either variant, and are served on their own bounds until the
seeder replaces the week (tools/sync_cache.py --backfill); they are never rewritten. Code from before sprint 2 globs
only SST_CACHE_DIR/sst/*.json.gz, so it never sees (or evicts) the weekly files: a rollback stays safe.
Entries carry a "meta" dict (data/sstmeta.py); old entries without one still load.

Freshness (sstmeta.refresh_due): a week fetched while some of its days were preliminary is
refetched after 12 h, one inside MUR's 7-day revision window after 24 h, an OISST or
incomplete week is retried for MUR after 6 h; anything else is final. A week that is due
is still served when the refetch fails.

Retention (evict): Render can't reach NOAA, so an evicted week only comes back through the seeder. The SST files may
use up to CACHE_MAX_FRACTION (70%) of the disk they're on (SST_CACHE_MAX_MB overrides); past that, old entries whose
week has a weekly file go first, then the least recently VIEWED (serving a week sets its file's access time,
touch_viewed). A small dedicated disk (<= 64 GB, like Render's 1 GB) also frees space when it is over 95% full. A file
viewed or written in the last 10 minutes is never evicted. MAX_ENTRIES is only a backstop on the count.
"""

import gzip
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from data import sstmeta

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("SST_CACHE_DIR", "./cache")) / "sst"
WEEKS_SUBDIR = "weeks"        # one file per week since sprint 2 (data/sstgrid)
MAX_ENTRIES = 4000            # backstop on the number of files; the disk budget below decides first
CACHE_MAX_FRACTION = 0.70     # SST files may use this share of their disk (SST_CACHE_MAX_MB overrides)
SMALL_DISK = 64 * 1024 ** 3   # a disk this small is the cache's own (Render's 1 GB): also keep 5% of it free
PROTECT_S = 600               # a file viewed or written this recently is never evicted
VIEW_TOUCH_S = 3600           # a view updates the file's access time at most this often
WANTED_FILE = "wanted.json"   # weeks the seeder should fetch (tools/sync_cache.py --wanted)
MAX_WANTED = 30
_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(adaptive|locked)\.json\.gz$")
_WEEK_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_week\.json\.gz$")
WEEK_NAME_RE = _WEEK_RE


def _ensure_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def cache_key(end_date: date, locked: bool) -> str:
    mode = "locked" if locked else "adaptive"
    return f"{end_date.isoformat()}_{mode}"


def _path_for(end_date: date, locked: bool) -> Path:
    return _ensure_dir() / f"{cache_key(end_date, locked)}.json.gz"


def entry_mtime(end_date: date, locked: bool) -> Optional[float]:
    try:
        return _path_for(end_date, locked).stat().st_mtime
    except OSError:
        return None


def get_cached(end_date: date, locked: bool) -> Optional[Dict]:
    """Read cached payload from disk.  Returns None on miss or error."""
    p = _path_for(end_date, locked)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        logger.info("Cache HIT: %s", p.name)
        return payload
    except Exception:
        logger.warning("Cache read failed for %s, treating as miss", p.name, exc_info=True)
        return None


def entry_meta(entry: Dict, end_date: date, locked: bool) -> Dict:
    """The entry's meta (filled in for old entries from its fields and file time)."""
    return sstmeta.meta_for_entry(entry, mtime=entry_mtime(end_date, locked))


def put_cache(end_date: date, locked: bool, payload: Dict) -> None:
    """Write payload to cache using atomic write (tmp + rename)."""
    _ensure_dir()
    target = _path_for(end_date, locked)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".tmp")
        os.close(fd)
        with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, str(target))
        size_kb = target.stat().st_size / 1024
        logger.info("Cache WRITE: %s (%.0f KB)", target.name, size_kb)
        _index_cache["t"] = 0.0
        evict(protect={target})
    except Exception:
        logger.warning("Cache write failed for %s", target.name, exc_info=True)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ---- One file per week (sprint 2) ----

def weeks_dir() -> Path:
    d = CACHE_DIR / WEEKS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def week_name(end_date: date) -> str:
    return f"{end_date.isoformat()}_week.json.gz"


def week_path(end_date: date) -> Path:
    return weeks_dir() / week_name(end_date)


def week_mtime(end_date: date) -> Optional[float]:
    try:
        return week_path(end_date).stat().st_mtime
    except OSError:
        return None


def get_week(end_date: date) -> Optional[Dict]:
    """The weekly entry (grid format) ending end_date, or None (missing or unreadable)."""
    p = week_path(end_date)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            entry = json.load(f)
        if entry.get("grid_format") is None or not isinstance(entry.get("grid"), str):
            raise ValueError("not a weekly grid entry")
        logger.info("Cache HIT: weeks/%s", p.name)
        return entry
    except Exception:
        logger.warning("Cache read failed for weeks/%s, treating as miss", p.name, exc_info=True)
        return None


def week_meta(entry: Dict, end_date: date) -> Dict:
    return sstmeta.meta_for_entry(entry, mtime=week_mtime(end_date))


def _atomic_write(target: Path, write) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    os.close(fd)
    try:
        write(tmp_path)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def put_week(end_date: date, entry: Dict) -> bool:
    """Write a weekly entry (atomic), then keep the cache inside its disk budget."""
    target = week_path(end_date)
    try:
        def write(tmp):
            with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
                json.dump(entry, f)
        _atomic_write(target, write)
        logger.info("Cache WRITE: weeks/%s (%.0f KB)", target.name, target.stat().st_size / 1024)
    except Exception:
        logger.warning("Cache write failed for weeks/%s", target.name, exc_info=True)
        return False
    _index_cache["t"] = 0.0
    evict(protect={target})
    return True


def put_week_file(end_date: date, data: bytes) -> Path:
    """Store an uploaded weekly file (already gzip JSON, validated by the caller) as it is."""
    target = week_path(end_date)

    def write(tmp):
        with open(tmp, "wb") as f:
            f.write(data)
    _atomic_write(target, write)
    _index_cache["t"] = 0.0
    evict(protect={target})
    return target


def touch_viewed(path: Optional[Path]) -> None:
    """Note that a week was just served: its file's access time is "last viewed" for eviction (set explicitly, so
    it works on noatime mounts; the modification time, which old entries' meta falls back on, is kept)."""
    if path is None:
        return
    try:
        st = path.stat()
        now = time.time()
        if now - st.st_atime > VIEW_TOUCH_S:
            os.utime(path, (now, st.st_mtime))
    except OSError:
        pass


def old_path(end_date: date, locked: bool) -> Path:
    return _path_for(end_date, locked)


def weeks_index() -> Dict:
    """What's saved, for the seeder's backfill (GET /api/cache/weeks): end dates with a weekly file, and end dates
    with only an entry from before sprint 2 (old coverage, whole degrees)."""
    weekly, old = set(), set()
    try:
        for p in weeks_dir().glob("*_week.json.gz"):
            m = _WEEK_RE.match(p.name)
            if m:
                weekly.add(m.group(1))
        for p in CACHE_DIR.glob("*.json.gz"):
            m = _NAME_RE.match(p.name)
            if m:
                old.add(m.group(1))
    except OSError:
        pass
    return {"weeks": sorted(weekly, reverse=True), "old_only": sorted(old - weekly, reverse=True)}


def _sst_files() -> List[Tuple[Path, os.stat_result, str, Optional[str]]]:
    """(path, stat, kind "week" | "old", end ISO) for every SST cache file."""
    out = []
    for d, rx, kind in ((CACHE_DIR, _NAME_RE, "old"), (CACHE_DIR / WEEKS_SUBDIR, _WEEK_RE, "week")):
        try:
            for p in d.glob("*.json.gz"):
                m = rx.match(p.name)
                try:
                    out.append((p, p.stat(), kind, m.group(1) if m else None))
                except OSError:
                    pass
        except OSError:
            pass
    return out


def budget_bytes() -> Optional[int]:
    env = os.environ.get("SST_CACHE_MAX_MB", "").strip()
    if env:
        try:
            return int(float(env) * 1024 * 1024)
        except ValueError:
            pass
    try:
        return int(shutil.disk_usage(_ensure_dir()).total * CACHE_MAX_FRACTION)
    except OSError:
        return None


def disk_report() -> Dict:
    """Numbers for the startup log, /api/debug/threads and /api/cache/weeks: files, bytes, the disk and the budget."""
    files = _sst_files()
    rep = {"weekly_files": sum(1 for f in files if f[2] == "week"),
           "weekly_mb": round(sum(f[1].st_size for f in files if f[2] == "week") / 1048576, 1),
           "old_files": sum(1 for f in files if f[2] == "old"),
           "old_mb": round(sum(f[1].st_size for f in files if f[2] == "old") / 1048576, 1)}
    try:
        du = shutil.disk_usage(_ensure_dir())
        rep.update(disk_mb=round(du.total / 1048576), disk_used_mb=round(du.used / 1048576),
                   disk_used_pct=round(100.0 * du.used / du.total, 1) if du.total else None)
    except OSError:
        pass
    b = budget_bytes()
    rep["budget_mb"] = round(b / 1048576) if b else None
    return rep


def evict(protect=(), max_entries: int = MAX_ENTRIES) -> List[str]:
    """Keep the SST files inside their budget (module doc). Returns the names removed."""
    try:
        files = _sst_files()
        if not files:
            return []
        total = sum(f[1].st_size for f in files)
        budget = budget_bytes()
        need_bytes = total - budget if budget else 0
        try:
            du = shutil.disk_usage(_ensure_dir())
            if du.total <= SMALL_DISK and du.free < 0.05 * du.total:
                need_bytes = max(need_bytes, int(0.10 * du.total) - du.free)
        except OSError:
            pass
        need_count = len(files) - max_entries
        if need_bytes <= 0 and need_count <= 0:
            return []
        weekly = {f[3] for f in files if f[2] == "week"}
        now = time.time()
        keep = {Path(p) for p in protect}
        cands = [f for f in files if f[0] not in keep and now - max(f[1].st_atime, f[1].st_mtime) > PROTECT_S]
        # superseded old entries first, then least recently viewed
        cands.sort(key=lambda f: (not (f[2] == "old" and f[3] in weekly), f[1].st_atime))
        removed = []
        for p, st, kind, _ in cands:
            if need_bytes <= 0 and need_count <= 0:
                break
            try:
                p.unlink()
            except OSError:
                continue
            need_bytes -= st.st_size
            need_count -= 1
            removed.append(p.name)
            logger.info("Cache EVICT: %s%s (last viewed %s)", "weeks/" if kind == "week" else "", p.name,
                        time.strftime("%Y-%m-%d", time.gmtime(st.st_atime)))
        if removed:
            _index_cache["t"] = 0.0
        return removed
    except Exception:
        logger.warning("Cache eviction failed", exc_info=True)
        return []


def is_stale(end_date: date) -> bool:
    """True when the saved week ending end_date should be refetched (see module doc)."""
    for locked in (False, True):
        entry = get_cached(end_date, locked)
        if entry:
            return sstmeta.refresh_due(entry_meta(entry, end_date, locked))
    return True  # nothing saved


def find_nearest_cached(end_date: date, locked: bool, max_offset: int = 3,
                        allow_stale: bool = False) -> Tuple[Optional[Dict], Optional[date]]:
    """Find the nearest cached entry within ±max_offset days.

    Returns (cached_payload, actual_end_date) or (None, None). Checks the exact date first,
    then +1, -1, +2, -2, ... Entries due a refresh are skipped unless allow_stale.
    """
    for offset in range(max_offset + 1):
        for sign in ([0] if offset == 0 else [1, -1]):
            candidate = end_date + timedelta(days=offset * sign)
            cached = get_cached(candidate, locked)
            if not cached:
                continue
            if not allow_stale and sstmeta.refresh_due(entry_meta(cached, candidate, locked)):
                continue
            if offset != 0:
                logger.info("Fuzzy cache HIT: requested %s, serving %s (offset %+d)",
                            end_date, candidate, offset * sign)
            return cached, candidate
    return None, None


# ---- The index of saved weeks (for "open a nearby saved week instead") ----

_index_cache = {"t": 0.0, "dates": []}


def saved_end_dates() -> List[date]:
    """End dates with a saved week (weekly file or an old entry), newest first. Cached for 60 s."""
    now = time.monotonic()
    if now - _index_cache["t"] < 60:
        return list(_index_cache["dates"])
    found = set()
    for _, _, _, end in _sst_files():
        if end:
            try:
                found.add(date.fromisoformat(end))
            except ValueError:
                pass
    dates = sorted(found, reverse=True)
    _index_cache.update(t=now, dates=dates)
    return list(dates)


def nearest_saved(end_date: date, n: int = 3, exclude=()) -> List[date]:
    """The n saved end dates closest to end_date (ties: the later one first)."""
    skip = set(exclude)
    cands = [d for d in saved_end_dates() if d not in skip]
    cands.sort(key=lambda d: (abs((d - end_date).days), -d.toordinal()))
    return cands[:n]


# ---- Weeks wanted by readers that NOAA couldn't send in time (the seeder fetches these) ----

_wanted_lock = threading.Lock()


def _wanted_path() -> Path:
    return _ensure_dir() / WANTED_FILE


def wanted_weeks() -> List[str]:
    with _wanted_lock:
        try:
            data = json.loads(_wanted_path().read_text())
            return [str(d) for d in data.get("weeks", [])][:MAX_WANTED]
        except (OSError, ValueError):
            return []


def _write_wanted(weeks: List[str]) -> None:
    p = _wanted_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"weeks": weeks}))
    os.replace(tmp, p)


def queue_week(end_date: date) -> bool:
    """Add a week to the seeder's list. False if the list is full (the week isn't queued)."""
    d = end_date.isoformat()
    with _wanted_lock:
        try:
            weeks = [str(w) for w in json.loads(_wanted_path().read_text()).get("weeks", [])]
        except (OSError, ValueError):
            weeks = []
        if d in weeks:
            return True
        if len(weeks) >= MAX_WANTED:
            return False
        weeks.append(d)
        try:
            _write_wanted(weeks)
        except OSError:
            logger.warning("Could not write %s", WANTED_FILE, exc_info=True)
            return False
    logger.info("Queued week %s for the seeder (%d waiting)", d, len(weeks))
    return True


def unqueue_week(end_date: date) -> None:
    d = end_date.isoformat()
    with _wanted_lock:
        try:
            weeks = [str(w) for w in json.loads(_wanted_path().read_text()).get("weeks", [])]
        except (OSError, ValueError):
            return
        if d in weeks:
            weeks.remove(d)
            try:
                _write_wanted(weeks)
            except OSError:
                pass


def evict_old(max_entries: int = MAX_ENTRIES) -> None:
    """Old name, kept for callers: see evict()."""
    evict(max_entries=max_entries)
