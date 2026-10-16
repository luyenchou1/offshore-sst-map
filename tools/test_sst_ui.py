"""Checks for the SST page's readout, legend, status and measure helpers (no server, no pytest):

    .venv/bin/python tools/test_sst_ui.py

The browser side (status strip, tap routing, layout at three sizes) is tools/smoke_sst.mjs.
"""

import sys
import traceback
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from layout.sidebar import day_marks                                    # noqa: E402
from map.colorscale import build_legend_component, legend_ticks          # noqa: E402
from map.measure import format_measurement, magnetic_variation           # noqa: E402
from map.pois import ALL_POIS, pois_payload                              # noqa: E402
from map.readout import (fmt_temp, measure_panel, selection_tip,         # noqa: E402
                         week_status_text, week_temps)


def text_of(c):
    """All text in a Dash component tree."""
    if c is None:
        return ""
    if isinstance(c, (str, int, float)):
        return str(c)
    if isinstance(c, (list, tuple)):
        return " ".join(text_of(x) for x in c)
    return text_of(getattr(c, "children", None))


def test_declination_matches_noaa():
    # NOAA's calculator (WMM-2025), Oct 2026, as quoted in docs/sst-review.md
    when = date(2026, 10, 16)
    assert abs(magnetic_variation(39.54, -72.05, when) - (-13.1)) < 0.1
    assert abs(magnetic_variation(42.35, -70.35, when) - (-14.2)) < 0.1
    # clamped outside the grid, and still west everywhere in the area
    assert -16 < magnetic_variation(50, -60, when) < -10


def test_measurement_true_and_magnetic():
    m = format_measurement(41.0, -71.8083, 39.54, -72.05, date(2026, 10, 16))   # Cartwright -> Hudson Canyon
    assert abs(m["nm"] - 88.4) < 0.1
    assert m["true_text"] == "187°"
    assert m["magnetic_text"] == "201°"            # 187 + 13.4 W
    assert "true" in m["label"] and "magnetic" in m["label"]
    m = format_measurement(41.0, -71.0, 41.5, -71.0)      # due north: 000 true
    assert m["true_text"] == "000°"


def test_week_status_text_without_and_with_meta():
    days = [f"2026-10-{d:02d}" for d in range(6, 13)]
    assert week_status_text({"dates": days}) == "Oct 6 to 12 loaded"
    # today's payloads: no meta, the requested end date comes from the callback
    assert week_status_text({"dates": days}, date(2026, 10, 14)) == \
        "Oct 6 to 12 loaded, the closest week to Oct 14"
    # lane D's meta (fake): a nearby week served, a day missing
    meta = {"source": "MUR", "requested_end": "2026-10-13", "served_end": "2026-10-12",
            "days": days[:6] + days[7:], "latest_available": "2026-10-15"}
    t = week_status_text({"dates": days[:5] + days[6:], "meta": meta})
    assert t == "Oct 6 to 12 loaded, the closest week to Oct 13 (6 of 7 days)", t
    assert week_status_text({"dates": ["2026-09-30", "2026-10-06"]}).startswith("Sep 30 to Oct 6")
    assert week_status_text({}) == "No days loaded"


def test_legend_says_scale_and_has_round_ticks():
    assert legend_ticks(56.3, 74.2) == [60, 65, 70]
    assert legend_ticks(30, 90) == [30, 40, 50, 60, 70, 80, 90]
    t = text_of(build_legend_component(56.3, 74.2))
    assert "this week's range" in t and "56" in t and "74" in t and "end colors" in t
    t = text_of(build_legend_component(30, 90, locked=True))
    assert "fixed scale" in t and "30 to 90" in t
    assert "broad pattern only" in text_of(build_legend_component(50, 60, coarse=True))


def test_temperatures_and_tooltips():
    assert fmt_temp(71.0) == "71 °F" and fmt_temp(71.34) == "71.3 °F" and fmt_temp(None) == "no data"
    lats = np.array([40.0, 40.01, 40.02])
    lons = np.array([-71.02, -71.01, -71.0])
    raw = {"lats": lats, "lons": lons,
           "raw_days": [{"arrF": np.full((3, 3), 60.0 + k), "date": f"2026-10-{6 + k:02d}"} for k in range(7)]}
    raw["raw_days"][3]["arrF"][1, 1] = np.nan
    temps = week_temps(raw, 40.01, -71.01)
    assert temps == [60.0, 61.0, 62.0, None, 64.0, 65.0, 66.0]
    assert week_temps(raw, 45.0, -71.0) == [None] * 7          # outside the grid: no data, not the edge
    dates = [d["date"] for d in raw["raw_days"]]
    tip = text_of(selection_tip({"kind": "poi", "name": "Hudson Canyon", "lat": 40.01, "lng": -71.01}, temps, dates, 6))
    assert "Hudson Canyon" in tip and "66 °F" in tip and "Mon, Oct 12" in tip
    assert "No data here" in text_of(selection_tip({"lat": 40.01, "lng": -71.01}, temps, dates, 3))
    assert "not available" in text_of(selection_tip({"lat": 40.01, "lng": -71.01}, None, dates, 3))


def test_measure_panel_states():
    assert "first point" in text_of(measure_panel({"mode": "a"}, None, None, None, ""))
    a = {"lat": 41.0, "lng": -71.8083, "label": "Cartwright"}
    assert "second point" in text_of(measure_panel({"mode": "b", "a": a}, None, 63.0, None, ""))
    b = {"lat": 39.54, "lng": -72.05, "label": None}
    m = format_measurement(a["lat"], a["lng"], b["lat"], b["lng"], date(2026, 10, 16))
    t = text_of(measure_panel({"mode": "done", "a": a, "b": b}, m, 63.0, 72.0, "Mon, Oct 12"))
    assert "88.4 nm" in t and "187° true" in t and "201° magnetic" in t
    assert "A: Cartwright · 63 °F" in t and "39.540°N, 72.050°W · 72 °F" in t
    assert measure_panel({"mode": "off"}, None, None, None, "") == ""


def test_day_marks_and_pois():
    assert day_marks(["2026-10-06", "2026-10-12"]) == {0: "Tu\n6", 1: "Mo\n12"}
    p = pois_payload()
    assert len(p["points"]) == len(ALL_POIS) == 32
    assert p["dump"]["s"] < p["dump"]["n"] and p["dump"]["w"] < p["dump"]["e"]


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
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
