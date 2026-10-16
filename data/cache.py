"""Disk-based cache for SST weeks, plus the list of weeks the seeder should fetch.

Each 7-day window is a gzip-compressed JSON file keyed by end_date and color-scale mode
(adaptive vs locked), in SST_CACHE_DIR/sst (env var, default ./cache). Entries carry a
"meta" dict (data/sstmeta.py); old entries without one still load.

Freshness (sstmeta.refresh_due): a week fetched while some of its days were preliminary is
refetched after 12 h, one inside MUR's 7-day revision window after 24 h, an OISST or
incomplete week is retried for MUR after 6 h; anything else is final. A week that is due
is still served when the refetch fails.
"""

import gzip
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from data import sstmeta

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("SST_CACHE_DIR", "./cache")) / "sst"
MAX_ENTRIES = 500
WANTED_FILE = "wanted.json"   # weeks the seeder should fetch (tools/sync_cache.py --wanted)
MAX_WANTED = 30
_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(adaptive|locked)\.json\.gz$")


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
        evict_old()
    except Exception:
        logger.warning("Cache write failed for %s", target.name, exc_info=True)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


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
    """End dates with a saved week (either mode), newest first. Cached for 60 s."""
    now = time.monotonic()
    if now - _index_cache["t"] < 60:
        return list(_index_cache["dates"])
    found = set()
    try:
        for p in CACHE_DIR.glob("*.json.gz"):
            m = _NAME_RE.match(p.name)
            if m:
                try:
                    found.add(date.fromisoformat(m.group(1)))
                except ValueError:
                    pass
    except OSError:
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
    """Delete oldest cache files if count exceeds max_entries."""
    try:
        files = sorted(CACHE_DIR.glob("*.json.gz"), key=lambda f: f.stat().st_mtime)
        if len(files) <= max_entries:
            return
        to_remove = files[: len(files) - max_entries]
        for f in to_remove:
            f.unlink()
            logger.info("Cache EVICT: %s", f.name)
        _index_cache["t"] = 0.0
    except Exception:
        logger.warning("Cache eviction failed", exc_info=True)
