"""Tests for the GotOne Labs analytics (data/labstats.py, /api/hit, /api/event, /api/feedback,
/labs/stats) against docs/labs-analytics-contract.md. No pytest needed:

    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES .venv/bin/python tools/test_labstats.py

Each test gets its own empty stats directory (LABS_STATS_DIR); nothing touches ./cache/stats.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="labstats-test-"))
os.environ["LABS_STATS_DIR"] = str(TMP / "boot")
for var in ("RENDER", "LABS_STATS_KEY", "LABS_HOST", "LABS_HOME_URL"):
    os.environ.pop(var, None)

import app as appmod  # noqa: E402
from data import labstats  # noqa: E402

CLIENT = appmod.server.test_client()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
RECOUNT = "https://striperrecount.gotoneapp.com"
LABS = "https://labs.gotoneapp.com"
_n = [0]


def fresh():
    """A new, empty stats dir for one test."""
    _n[0] += 1
    d = TMP / f"t{_n[0]}"
    os.environ["LABS_STATS_DIR"] = str(d)
    return d


def post(path, body, origin=LABS, ua=UA, ip="203.0.113.7", **headers):
    h = {"User-Agent": ua, "X-Forwarded-For": ip, **headers}
    if origin:
        h["Origin"] = origin
    data = body if isinstance(body, (str, bytes)) else json.dumps(body)
    r = CLIENT.post(path, data=data, headers=h, content_type="text/plain;charset=UTF-8")
    assert r.status_code == 204, (path, r.status_code)
    return r


def hit(page, **kw):
    return post("/api/hit", {"p": page, "r": kw.pop("ref", "google.com")}, **kw)


def event(tool, name, **kw):
    return post("/api/event", {"t": tool, "e": name}, **kw)


def fb(tool="sst", v="up", n="", w="", d=10, **kw):
    body = {"t": tool, "v": v, "n": n, "w": w, "d": d}
    return post("/api/feedback", body, **kw)


def tool(s, t):
    return next(x for x in s["tools"] if x["id"] == t)


def ev_count(s, t, name):
    e = next(x for x in tool(s, t)["events"] if x["event"] == name)
    return e["count"], e["visitors"]


def rows(sql):
    con = labstats._connect()                          # creates the tables if nothing was written
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------- hits --
def test_hits_every_page_id():
    fresh()
    for p in labstats.PAGES:
        hit(p, origin=RECOUNT if p.startswith("recount") else LABS)
    s = labstats.summary(7)
    for t in ("sst", "currents", "labs"):
        assert tool(s, t)["views"] == 1, t
    rc = tool(s, "recount")
    assert rc["views"] == 9 and rc["visitors"] == 1, rc     # one visitor across nine pages
    assert {p["page"]: p["views"] for p in rc["pages"]} == {
        p: 1 for p in labstats.PAGES if p.startswith("recount")}
    assert all(p["visitors"] == 1 for p in rc["pages"])
    assert rc["referrers"] == [{"host": "google.com", "views": 9}]
    assert s["daily"][0]["recount"] == {"views": 9, "visitors": 1}


def test_hit_cross_origin_recount_counted():
    fresh()
    hit("recount/count", origin=RECOUNT)
    hit("recount", origin="http://striperrecount.gotoneapp.com")
    hit("recount/about", origin=None)                  # no Origin header: allowed for hits
    assert tool(labstats.summary(7), "recount")["views"] == 3


def test_hit_foreign_origin_ignored():
    fresh()
    for o in ("https://evil.example.com", "https://gotoneapp.com.evil.com", "null",
              "https://striperrecount.gotoneapp.co"):
        hit("sst", origin=o)
        event("sst", "load", origin=o)
    s = labstats.summary(7)
    assert tool(s, "sst")["views"] == 0 and ev_count(s, "sst", "load") == (0, 0)


def test_unknown_page_tool_event_ignored():
    fresh()
    for p in ("catches", "recount/nope", "RECOUNT", "", "sst/extra"):
        hit(p)
    event("sst", "nope")
    event("recount", "turn_everything")
    event("labs", "load")
    event("mars", "play")
    event("sst", "LOAD")
    s = labstats.summary(7)
    assert sum(t["views"] for t in s["tools"]) == 0
    assert rows("SELECT COUNT(*) FROM events") == [(0,)]


def test_bots_ignored():
    fresh()
    for ua in ("Googlebot/2.1 (+http://www.google.com/bot.html)",
               "Mozilla/5.0 HeadlessChrome/130.0", "curl/8.4.0", "python-requests/2.32", ""):
        hit("currents", ua=ua)
        event("currents", "play", ua=ua)
        fb("currents", ua=ua)
    s = labstats.summary(7)
    assert tool(s, "currents")["views"] == 0
    assert ev_count(s, "currents", "play") == (0, 0)
    assert tool(s, "currents")["feedback"]["up"] == 0


def test_dnt_gpc_ignored_for_hits_and_events_but_feedback_kept():
    fresh()
    hit("sst", DNT="1")
    hit("sst", **{"Sec-GPC": "1"})
    event("sst", "play", DNT="1")
    event("sst", "play", **{"Sec-GPC": "1"})
    fb("sst", v="down", DNT="1")
    fb("sst", v="up", ip="203.0.113.9", **{"Sec-GPC": "1"})
    s = labstats.summary(7)
    assert tool(s, "sst")["views"] == 0
    assert ev_count(s, "sst", "play") == (0, 0)
    assert tool(s, "sst")["feedback"] == {"up": 1, "down": 1, "notes": 0}


def test_bad_bodies_ignored():
    fresh()
    for body in ("", "not json", "[1,2]", '"sst"', "{", json.dumps({"p": "sst", "r": "x" * 600})):
        post("/api/hit", body)
        post("/api/event", body)
        post("/api/feedback", body)
    post("/api/feedback", {"t": "sst", "v": "up", "n": "x" * 5000, "w": "", "d": 10})   # > 4 KB
    assert sum(t["views"] for t in labstats.summary(7)["tools"]) == 0
    assert rows("SELECT COUNT(*) FROM feedback") == [(0,)]


def test_view_cap_per_visitor():
    fresh()
    for _ in range(labstats.MAX_VIEWS_PER_VISITOR + 5):
        hit("currents")
    hit("currents", ip="198.51.100.1")
    t = tool(labstats.summary(7), "currents")
    assert t["views"] == labstats.MAX_VIEWS_PER_VISITOR + 1 and t["visitors"] == 2


# -------------------------------------------------------------- events --
def test_events_counted_with_unique_visitors():
    fresh()
    event("sst", "load")
    event("sst", "load")
    event("sst", "load", ip="198.51.100.2")
    event("recount", "video_play", origin=RECOUNT)
    event("recount", "turn_pie", origin=None)
    event("currents", "inlet")
    s = labstats.summary(7)
    assert ev_count(s, "sst", "load") == (3, 2)
    assert ev_count(s, "recount", "video_play") == (1, 1)
    assert ev_count(s, "recount", "turn_pie") == (1, 1)
    assert ev_count(s, "currents", "inlet") == (1, 1)
    assert tool(s, "sst")["events"][0]["event"] == "load"        # most used first


def test_event_cap_per_visitor():
    fresh()
    for _ in range(labstats.MAX_EVENTS_PER_VISITOR + 10):
        event("currents", "scrub")
    event("currents", "scrub", ip="198.51.100.3")
    assert ev_count(labstats.summary(7), "currents", "scrub") == (
        labstats.MAX_EVENTS_PER_VISITOR + 1, 2)


def test_recount_whitelist_matches_contract():
    sections = ("count", "calendar", "ghosts", "pie", "removals", "states", "next")
    want = {"video_play", "video_25", "video_50", "video_75", "video_end",
            "share_four", "share_page", "lyrics", "short_version",
            "out_asga", "out_gotone", "out_asmfc", "out_labs"}
    want |= {f"{k}_{s}" for k in ("reach", "turn", "end") for s in sections}
    assert set(labstats.EVENTS["recount"]) == want
    pages = {"recount", "recount/about"} | {f"recount/{s}" for s in sections}
    assert set(labstats.PAGES) == pages | {"labs", "sst", "currents"}


# ------------------------------------------------------------ feedback --
def test_feedback_vote_and_note_joined():
    fresh()
    fb("currents", v="up")
    fb("currents", v="", n="  The Race timing was spot on  ")
    fb("currents", v="", n="note with no vote", ip="198.51.100.4")
    fb("sst", v="down", n="Cloudy week", ip="198.51.100.5")       # vote and note in one post
    s = labstats.summary(7)
    assert tool(s, "currents")["feedback"] == {"up": 1, "down": 0, "notes": 2}
    assert tool(s, "sst")["feedback"] == {"up": 0, "down": 1, "notes": 1}
    notes = {n["note"]: n for n in s["notes"]}
    assert notes["The Race timing was spot on"]["vote"] == "up"
    assert notes["note with no vote"]["vote"] == ""
    assert notes["Cloudy week"]["vote"] == "down"
    assert s["notes"][0]["note"] == "Cloudy week"                # newest first


def test_feedback_from_recount_cross_origin():
    fresh()
    fb("recount", v="up", origin=RECOUNT)
    assert tool(labstats.summary(7), "recount")["feedback"]["up"] == 1


def test_feedback_requires_origin():
    fresh()
    fb("sst", origin=None)
    fb("sst", origin="https://evil.example.com")
    fb("sst", origin="null")
    assert rows("SELECT COUNT(*) FROM feedback") == [(0,)]
    fb("sst", origin="http://localhost:8052")                    # dev server: ours
    assert rows("SELECT COUNT(*) FROM feedback") == [(1,)]


def test_feedback_honeypot_dwell_vote_rules():
    fresh()
    fb(w="https://spam.example")
    fb(w=" ")
    fb(w=0)
    fb(d=2.9)
    fb(d=None)
    fb(d="NaN")
    fb(d="soon")
    fb(v="", n="")                     # nothing to record
    fb(v="", n=" \n\t ")              # only whitespace / controls
    fb(v="maybe")
    fb(v=1)
    fb(tool="labs")
    fb(tool="mars")
    fb(n=["not", "text"])
    assert rows("SELECT COUNT(*) FROM feedback") == [(0,)]
    fb(d=3)                            # exactly 3 s is enough
    fb(d="7.5", v="down")
    assert rows("SELECT COUNT(*) FROM feedback") == [(2,)]


def test_feedback_note_length_and_cleaning():
    fresh()
    fb(v="", n="x" * 501)
    assert rows("SELECT COUNT(*) FROM feedback") == [(0,)]
    fb(v="", n="  " + "y" * 500 + "  ")                            # trimmed to 500: ok
    fb(v="", n="a\x00b\x07c\r\nd‮e\u0085f\tg", ip="198.51.100.6")
    got = [r[0] for r in rows("SELECT note FROM feedback ORDER BY id")]
    assert got == ["y" * 500, "abc de f g"], got


def test_feedback_rate_limits():
    fresh()
    for i in range(labstats.MAX_FEEDBACK_PER_VISITOR + 3):
        fb("sst", v="up" if i % 2 else "down")
    fb("currents", v="up")                                       # other tools have their own 5
    assert rows("SELECT tool, COUNT(*) FROM feedback GROUP BY tool ORDER BY tool") == [
        ("currents", 1), ("sst", labstats.MAX_FEEDBACK_PER_VISITOR)]
    fresh()
    for i in range(labstats.MAX_FEEDBACK_PER_DAY + 20):
        fb("recount", v="up", ip=f"10.0.{i // 250}.{i % 250}", origin=RECOUNT)
    assert rows("SELECT COUNT(*) FROM feedback") == [(labstats.MAX_FEEDBACK_PER_DAY,)]


# ------------------------------------------------------- data lifecycle --
def test_old_hashes_pruned_but_counts_kept():
    fresh()
    old = (datetime.now(labstats.ET) - timedelta(days=120)).strftime("%Y-%m-%d")
    labstats.summary(7)                                          # creates the tables
    con = sqlite3.connect(labstats.stats_dir() / "labs.sqlite")
    with con:
        con.executemany("INSERT INTO views VALUES (?,?,?)",
                        [(old, "recount", 3), (old, "recount/pie", 2), (old, "sst", 1)])
        con.executemany("INSERT INTO visitors VALUES (?,?,?,?)",
                        [(old, "recount", "h1", 2), (old, "recount/pie", "h1", 1),
                         (old, "recount", "h2", 1), (old, "recount/pie", "h3", 1),
                         (old, "sst", "h1", 1)])
        con.executemany("INSERT INTO event_visitors VALUES (?,?,?,?,?)",
                        [(old, "sst", "load", "h1", 2), (old, "sst", "load", "h2", 1)])
        con.execute("INSERT INTO events VALUES (?,?,?,?)", (old, "sst", "load", 3))
        con.execute("INSERT INTO feedback (day, ts, tool, vote, note, note_vote, h) "
                    "VALUES (?,?,?,?,?,?,?)", (old, old + " 09:00", "sst", "up", "", "", "h1"))
    con.close()
    labstats._pruned.clear()
    hit("currents")                                              # first write today prunes
    assert rows(f"SELECT COUNT(*) FROM visitors WHERE day = '{old}'") == [(0,)]
    assert rows(f"SELECT COUNT(*) FROM event_visitors WHERE day = '{old}'") == [(0,)]
    assert rows("SELECT h FROM feedback") == [(None,)]
    s = labstats.summary(365)
    rc = tool(s, "recount")
    assert rc["views"] == 5 and rc["visitors"] == 3, rc          # h1, h2, h3 across its pages
    assert {p["page"]: p["visitors"] for p in rc["pages"]}["recount/pie"] == 2
    assert tool(s, "sst")["visitors"] == 1
    assert ev_count(s, "sst", "load") == (3, 2)
    assert tool(s, "sst")["feedback"]["up"] == 1
    assert tool(labstats.summary(30), "recount")["views"] == 0   # outside the period


def test_existing_database_keeps_working():
    """A stats DB from before this change (only views / visitors / referrers) still reads."""
    d = fresh()
    d.mkdir(parents=True)
    today = datetime.now(labstats.ET).strftime("%Y-%m-%d")
    con = sqlite3.connect(d / "labs.sqlite")
    con.executescript("""
CREATE TABLE views (day TEXT, page TEXT, n INTEGER, PRIMARY KEY (day, page));
CREATE TABLE visitors (day TEXT, page TEXT, h TEXT, n INTEGER, PRIMARY KEY (day, page, h));
CREATE TABLE referrers (day TEXT, page TEXT, host TEXT, n INTEGER, PRIMARY KEY (day, page, host));
""")
    with con:
        con.execute("INSERT INTO views VALUES (?, 'sst', 4)", (today,))
        con.execute("INSERT INTO visitors VALUES (?, 'sst', 'abc', 4)", (today,))
        con.execute("INSERT INTO referrers VALUES (?, 'sst', 'gotoneapp.com (embedded)', 4)",
                    (today,))
    con.close()
    hit("sst", ref="facebook.com")
    s = labstats.summary(30)
    assert tool(s, "sst")["views"] == 5 and tool(s, "sst")["visitors"] == 2
    assert {r["host"] for r in tool(s, "sst")["referrers"]} == {
        "gotoneapp.com (embedded)", "facebook.com"}


# ------------------------------------------------------------ dashboard --
def test_dashboard_renders_and_escapes():
    fresh()
    hit("sst")
    hit("recount/ghosts", origin=RECOUNT, ref="<b>bad</b>.example")
    event("sst", "readout")
    event("recount", "lyrics", origin=RECOUNT)
    fb("currents", v="up")
    fb("currents", v="", n='<script>alert("x")</script> & <b>bold</b>')
    for days in (7, 30, 90, 365):
        r = CLIENT.get(f"/labs/stats?days={days}")
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "no-store"
        assert "noindex" in r.headers.get("X-Robots-Tag", "")
        page = r.get_data(as_text=True)
        assert "<script" not in page and "<b>bold" not in page and "<b>bad" not in page
        assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;" in page
        for name in ("Offshore SST", "Currents", "The Striper Recount", "Latest notes",
                     "By day", "Feature use", "ghosts.html", "Tapped the map for a temperature",
                     "Opened the lyrics", "name=viewport", "noindex"):
            assert name in page, (name, days)
        assert "—" not in page                                    # no em dashes
    j = CLIENT.get("/api/labs/stats?days=7").get_json()
    assert tool(j, "sst")["views"] == 1 and j["notes"][0]["vote"] == "up"
    assert CLIENT.get("/api/labs/stats").headers["Cache-Control"] == "no-store"


def test_stats_closed_on_render_without_key():
    fresh()
    os.environ["RENDER"] = "true"
    try:
        assert CLIENT.get("/labs/stats").status_code == 404
        assert CLIENT.get("/api/labs/stats").status_code == 404
        appmod.LABS_STATS_KEY = "s3cret"
        assert CLIENT.get("/labs/stats?key=wrong").status_code == 404
        assert CLIENT.get("/api/labs/stats").status_code == 404
        assert CLIENT.get("/labs/stats?key=s3cret&days=90").status_code == 200
        assert CLIENT.get("/api/labs/stats?key=s3cret").status_code == 200
        page = CLIENT.get("/labs/stats?key=s3cret").get_data(as_text=True)
        assert "?key=s3cret&amp;days=365" in page                  # period links keep the key
    finally:
        os.environ.pop("RENDER", None)
        appmod.LABS_STATS_KEY = None


# ------------------------------------------------------------ redirects --
def test_redirects_leave_api_alone():
    fresh()
    appmod.LABS_HOME_URL = "https://www.gotoneapp.com/labs"
    appmod.LABS_HOST = "labs.gotoneapp.com"
    try:
        old = {"Host": "sst.gotoneapp.com"}
        new = {"Host": "labs.gotoneapp.com"}
        # Pages still redirect as before ...
        r = CLIENT.get("/", headers=new)
        assert r.status_code == 302 and r.headers["Location"] == appmod.LABS_HOME_URL
        assert CLIENT.get("/labs", headers=new).status_code == 302
        r = CLIENT.get("/", headers=old)
        assert r.status_code == 301 and r.headers["Location"] == "https://labs.gotoneapp.com/sst"
        assert CLIENT.get("/currents?x=1", headers=old).headers["Location"] == \
            "https://labs.gotoneapp.com/currents?x=1"
        assert CLIENT.get("/striper-recount", headers=new).status_code == 302
        # ... but never an API, beacon, asset or the stats, on either host, GET or POST
        for h in (old, new):
            for path in ("/api/hit", "/api/event", "/api/feedback"):
                r = CLIENT.post(path, data='{"p":"sst","t":"sst","e":"load","v":"up","d":9}',
                                headers={**h, "User-Agent": UA, "Origin": LABS},
                                content_type="text/plain")
                assert r.status_code == 204, (path, h, r.status_code)
                assert CLIENT.get(path, headers=h).status_code not in (301, 302), path
            for path in ("/api/currents/manifest.json", "/api/labs/stats", "/labs/stats",
                         "/assets/labs.js", "/api/striper-recount", "/api/labs"):
                r = CLIENT.get(path, headers=h)
                assert r.status_code not in (301, 302), (path, h, r.status_code)
        s = labstats.summary(7)
        assert tool(s, "sst")["views"] == 2 and tool(s, "sst")["visitors"] == 1   # both hosts
        assert ev_count(s, "sst", "load") == (2, 1)
        assert tool(s, "sst")["feedback"]["up"] == 2
    finally:
        appmod.LABS_HOME_URL = ""
        appmod.LABS_HOST = ""


def _json(component):
    return json.dumps(component.to_plotly_json(), default=lambda o: (
        o.to_plotly_json() if hasattr(o, "to_plotly_json") else str(o)))


def test_pages_render_with_helpful_control():
    """/sst and /currents layouts carry the Helpful container and the event hooks."""
    with appmod.server.test_request_context("/"):
        for path, t in (("/sst", "sst"), ("/currents", "currents")):
            blob = _json(appmod.route_page(path, ""))
            assert '"labs-helpful"' in blob and f'"data-tool": "{t}"' in blob, path
        sst = _json(appmod.build_sst_page())
    assert '"data-labs-event": "step"' in sst and "sst-events-sink" in sst


def test_client_event_names_whitelisted():
    """Every name the client code sends for sst / currents is in labstats.EVENTS."""
    import re
    js = (ROOT / "assets" / "currents.js").read_text()
    sent = set(re.findall(r'track\("([a-z0-9_]+)"\)', js))
    sent |= {"view_today", "view_cycle", "view_weeks", "inlet", "spot"}
    py = (ROOT / "app.py").read_text()
    sst_sent = set(re.findall(r'ev\("([a-z0-9_]+)"\)', py))
    sst_sent |= set(re.findall(r'(?:contours|gebco|gfw): "([a-z_]+)"', py))
    sst_sent.add("step")                                         # data-labs-event in sidebar.py
    # the Colors panel (assets/sstlook.js): track("..."), the preset map and track(id) for the toggles
    look = (ROOT / "assets" / "sstlook.js").read_text()
    sst_sent |= set(re.findall(r'track\("([a-z0-9_]+)"\)', look))
    sst_sent |= set(re.findall(r': "(preset_[a-z]+)"', look))
    toggles = re.search(r"var TOGGLES = \[(.*?)\];", look, re.S).group(1)
    sst_sent |= set(re.findall(r'id: "([a-z]+)"', toggles))
    assert "track(id)" in look and "track(EVENT[id])" in look
    contract = (ROOT / "docs" / "labs-analytics-contract.md").read_text()
    missing = [e for e in labstats.EVENTS["sst"] if f"`{e}`" not in contract]
    assert not missing, f"not in docs/labs-analytics-contract.md: {missing}"
    assert '"how_it_works"' in py
    sent.add("how_it_works")
    assert sent == set(labstats.EVENTS["currents"]), sent ^ set(labstats.EVENTS["currents"])
    assert sst_sent == set(labstats.EVENTS["sst"]), sst_sent ^ set(labstats.EVENTS["sst"])


def main():
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
