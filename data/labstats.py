"""Anonymous counting for GotOne Labs: page views, feature use and "Helpful?" feedback.

One service for every Labs tool: Offshore SST and Currents (this app) and The Striper Recount
(a static site at striperrecount.gotoneapp.com that posts here cross-origin). The shared rules
live in docs/labs-analytics-contract.md; the endpoints are in app.py (/api/hit, /api/event,
/api/feedback) and the private dashboard is /labs/stats (layout/statsview.py).

Same rules as Plotline's counter: no cookies, nothing stored about a visitor. Daily unique
visitors and the rate limits come from a hash of a server secret, the date, the IP and the
browser string; the raw values are never written, and a new date makes a new, unlinkable hash.
Hashes older than 90 days are deleted (their daily unique counts are kept first, in `uniques`,
so a 365-day view still has visitor numbers). Do Not Track / Global Privacy Control and bots
are never counted. Feedback is the exception to DNT: the reader chose to send it.

SQLite on the persistent disk ($SST_CACHE_DIR/stats/labs.sqlite, or $LABS_STATS_DIR), one short
connection per request (1 worker, 16 threads: SQLite's own locking suffices).
"""

import hashlib
import math
import os
import re
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ---- Tools and pages ----
# Tool id -> name, in dashboard order. "labs" is the old in-app home (it now redirects to
# gotoneapp.com/labs); it stays for its history.
TOOLS = {
    "sst": "Offshore SST",
    "currents": "Currents",
    "recount": "The Striper Recount",
    "labs": "Labs home",
}
RECOUNT_SECTIONS = ("count", "calendar", "ghosts", "pie", "removals", "states", "next")
# A page's tool is the part before "/". The Striper Recount: "recount" is the home page (the
# video plus all seven interactives), recount/<section> the single-page versions, recount/about.
PAGES = (("labs", "sst", "currents", "recount")
         + tuple(f"recount/{s}" for s in RECOUNT_SECTIONS) + ("recount/about",))
PAGE_NAMES = {"recount": "Home (video + all seven)", "recount/about": "About"}
PAGE_NAMES.update({f"recount/{s}": f"{s}.html" for s in RECOUNT_SECTIONS})


def tool_of(page):
    return (page or "").split("/")[0]


# ---- Feature use: the whitelist (anything else is ignored) ----
# Names are [a-z0-9_]{1,32}. Clients send each at most once per page view, so counts read as
# "page views that did X". The SST and Currents lists are what assets/labs.js, assets/sst.js and
# assets/currents.js really send (see the contract); the recount list belongs to that site.
_RECOUNT_EVENTS = {
    "video_play": "Started the video",
    "video_25": "Watched 25% of the video",
    "video_50": "Watched 50% of the video",
    "video_75": "Watched 75% of the video",
    "video_end": "Watched the video to the end",
}
for _s in RECOUNT_SECTIONS:
    _RECOUNT_EVENTS[f"reach_{_s}"] = f"Reached the {_s} section"
for _s in RECOUNT_SECTIONS:
    _RECOUNT_EVENTS[f"turn_{_s}"] = f"Used the {_s} \"Your turn\" gesture"
for _s in RECOUNT_SECTIONS:
    _RECOUNT_EVENTS[f"end_{_s}"] = f"Reached the end of {_s}"
_RECOUNT_EVENTS.update({
    "share_four": "Copied or shared the four answers",
    "share_page": "Footer Share",
    "lyrics": "Opened the lyrics",
    "short_version": "Opened the short version",
    "out_asga": "Clicked through to ASGA",
    "out_gotone": "Clicked through to GotOne",
    "out_asmfc": "Clicked through to ASMFC",
    "out_labs": "Clicked through to GotOne Labs",
})

EVENTS = {
    "sst": {
        "load": "Pressed Fetch SST to load a week (the automatic first load isn't counted)",
        "play": "Played the week's animation",
        "step": "Moved to another day (step buttons or the day slider)",
        "readout": "Tapped the map for a temperature",
        "poi": "Tapped a fishing spot for its details",
        "measure": "Measured a distance (both points placed)",
        "spots": "Opened the spot list",
        "chart": "Turned on the nautical chart",
        "bathymetry": "Turned on bathymetry",
        "gfw": "Turned on fishing activity",
        "lock_scale": "Locked the color scale",
        "opacity": "Changed the SST opacity",
    },
    "currents": {
        "view_today": "Switched to the Today view",
        "view_cycle": "Switched to the Tide cycle view",
        "view_weeks": "Switched to the Example weeks view",
        "date": "Picked another day (Today view)",
        "now": "Pressed Now",
        "strength": "Picked a tide strength (Tide cycle view)",
        "week": "Picked another example week",
        "spot": "Selected a NOAA station (list or map)",
        "inlet": "Selected a harbor-inlet estimate",
        "how_it_works": "Opened How it works (not the automatic first-visit opening)",
        "scrub": "Set the time on the chart (click, tap or drag)",
        "play": "Started playback",
        "step": "Stepped an hour back or forward",
        "speed": "Changed the playback speed",
        "depth": "Switched surface / depth-averaged",
        "fullscreen": "Went full screen",
    },
    "recount": _RECOUNT_EVENTS,
}
FEEDBACK_TOOLS = tuple(EVENTS)                  # sst, currents, recount
EVENT_RE = re.compile(r"^[a-z0-9_]{1,32}$")
assert all(EVENT_RE.match(e) for evs in EVENTS.values() for e in evs)

# ---- Limits ----
ET = ZoneInfo("America/New_York")
KEEP_DAYS = 90
MAX_VIEWS_PER_VISITOR = 30          # per page per day: a reloading bot can't run up the count
MAX_EVENTS_PER_VISITOR = 40         # per event per day
MAX_FEEDBACK_PER_VISITOR = 5        # per tool per day
MAX_FEEDBACK_PER_DAY = 300          # everyone, all tools
MIN_FEEDBACK_SECONDS = 3            # on the page before a vote or note counts
MAX_NOTE = 500                      # characters, after cleaning
BOT_UA = re.compile(r"bot|crawl|spider|slurp|preview|headless|python|curl|wget|httpclient|"
                    r"facebookexternalhit|embedly|monitor|uptime|lighthouse", re.I)
HOST_RE = re.compile(r"[^a-z0-9.\-]")
_LINE_BREAKS = re.compile(r"[\r\n\t\v\f\u0085  ]+")
# Bidi controls can make an escaped note display misleadingly; drop them with the C0/C1 controls.
_BIDI = set("‎‏‪‫‬‭‮⁦⁧⁨⁩")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS views (day TEXT, page TEXT, n INTEGER, PRIMARY KEY (day, page));
CREATE TABLE IF NOT EXISTS visitors (day TEXT, page TEXT, h TEXT, n INTEGER,
                                     PRIMARY KEY (day, page, h));
CREATE TABLE IF NOT EXISTS referrers (day TEXT, page TEXT, host TEXT, n INTEGER,
                                      PRIMARY KEY (day, page, host));
CREATE TABLE IF NOT EXISTS events (day TEXT, tool TEXT, event TEXT, n INTEGER,
                                   PRIMARY KEY (day, tool, event));
CREATE TABLE IF NOT EXISTS event_visitors (day TEXT, tool TEXT, event TEXT, h TEXT, n INTEGER,
                                           PRIMARY KEY (day, tool, event, h));
CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, ts TEXT,
                                     tool TEXT, vote TEXT, note TEXT, note_vote TEXT, h TEXT);
CREATE INDEX IF NOT EXISTS feedback_day ON feedback (day, tool, h);
CREATE TABLE IF NOT EXISTS uniques (day TEXT, kind TEXT, key TEXT, u INTEGER,
                                    PRIMARY KEY (day, kind, key));
"""
# kind = "page" (key: page id), "tool" (key: tool id, distinct across its pages) or "event"
# (key: "tool/event"): the daily unique-visitor counts of days whose hashes were deleted.

_TOOL_SQL = "CASE WHEN instr(page, '/') > 0 THEN substr(page, 1, instr(page, '/') - 1) ELSE page END"


def stats_dir():
    """$LABS_STATS_DIR, else $SST_CACHE_DIR/stats (read per call so tests can point it elsewhere)."""
    d = os.environ.get("LABS_STATS_DIR", "").strip()
    return Path(d) if d else Path(os.environ.get("SST_CACHE_DIR", "./cache")) / "stats"


def _connect():
    d = stats_dir()
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(d / "labs.sqlite", timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def _secret():
    """Random per-server secret for the visitor hash, kept on the disk."""
    p = stats_dir() / "salt"
    try:
        return p.read_bytes()
    except OSError:
        p.parent.mkdir(parents=True, exist_ok=True)
        s = secrets.token_bytes(32)
        p.write_bytes(s)
        return s


def _today():
    return datetime.now(ET).strftime("%Y-%m-%d")


def _hash(day, ip, user_agent):
    return hashlib.sha256(_secret() + day.encode() + (ip or "").encode() +
                          (user_agent or "").encode()).hexdigest()[:20]


def _is_bot(user_agent):
    return not user_agent or bool(BOT_UA.search(user_agent))


_pruned = {}                        # db dir -> day last pruned (once a day per process)


def _maybe_prune(con, day):
    """Delete visitor hashes older than KEEP_DAYS, keeping their daily unique counts first."""
    key = str(stats_dir())
    if _pruned.get(key) == day:
        return
    cutoff = (datetime.now(ET) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    con.execute("INSERT OR IGNORE INTO uniques SELECT day, 'page', page, COUNT(*) FROM visitors "
                "WHERE day < ? GROUP BY day, page", (cutoff,))
    con.execute(f"INSERT OR IGNORE INTO uniques SELECT day, 'tool', {_TOOL_SQL} AS t, "
                "COUNT(DISTINCT h) FROM visitors WHERE day < ? GROUP BY day, t", (cutoff,))
    con.execute("INSERT OR IGNORE INTO uniques SELECT day, 'event', tool || '/' || event, COUNT(*) "
                "FROM event_visitors WHERE day < ? GROUP BY day, tool, event", (cutoff,))
    con.execute("DELETE FROM visitors WHERE day < ?", (cutoff,))
    con.execute("DELETE FROM event_visitors WHERE day < ?", (cutoff,))
    con.execute("UPDATE feedback SET h = NULL WHERE day < ? AND h IS NOT NULL", (cutoff,))
    _pruned[key] = day


def clean_referrer(r):
    """A referrer host only: lowercase, no scheme/path, 80 chars at most."""
    r = (r or "").strip().lower()
    embedded = r.endswith(" (embedded)")
    r = r.replace(" (embedded)", "")
    r = HOST_RE.sub("", r.split("/")[0])[:80]
    if r.startswith("www."):
        r = r[4:]
    return (r or "direct") + (" (embedded)" if embedded else "")


def clean_note(note):
    """One line of text: line breaks become spaces, control and bidi characters go, trimmed.
    Returns None when it isn't text or is longer than MAX_NOTE afterwards."""
    if note is None:
        return ""
    if not isinstance(note, str):
        return None
    s = _LINE_BREAKS.sub(" ", note)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cc" and ch not in _BIDI).strip()
    return s if len(s) <= MAX_NOTE else None


# ---- Recording ----
def record(page, referrer, ip, user_agent, dnt=False):
    """Count one page view. Returns False when it isn't counted."""
    if page not in PAGES or dnt or _is_bot(user_agent):
        return False
    day = _today()
    h = _hash(day, ip, user_agent)
    con = _connect()
    try:
        with con:
            _maybe_prune(con, day)
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
        return True
    finally:
        con.close()


def record_event(tool, event, ip, user_agent, dnt=False):
    """Count one use of a whitelisted feature. Returns False when it isn't counted."""
    if (tool not in EVENTS or event not in EVENTS[tool] or dnt or _is_bot(user_agent)):
        return False
    day = _today()
    h = _hash(day, ip, user_agent)
    con = _connect()
    try:
        with con:
            _maybe_prune(con, day)
            cur = con.execute("SELECT n FROM event_visitors WHERE day=? AND tool=? AND event=? "
                              "AND h=?", (day, tool, event, h)).fetchone()
            if cur and cur[0] >= MAX_EVENTS_PER_VISITOR:
                return False
            con.execute("INSERT INTO event_visitors VALUES (?,?,?,?,1) "
                        "ON CONFLICT(day,tool,event,h) DO UPDATE SET n = n + 1",
                        (day, tool, event, h))
            con.execute("INSERT INTO events VALUES (?,?,?,1) ON CONFLICT(day,tool,event) "
                        "DO UPDATE SET n = n + 1", (day, tool, event))
        return True
    finally:
        con.close()


def record_feedback(tool, vote, note, honeypot, seconds, ip, user_agent):
    """Store one "Helpful?" vote and/or note (the origin check is app.py's). Recorded even with
    DNT/GPC on. Returns False when it's rejected (see the contract's acceptance rules)."""
    if tool not in FEEDBACK_TOOLS or honeypot not in (None, "") or _is_bot(user_agent):
        return False
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(seconds) or seconds < MIN_FEEDBACK_SECONDS:
        return False
    vote = vote if isinstance(vote, str) else (None if vote is not None else "")
    if vote not in ("up", "down", ""):
        return False
    note = clean_note(note)
    if note is None or not (vote or note):
        return False
    now = datetime.now(ET)
    day = now.strftime("%Y-%m-%d")
    h = _hash(day, ip, user_agent)
    con = _connect()
    try:
        with con:
            _maybe_prune(con, day)
            mine = con.execute("SELECT COUNT(*) FROM feedback WHERE day=? AND tool=? AND h=?",
                               (day, tool, h)).fetchone()[0]
            if mine >= MAX_FEEDBACK_PER_VISITOR:
                return False
            if con.execute("SELECT COUNT(*) FROM feedback WHERE day=?",
                           (day,)).fetchone()[0] >= MAX_FEEDBACK_PER_DAY:
                return False
            note_vote = vote
            if note and not vote:       # a note after a vote: show it with that vote
                row = con.execute("SELECT vote FROM feedback WHERE day=? AND tool=? AND h=? "
                                  "AND vote != '' ORDER BY id DESC LIMIT 1",
                                  (day, tool, h)).fetchone()
                note_vote = row[0] if row else ""
            con.execute("INSERT INTO feedback (day, ts, tool, vote, note, note_vote, h) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (day, now.strftime("%Y-%m-%d %H:%M"), tool, vote, note,
                         note_vote if note else "", h))
        return True
    finally:
        con.close()


# ---- Reading ----
def _add(d, k, n):
    d[k] = d.get(k, 0) + (n or 0)


def summary(days=30, notes=50):
    """Everything the private dashboard shows, for the last `days` days (Eastern time).

    Visitors are unique per day (someone who comes back on three days counts three times);
    a tool's daily visitors are distinct across its pages."""
    con = _connect()
    try:
        since = (datetime.now(ET) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        q = lambda sql, *a: con.execute(sql, (since,) + a).fetchall()   # noqa: E731

        # Per day x page views; per day x page and day x tool unique visitors (live hashes
        # plus the counts kept for days whose hashes were deleted: the two never overlap).
        page_views, page_vis, tool_views, tool_vis = {}, {}, {}, {}
        for day, page, n in q("SELECT day, page, n FROM views WHERE day >= ?"):
            _add(page_views, (day, page), n)
            _add(tool_views, (day, tool_of(page)), n)
        for day, page, u in q("SELECT day, page, COUNT(*) FROM visitors WHERE day >= ? "
                              "GROUP BY day, page"):
            _add(page_vis, (day, page), u)
        for day, t, u in q(f"SELECT day, {_TOOL_SQL} AS t, COUNT(DISTINCT h) FROM visitors "
                           "WHERE day >= ? GROUP BY day, t"):
            _add(tool_vis, (day, t), u)
        for day, kind, key, u in q("SELECT day, kind, key, u FROM uniques WHERE day >= ? "
                                   "AND kind IN ('page', 'tool')"):
            _add(page_vis if kind == "page" else tool_vis, (day, key), u)

        days_seen = sorted({d for d, _ in tool_views} | {d for d, _ in tool_vis}, reverse=True)
        daily = [{"day": d, **{t: {"views": tool_views.get((d, t), 0),
                                   "visitors": tool_vis.get((d, t), 0)} for t in TOOLS}}
                 for d in days_seen]

        totals = {t: {"views": 0} for t in TOOLS}
        for page, n in con.execute("SELECT page, SUM(n) FROM views GROUP BY page"):
            totals.setdefault(tool_of(page), {"views": 0})["views"] += n or 0

        refs = {}
        for page, host, n in q("SELECT page, host, SUM(n) FROM referrers WHERE day >= ? "
                               "GROUP BY page, host"):
            _add(refs.setdefault(tool_of(page), {}), host, n)

        ev_count, ev_vis = {}, {}
        for t, e, n in q("SELECT tool, event, SUM(n) FROM events WHERE day >= ? "
                         "GROUP BY tool, event"):
            ev_count[(t, e)] = n or 0
        for t, e, u in q("SELECT tool, event, COUNT(*) FROM event_visitors WHERE day >= ? "
                         "GROUP BY tool, event"):
            _add(ev_vis, (t, e), u)
        for key, u in q("SELECT key, SUM(u) FROM uniques WHERE day >= ? AND kind = 'event' "
                        "GROUP BY key"):
            t, _, e = key.partition("/")
            _add(ev_vis, (t, e), u)

        votes = {}
        for t, v, n in q("SELECT tool, vote, COUNT(*) FROM feedback WHERE day >= ? "
                         "AND vote IN ('up', 'down') GROUP BY tool, vote"):
            votes[(t, v)] = n
        note_counts = dict(q("SELECT tool, COUNT(*) FROM feedback WHERE day >= ? AND note != '' "
                             "GROUP BY tool"))
        latest = [{"day": d, "time": ts, "tool": t, "tool_name": TOOLS.get(t, t),
                   "vote": nv or "", "note": n}
                  for d, ts, t, nv, n in q("SELECT day, ts, tool, note_vote, note FROM feedback "
                                           "WHERE day >= ? AND note != '' ORDER BY id DESC "
                                           "LIMIT ?", notes)]

        tools = []
        for t, name in TOOLS.items():
            pages = [p for p in PAGES if tool_of(p) == t]
            entry = {
                "id": t, "name": name,
                "views": sum(n for (d, k), n in tool_views.items() if k == t),
                "visitors": sum(n for (d, k), n in tool_vis.items() if k == t),
                "all_time_views": totals.get(t, {}).get("views", 0),
                "referrers": [{"host": h, "views": n} for h, n in
                              sorted(refs.get(t, {}).items(), key=lambda x: -x[1])[:15]],
            }
            if len(pages) > 1:
                entry["pages"] = [{"page": p, "name": PAGE_NAMES.get(p, p),
                                   "views": sum(n for (d, k), n in page_views.items() if k == p),
                                   "visitors": sum(n for (d, k), n in page_vis.items() if k == p)}
                                  for p in pages]
            if t in EVENTS:
                evs = [{"event": e, "label": label, "count": ev_count.get((t, e), 0),
                        "visitors": ev_vis.get((t, e), 0)} for e, label in EVENTS[t].items()]
                entry["events"] = sorted(evs, key=lambda x: (-x["count"], -x["visitors"]))
                entry["feedback"] = {"up": votes.get((t, "up"), 0),
                                     "down": votes.get((t, "down"), 0),
                                     "notes": note_counts.get(t, 0)}
            tools.append(entry)

        first = con.execute("SELECT MIN(day) FROM views").fetchone()[0]
        hashes_since = (datetime.now(ET) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        overall_refs = sorted(((h, t, n) for t, hs in refs.items() for h, n in hs.items()),
                              key=lambda x: -x[2])[:25]
        return {
            "days": days, "since": since, "first_day": first, "hashes_since": hashes_since,
            "tools": tools, "totals": totals, "daily": daily, "notes": latest,
            "referrers": [{"host": h, "tool": t, "views": n} for h, t, n in overall_refs],
        }
    finally:
        con.close()
