#!/usr/bin/env python3
"""Instagram images for GotOne Labs -> build/social/ (not served; post them by hand).

  post-1-labs.jpg, post-2-sst.jpg, post-3-currents.jpg     a 3-slide carousel, 1080 x 1350 (4:5)
  square-sst.jpg, square-currents.jpg                      single posts, 1080 x 1080
  story-1-labs.jpg, story-2-sst.jpg, story-3-currents.jpg  stories, 1080 x 1920; text stays out of
                                                           Instagram's top 250 px and bottom 340 px,
                                                           and the bottom is left clear for a link sticker
  captions.txt                                             caption drafts

The SST maps are the live map itself (tools/shoot_sst_preview.mjs at zoom 7, 2x, one page load per
size, so the depth labels read on a phone); the map shows canyon names only from zoom 8, so they are
added here from map/pois.py. Currents is the tide-stage atlas drawn by
tools/build_labs_previews.currents_preview (The Race at its strongest: an ebb, average tide). The Labs
slides use the landing-page tiles in assets/labs/ (rebuild those first after a look change).

  .venv/bin/python tools/build_social.py [--no-shoot] [--base https://labs.gotoneapp.com]
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import date

from PIL import Image, ImageDraw, ImageFont

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
import build_labs_previews as P            # noqa: E402
from map.pois import ALL_POIS               # noqa: E402

OUT = os.path.join(REPO, "build", "social")
NAVY, NAVY_DARK, YELLOW, WHITE, SOFT = P.NAVY, P.NAVY_DARK, P.YELLOW, "#ffffff", "#c7d2fe"
HALO = (10, 22, 40)
SIDE = 60                                   # left / right margin

SST_VIEW = (40.1, -71.22, 7)                 # centre lat, lon, zoom: New Jersey to Cape Cod, the canyons, the Gulf Stream
CANYONS = ("Hudson", "Block", "Spencer", "Veatch", "Hydrographer")
SIDE_OF_DOT = {"Veatch": "left", "Hydrographer": "right"}   # side by side on the shelf edge: names apart
CUR_CENTRE, CUR_SPAN = -71.86, 0.86         # Plum Gut to Block Island; the atlas covers 40.93 to 41.38 N

# Per format: canvas, where the text starts, type sizes, the map block (same for both tools) and where
# the content ends (spare height is spread over the gaps; stories end above the reply bar).
FMT = {
    "post": dict(W=1080, H=1350, top=56, logo=54, title=96, line=37, gap=28, map_h=760, foot=40, end=1296),
    "square": dict(W=1080, H=1080, top=48, logo=46, title=80, line=31, gap=22, map_h=560, foot=36, end=1030),
    "story": dict(W=1080, H=1920, top=250, logo=58, title=112, line=42, gap=34, map_h=760, foot=44, end=1576),
}
TOOLS = {
    "sst": dict(title="Offshore SST", line="Find the temperature breaks, Cape May to Portland, Maine.",
                url="gotoneapp.com/sst"),
    "currents": dict(title="Currents", line="The current doesn't follow the tide table. See when it turns.",
                     url="gotoneapp.com/currents"),
}


def font(size, bold=True):
    for path in ("/System/Library/Fonts/HelveticaNeue.ttc", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(path, size, index=1 if bold else 0)
        except OSError:
            continue
    return ImageFont.load_default()


def spectrum():
    """The SST map's default palette, read from assets/sstengine.js (one source of truth)."""
    js = open(os.path.join(REPO, "assets", "sstengine.js")).read()
    m = re.search(r"spectrum:\s*\[([^\]]+)\]", js)
    return re.findall(r"#[0-9a-fA-F]{6}", m.group(1))


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ramp_at(stops, t):
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    a, b = rgb(stops[i]), rgb(stops[i + 1])
    f = t - i
    return tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))


def balanced(d, text, f, width):
    """One line if it fits, else two: at a sentence end if there is one, else balanced (commas help)."""
    if d.textlength(text, font=f) <= width:
        return [text]
    words, best = text.split(), None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        wa, wb = d.textlength(a, font=f), d.textlength(b, font=f)
        if max(wa, wb) > width:
            continue
        score = max(wa, wb) - {".": width * 0.4, ",": width * 0.12}.get(a[-1], 0)
        if best is None or score < best[0]:
            best = (score, [a, b])
    return best[1] if best else wrap(d, text, f, width)


def wrap(d, text, f, width):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=f) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]


# ------------------------------------------------------------------ pieces
def lockup(card, x, y, h):
    """GotOne wordmark | Labs, as on the Labs page and the link cards."""
    logo = Image.open(os.path.join(P.OUT, "gotone-wordmark.png")).convert("RGBA")
    logo = logo.resize((round(logo.width * h / logo.height), h), Image.LANCZOS)
    card.paste(logo, (x, y), logo)
    d = ImageDraw.Draw(card)
    lx = x + logo.width + round(h * 0.36)
    d.line([(lx, y + round(h * 0.1)), (lx, y + round(h * 0.9))], fill=WHITE, width=max(2, h // 26))
    d.text((lx + round(h * 0.32), y + h // 2), "Labs", fill=YELLOW, font=font(round(h * 0.68)), anchor="lm")


def header(card, f, title, line):
    """Lockup, title, one or two lines; returns the y where the map block starts."""
    d = ImageDraw.Draw(card)
    lockup(card, SIDE, f["top"], f["logo"])
    y = f["top"] + f["logo"] + round(f["title"] * 0.42)
    tf = font(f["title"])
    d.text((SIDE, y), title, fill=YELLOW, font=tf, anchor="lt")
    y += round(f["title"] * 1.12)
    lf = font(f["line"], bold=False)
    for ln in balanced(d, line, lf, f["W"] - 2 * SIDE):
        d.text((SIDE, y), ln, fill=WHITE, font=lf, anchor="lt")
        y += round(f["line"] * 1.3)
    return y + f["gap"] - round(f["line"] * 0.3)


def legend(card, y, label, stops, ticks, f):
    """A color bar on the navy under the map: label, bar, tick labels. ticks = [(t 0..1, text)]."""
    d = ImageDraw.Draw(card)
    s = f["foot"] / 40
    d.text((SIDE, y), label, fill=SOFT, font=font(round(24 * s), bold=False), anchor="lt")
    by, bw, bh = y + round(36 * s), min(700, f["W"] - 2 * SIDE), round(16 * s)
    for i in range(bw):
        d.line([(SIDE + i, by), (SIDE + i, by + bh)], fill=ramp_at(stops, i / (bw - 1)))
    tf = font(round(21 * s), bold=False)
    for t, text in ticks:
        x = SIDE + t * (bw - 1)
        d.line([(x, by + bh), (x, by + bh + round(5 * s))], fill=SOFT, width=2)
        d.text((x, by + bh + round(8 * s)), text, fill=WHITE, font=tf, anchor="mt")
    return by + bh + round(8 * s) + round(21 * s) * 1.2


def legend_h(f):
    s = f["foot"] / 40
    return round(36 * s) + round(16 * s) + round(8 * s) + round(21 * s) * 1.2


def arrow(d, x, y, size, fill):
    """A right arrow ending at x, centred on y (Helvetica Neue has no arrow glyph)."""
    w = max(3, size // 12)
    d.line([(x - size, y), (x - size * 0.3, y)], fill=fill, width=w)
    d.polygon([(x, y), (x - size * 0.42, y - size * 0.3), (x - size * 0.42, y + size * 0.3)], fill=fill)


def footer(card, y, url, f, right=None):
    d = ImageDraw.Draw(card)
    ff = font(f["foot"])
    d.text((SIDE, y), "Free at ", fill=WHITE, font=ff, anchor="lt")
    d.text((SIDE + d.textlength("Free at ", font=ff), y), url, fill=YELLOW, font=ff, anchor="lt")
    if right:                               # "Swipe" and an arrow, right-aligned
        ax = f["W"] - SIDE
        arrow(d, ax, y + f["foot"] * 0.55, round(f["foot"] * 1.1), YELLOW)
        d.text((ax - round(f["foot"] * 1.35), y), right, fill=YELLOW, font=ff, anchor="rt")


def merc_px(lat, lon, z):
    s = 256 * 2 ** z
    x = (lon + 180) / 360 * s
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * s
    return x, y


def label_canyons(img, view, scale):
    """Canyon names in the map's own label style (white, dark halo), placed by Web Mercator from the
    view the screenshot was taken at."""
    d = ImageDraw.Draw(img)
    W, H = img.size
    lat_c, lon_c, z = view
    cx, cy = merc_px(lat_c, lon_c, z)
    f = font(24)
    for name, lat, lon in ALL_POIS:
        short = name.replace(" Canyon", "")
        if not name.endswith(" Canyon") or short not in CANYONS:
            continue
        x, y = merc_px(lat, lon, z)
        px, py = W / 2 + (x - cx) * scale, H / 2 + (y - cy) * scale
        if not (16 < px < W - 16 and 20 < py < H - 20):
            continue
        d.ellipse([px - 6, py - 6, px + 6, py + 6], fill=WHITE, outline=HALO, width=3)
        tw = d.textlength(short, font=f)
        right = SIDE_OF_DOT.get(short) == "right" or (SIDE_OF_DOT.get(short) != "left" and px + 14 + tw < W - 6)
        if right and px + 14 + tw > W - 4:          # pinned right but short of room: nudge the name in
            px = W - 4 - 14 - tw
        d.text((px + 14, py) if right else (px - 14, py), short, font=f, fill=WHITE,
               anchor="lm" if right else "rm", stroke_width=3, stroke_fill=HALO)


def nice_ticks(lo, hi):
    span = hi - lo
    step = next(s for s in (2, 4, 5, 10, 20) if span / s <= 6)
    first = math.ceil(lo / step) * step
    return [((v - lo) / span, f"{v}") for v in range(first, hi + 1, step)]


# ------------------------------------------------------------------ maps
def shoot_sst(fmt, base):
    """build/social/sst-<fmt>.png + .json (day, range) from one load of the live map."""
    h = FMT[fmt]["map_h"]
    png = os.path.join(OUT, f"sst-{fmt}.png")
    lat, lon, z = SST_VIEW
    r = subprocess.run(["node", os.path.join(REPO, "tools", "shoot_sst_preview.mjs"), "--base", base,
                        "--size", f"540x{h // 2}", "--scale", "2", "--view", f"{lat},{lon},{z}", "--out", png],
                       capture_output=True, text=True)
    m = re.search(r": (\d{4}-\d\d-\d\d), (\d+)-(\d+) F", r.stdout)
    if r.returncode or not m:
        sys.exit(f"shoot_sst_preview failed for {fmt}:\n{r.stdout}{r.stderr}")
    meta = {"day": m.group(1), "min": int(m.group(2)), "max": int(m.group(3))}
    json.dump(meta, open(png[:-4] + ".json", "w"))
    print(f"  {os.path.relpath(png, REPO)}: {meta}")


def sst_map(fmt):
    png = os.path.join(OUT, f"sst-{fmt}.png")
    im = Image.open(png).convert("RGB")
    label_canyons(im, SST_VIEW, 2)
    return im, json.load(open(png[:-4] + ".json"))


def currents_map(fmt):
    f = FMT[fmt]
    path = os.path.join(OUT, f"currents-{fmt}.png")
    P.currents_preview(out=path, width=f["W"], height=f["map_h"], lon_span=CUR_SPAN, fontsize=22,
                       lon_c=CUR_CENTRE)
    return Image.open(path).convert("RGB")


# ------------------------------------------------------------------ cards
def tool_card(tool, fmt):
    f, t = FMT[fmt], TOOLS[tool]
    card = Image.new("RGB", (f["W"], f["H"]), NAVY)
    y = header(card, f, t["title"], t["line"])
    if tool == "sst":
        im, meta = sst_map(fmt)
        day = date.fromisoformat(meta["day"])
        label = f"Sea-surface temperature, °F  ·  {day:%b} {day.day}, {day.year}"
        stops, ticks = spectrum(), nice_ticks(meta["min"], meta["max"])
    else:
        im = currents_map(fmt)
        label = "Current speed, knots  ·  The Race at full ebb, an average tide"
        stops = [c[:7] for c in P.CURRENT_RAMP]
        kn_max = P.CURRENT_MAX / 0.514444
        ticks = [(k / kn_max, f"{k}") for k in range(0, int(kn_max) + 1)]
    g2, g3 = round(f["gap"] * 0.55), round(f["gap"] * 0.9)
    spare = f["end"] - (y + f["map_h"] + g2 + legend_h(f) + g3 + f["foot"])
    if spare < -1:
        print(f"  warning: {tool} {fmt} runs {-spare:.0f} px past its end")
    spare = max(spare, 0)
    y += round(spare * 0.4)
    card.paste(im, (0, y))
    y = legend(card, y + f["map_h"] + g2 + round(spare * 0.2), label, stops, ticks, f)
    footer(card, round(y + g3 + spare * 0.4), t["url"], f)
    return card


def tile(name, w, h, at=0.5):
    im = Image.open(os.path.join(P.OUT, name)).convert("RGB")
    im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    top = round((im.height - h) * at)
    return im.crop((0, top, w, top + h))


def labs_card(fmt):
    f = FMT[fmt]
    card = Image.new("RGB", (f["W"], f["H"]), NAVY)
    d = ImageDraw.Draw(card)
    lockup(card, SIDE, f["top"], round(f["logo"] * 1.35))
    y = f["top"] + round(f["logo"] * 1.35) + round(f["title"] * 0.5)
    hf = font(round(f["title"] * 0.86))
    for ln in ("Free tools for", "saltwater anglers"):
        d.text((SIDE, y), ln, fill=WHITE, font=hf, anchor="lt")
        y += round(f["title"] * 0.86 * 1.12)
    tw, th, between = f["W"] - 2 * SIDE, (400 if fmt == "post" else 410), 30
    spare = max(f["end"] - (y + f["gap"] + 2 * th + between + f["gap"] + f["foot"]), 0)
    y += f["gap"] + round(spare * 0.35)
    # name plates on a corner of each tile clear of its place names
    for k, (name, at, title, line, corner) in enumerate((
            ("sst.jpg", 0.3, "Offshore SST", "Daily sea temperature, Cape May to Portland", "bottom"),
            ("currents.jpg", 0.5, "Currents", "When the current really turns at The Race", "top"))):
        card.paste(tile(name, tw, th, at), (SIDE, y))
        tf, lf = font(38), font(24, bold=False)
        pw = max(d.textlength(title, font=tf), d.textlength(line, font=lf)) + 40
        py = y + th - 100 if corner == "bottom" else y
        plate = Image.new("RGBA", (round(pw), 100), (9, 29, 80, 225))
        card.paste(plate, (SIDE, py), plate)
        d.text((SIDE + 20, py + 14), title, fill=YELLOW, font=tf, anchor="lt")
        d.text((SIDE + 20, py + 62), line, fill=WHITE, font=lf, anchor="lt")
        y += th + (between + round(spare * 0.25) if k == 0 else 0)
    footer(card, y + f["gap"] + round(spare * 0.4), "gotoneapp.com/labs", f,
           right="Swipe" if fmt == "post" else None)
    return card


CAPTIONS = """\
CAROUSEL (post-1, post-2, post-3)

Two free tools for saltwater anglers, from GotOne Labs.

Offshore SST: daily satellite sea-surface temperature from Cape May to Portland, Maine, out past the canyons to the Gulf Stream. Colors spread across this week's water so the breaks stand out, depth lines on top, and seven days to step through to see which edges hold.

Currents: the current doesn't follow the tide table. At The Race, slack comes about 2 hours 40 minutes after Montauk high or low water. See when it turns at The Race, Plum Gut, Montauk and Block Island Sound, for any day through 2030.

Free, no sign-up. Link in bio: gotoneapp.com/labs

#GotOne #saltwaterfishing #offshorefishing #tunafishing #canyonfishing #montauk #blockisland #fishingtips

SQUARE: OFFSHORE SST

Where's the break this week? Offshore SST maps daily satellite sea-surface temperature from Cape May to Portland, Maine, out to the canyons and the Gulf Stream, with depth lines on top. Step through the week to see which edges hold.

Free at gotoneapp.com/sst (link in bio)

#GotOne #offshorefishing #tunafishing #canyonfishing #seasurfacetemperature

SQUARE: CURRENTS

Slack at The Race comes about 2 hours 40 minutes after Montauk high or low water, and Plum Gut turns about an hour and a half after. Currents shows when the water really turns at The Race, Plum Gut, Montauk and Block Island Sound, for any day through 2030.

Free at gotoneapp.com/currents (link in bio)

#GotOne #saltwaterfishing #montauk #striperfishing #fishingtips

STORIES (story-1, story-2, story-3)

Add a Link sticker in the clear space at the bottom of each frame: gotoneapp.com/labs on the first, gotoneapp.com/sst on the second, gotoneapp.com/currents on the third.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-shoot", action="store_true", help="reuse build/social/sst-*.png from the last run")
    ap.add_argument("--base", default="https://labs.gotoneapp.com", help="where the SST map is loaded from")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if not a.no_shoot:
        for fmt in FMT:
            shoot_sst(fmt, a.base)
    jobs = [("post-1-labs", lambda: labs_card("post")), ("post-2-sst", lambda: tool_card("sst", "post")),
            ("post-3-currents", lambda: tool_card("currents", "post")),
            ("square-sst", lambda: tool_card("sst", "square")),
            ("square-currents", lambda: tool_card("currents", "square")),
            ("story-1-labs", lambda: labs_card("story")), ("story-2-sst", lambda: tool_card("sst", "story")),
            ("story-3-currents", lambda: tool_card("currents", "story"))]
    for name, make in jobs:
        path = os.path.join(OUT, name + ".jpg")
        make().save(path, quality=92, optimize=True)
        print(f"  {os.path.relpath(path, REPO)}: {os.path.getsize(path) / 1024:.0f} KB")
    open(os.path.join(OUT, "captions.txt"), "w").write(CAPTIONS)


if __name__ == "__main__":
    main()
