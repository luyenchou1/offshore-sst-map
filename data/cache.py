"""Disk-based cache for pre-rendered SST payloads.

Each 7-day window is stored as a gzip-compressed JSON file keyed by
end_date and color-scale mode (adaptive vs locked).  Files live in
SST_CACHE_DIR (env var, default ./cache).

Cache invalidation:
  - Dates > 3 days old: permanent (MUR data is finalized).
  - Dates within 3 days: stale after 12 hours (new MUR data may appear).
"""

import gzip
import json
import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("SST_CACHE_DIR", "./cache")) / "sst"
MAX_ENTRIES = 500


def _ensure_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def cache_key(end_date: date, locked: bool) -> str:
    mode = "locked" if locked else "adaptive"
    return f"{end_date.isoformat()}_{mode}"


def _path_for(end_date: date, locked: bool) -> Path:
    return _ensure_dir() / f"{cache_key(end_date, locked)}.json.gz"


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


def put_cache(end_date: date, locked: bool, payload: Dict) -> None:
    """Write payload to cache using atomic write (tmp + rename)."""
    _ensure_dir()
    target = _path_for(end_date, locked)
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(CACHE_DIR), suffix=".tmp"
        )
        os.close(fd)
        with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, str(target))
        size_kb = target.stat().st_size / 1024
        logger.info("Cache WRITE: %s (%.0f KB)", target.name, size_kb)
        evict_old()
    except Exception:
        logger.warning("Cache write failed for %s", target.name, exc_info=True)
        # Clean up temp file if it still exists
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def is_stale(end_date: date) -> bool:
    """Check if a cached entry might be outdated.

    MUR data has ~2-day latency.  Entries for dates within 3 days of
    today may have been cached before all 7 days had data.  We consider
    them stale if the cache file is older than 12 hours.
    """
    if end_date < date.today() - timedelta(days=3):
        return False  # Finalized data — never stale

    p = _path_for(end_date, False)  # Check adaptive; locked staleness is the same
    if not p.exists():
        p = _path_for(end_date, True)
    if not p.exists():
        return True  # No cache at all

    mtime = datetime.fromtimestamp(p.stat().st_mtime)
    age_hours = (datetime.now() - mtime).total_seconds() / 3600
    if age_hours > 12:
        logger.info("Cache STALE: %s (%.1f hours old)", p.name, age_hours)
        return True
    return False


def find_nearest_cached(end_date: date, locked: bool, max_offset: int = 3) -> tuple:
    """Find the nearest cached entry within ±max_offset days.

    Returns (cached_payload, actual_end_date) or (None, None) if no
    nearby cache exists.  Checks the exact date first, then alternates
    +1, -1, +2, -2, … up to ±max_offset.
    """
    for offset in range(max_offset + 1):
        for sign in ([0] if offset == 0 else [1, -1]):
            candidate = end_date + timedelta(days=offset * sign)
            cached = get_cached(candidate, locked)
            if cached and not is_stale(candidate):
                if offset != 0:
                    logger.info(
                        "Fuzzy cache HIT: requested %s, serving %s (offset %+d)",
                        end_date, candidate, offset * sign,
                    )
                return cached, candidate
    return None, None


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
    except Exception:
        logger.warning("Cache eviction failed", exc_info=True)
