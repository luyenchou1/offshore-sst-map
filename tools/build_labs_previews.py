#!/usr/bin/env python3
"""Preview images for the GotOne Labs landing page -> assets/labs/.

Drawn from the apps' own data so the tiles show what each tool looks like:

  sst.jpg       the live SST map itself (tools/shoot_sst_preview.mjs screenshots it:
                spectrum colours, depth lines, canyon names, dark land), New Jersey
                to Nantucket, the canyons and the Gulf Stream
  currents.jpg  the tide-stage atlas at average strength, mid-flood: streamlines
                coloured by speed with the Currents page's colour ramp
  og-sst.jpg, og-currents.jpg  1200 x 630 cards for the tool pages themselves (tool_cards)
  og.jpg        1200 x 630 social-share card (link previews in Facebook groups,
                texts, forums) built from the two and gotone-wordmark.png (the
                brand's horizontal white logotype from GotOne/Graphics, trimmed
                and without its "!" - wordmark())

No map tiles are baked in (Esri's terms want attribution on every use), only
our own data. Rerun after a redesign or to refresh the SST week:

  node tools/shoot_sst_preview.mjs            # -> build/sst-shot.png (one load of the live page)
  .venv/bin/python tools/build_labs_previews.py [--sst-shot build/sst-shot.png]
"""

import argparse
import gzip
import json
import math
import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
OUT = os.path.join(REPO, "assets", "labs")
NAVY = "#091d50"          # gotoneapp.com
NAVY_DARK = "#0a1628"     # the apps' header / sidebar
YELLOW = "#ffe000"
W, H = 1280, 720          # 16:9 tiles


def sst_preview(shot):
    """assets/labs/sst.jpg from a screenshot of the live SST map (tools/shoot_sst_preview.mjs): the
    middle 16:9 of it at 1280 x 720. The map draws itself, so the tile has the map's own look."""
    from PIL import Image

    im = Image.open(shot).convert("RGB")
    w, h = im.size
    if w / h > W / H:                              # crop to 16:9 around the centre
        cw = round(h * W / H)
        im = im.crop(((w - cw) // 2, 0, (w - cw) // 2 + cw, h))
    else:
        ch = round(w * H / W)
        im = im.crop((0, (h - ch) // 2, w, (h - ch) // 2 + ch))
    os.makedirs(OUT, exist_ok=True)
    im.resize((W, H), Image.LANCZOS).save(os.path.join(OUT, "sst.jpg"), quality=85, optimize=True)


def currents_preview():
    """Average-strength atlas at the phase bin of strongest flow at The Race."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    from data.currents import unpack_frames

    cdir = os.path.join(REPO, "cache", "currents")
    with open(os.path.join(cdir, "manifest.json")) as f:
        man = json.load(f)
    raw = open(os.path.join(cdir, man["atlas"]["files"]["surface"]), "rb").read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    h, mask, U, V = unpack_frames(raw)            # int8 (nf, ny, nx), row 0 = north
    nb = h["nframes"] // 3                         # 3 range bases x phase bins
    lon0, lat0, lon1, lat1 = h["bbox"]
    ny, nx = mask.shape
    # The Race (41.235 N, 72.06 W): the average-range bin where it runs hardest.
    i = int(round((-72.06 - lon0) / (lon1 - lon0) * (nx - 1)))
    j = int(round((lat1 - 41.235) / (lat1 - lat0) * (ny - 1)))
    spd = [math.hypot(U[nb + b, j, i], V[nb + b, j, i]) for b in range(nb)]
    k = nb + int(np.argmax(spd))
    u, v = U[k].astype(float) * h["scale"], V[k].astype(float) * h["scale"]
    water = mask.astype(bool)
    u = np.where(water, u, np.nan)[::-1]          # south-up rows for streamplot
    v = np.where(water, v, np.nan)[::-1]
    s = np.hypot(u, v)
    x = np.linspace(lon0, lon1, nx)
    y = np.linspace(lat0, lat1, ny)
    ramp = LinearSegmentedColormap.from_list("cur", [
        "#28509699", "#2d69af", "#3287c3", "#3ca5cd", "#55c3c8", "#82d7af",
        "#c3e47d", "#f5d45c", "#faaf46", "#fff0cd"])

    from data.geo import load_land_gdf

    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    fig.patch.set_facecolor(NAVY_DARK)
    ax.set_facecolor(NAVY_DARK)
    ax.streamplot(x, y, u, v, color=s, cmap=ramp, norm=matplotlib.colors.Normalize(0, 1.6),
                  density=3.2, linewidth=0.6 + 1.6 * np.clip(s / 1.6, 0, 1), arrowsize=0.6,
                  zorder=2)
    # The model's land (fills small islands Natural Earth omits, e.g. Plum and
    # Great Gull), then smooth coastlines over its 400 m stair-steps.
    ax.imshow(np.where(water[::-1], np.nan, 1.0), extent=[lon0, lon1, lat0, lat1], origin="lower",
              cmap=LinearSegmentedColormap.from_list("l", ["#24314a", "#24314a"]),
              interpolation="nearest", zorder=1)
    load_land_gdf().plot(ax=ax, color="#24314a", edgecolor="#3b4a66", linewidth=0.7, zorder=3)
    for name, lon, lat in (("The Race", -72.06, 41.205), ("Plum Gut", -72.215, 41.152),
                           ("Montauk", -71.93, 41.03), ("Block Island", -71.58, 41.17),
                           ("Gardiners Bay", -72.16, 41.075)):
        ax.text(lon, lat, name, color="#e2e8f0", fontsize=15, fontweight="bold", ha="center",
                va="center", zorder=4, alpha=0.9)
    cy = (lat0 + lat1) / 2
    lon_span = (lat1 - lat0) * (W / H) / math.cos(math.radians(cy))
    cx = (lon0 + lon1) / 2
    ax.set_xlim(cx - lon_span / 2, cx + lon_span / 2)
    ax.set_ylim(lat0, lat1)
    ax.set_aspect(1 / math.cos(math.radians(cy)))
    ax.axis("off")
    fig.savefig(os.path.join(OUT, "currents.jpg"), dpi=100, facecolor=NAVY_DARK,
                pil_kwargs={"quality": 82, "optimize": True})
    plt.close(fig)


LOGO_SRC = os.path.expanduser(
    "~/Code/GotOne/Graphics/Logo Files/GotOneLogo5(Transparent_White_Logotype_Hor).png")


def wordmark():
    """assets/labs/gotone-wordmark.png: the brand's white horizontal logotype,
    trimmed, without its trailing "!" (cut at the last gap between glyphs),
    112 px tall (2x for a ~56 px header). Kept as is if the source is absent."""
    from PIL import Image

    if not os.path.exists(LOGO_SRC):
        return
    im = Image.open(LOGO_SRC).convert("RGBA")
    im = im.crop(im.getbbox())
    ink = (np.array(im)[:, :, 3] > 40).any(axis=0)
    # Start of the last real gap between glyphs (>= 20 px; faint edge columns don't count).
    gap = max(i for i in range(1, len(ink) - 20)
              if ink[i - 1] and not ink[i:i + 20].any())
    im = im.crop((0, 0, gap, im.height))
    im = im.crop(im.getbbox())
    h = 112
    im = im.resize((round(im.width * h / im.height), h), Image.LANCZOS)
    im.save(os.path.join(OUT, "gotone-wordmark.png"), optimize=True)


def og_card():
    """1200 x 630 link-preview card: title band over the two previews."""
    from PIL import Image, ImageDraw, ImageFont

    card = Image.new("RGB", (1200, 630), NAVY)
    for k, name in enumerate(("sst.jpg", "currents.jpg")):
        im = Image.open(os.path.join(OUT, name)).convert("RGB").resize((560, 315))
        card.paste(im, (27 + k * 586, 270))
    d = ImageDraw.Draw(card)

    def font(size, bold=True):
        for path, idx in (("/System/Library/Fonts/HelveticaNeue.ttc", 1 if bold else 0),
                          ("/System/Library/Fonts/Helvetica.ttc", 1 if bold else 0)):
            try:
                return ImageFont.truetype(path, size, index=idx)
            except OSError:
                continue
        return ImageFont.load_default()

    logo = Image.open(os.path.join(OUT, "gotone-wordmark.png")).convert("RGBA")
    logo = logo.resize((int(logo.width * 64 / logo.height), 64), Image.LANCZOS)
    card.paste(logo, (40, 40), logo)
    x = 40 + logo.width + 22
    d.line([(x, 46), (x, 98)], fill=(255, 255, 255), width=2)
    d.text((x + 20, 50), "Labs", fill=YELLOW, font=font(44))
    d.text((40, 140), "Free tools for saltwater anglers", fill="#ffffff", font=font(46))
    d.text((40, 205), "Offshore sea-surface temperature  ·  Tides vs. currents, eastern Long Island",
           fill="#c7d2fe", font=font(24, bold=False))
    card.save(os.path.join(OUT, "og.jpg"), quality=85, optimize=True)


TOOL_CARDS = (   # one link-preview card per tool page (app.py serves each page's own og:image)
    ("sst", "Offshore SST", "Find the temperature breaks, Cape May to Portland, Maine.", 0.25),
    ("currents", "Currents", "The current doesn't follow the tide table. See when it turns.", 0.82),
)


def tool_cards():
    """assets/labs/og-<tool>.jpg: 1200 x 630 cards for /sst and /currents: the GotOne Labs mark, the tool's
    name and one line over a band of the tool's own preview."""
    from PIL import Image, ImageDraw, ImageFont

    def font(size, bold=True):
        for path in ("/System/Library/Fonts/HelveticaNeue.ttc", "/System/Library/Fonts/Helvetica.ttc"):
            try:
                return ImageFont.truetype(path, size, index=1 if bold else 0)
            except OSError:
                continue
        return ImageFont.load_default()

    logo = Image.open(os.path.join(OUT, "gotone-wordmark.png")).convert("RGBA")
    logo = logo.resize((int(logo.width * 50 / logo.height), 50), Image.LANCZOS)
    for key, title, line, at in TOOL_CARDS:   # at: where the 380 px band sits in the preview, 0 top .. 1 bottom
        card = Image.new("RGB", (1200, 630), NAVY)
        shot = Image.open(os.path.join(OUT, key + ".jpg")).convert("RGB")
        shot = shot.resize((1200, round(shot.height * 1200 / shot.width)), Image.LANCZOS)
        top = round((shot.height - 380) * at)
        card.paste(shot.crop((0, top, 1200, top + 380)), (0, 250))
        d = ImageDraw.Draw(card)
        card.paste(logo, (40, 34), logo)
        x = 40 + logo.width + 18
        d.line([(x, 39), (x, 80)], fill=(255, 255, 255), width=2)
        d.text((x + 16, 42), "Labs", fill=YELLOW, font=font(34))
        d.text((40, 106), title, fill=YELLOW, font=font(60))
        d.text((40, 186), line, fill="#ffffff", font=font(27, bold=False))
        card.save(os.path.join(OUT, f"og-{key}.jpg"), quality=85, optimize=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sst-shot", default=os.path.join(REPO, "build", "sst-shot.png"),
                    help="screenshot of the SST map from tools/shoot_sst_preview.mjs")
    ap.add_argument("--cards-only", action="store_true",
                    help="only rebuild the per-tool link-preview cards from the existing previews")
    a = ap.parse_args()
    if a.cards_only:
        tool_cards()
        for key, *_ in TOOL_CARDS:
            print(f"assets/labs/og-{key}.jpg: {os.path.getsize(os.path.join(OUT, f'og-{key}.jpg')) / 1024:.0f} KB")
        return
    if not os.path.exists(a.sst_shot):
        sys.exit(f"{a.sst_shot} is missing: run node tools/shoot_sst_preview.mjs first")
    sst_preview(a.sst_shot)
    currents_preview()
    wordmark()
    og_card()
    tool_cards()
    for n in ("sst.jpg", "currents.jpg", "og.jpg", "og-sst.jpg", "og-currents.jpg"):
        print(f"assets/labs/{n}: {os.path.getsize(os.path.join(OUT, n)) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
