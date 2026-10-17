"""Color scale bounds and legend component.

Bug 2 fix: removed 48°F hard floor that broke winter/spring visualization.
"""

import math

import numpy as np
from dash import html

SST_COLORS = [
    "#2c7fb8", "#41b6c4", "#7fcdbb", "#c7e9b4", "#ffffcc",
    "#fde68a", "#fca35d", "#fb6a4a", "#ef3b2c", "#cb181d", "#99000d",
]

# The default palette since sprint 2 ("spectrum", design-notes.md: Turbo sampled from 5% to 100%, 13 even stops, linear
# in sRGB). assets/sstengine.js has the same stops (PALETTES.spectrum); SST_COLORS above is "classic".
SPECTRUM_COLORS = [
    "#3d358b", "#476ee6", "#3ba0fd", "#1bd0d5", "#2aefa1", "#75fe5c", "#b4f836",
    "#e3db38", "#fcb136", "#f9751d", "#e14109", "#b71d02", "#7a0403",
]


def compute_color_bounds(arrF: np.ndarray, locked: bool = False):
    """Return (vmin, vmax) for the SST color scale.

    Bug 2 fix: The old code used max(percentile_5, 48) which made the entire
    winter color map collapse to a single color.  Now uses 28°F floor and
    ensures at least 5°F of spread.
    """
    if locked:
        return 30.0, 90.0

    finite = arrF[np.isfinite(arrF)]
    if finite.size < 50:
        return 30.0, 90.0

    vmin = max(float(np.nanpercentile(finite, 5)), 28.0)
    vmax = min(float(np.nanpercentile(finite, 95)), 95.0)

    # Ensure at least 5°F spread for visual differentiation
    if vmax - vmin < 5.0:
        mid = (vmin + vmax) / 2
        vmin = mid - 3.0
        vmax = mid + 3.0

    return vmin, vmax


def legend_ticks(vmin: float, vmax: float, max_ticks: int = 6):
    """Round tick values (every 1, 2, 5, 10 or 20 °F) inside [vmin, vmax]."""
    span = max(vmax - vmin, 1e-6)
    step = 20
    for s in (1, 2, 5, 10, 20):
        if span / s <= max_ticks:
            step = s
            break
    v = math.ceil(vmin / step) * step
    out = []
    while v <= vmax + 1e-9:
        out.append(v)
        v += step
    return out


def build_legend_component(vmin: float, vmax: float, res_km: float = None,
                           locked: bool = False, coarse: bool = False):
    """The map's color legend (a dark translucent card like Currents'). It says
    whether the scale is this week's range (adaptive, the default) or fixed
    (Same colors every week), with round ticks and what the end colors mean.
    Phones get a compact version (assets/sst.css). The source and grid size
    are in the status strip under the map."""
    gradient = ", ".join(SPECTRUM_COLORS)   # SSTEngine's default palette
    span = max(vmax - vmin, 1e-6)
    ticks = []
    for v in legend_ticks(vmin, vmax):
        pct = (v - vmin) / span * 100
        if pct < 4 or pct > 96:          # too close to an end to read; the note says the ends
            continue
        ticks.append(html.Span(f"{v:.0f}", className="sst-legend-tick", style={"left": f"{pct:.1f}%"}))
    scale = "fixed scale" if locked else "this week's range"
    if locked:
        note = f"Same colors every week: {vmin:.0f} to {vmax:.0f} \u00b0F."
    else:
        note = (f"Colder than {vmin:.0f} or warmer than {vmax:.0f} \u00b0F "
                "shows in the end colors.")
    children = [
        html.Div([html.Span("Water \u00b0F", className="sst-legend-title"),
                  html.Span(scale, className="sst-legend-scale")], className="sst-legend-head"),
        html.Div([html.Span(f"{vmin:.0f}", className="sst-legend-end"),
                  html.Div(className="sst-legend-bar",
                           style={"background": f"linear-gradient(to right, {gradient})"}),
                  html.Span(f"{vmax:.0f}", className="sst-legend-end")],
                 className="sst-legend-row"),
        html.Div(ticks, className="sst-legend-ticks"),
        html.Div(note, className="sst-legend-note"),
    ]
    if coarse:
        children.append(html.Div("25 km grid: broad pattern only", className="sst-legend-warn"))
    return html.Div(children, className="sst-legend" + (" locked" if locked else ""))
