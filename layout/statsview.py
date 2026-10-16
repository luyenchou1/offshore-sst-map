"""The private GotOne Labs stats page (/labs/stats?key=...): plain HTML from labstats.summary().

Per tool: views and daily-unique visitors for the period (The Striper Recount by page), where
visits came from, feature use and Helpful votes; then the latest notes and the by-day table.
Every value from the database is escaped (notes and referrer hosts come from visitors).
"""

from html import escape
from urllib.parse import quote

PERIODS = (7, 30, 90, 365)
_VOTE = {"up": "Helpful", "down": "Not helpful", "": "no vote"}

_CSS = """
body{font:15px/1.45 "Helvetica Neue",Helvetica,Arial,sans-serif;margin:0;background:#f3f5fa;color:#0f1d3d}
.w{max-width:900px;margin:0 auto;padding:20px 16px 48px} h1{color:#091d50;margin:0 0 4px;font-size:1.5rem}
h2{font-size:1.2rem;margin:34px 0 6px;color:#091d50;border-top:3px solid #091d50;padding-top:10px}
h3{font-size:.98rem;margin:18px 0 6px;color:#091d50} .m{color:#44506a;font-size:.9rem;margin:4px 0}
.per a{display:inline-block;margin:2px 6px 2px 0;padding:3px 10px;border-radius:100px;background:#fff;
color:#091d50;text-decoration:none;border:1px solid #c9d1e3}
.per a.on{background:#091d50;color:#fff;border-color:#091d50}
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.kpi{background:#fff;border-radius:10px;padding:8px 12px;min-width:92px}
.kpi b{display:block;font-size:1.35rem;color:#091d50;line-height:1.2} .kpi span{font-size:.8rem;color:#44506a}
table{border-collapse:collapse;width:100%;background:#fff;border-radius:10px;overflow:hidden}
th,td{padding:7px 10px;border-bottom:1px solid #e5e9f2;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left} th{background:#091d50;color:#fff;font-weight:600}
td.l{white-space:normal;min-width:9em} .v{color:#44506a} .s{overflow-x:auto}
.code{font-family:Menlo,Consolas,monospace;font-size:.82rem;color:#44506a}
.unused{color:#44506a;font-size:.85rem;margin:6px 0 0}
.note{background:#fff;border-radius:10px;padding:9px 12px;margin:0 0 8px}
.note .h{font-size:.82rem;color:#44506a;margin-bottom:2px} .note p{margin:0;overflow-wrap:anywhere}
.up{color:#0a7d38;font-weight:600} .down{color:#b42318;font-weight:600}
"""


def _table(head, rows, empty="None yet"):
    cols = len(head)
    body = "".join(rows) or f"<tr><td colspan={cols}>{empty}</td></tr>"
    return (f"<div class=s><table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>"
            f"{body}</table></div>")


def _kpi(value, label):
    return f"<div class=kpi><b>{value:,}</b><span>{label}</span></div>"


def _tool_section(t):
    out = [f"<h2>{escape(t['name'])}</h2><div class=kpis>",
           _kpi(t["views"], "views"), _kpi(t["visitors"], "visitors (daily unique)")]
    fb = t.get("feedback")
    if fb is not None:
        out += [_kpi(fb["up"], "Helpful"), _kpi(fb["down"], "Not helpful"), _kpi(fb["notes"], "notes")]
    out.append("</div>")
    if t.get("pages"):
        out.append("<h3>By page</h3>")
        out.append(_table(["Page", "Views", "Visitors"], [
            f"<tr><td class=l>{escape(p['name'])} <span class=code>{escape(p['page'])}</span></td>"
            f"<td>{p['views']:,}</td><td class=v>{p['visitors']:,}</td></tr>" for p in t["pages"]]))
    out.append("<h3>Where visits came from</h3>")
    out.append(_table(["Site", "Views"], [
        f"<tr><td class=l>{escape(r['host'])}</td><td>{r['views']:,}</td></tr>"
        for r in t["referrers"]]))
    if "events" in t:
        used = [e for e in t["events"] if e["count"]]
        unused = [e for e in t["events"] if not e["count"]]
        out.append("<h3>Feature use</h3>")
        out.append(_table(["What people did", "Page views", "Visitors"], [
            f"<tr><td class=l>{escape(e['label'])} <span class=code>{escape(e['event'])}</span></td>"
            f"<td>{e['count']:,}</td><td class=v>{e['visitors']:,}</td></tr>" for e in used]))
        if unused:
            out.append("<p class=unused>Not used yet: " +
                       ", ".join(f"<span class=code>{escape(e['event'])}</span>" for e in unused)
                       + "</p>")
    return "".join(out)


def _notes(notes):
    if not notes:
        return "<p class=m>None yet.</p>"
    return "".join(
        f"<div class=note><div class=h>{escape(n['time'])} · {escape(n['tool_name'])} · "
        f"<span class={escape(n['vote']) or 'v'}>{_VOTE.get(n['vote'], 'no vote')}</span></div>"
        f"<p>{escape(n['note'])}</p></div>" for n in notes)


def render_stats_page(s, key=""):
    days = s["days"]
    k = quote(key or "", safe="")
    links = "".join(f"<a href='?key={k}&amp;days={n}'{' class=on' if n == days else ''}>"
                    f"{n} days</a>" for n in PERIODS)
    tools = [t for t in s["tools"] if t["id"] != "labs" or t["views"] or t["all_time_views"]]
    head = "<tr><th>Day</th>" + "".join(f"<th colspan=2>{escape(t['name'])}</th>"
                                        for t in tools) + "</tr>"
    sub = "<tr><th></th>" + "<th>views</th><th>visitors</th>" * len(tools) + "</tr>"
    rows = "".join(
        f"<tr><td>{d['day']}</td>" + "".join(
            f"<td>{d[t['id']]['views']:,}</td><td class=v>{d[t['id']]['visitors']:,}</td>"
            for t in tools) + "</tr>" for d in s["daily"])
    total = ("<tr><td><b>Total</b></td>" + "".join(
        f"<td><b>{t['views']:,}</b></td><td class=v><b>{t['visitors']:,}</b></td>"
        for t in tools) + "</tr>")
    all_time = _table(["", *[escape(t["name"]) for t in tools]],
                      ["<tr><td>Views</td>" + "".join(f"<td>{t['all_time_views']:,}</td>"
                                                      for t in tools) + "</tr>"])
    older = ("" if days <= 90 else
             f" Visitor counts before {s['hashes_since']} come from the daily totals kept when "
             "the hashes were deleted.")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=robots content="noindex,nofollow">
<title>GotOne Labs stats</title><style>{_CSS}</style></head><body><div class=w>
<h1>GotOne Labs stats</h1>
<p class="m per">Last {days} days (Eastern time): {links}</p>
<p class=m>Visitors are unique per day: someone who comes back on three days counts three times.
"Page views" in feature use means page views that did it at least once. No cookies; nothing
about a visitor is stored; Do Not Track respected (feedback is sent only when someone chooses
to).{older}</p>
{''.join(_tool_section(t) for t in tools)}
<h2>Latest notes</h2>{_notes(s['notes'])}
<h2>By day</h2><div class=s><table>{head}{sub}{total}{rows}</table></div>
<h3>All time (views, since {escape(s['first_day'] or '-')})</h3>{all_time}
</div></body></html>"""
