"""Checks for the SST look (sprint 2, lane V): the depth contours asset and its route, the Hudson Canyon spot at
the canyon head, the /sst layout pieces assets/sstlook.js needs (Colors panel, live legend, dark basemap, the data
element), the color presets against the designer's notes, and the How it works copy. No server, no network:

    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES .venv/bin/python tools/test_sst_look.py

The page itself (the panel at three sizes, the contours toggle, spot labels, events) is in tools/smoke_sst.mjs.
"""

import gzip
import json
import math
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LABS_STATS_DIR", tempfile.mkdtemp(prefix="sst-look-test-"))

import app as appmod  # noqa: E402
from map import depth  # noqa: E402
from map.pois import ALL_POIS  # noqa: E402

ASSET = ROOT / "assets" / "sst" / "contours.json.gz"
LOOK_JS = (ROOT / "assets" / "sstlook.js").read_text()
LEVELS = {"30fa": 54.864, "40fa": 73.152, "100fa": 182.88, "500m": 500.0, "1000m": 1000.0, "2000m": 2000.0}


def _doc():
    return json.loads(gzip.decompress(ASSET.read_bytes()))


def _decode(piece, q):
    x = y = 0
    out = []
    for i in range(1, len(piece), 2):
        x += piece[i]
        y += piece[i + 1]
        out.append((x / q, y / q))
    return out


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# ------------------------------------------------------------------------------------------------- asset --
def test_contours_asset_small_and_complete():
    size = ASSET.stat().st_size
    assert size < 300 * 1024, f"{size / 1024:.0f} KB gzipped (budget 300)"
    doc = _doc()
    assert doc["format"] == "GOCONT1" and doc["q"] == 10000
    assert doc["bbox"] == [37.8, -76.0, 44.0, -64.8]
    ids = [lv["id"] for lv in doc["levels"]]
    assert set(ids) == set(LEVELS), ids
    labels = {lv["id"]: lv["label"] for lv in doc["levels"]}
    assert labels["30fa"] == "30 fa" and labels["100fa"] == "100 fa" and labels["2000m"] == "2000 m"
    q = doc["q"]
    s, w, n, e = doc["bbox"]
    for lv in doc["levels"]:
        assert abs(lv["depth_m"] - LEVELS[lv["id"]]) < 0.01, lv["id"]
        assert lv["unit"] == ("fa" if lv["id"].endswith("fa") else "m")
        assert lv["lines"], lv["id"]
        km = 0.0
        for piece in lv["lines"]:
            pts = _decode(piece, q)
            assert len(pts) >= 2
            for lon, lat in pts:
                assert s - 1e-4 <= lat <= n + 1e-4 and w - 1e-4 <= lon <= e + 1e-4, (lv["id"], lat, lon)
            km += piece[0] / 1000
        # every level runs the length of the shelf edge or more (about 1500 km of slope in the box)
        assert km > 1000, (lv["id"], km)


def test_contours_have_no_lake_lines():
    """No line inland: Seneca and Cayuga (beds below sea level) and Lake Champlain are masked out."""
    doc = _doc()
    for lv in doc["levels"]:
        for piece in lv["lines"]:
            for lon, lat in _decode(piece, doc["q"]):
                assert not (lat > 42.0 and lon < -75.0), (lv["id"], lat, lon)          # Finger Lakes
                assert not (lat > 43.5 and -73.6 < lon < -73.0), (lv["id"], lat, lon)  # Champlain


def test_shelf_edge_passes_hudson_canyon():
    """The 100 fathom line reaches into Hudson Canyon close to its head."""
    doc = _doc()
    lv = next(x for x in doc["levels"] if x["id"] == "100fa")
    head = doc["checks"]["hudson_head"]
    best = min(_km(head, (lat, lon)) for p in lv["lines"] for lon, lat in _decode(p, doc["q"]))
    assert best < 5, f"100 fa line {best:.1f} km from the canyon head"


def test_contours_route_gzip_etag():
    c = appmod.server.test_client()
    r = c.get(depth.ROUTE)
    assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip"
    assert r.headers["Content-Type"].startswith("application/json")
    assert "max-age=604800" in r.headers["Cache-Control"]
    assert r.data == ASSET.read_bytes()
    etag = r.headers["ETag"]
    r2 = c.get(depth.ROUTE, headers={"If-None-Match": etag})
    assert r2.status_code == 304 and not r2.data
    assert depth.contours_url().startswith(depth.ROUTE + "?v=") and etag.strip('"') in depth.contours_url()


# ------------------------------------------------------------------------------------------ Hudson Canyon --
def test_hudson_canyon_at_the_head():
    pos = {n: (la, lo) for n, la, lo in ALL_POIS}
    hud = pos["Hudson Canyon"]
    head = _doc()["checks"]["hudson_head"]
    assert _km(hud, head) < 2, (hud, head)
    assert abs(hud[0] - 39.64) < 0.02 and abs(hud[1] + 72.44) < 0.02, hud
    assert len(ALL_POIS) == 32 and len(set(pos)) == 32


# ------------------------------------------------------------------------------------------------ layout --
def _sst_layout_json():
    with appmod.server.test_request_context("/"):
        return json.dumps(appmod.build_sst_page().to_plotly_json(), default=lambda o: o.to_plotly_json()
                          if hasattr(o, "to_plotly_json") else str(o))


def test_layout_has_panel_legend_and_data():
    blob = _sst_layout_json()
    for part in ('"sst-color-panel"', '"sst-legend-live"', '"sst-look-data"', '"legend-container"',
                 '"sst-labels-pane"', "World_Dark_Gray_Base", "World_Dark_Gray_Reference",
                 "OpenStreetMap contributors"):
        assert part in blob, part
    assert "World_Light_Gray_Base" not in blob                  # the catch map keeps it, /sst doesn't
    m = re.search(r'"data-contours": "([^"]+)"', blob)
    assert m and m.group(1) == depth.contours_url()
    spots = json.loads(json.loads('"' + re.search(r'"data-spots": "((?:[^"\\]|\\.)*)"', blob).group(1) + '"'))
    assert len(spots) == 32 and ["Hudson Canyon", 39.642, -72.44] in spots


def test_catch_map_keeps_light_basemap():
    src = (ROOT / "layout" / "catchview.py").read_text()
    assert "World_Light_Gray_Base" in src and "Dark_Gray" not in src


def test_color_strength_defaults_to_opaque():
    src = (ROOT / "layout" / "sidebar.py").read_text()
    block = src[src.index('id="sst-opacity"'):][:600]
    assert re.search(r"value=1\.0", block), block


# -------------------------------------------------------------------------------------- colors + presets --
def test_presets_match_the_design_notes():
    assert '{ id: "tuna", label: "Tuna", min: 62, max: 76 }' in LOOK_JS
    assert '{ id: "marlin", label: "Marlin", min: 70, max: 84 }' in LOOK_JS
    assert "GULF_SPAN = 18" in LOOK_JS and "AUTO_MIN_SPAN = 8" in LOOK_JS
    assert "var LIMITS = [28, 90]" in LOOK_JS
    stops = re.search(r"var STOPS = \[(.*?)\];", LOOK_JS, re.S).group(1)
    want = ["#3d358b", "#476ee6", "#3ba0fd", "#1bd0d5", "#2aefa1", "#75fe5c", "#b4f836",
            "#e3db38", "#fcb136", "#f9751d", "#e14109", "#b71d02", "#7a0403"]
    assert re.findall(r'"(#[0-9a-f]{6})"', stops) == want
    # defaults: Breaks off (decided), depth lines on, prefs key versioned
    assert re.search(r"stepped: false, breaks: false, contours: true", LOOK_JS)
    assert not re.search(r"haze", LOOK_JS.split("Luyen, Oct 16 2026")[1], re.I)       # the haze was dropped
    assert 'PREF_KEY = "gotone-sst-colors-v1"' in LOOK_JS


def test_stub_engine_stays_out_of_assets():
    assert not list((ROOT / "assets").rglob("*stub*"))
    assert (ROOT / "tools" / "sst_engine_stub.js").is_file()


# ------------------------------------------------------------------------------------------ How it works --
def test_how_it_works_copy():
    from layout.sstabout import about_body

    def text(c):
        if isinstance(c, str):
            return c
        if isinstance(c, (list, tuple)):
            return " ".join(text(x) for x in c)
        ch = getattr(c, "children", None)
        return text(ch) if ch is not None else ""

    t = text(about_body())
    assert "—" not in t
    for want in ("Tuna (62 to 76 °F)", "Marlin (70 to 84 °F)", "Gulf Stream", "Custom", "Fit to view",
                 "Stepped", "Breaks", "half a degree per nautical mile", "30, 40 and 100 fathoms",
                 "500, 1000 and 2000 meters", "Oceanographer Canyon", "ETOPO 2022", "dark purple",
                 "tenth of a degree"):
        assert want in t, want
    assert "Same colors every week" not in t                    # replaced by the fixed presets
    assert "Haze" not in t and "haze" not in t                  # dropped (analysis_error barely varies)
    assert "Where clouds hide the water" in t and "smooth" in t  # the clouds paragraph stays
    for name in ("assets/sstlook.js", "assets/sstlook.css", "tools/build_contours.py", "map/depth.py"):
        assert "—" not in (ROOT / name).read_text(), name


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
