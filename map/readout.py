"""Readouts for the SST map: the tapped-point / spot card and the measure panel.

Built by app.py's render_selection for the day on the map, so a reading, a spot
card or a measurement survives day (and week) changes and shows each day's value.
Styles: .sst-tip* in assets/tooltips.css, .mr-* in assets/sst.css.
"""

from datetime import datetime

import numpy as np
from dash import html

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def fmt_temp(t):
    """'71 °F' for whole degrees (today's data), '71.3 °F' once tenths arrive."""
    if t is None:
        return "no data"
    if abs(t - round(t)) < 0.05:
        return f"{round(t):.0f} °F"
    return f"{t:.1f} °F"


def fmt_pos(lat, lng):
    return f"{lat:.3f}°N, {abs(lng):.3f}°{'W' if lng < 0 else 'E'}"


def fmt_day(iso, with_year=False):
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
    s = f"{_DAYS[d.weekday()]}, {d.strftime('%b')} {d.day}"
    return s + (f", {d.year}" if with_year else "")


def week_temps(raw, lat, lng):
    """The point's temperature on each day of the loaded week (None = no data,
    land or outside the grid)."""
    lats, lons = raw["lats"], raw["lons"]
    n = len(raw["raw_days"])
    if len(lats) < 2 or len(lons) < 2:
        return [None] * n
    half_lat = abs(float(lats[1] - lats[0])) / 2
    half_lon = abs(float(lons[1] - lons[0])) / 2
    if not (float(np.min(lats)) - half_lat <= lat <= float(np.max(lats)) + half_lat
            and float(np.min(lons)) - half_lon <= lng <= float(np.max(lons)) + half_lon):
        return [None] * n
    i = int(np.argmin(np.abs(lats - lat)))
    j = int(np.argmin(np.abs(lons - lng)))
    out = []
    for d in raw["raw_days"]:
        v = d["arrF"][i, j]
        out.append(float(v) if np.isfinite(v) else None)
    return out


def _week_strip(temps, dates, idx):
    """Seven small cells: each day's temperature over its day of the month,
    today's (the day on the map) highlighted."""
    if not temps or all(t is None for t in temps):
        return None
    cells = []
    for k, t in enumerate(temps):
        day = (dates[k][8:10].lstrip("0") if k < len(dates) and dates[k] else "")
        cells.append(html.Div(
            [html.Div("" if t is None else f"{round(t):.0f}", className="sst-wk-t"),
             html.Div(day, className="sst-wk-d")],
            className="sst-wk-cell" + (" cur" if k == idx else ""),
        ))
    return html.Div(cells, className="sst-week", title="This spot each day of the week, °F")


def selection_tip(sel, temps, dates, idx):
    """Tooltip body for a reading, a spot or a point inside The Dump."""
    t = temps[idx] if temps and 0 <= idx < len(temps) else None
    kind = sel.get("kind", "reading")
    children = []
    if kind in ("poi", "dump"):
        children.append(html.Div(sel.get("name", ""), className="sst-tip-name"))
    if temps is None:                    # the week's grid isn't in memory or on disk
        children.append(html.Div("Temperature not available; load the week again",
                                 className="sst-tip-none"))
    elif t is None:
        children.append(html.Div("No data here", className="sst-tip-none"))
    else:
        children.append(html.Div(fmt_temp(t), className="sst-tip-temp"))
    if 0 <= idx < len(dates):
        children.append(html.Div(fmt_day(dates[idx]), className="sst-tip-date"))
    children.append(html.Div(fmt_pos(sel["lat"], sel["lng"]), className="sst-tip-pos"))
    strip = _week_strip(temps, dates, idx)
    if strip is not None:
        children.append(strip)
    return html.Div(children, className="sst-tip" + (" sst-tip-poi" if kind == "poi" else ""))


def point_label(pt):
    return pt.get("label") or fmt_pos(pt["lat"], pt["lng"])


def measure_panel(measure, m, temp_a, temp_b, day_label):
    """The sidebar's measure readout (light text on the navy sidebar)."""
    mode = (measure or {}).get("mode", "off")
    if mode == "a":
        return html.Div("Tap the first point (A) on the map.", className="mr-hint")
    a = measure.get("a") or {}
    if mode == "b":
        return html.Div([
            html.Div(f"A: {point_label(a)} · {fmt_temp(temp_a)}", className="mr-pt"),
            html.Div("Now tap the second point (B).", className="mr-hint"),
        ])
    if mode != "done" or not m:
        return ""
    b = measure.get("b") or {}
    return html.Div([
        html.Div(f"{m['nm']:.1f} nm ({m['mi']:.1f} mi)", className="mr-dist"),
        html.Div(f"Course {m['true_text']} true, {m['magnetic_text']} magnetic ({m['compass']})",
                 className="mr-course"),
        html.Div(f"A: {point_label(a)} · {fmt_temp(temp_a)}", className="mr-pt"),
        html.Div(f"B: {point_label(b)} · {fmt_temp(temp_b)}", className="mr-pt"),
        html.Div(f"Temperatures on {day_label}. Magnetic uses {m['variation_text']} variation "
                 "at A (WMM-2025).", className="mr-note"),
    ])

