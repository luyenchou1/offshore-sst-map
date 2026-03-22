"""Color scale bounds and legend component.

Bug 2 fix: removed 48°F hard floor that broke winter/spring visualization.
"""

import numpy as np
from dash import html

SST_COLORS = [
    "#2c7fb8", "#41b6c4", "#7fcdbb", "#c7e9b4", "#ffffcc",
    "#fde68a", "#fca35d", "#fb6a4a", "#ef3b2c", "#cb181d", "#99000d",
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


def build_legend_component(vmin: float, vmax: float, res_km: float = None):
    """Return a Dash html.Div positioned as a map overlay color legend."""
    gradient = ", ".join(SST_COLORS)
    n_ticks = 5
    tick_vals = np.linspace(vmin, vmax, n_ticks)
    ticks = []
    for i, v in enumerate(tick_vals):
        pct = i / (n_ticks - 1) * 100
        ticks.append(
            html.Span(
                f"{v:.0f}",
                style={
                    "position": "absolute",
                    "left": f"{pct}%",
                    "transform": "translateX(-50%)",
                    "fontSize": "11px",
                    "color": "#333",
                },
            )
        )

    # Format resolution label
    if res_km is not None:
        if res_km < 2:
            res_label = f"{res_km:.1f} km grid"
        else:
            res_label = f"{res_km:.0f} km grid"
    else:
        res_label = None

    children = [
        html.Div(
            "SST (°F)",
            style={
                "fontWeight": "600",
                "fontSize": "12px",
                "marginBottom": "4px",
                "color": "#333",
            },
        ),
        html.Div(
            style={
                "width": "200px",
                "height": "14px",
                "background": f"linear-gradient(to right, {gradient})",
                "borderRadius": "2px",
                "border": "1px solid #999",
            }
        ),
        html.Div(
            ticks,
            style={
                "position": "relative",
                "width": "200px",
                "height": "18px",
                "marginTop": "2px",
            },
        ),
    ]

    if res_label:
        children.append(
            html.Div(
                res_label,
                style={
                    "fontSize": "10px",
                    "color": "#666",
                    "marginTop": "4px",
                    "textAlign": "right",
                },
            )
        )

    return html.Div(
        children,
        style={
            "position": "absolute",
            "bottom": "40px",
            "right": "10px",
            "zIndex": "999",
            "backgroundColor": "rgba(255,255,255,0.92)",
            "padding": "8px 12px",
            "borderRadius": "4px",
            "boxShadow": "0 1px 4px rgba(0,0,0,0.3)",
        },
    )
