"""Anonymous page counter for GotOne Labs (/, /sst, /currents).

Same rules as Plotline's counter: no cookies, nothing stored about a visitor.
A page reports {p: page, r: referrer host} once per view (assets/labs.js via a
clientside callback in app.py; only on the production hosts, never with Do
Not Track). Daily unique visitors come from a hash of a server secret, the
date, the IP and the browser string; the raw values are never written, and a
new date makes a new, unlinkable hash. Hashes older than 90 days are deleted.

SQLite on the persistent disk ($SST_CACHE_DIR/stats/labs.sqlite), one short
connection per request (1 worker, 16 threads: SQLite's own locking suffices).
Stats are private: /labs/stats?key=... and /api/labs/stats?key=... (app.py).
"""

import hashlib
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PAGES = ("labs", "sst", "currents")
ET = ZoneInfo("America/New_York")
DIR = Path(os.environ.get("SST_CACHE_DIR", "./cache")) / "stats"
DB = DIR / "labs.sqlite"
KEEP_DAYS = 90
MAX_VIEWS_PER_VISITOR = 30          # per page per day: a reloading bot can't run up the count
BOT_UA = re.compile(r"bot|crawl|spider|slurp|preview|headless|python|curl|wget|httpclient|"
                    r"facebookexternalhit|embedly|monitor|uptime|lighthouse", re.I)
HOST_RE = re.compile(r"[^a-z0-9.\-]")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS views (day TEXT, page TEXT, n INTEGER, PRIMARY KEY (day, page));
CREATE TABLE IF NOT EXISTS visitors (day TEXT, page TEXT, h TEXT, n INTEGER,
                                     PRIMARY KEY (day, page, h));
CREATE TABLE IF NOT EXISTS referrers (day TEXT, page TEXT, host TEXT, n INTEGER,
                                      PRIMARY KEY (day, page, host));
"""


def _connect():
    DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def _secret():
    """Random per-server secret for the visitor hash, kept on the disk."""
    p = DIR / "salt"
    try:
        return p.read_bytes()
    except OSError:
        DIR.mkdir(parents=True, exist_ok=True)
        s = secrets.token_bytes(32)
        p.write_bytes(s)
        return s


def clean_referrer(r):
    """A referrer host only: lowercase, no scheme/path, 80 chars at most."""
    r = (r or "").strip().lower()
    embedded = r.endswith(" (embedded)")
    r = r.replace(" (embedded)", "")
    r = HOST_RE.sub("", r.split("/")[0])[:80]
    if r.startswith("www."):
        r = r[4:]
    return (r or "direct") + (" (embedded)" if embedded else "")


def record(page, referrer, ip, user_agent, dnt=False):
    """Count one page view. Returns False when it isn't counted."""
    if page not in PAGES or dnt or not user_agent or BOT_UA.search(user_agent):
        return False
    day = datetime.now(ET).strftime("%Y-%m-%d")
    h = hashlib.sha256(_secret() + day.encode() + (ip or "").encode() +
                       user_agent.encode()).hexdigest()[:20]
    con = _connect()
    try:
        with con:
            cur = con.execute("SELECT n FROM visitors WHERE day=? AND page=? AND h=?",
                              (day, page, h)).fetchone()
            if cur and cur[0] >= MAX_VIEWS_PER_VISITOR:
                return False
            con.execute("INSERT INTO visitors VALUES (?,?,?,1) ON CONFLICT(day,page,h) "
                        "DO UPDATE SET n = n + 1", (day, page, h))
            con.execute("INSERT INTO views VALUES (?,?,1) ON CONFLICT(day,page) "
                        "DO UPDATE SET n = n + 1", (day, page))
            ref = clean_referrer(referrer)
            if ref != "internal":
                con.execute("INSERT INTO referrers VALUES (?,?,?,1) ON CONFLICT(day,page,host) "
                            "DO UPDATE SET n = n + 1", (day, page, ref))
            if not cur:                                   # first view today: prune old hashes
                cutoff = (datetime.now(ET) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
                con.execute("DELETE FROM visitors WHERE day < ?", (cutoff,))
        return True
    finally:
        con.close()


def summary(days=30):
    """Totals, the last `days` days per page, and top referrers."""
    con = _connect()
    try:
        since = (datetime.now(ET) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        daily = {}
        for day, page, n in con.execute(
                "SELECT day, page, n FROM views WHERE day >= ? ORDER BY day", (since,)):
            daily.setdefault(day, {})[page] = {"views": n, "visitors": 0}
        for day, page, u in con.execute(
                "SELECT day, page, COUNT(*) FROM visitors WHERE day >= ? GROUP BY day, page",
                (since,)):
            daily.setdefault(day, {}).setdefault(page, {"views": 0})["visitors"] = u
        totals = {p: {"views": 0} for p in PAGES}
        for page, n in con.execute("SELECT page, SUM(n) FROM views GROUP BY page"):
            totals.setdefault(page, {})["views"] = n
        refs = [{"host": h, "page": p, "views": n} for h, p, n in con.execute(
            "SELECT host, page, SUM(n) AS s FROM referrers WHERE day >= ? "
            "GROUP BY host, page ORDER BY s DESC LIMIT 25", (since,))]
        first = con.execute("SELECT MIN(day) FROM views").fetchone()[0]
        return {"since": since, "first_day": first, "totals": totals,
                "daily": [{"day": d, **{p: daily[d].get(p, {"views": 0, "visitors": 0})
                                        for p in PAGES}} for d in sorted(daily, reverse=True)],
                "referrers": refs}
    finally:
        con.close()
