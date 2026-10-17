#!/usr/bin/env python3
"""Build SST weeks where NOAA is reachable and push them to the live app (Render can't reach NOAA).

Why this exists: NOAA's firewall blocks ERDDAP from Render's datacenter IP (connections are refused / unreachable),
so the deployed app can't fetch SST on a cache miss. This script runs somewhere ERDDAP IS reachable (a residential IP
or a GitHub runner), builds the weekly cache files the app reads (data/sstgrid: one file per week, 0.1 F over the
coverage box in config.json, land masked), and POSTs them to /api/cache/upload as <end>_week.json.gz.

What it seeds, in this order, within --max-minutes:
  1. the --count end dates ending on the newest MUR day (ERDDAP time[last]; today - --start-back if that lookup
     fails), so the page's default week is ready and recent weeks pick up MUR's daily revisions;
  2. the weeks readers asked for that NOAA couldn't send in time (GET /api/cache/wanted, at most --max-wanted);
  3. --backfill N: weeks the server still has only in the format from before sprint 2 (the old, smaller area in
     whole degrees; GET /api/cache/weeks "old_only"), newest first, --backfill-delay seconds apart. The server's index
     is the state, so a run that stops (time cap, NOAA 503s, a crash) simply resumes on the next run.
Only complete MUR weeks are uploaded (never OISST, never a week with a day missing because NOAA was busy). Days are
fetched once per run (weeks ending on consecutive days share six days). A backfill week that fails (NOAA busy) is
tried once more after a pause, then left for the next run; backfill failures don't fail the run.

Usage (from repo root, with the project venv):
  CACHE_UPLOAD_TOKEN=secret python tools/sync_cache.py --base-url https://sst.gotoneapp.com
  python tools/sync_cache.py --dry-run                    # build only, no upload
  python tools/sync_cache.py --dates 2014-08-20           # just these end dates
  python tools/sync_cache.py --backfill 24                # + up to 24 old-format weeks
  python tools/sync_cache.py --dates 2026-10-15 --out /tmp/sst-cache   # write files into a local cache dir
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from collections import OrderedDict
from datetime import date, timedelta

import requests

# Make `data.*` importable regardless of where this is launched from
# (cron / CI put the script's own dir on sys.path, not the repo root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Clean imports only — deliberately NOT importing app.py (it starts a
# pre-warm thread at import and, in some branches, loads extra data).
from data import geo, sstgrid  # noqa: E402
from data.cache import week_name  # noqa: E402
from data.erddap import SSTUnavailable, get_sst_multiday, latest_mur_date  # noqa: E402

WEEK_BUDGET_S = 150    # a seed run can wait for NOAA; readers get ~22 s
LEVEL = 9              # gzip level for the week's grid: 588 KB vs 641 KB at 6 for the coverage box, ~3.6 s
DAY_CACHE_MAX = 40     # days kept per run (~2.8 MB each for the coverage box)
RETRY_PAUSE_S = 30     # a backfill week NOAA was too busy for is tried once more after this
sleep = time.sleep     # tests replace it (rate limiting is checked, not waited for)


class DayCache(OrderedDict):
    """Days fetched in this run, shared by overlapping weeks (keys from data/erddap.fetch_days). Bounded."""

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        while len(self) > DAY_CACHE_MAX:
            self.popitem(last=False)


def _land_mask_ready() -> bool:
    """The Natural Earth land mask is loaded (downloaded once per run; tests replace this)."""
    return geo.load_land_gdf(wait_s=90) is not None


def build_week_file(sst: dict, cfg: dict, level: int = LEVEL):
    """(filename, gzip JSON bytes, entry) for a week get_sst_multiday() returned: the same entry the app builds
    (data/sstgrid.entry_from_fetch), uploaded as is."""
    entry = sstgrid.entry_from_fetch(sst, cfg, level=level)
    end = date.fromisoformat(entry["dates"][-1])
    blob = gzip.compress(json.dumps(entry).encode("utf-8"), compresslevel=6)
    return week_name(end), blob, entry


def build_raw_payload(sst: dict, cfg: dict, locked: bool = False) -> dict:
    """Old name, kept for callers: the weekly entry (one file per week since sprint 2; `locked` is ignored)."""
    return sstgrid.entry_from_fetch(sst, cfg)


def upload_cache_file(base_url, token, fname, blob, attempts=5):
    """POST one cache file, retrying transient Render 5xx / network errors.

    Render runs a single worker; rapid uploads can momentarily saturate it and
    its proxy returns 502/503. Those are transient — back off and retry rather
    than dropping the file.
    """
    url = f"{base_url.rstrip('/')}/api/cache/upload"
    headers = {
        "X-Upload-Token": token,
        "X-Cache-Filename": fname,
        "Content-Type": "application/octet-stream",
    }
    backoff = [3, 8, 15, 25]
    for i in range(attempts):
        try:
            r = requests.post(url, data=blob, headers=headers, timeout=90)
            if r.status_code == 200:
                return True, r.json()
            if r.status_code in (500, 502, 503, 504) and i < attempts - 1:
                wait = backoff[min(i, len(backoff) - 1)]
                print(f"  {fname}: HTTP {r.status_code} (worker busy) — retry in {wait}s",
                      flush=True)
                sleep(wait)
                continue
            return False, f"HTTP {r.status_code} {r.text[:120]}"
        except requests.RequestException as e:
            if i < attempts - 1:
                wait = backoff[min(i, len(backoff) - 1)]
                print(f"  {fname}: {type(e).__name__} — retry in {wait}s", flush=True)
                sleep(wait)
                continue
            return False, f"{type(e).__name__}: {e}"
    return False, "exhausted retries"


def _get_json(base_url, path, token, what):
    try:
        r = requests.get(f"{base_url.rstrip('/')}{path}", headers={"X-Upload-Token": token}, timeout=30)
        if r.status_code != 200:
            print(f"{what}: HTTP {r.status_code} (skipped)", flush=True)
            return None
        return r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"{what}: {type(e).__name__} (skipped)", flush=True)
        return None


def _dates(values):
    out = []
    for w in values or []:
        try:
            out.append(date.fromisoformat(str(w)))
        except ValueError:
            pass
    return out


def fetch_wanted(base_url, token):
    """Weeks readers asked for that NOAA couldn't send (the app's /api/cache/wanted)."""
    js = _get_json(base_url, "/api/cache/wanted", token, "wanted list")
    return _dates((js or {}).get("weeks"))


def fetch_backfill(base_url, token):
    """Weeks the server has only in the format from before sprint 2, newest first (the app's /api/cache/weeks)."""
    js = _get_json(base_url, "/api/cache/weeks", token, "backfill list")
    if js is None:
        return []
    disk = js.get("disk") or {}
    print(f"server cache: {len(js.get('weeks') or [])} weekly files, {len(js.get('old_only') or [])} weeks only "
          f"in the old format; disk {disk}", flush=True)
    return sorted(_dates(js.get("old_only")), reverse=True)


def plan(args, cfg, newest):
    """[(end date, kind)] in seeding order: recent, wanted, backfill (no duplicates, nothing past the newest day)."""
    if args.dates:
        return [(date.fromisoformat(d.strip()), "listed") for d in args.dates.split(",") if d.strip()]
    out = [(newest - timedelta(days=i), "recent") for i in range(args.count)]
    seen = {d for d, _ in out}
    if args.token and not args.no_wanted and not args.out:
        wanted = [w for w in fetch_wanted(args.base_url, args.token) if w not in seen and w <= newest]
        wanted = wanted[: args.max_wanted]
        if wanted:
            print(f"reader-requested weeks: {', '.join(str(w) for w in wanted)}", flush=True)
        out += [(w, "wanted") for w in wanted]
        seen.update(wanted)
    if args.backfill > 0 and args.token and not args.out:
        todo = [w for w in fetch_backfill(args.base_url, args.token) if w not in seen and w <= newest]
        if todo:
            print(f"backfill: {len(todo)} weeks to go, up to {args.backfill} this run "
                  f"({', '.join(str(w) for w in todo[: args.backfill])})", flush=True)
        out += [(w, "backfill") for w in todo[: args.backfill]]
    return out


def seed(args, cfg) -> int:
    t_start = time.monotonic()
    if args.dates:
        newest = None
    else:
        latest = latest_mur_date(cfg, max_wait=20.0)
        newest = latest or (date.today() - timedelta(days=args.start_back))
        print(f"newest MUR day: {latest or 'unknown'}; seeding from {newest}", flush=True)
    todo = plan(args, cfg, newest)
    if todo and not _land_mask_ready():               # never upload a week whose land isn't masked
        print("ERROR: the Natural Earth land mask didn't load; nothing seeded", file=sys.stderr, flush=True)
        return 1
    dest = args.out or args.base_url
    print(f"seeding {len(todo)} weeks -> {dest}", flush=True)
    day_cache = DayCache()
    ok = fail = bf_fail = 0
    left = []
    prev_kind = None
    for k, (ed, kind) in enumerate(todo):
        if time.monotonic() - t_start > args.max_minutes * 60:
            left = todo[k:]
            break
        if kind == "backfill" and prev_kind == "backfill":
            sleep(args.backfill_delay)                     # be gentle with NOAA between old weeks
        prev_kind = kind
        fname = week_name(ed)
        tries = 2 if kind == "backfill" else 1
        for attempt in range(tries):
            try:
                t = time.monotonic()
                sst = get_sst_multiday(ed, cfg, budget_s=WEEK_BUDGET_S, allow_fallback=False,
                                       require_full=True, concurrency=2, cache=day_cache)
                fname, blob, entry = build_week_file(sst, cfg, level=args.level)
                days = entry["meta"]["days"]
                print(f"built {fname}  {len(blob) / 1024:.0f} KB (grid {entry['grid_bytes'] / 1024:.0f} KB)  "
                      f"{days[0]}..{days[-1]} ({len(days)} days) in {time.monotonic() - t:.1f}s [{kind}]", flush=True)
                if args.out:
                    path = os.path.join(args.out, "sst", "weeks")
                    os.makedirs(path, exist_ok=True)
                    with open(os.path.join(path, fname), "wb") as f:
                        f.write(blob)
                    ok += 1
                elif args.dry_run:
                    ok += 1
                else:
                    success, info = upload_cache_file(args.base_url, args.token, fname, blob)
                    if success:
                        print(f"  uploaded: {info}", flush=True)
                        ok += 1
                    else:
                        print(f"  UPLOAD FAILED {fname}: {info}", file=sys.stderr, flush=True)
                        fail += 1
                    sleep(args.upload_delay)
                break
            except Exception as e:  # noqa: BLE001 — report and continue to the next week
                busy = isinstance(e, SSTUnavailable) and e.reason in ("busy", "network")
                if attempt + 1 < tries and busy:
                    print(f"  {fname}: NOAA {e.reason}; trying again in {RETRY_PAUSE_S}s", flush=True)
                    sleep(RETRY_PAUSE_S)
                    continue
                print(f"  FAILED {fname}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
                if kind == "backfill":
                    bf_fail += 1
                else:
                    fail += 1
    if left:
        print(f"time cap ({args.max_minutes} min) reached; {len(left)} weeks left for the next run "
              f"({sum(1 for _, kd in left if kd == 'backfill')} backfill)", flush=True)
    print(f"done: {ok} ok, {fail} failed, {bf_fail} backfill failed (retried next run), "
          f"{time.monotonic() - t_start:.0f}s")
    return 0 if fail == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("SYNC_BASE_URL", "https://sst.gotoneapp.com"),
                    help="Live app base URL")
    ap.add_argument("--token", default=os.environ.get("CACHE_UPLOAD_TOKEN"))
    ap.add_argument("--start-back", type=int, default=1,
                    help="If NOAA's newest day can't be looked up: most recent end-date = today - start_back "
                         "(default 1)")
    ap.add_argument("--count", type=int, default=10,
                    help="Number of consecutive end-dates to seed, ending on the newest day (default 10)")
    ap.add_argument("--max-wanted", type=int, default=15,
                    help="At most this many reader-requested weeks per run (default 15)")
    ap.add_argument("--no-wanted", action="store_true", help="Skip the reader-requested weeks")
    ap.add_argument("--backfill", type=int, default=0,
                    help="Up to this many weeks the server has only in the old format (default 0)")
    ap.add_argument("--backfill-delay", type=float, default=20.0,
                    help="Seconds between backfill weeks (default 20)")
    ap.add_argument("--max-minutes", type=float, default=40.0,
                    help="Stop starting new weeks after this many minutes (default 40)")
    ap.add_argument("--dates", default="", help="Comma-separated end dates to seed instead")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--level", type=int, default=LEVEL, help=f"gzip level of the week's grid (default {LEVEL})")
    ap.add_argument("--dry-run", action="store_true", help="Build but do not upload")
    ap.add_argument("--out", default="", help="Write the weekly files under DIR/sst/weeks instead of uploading")
    ap.add_argument(
        "--upload-delay", type=float, default=2.0,
        help="Seconds to pause between uploads (avoids saturating the single "
             "Render worker; default 2.0)",
    )
    args = ap.parse_args(argv)

    if not args.token and not (args.dry_run or args.out):
        print("ERROR: no token (set CACHE_UPLOAD_TOKEN or pass --token)", file=sys.stderr)
        return 2

    with open(args.config) as f:
        cfg = json.load(f)
    return seed(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
