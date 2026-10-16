"""Checks for the SST page's readout, legend, status and measure helpers, and that the overlay image is drawn where
its data is (no server, no pytest):

    .venv/bin/python tools/test_sst_ui.py

The browser side (status strip, tap routing, layout at three sizes, the drawn colors against tap readouts) is
tools/smoke_sst.mjs. The overlay checks also run on a saved week from the cache ($SST_CACHE_DIR or ./cache), if any.
"""

import base64
import gzip
import io
import json
import os
import sys
import traceback
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.geo import grid_bounds                                         # noqa: E402
from map.colorscale import build_legend_component, legend_ticks          # noqa: E402
from map.measure import format_measurement, magnetic_variation           # noqa: E402
from map.overlay import (MAX_ROW_DEG, array_to_rgba, merc_lat, merc_y,   # noqa: E402
                         mercator_row_sources, sst_to_base64_png)
from map.pois import ALL_POIS, pois_payload                              # noqa: E402
from map.readout import fmt_temp, measure_panel, selection_tip, week_temps  # noqa: E402

KM_PER_DEG = 111.2


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


def test_pois_payload():
    p = pois_payload()
    assert len(p["points"]) == len(ALL_POIS) == 32
    assert p["dump"]["s"] < p["dump"]["n"] and p["dump"]["w"] < p["dump"]["e"]


# ---------------- the overlay is drawn where its data is (relaunch, Oct 16 2026) ----------------
# Leaflet draws an ImageOverlay's pixel rows evenly spaced in Web Mercator between its bounds. These checks use that
# model (tools/smoke_sst.mjs checks Leaflet against it in the browser): the screen latitude of a pixel row, and the
# pixel under a lat/lon.

def _screen_lat(frac_from_top, bounds):
    y_s, y_n = merc_y(bounds[0][0]), merc_y(bounds[1][0])
    return merc_lat(y_n - np.asarray(frac_from_top) * (y_n - y_s))


def _pixel_at(lat, lon, bounds, shape):
    (s, w), (n, e) = bounds
    y_s, y_n = merc_y(s), merc_y(n)
    r = int((y_n - merc_y(lat)) / (y_n - y_s) * shape[0])
    c = int((lon - w) / (e - w) * shape[1])
    return min(max(r, 0), shape[0] - 1), min(max(c, 0), shape[1] - 1)


def _edge_error_km(src, lats, bounds):
    """Where each boundary between two source rows is drawn vs where it is: (max km, mean km, latitude of the max).
    src[k] is the source row in image row k (np.arange(len(lats)) = drawn as is, the way before the fix)."""
    k = np.nonzero(np.diff(src))[0]
    drawn = _screen_lat((k + 1) / len(src), bounds)
    true = (lats[src[k]] + lats[src[k + 1]]) / 2
    err = np.abs(drawn - true) * KM_PER_DEG
    i = int(np.argmax(err))
    return float(err.max()), float(err.mean()), float(true[i])


def _mur_grid():
    lats = np.round(np.linspace(43.80, 38.80, 501), 4)          # the app's MUR AOI grid, north first
    lons = np.round(np.linspace(-74.96, -68.80, 617), 4)
    return lats, lons, grid_bounds(lats, lons)


def _decode(data_uri):
    return np.array(Image.open(io.BytesIO(base64.b64decode(data_uri.split(",", 1)[1]))).convert("RGBA"))


def _color(t, vmin, vmax):
    return array_to_rgba(np.array([[np.nan if t is None else t]], dtype=np.float64), vmin, vmax)[0, 0]


def _agreement(img, raw, bounds, points, vmin, vmax):
    """For each (lat, lon): the drawn pixel vs the tap readout there (week_temps, the raw arrays). Returns (exact,
    edge, wrong): edge = within half an image row of a cell edge and showing the cell at the pixel's own centre."""
    exact = edge = 0
    wrong = []
    lats = raw["lats"]
    for lat, lon in points:
        r, c = _pixel_at(lat, lon, bounds, img.shape)
        got = img[r, c]
        if np.array_equal(got, _color(week_temps(raw, lat, lon)[0], vmin, vmax)):
            exact += 1
            continue
        top, centre, bottom = _screen_lat(np.array([r, r + 0.5, r + 1]) / img.shape[0], bounds)
        to_edge = np.median(np.abs(np.diff(lats))) / 2 - np.abs(lats - lat).min()     # degrees to the cell's edge
        cell_edge = to_edge <= (top - bottom) / 2 + 1e-9
        if cell_edge and np.array_equal(got, _color(week_temps(raw, float(centre), lon)[0], vmin, vmax)):
            edge += 1
            continue
        wrong.append((lat, lon))
    return exact, edge, wrong


NAMED = [(39.54, -72.05), (39.73, -71.75), (40.45, -71.0), (41.05, -71.158), (41.30, -70.75), (41.30, -69.90),
         (40.075, -70.45), (42.35, -70.35), (43.20, -69.70), (39.10, -73.15)]


def test_overlay_rows_follow_web_mercator():
    lats, lons, bounds = _mur_grid()
    src = mercator_row_sources(lats, bounds[0][0], bounds[1][0])
    assert len(src) > len(lats) and np.all(np.diff(src) >= 0) and set(src) == set(range(len(lats)))  # none skipped
    centres = _screen_lat((np.arange(len(src)) + 0.5) / len(src), bounds)
    assert np.all(np.abs(centres - lats[src]) <= 0.005 + 1e-9)        # each image row shows the cell under it
    assert np.max(np.abs(np.diff(centres))) <= MAX_ROW_DEG + 1e-9
    before = _edge_error_km(np.arange(len(lats)), lats, bounds)
    after = _edge_error_km(src, lats, bounds)
    print(f"      MUR rows: before max {before[0]:.2f} km (at {before[2]:.2f} N), mean {before[1]:.2f}; "
          f"after max {after[0]:.3f} km, mean {after[1]:.3f} ({len(src)} image rows for {len(lats)})")
    assert before[0] > 5.0 and 40.8 < before[2] < 41.8
    assert after[0] < 0.3
    # OISST (0.25 degree): rows still at most MAX_ROW_DEG tall, so its edges land as close
    ol = np.arange(43.625, 38.874, -0.25)
    ob = grid_bounds(ol, np.arange(-74.875, -68.874, 0.25))
    osrc = mercator_row_sources(ol, ob[0][0], ob[1][0])
    o_before, o_after = _edge_error_km(np.arange(len(ol)), ol, ob), _edge_error_km(osrc, ol, ob)
    print(f"      OISST rows: before max {o_before[0]:.2f} km, after max {o_after[0]:.3f} km")
    assert o_before[0] > 4.0 and o_after[0] < 0.3


def test_overlay_colors_match_the_readout_at_map_positions():
    lats, lons, bounds = _mur_grid()
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    arr = np.round(64 + 6 * np.sin(2 * np.pi * la / 0.08) + 3 * np.cos(2 * np.pi * lo / 0.3))   # 9 km bands
    arr[(lo < -73.6) & (la > 40.55)] = np.nan                      # "land"
    arr[(la < 39.0) & (lo < -74.5)] = np.nan                       # outside the AOI
    vmin, vmax = 56.0, 72.0
    raw = {"lats": lats, "lons": lons, "raw_days": [{"arrF": arr, "date": "2026-10-15"}]}
    img = _decode(sst_to_base64_png(arr, vmin, vmax, lats=lats, bounds=bounds))
    rng = np.random.default_rng(16)
    rand = list(zip(rng.uniform(38.80, 43.80, 600), rng.uniform(-74.96, -68.80, 600)))
    land = (40.80, -74.00)
    assert img[_pixel_at(*land, bounds, img.shape)][3] == 0 and week_temps(raw, *land)[0] is None
    exact, edge, wrong = _agreement(img, raw, bounds, NAMED, vmin, vmax)
    assert not wrong and exact >= len(NAMED) - 1, (exact, edge, wrong)     # 40.075 N is on a cell edge
    exact, edge, wrong = _agreement(img, raw, bounds, rand, vmin, vmax)
    # drawn the old way (rows evenly spaced in latitude, one per source row) over the same bounds
    old = array_to_rgba(arr, vmin, vmax)
    o_exact, _, o_wrong = _agreement(old, raw, bounds, rand, vmin, vmax)
    print(f"      synthetic 9 km bands, 600 random points: after {exact} exact + {edge} at a cell edge, "
          f"{len(wrong)} wrong; before {len(o_wrong)} wrong")
    assert not wrong, wrong[:5]
    assert len(o_wrong) > 200


def _saved_week():
    d = Path(os.environ.get("SST_CACHE_DIR") or ROOT / "cache") / "sst"
    files = sorted(d.glob("*_adaptive.json.gz")) if d.is_dir() else []
    for p in reversed(files):
        try:
            with gzip.open(p, "rt") as f:
                e = json.load(f)
        except Exception:
            continue
        if e.get("raw_days") and isinstance(e["raw_days"][0]["arrF"], str):
            return p.name, e
    return None, None


def _npy(s):
    return np.load(io.BytesIO(base64.b64decode(s)))


def test_overlay_on_a_saved_week():
    name, e = _saved_week()
    if not e:
        print("      (no saved week in the cache: skipped)")
        return
    lats, lons = _npy(e["lats"]), _npy(e["lons"])
    arr = _npy(e["raw_days"][-1]["arrF"])
    raw = {"lats": lats, "lons": lons, "raw_days": [{"arrF": arr, "date": e["dates"][-1]}]}
    vmin, vmax = e["vmin"], e["vmax"]
    bounds = grid_bounds(lats, lons)
    img = _decode(sst_to_base64_png(arr, vmin, vmax, lats=lats, bounds=bounds))
    rng = np.random.default_rng(10)
    pts = NAMED + list(zip(rng.uniform(38.80, 43.80, 1500), rng.uniform(-74.96, -68.80, 1500)))
    exact, edge, wrong = _agreement(img, raw, bounds, pts, vmin, vmax)
    msg = f"      {name}, {len(pts)} points: after {exact} exact + {edge} at a cell edge, {len(wrong)} wrong"
    if e.get("frames") and e.get("frame_format") is None:              # its saved frame, drawn the old way
        o_bounds = e["bounds"]
        o_exact, o_edge, o_wrong = _agreement(_decode(e["frames"][-1]), raw, o_bounds, pts, vmin, vmax)
        o_src = np.arange(len(lats))
        msg += (f"; its saved frame (bounds {o_bounds[0][0]:.3f} to {o_bounds[1][0]:.3f} N): {len(o_wrong)} wrong, "
                f"cell edges up to {_edge_error_km(o_src, lats, o_bounds)[0]:.2f} km off")
    print(msg)
    assert not wrong, wrong[:5]


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
