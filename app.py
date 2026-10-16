"""Offshore SST Map — NJ to MA AOI (Dash + Dash Leaflet)

Daily sea-surface temperature visualization for the offshore corridor
from New York Harbor to Massachusetts. Data from NOAA CoastWatch ERDDAP
(MUR GHRSST preferred, OISST fallback). Temperatures in °F.

Supports 7-day animated windows: pick any end date back to 2002 and
step or auto-play through the week's SST evolution.
"""

import base64
import io
import os
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests as http_requests
from flask import Response, abort, jsonify, redirect, request, send_from_directory

import dash
import dash_bootstrap_components as dbc
import dash_leaflet as dl
import numpy as np
from dash import Input, Output, State, ctx, dcc, html

from data.cache import find_nearest_cached, get_cached, is_stale, put_cache
from data.catches import (
    build_catch_payload,
    get_all_groups,
    load_catches,
)
from data import currents as currents_data
from data import labstats
from data.convert import upsample_visual
from data.erddap import get_sst_multiday
from data.geo import mask_aoi_rasterized, mask_land_rasterized, orient_to_leaflet
from layout.catchview import build_catch_page
from layout.currentsview import build_currents_page
from layout.labsview import build_labs_page
from layout.mapview import build_map
from layout.statsview import render_stats_page
from layout.sidebar import build_sidebar
from map.colorscale import build_legend_component, compute_color_bounds
from map.measure import format_measurement
from map.overlay import sst_to_base64_png
from map.pois import (
    build_aoi_geojson, build_poi_markers, build_poi_tooltip,
    find_nearest_poi, get_all_poi_names, _lookup_temp,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

with open("config.json") as f:
    CFG = json.load(f)

LABS_DESCRIPTION = ("Free tools for saltwater anglers from GotOne: offshore sea-surface "
                    "temperature, and when the current really turns around Montauk, The Race "
                    "and Plum Gut.")

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    title="GotOne Labs",
    update_title=None,   # assets/labs.js sets each page's title; don't flash "Updating..."
    suppress_callback_exceptions=True,  # multi-page: callbacks span all pages
    meta_tags=[
        {"name": "viewport", "content": "width=device-width, initial-scale=1, viewport-fit=cover"},
        # Link previews (Facebook groups, texts, forums) for every page.
        {"name": "description", "content": LABS_DESCRIPTION},
        {"property": "og:title", "content": "GotOne Labs · Free tools for saltwater anglers"},
        {"property": "og:description", "content": LABS_DESCRIPTION},
        {"property": "og:image", "content": "https://labs.gotoneapp.com/assets/labs/og.jpg"},
        {"property": "og:url", "content": "https://labs.gotoneapp.com/"},
        {"property": "og:type", "content": "website"},
        {"name": "twitter:card", "content": "summary_large_image"},
    ],
)
server = app.server  # for gunicorn


# ---- Each tool page carries its own link preview ----
# Link-preview crawlers never run the page's scripts, so the card has to be in the HTML the server sends for
# each address. /sst and /currents get their own title, description and card (assets/labs/og-<tool>.jpg, from
# tools/build_labs_previews.py); everything else keeps the Labs card above. ?v= busts preview caches on a new card.
def _tool_previews():
    import hashlib
    import html as _html
    from layout.labsview import TOOLS
    out = {}
    for t in TOOLS:
        key = t["href"].strip("/")
        img = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "labs", f"og-{key}.jpg")
        if t["href"].startswith("http") or not os.path.exists(img):
            continue
        v = hashlib.md5(open(img, "rb").read()).hexdigest()[:8]
        out["/" + key] = {"title": f"{t['title']} · GotOne Labs", "desc": t["text"],
                          "image": f"https://labs.gotoneapp.com/assets/labs/og-{key}.jpg?v={v}",
                          "url": f"https://labs.gotoneapp.com/{key}", "esc": _html.escape}
    return out


_TOOL_PREVIEWS = _tool_previews()
_dash_interpolate_index = app.interpolate_index


def _interpolate_index(**kw):
    try:
        path = (request.path.rstrip("/") or "/").lower()
    except RuntimeError:
        path = ""
    pv = _TOOL_PREVIEWS.get(path)
    if pv:
        import re as _re
        e = pv["esc"]
        swap = {"og:title": pv["title"], "og:description": pv["desc"], "og:image": pv["image"],
                "og:url": pv["url"], "description": pv["desc"]}
        metas = kw.get("metas", "")
        for k, val in swap.items():
            metas = _re.sub(r'((?:name|property)="%s" content=")[^"]*(")' % _re.escape(k),
                            lambda m: m.group(1) + e(val, quote=True) + m.group(2), metas)
        kw["metas"] = metas
        kw["title"] = e(pv["title"])
    return _dash_interpolate_index(**kw)


app.interpolate_index = _interpolate_index


# ---- Hosts: labs.gotoneapp.com is home; sst.gotoneapp.com is the old address ----
# On the old host "/" still opens the SST map (the Squarespace embed points there).
# Set LABS_HOST=labs.gotoneapp.com in Render once that domain works: page loads
# on the old host then redirect there ("/" -> "/sst"). APIs are never redirected
# (the GitHub workflows upload to the old host; a redirected POST becomes a GET).
LEGACY_HOSTS = ("sst.gotoneapp.com",)
LABS_HOST = os.environ.get("LABS_HOST", "").strip().lower()
# The Labs home can live on the marketing site instead (gotoneapp.com/labs, a Squarespace page with
# the site's own header and footer). Set LABS_HOME_URL=https://www.gotoneapp.com/labs in Render once
# that page is up: every "<- GotOne Labs" link goes there, and this app's own home ("/" on the labs
# host, and /labs anywhere) redirects there. Unset = the app's own Labs home, as before.
LABS_HOME_URL = os.environ.get("LABS_HOME_URL", "").strip()


def _request_host():
    try:
        return request.host.split(":")[0].lower()
    except RuntimeError:                # outside a request
        return ""


def labs_href():
    """Link to the Labs home: LABS_HOME_URL if set, else "/" (on the old host "/" is the SST map)."""
    if LABS_HOME_URL:
        return LABS_HOME_URL
    return "/labs" if _request_host() in LEGACY_HOSTS else "/"


# Tools hosted elsewhere, so labs.gotoneapp.com/<name> works like the app's own pages
EXTERNAL_TOOLS = {"/striper-recount": "https://striperrecount.gotoneapp.com/"}

# Paths no host / home redirect may ever touch: the APIs (beacons, uploads, GFW tiles; a
# redirected POST turns into a GET and loses its body), Dash's own routes, assets, the stats.
_NEVER_REDIRECT = ("/api/", "/_dash", "/assets/", "/_favicon", "/_reload", "/labs/stats")


def _redirectable():
    return request.method == "GET" and not request.path.startswith(_NEVER_REDIRECT)


@server.before_request
def redirect_external_tools():
    if not _redirectable():
        return None
    to = EXTERNAL_TOOLS.get(request.path.rstrip("/").lower())
    return redirect(to, code=302) if to else None


@server.before_request
def redirect_labs_home():
    """With LABS_HOME_URL set, the app's own Labs home redirects to it (302, so it can move later).
    /labs/stats and the APIs are untouched; the old host's "/" is still the SST map."""
    if not LABS_HOME_URL or not _redirectable():
        return None
    path = request.path.rstrip("/") or "/"
    if path == "/labs" or (path == "/" and _request_host() not in LEGACY_HOSTS):
        return redirect(LABS_HOME_URL, code=302)
    return None


@server.before_request
def redirect_old_host():
    if not LABS_HOST or not _redirectable() or _request_host() not in LEGACY_HOSTS:
        return None
    path = request.path
    qs = request.query_string.decode()
    return redirect(f"https://{LABS_HOST}{'/sst' if path in ('', '/') else path}"
                    + (f"?{qs}" if qs else ""), code=301)


# ---- Allow iframe embedding from GotOne website ----
@server.after_request
def set_iframe_headers(response):
    # Allow embedding on gotoneapp.com (Squarespace) and the app's own hosts
    response.headers["Content-Security-Policy"] = (
        "frame-ancestors 'self' https://www.gotoneapp.com https://gotoneapp.com "
        "https://labs.gotoneapp.com https://sst.gotoneapp.com "
        "https://offshore-sst-map.onrender.com"
    )
    # Remove X-Frame-Options if set by any middleware (CSP takes precedence)
    response.headers.pop("X-Frame-Options", None)
    return response


# ---- GotOne Labs analytics (data/labstats.py; docs/labs-analytics-contract.md) ----
# Beacons from assets/labs.js and from The Striper Recount (striperrecount.gotoneapp.com, cross-
# origin): a JSON string sent as text/plain by navigator.sendBeacon, so no CORS preflight. Every
# endpoint answers 204 whatever happens; counting must never fail a page.
_OUR_ORIGIN = __import__("re").compile(
    r"^https?://(([a-z0-9-]+\.)*gotoneapp\.com|[a-z0-9-]+\.onrender\.com|localhost|127\.0\.0\.1)(:\d+)?$")


def _origin_ok(required=False):
    """Hits and events: no Origin is fine, a foreign one isn't. Feedback: must be ours."""
    origin = request.headers.get("Origin", "")
    return bool(_OUR_ORIGIN.match(origin)) if origin else not required


def _beacon_json(limit):
    """The beacon body as a dict, or None (too long, not JSON, not an object)."""
    try:
        if (request.content_length or 0) > limit:
            return None
        raw = request.stream.read(limit + 1)
        if len(raw) > limit:
            return None
        j = json.loads(raw.decode("utf-8", "replace") or "{}")
    except (ValueError, OSError):
        return None
    return j if isinstance(j, dict) else None


def _visitor():
    """(ip, user agent, DNT/GPC on). Only hashed, never stored (labstats)."""
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0]
    dnt = request.headers.get("DNT") == "1" or request.headers.get("Sec-GPC") == "1"
    return ip.strip(), request.headers.get("User-Agent", ""), dnt


@server.route("/api/hit", methods=["POST"])
def labs_hit():
    """One page view: {"p": page id (labstats.PAGES), "r": referrer host}. Always 204."""
    j = _beacon_json(500) if _origin_ok() else None
    if j is not None:
        ip, ua, dnt = _visitor()
        try:
            labstats.record(str(j.get("p", ""))[:20], str(j.get("r", ""))[:120], ip, ua, dnt=dnt)
        except Exception:                       # never fail a page over counting
            logger.warning("labs hit not recorded", exc_info=True)
    return "", 204


@server.route("/api/event", methods=["POST"])
def labs_event():
    """One feature use: {"t": tool, "e": event in labstats.EVENTS[tool]}. Always 204."""
    j = _beacon_json(500) if _origin_ok() else None
    if j is not None:
        ip, ua, dnt = _visitor()
        try:
            labstats.record_event(str(j.get("t", ""))[:20], str(j.get("e", ""))[:40], ip, ua,
                                  dnt=dnt)
        except Exception:
            logger.warning("labs event not recorded", exc_info=True)
    return "", 204


@server.route("/api/feedback", methods=["POST"])
def labs_feedback():
    """The Helpful vote and/or note: {"t", "v": "up"|"down"|"", "n", "w": honeypot, "d": seconds
    on the page}. Needs our Origin; kept even with DNT/GPC (the reader chose to send it). 204."""
    j = _beacon_json(4096) if _origin_ok(required=True) else None
    if j is not None:
        ip, ua, _ = _visitor()
        try:
            labstats.record_feedback(str(j.get("t", ""))[:20], j.get("v"), j.get("n"), j.get("w"),
                                     j.get("d"), ip, ua)
        except Exception:
            logger.warning("labs feedback not recorded", exc_info=True)
    return "", 204


# Private stats: /labs/stats?key=... (LABS_STATS_KEY). Unset -> open locally,
# closed on Render (like /catches).
LABS_STATS_KEY = os.environ.get("LABS_STATS_KEY", "").strip() or None


def _stats_allowed():
    if not LABS_STATS_KEY:
        return not os.environ.get("RENDER")
    import hmac
    return hmac.compare_digest(request.args.get("key", ""), LABS_STATS_KEY)


def _stats_days():
    try:
        return max(1, min(365, int(request.args.get("days", 30))))
    except ValueError:
        return 30


_PRIVATE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}


@server.route("/api/labs/stats")
def labs_stats_json():
    if not _stats_allowed():
        abort(404)
    resp = jsonify(labstats.summary(_stats_days()))
    resp.headers.update(_PRIVATE)
    return resp


@server.route("/labs/stats")
def labs_stats_page():
    if not _stats_allowed():
        abort(404)
    html_out = render_stats_page(labstats.summary(_stats_days()), request.args.get("key", ""))
    return Response(html_out, mimetype="text/html", headers=_PRIVATE)


# ---- Global Fishing Watch tile proxy ----
# GFW 4Wings API requires Bearer auth in headers, which Leaflet can't do.
# Proxy tiles through Flask so the token stays server-side.
_gfw_style_cache = {"url_template": None, "date_range": None}
_gfw_date_range = None  # "YYYY-MM-DD,YYYY-MM-DD" — updated by fetch_sst_data
_gfw_tile_cache = {}  # (z, x, y, dr) → (content_bytes, status_code)
_GFW_TILE_CACHE_MAX = 200  # max cached tiles before eviction (~2-4 MB)


# ---- GotOne catches: load static data once at module import ----
load_catches()


@server.route("/api/catches/upload", methods=["POST"])
def catches_upload():
    """Receive a freshly anonymized catch CSV from the off-Render Firebase sync.

    Mirrors the SST `/api/cache/upload` pattern: the Firestore pull + PII
    stripping happens off the public app (a CI runner with the service
    account), then the cleaned CSV is pushed here and hot-reloaded — no
    redeploy. Auth: shared secret in X-Upload-Token vs CATCHES_UPLOAD_TOKEN
    (endpoint disabled with 403 if that env var is unset). X-Catches-Filename
    selects which file (allow-listed to prevent path traversal).
    """
    import hmac

    from data.catches import ALLOWED_UPLOAD_FILES, load_catches as _reload

    token = os.environ.get("CATCHES_UPLOAD_TOKEN")
    if not token:
        return (json.dumps({"error": "upload disabled (CATCHES_UPLOAD_TOKEN not set)"}),
                403, {"Content-Type": "application/json"})

    supplied = request.headers.get("X-Upload-Token", "")
    if not (supplied and hmac.compare_digest(supplied, token)):
        return (json.dumps({"error": "unauthorized"}), 401, {"Content-Type": "application/json"})

    fname = request.headers.get("X-Catches-Filename", "")
    if fname not in ALLOWED_UPLOAD_FILES:
        return (json.dumps({"error": f"invalid filename: {fname!r}"}),
                400, {"Content-Type": "application/json"})

    data = request.get_data()
    if not data:
        return (json.dumps({"error": "empty body"}), 400, {"Content-Type": "application/json"})
    if len(data) > 16 * 1024 * 1024:  # 16 MB ceiling
        return (json.dumps({"error": "file too large"}), 413, {"Content-Type": "application/json"})

    # Sanity-check the CSV header before overwriting the live file.
    first_line = data.split(b"\n", 1)[0].decode("utf-8", "replace")
    expected = "catch_time" if fname == "gotone_catches.csv" else "__id__"
    if expected not in first_line:
        return (json.dumps({"error": f"unexpected CSV header for {fname}"}),
                400, {"Content-Type": "application/json"})

    dest = ALLOWED_UPLOAD_FILES[fname]
    os.makedirs(os.path.dirname(dest), exist_ok=True)  # persistent dir may not exist yet
    with open(dest, "wb") as f:
        f.write(data)
    n = _reload()
    return (json.dumps({"ok": True, "file": fname, "loaded": n}),
            200, {"Content-Type": "application/json"})


def _get_gfw_style(date_range: str) -> str | None:
    """Call GFW generate-png to get a styled tile URL template.

    Must be called once per date range before tiles can be fetched.
    Returns the full URL template with {z}/{x}/{y} placeholders, or None.
    """
    token = os.environ.get("GFW_API_TOKEN")
    if not token:
        return None
    try:
        resp = http_requests.post(
            "https://gateway.api.globalfishingwatch.org/v3/4wings/generate-png",
            params={
                "datasets[0]": "public-global-fishing-effort:latest",
                "interval": "DAY",
                "date-range": date_range,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        url = resp.json().get("url", "")
        # The API returns URLs pointing to the prod subdomain which has
        # SSL issues with Python's ssl module. Use the main domain instead.
        url = url.replace(
            "gateway.api.prod.globalfishingwatch.org",
            "gateway.api.globalfishingwatch.org",
        )
        return url
    except Exception as e:
        logger.warning("GFW generate-png failed: %s", e)
        return None


@server.route("/api/gfw/<int:z>/<int:x>/<int:y>.png")
def gfw_tile_proxy(z, x, y):
    """Proxy GFW fishing effort tiles with auth header.

    Includes server-side tile caching so repeat requests (zoom back,
    pan back) return instantly without hitting GFW API. Both 200s and
    404s are cached — 404s are ~half of all requests and each saves
    a ~1s round-trip to GFW.
    """
    global _gfw_style_cache
    token = os.environ.get("GFW_API_TOKEN")
    if not token:
        return "", 204

    # Determine date range — match SST window or default to last 7 days
    if _gfw_date_range:
        dr = _gfw_date_range
    else:
        end = date.today() - timedelta(days=4)
        start = end - timedelta(days=6)
        dr = f"{start},{end}"

    # Check server-side tile cache first (instant, no GFW API call)
    tile_key = (z, x, y, dr)
    cached = _gfw_tile_cache.get(tile_key)
    if cached is not None:
        content, status = cached
        if status == 200:
            return Response(
                content,
                content_type="image/png",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        return "", status

    # Refresh style if date range changed or not yet initialized
    if _gfw_style_cache["url_template"] is None or _gfw_style_cache["date_range"] != dr:
        url_tpl = _get_gfw_style(dr)
        _gfw_style_cache = {"url_template": url_tpl, "date_range": dr}

    if not _gfw_style_cache["url_template"]:
        return "", 204

    tile_url = (_gfw_style_cache["url_template"]
                .replace("{z}", str(z))
                .replace("{x}", str(x))
                .replace("{y}", str(y)))

    try:
        resp = http_requests.get(
            tile_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        # Cache both successes and failures (404s are ~half of requests)
        if resp.status_code == 200:
            _gfw_tile_cache[tile_key] = (resp.content, 200)
        elif resp.status_code in (404, 204):
            _gfw_tile_cache[tile_key] = (b"", resp.status_code)
        # Evict if cache too large (simple clear — tiles repopulate fast)
        if len(_gfw_tile_cache) > _GFW_TILE_CACHE_MAX:
            _gfw_tile_cache.clear()
        if resp.status_code != 200:
            return "", resp.status_code
        return Response(
            resp.content,
            content_type=resp.headers.get("Content-Type", "image/png"),
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        return "", 502


# ---- Pre-cache endpoint ----
# Populates disk cache for historical dates so users get instant loads.
# Runs in a background thread to avoid blocking the web worker.
_precache_status = {"running": False, "done": 0, "total": 0, "errors": []}


@server.route("/api/precache")
def precache_endpoint():
    """Trigger background pre-caching of tuna season dates.

    Query params:
      start_year (int, default 2020)
      end_year   (int, default 2025)
      months     (comma-sep ints, default "6,7,8,9,10,11")
      interval   (int days between dates, default 7 = weekly)
      delay      (int seconds between fetches, default 30)

    Returns status JSON. Hit again to check progress.

    Locked like /api/cache/upload: the X-Upload-Token header must match CACHE_UPLOAD_TOKEN (403 when the token
    isn't configured, 401 otherwise), so nobody can start an hours-long crawl from the open web.
    """
    import hmac
    token = os.environ.get("CACHE_UPLOAD_TOKEN")
    if not token:
        return (json.dumps({"error": "precache disabled (CACHE_UPLOAD_TOKEN not set)"}),
                403, {"Content-Type": "application/json"})
    supplied = request.headers.get("X-Upload-Token", "")
    if not (supplied and hmac.compare_digest(supplied, token)):
        return (json.dumps({"error": "unauthorized"}), 401, {"Content-Type": "application/json"})
    if _precache_status["running"]:
        return json.dumps(_precache_status), 200, {"Content-Type": "application/json"}

    start_year = int(request.args.get("start_year", 2020))
    end_year = int(request.args.get("end_year", 2025))
    months = [int(m) for m in request.args.get("months", "6,7,8,9,10,11").split(",")]
    interval = int(request.args.get("interval", 7))
    delay = int(request.args.get("delay", 45))

    # Build list of target dates (every `interval` days during target months)
    from datetime import date as _date
    target_dates = []
    for year in range(start_year, end_year + 1):
        for month in months:
            d = _date(year, month, 1)
            while d.month == month:
                target_dates.append(d)
                d += timedelta(days=interval)

    # Filter out dates already cached
    uncached = []
    for d in target_dates:
        cached = get_cached(d, False)  # adaptive mode
        if not cached:
            uncached.append(d)

    if not uncached:
        return json.dumps({"message": "All dates already cached", "total": len(target_dates)}), 200, {"Content-Type": "application/json"}

    _precache_status["running"] = True
    _precache_status["done"] = 0
    _precache_status["total"] = len(uncached)
    _precache_status["errors"] = []

    def _run_precache(dates, delay_s):
        import gc
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        # Hard timeout per date — prevents ERDDAP hangs from blocking the
        # worker so long that Render's health check fails and restarts it.
        PER_DATE_TIMEOUT = 120  # seconds

        for d in dates:
            # Re-check cache in case a previous run cached this date
            # before the worker restarted
            if get_cached(d, False):
                _precache_status["done"] += 1
                logger.info("Pre-cache: already cached %s, skipping", d)
                continue

            sst = disk_payload = None
            try:
                logger.info("Pre-cache: fetching %s", d)

                # Run the fetch + process in a thread with a hard timeout
                # so a hung ERDDAP request can't block forever
                def _fetch_and_cache(date_val):
                    s = get_sst_multiday(date_val, CFG)
                    dp = _precache_single_date(s, locked=False)
                    put_cache(date_val, False, dp)
                    return True

                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_fetch_and_cache, d)
                    future.result(timeout=PER_DATE_TIMEOUT)

                _precache_status["done"] += 1
                logger.info("Pre-cache: cached %s (%d/%d)",
                            d, _precache_status["done"], _precache_status["total"])
            except FuturesTimeout:
                logger.warning("Pre-cache: TIMEOUT after %ds for %s, skipping",
                               PER_DATE_TIMEOUT, d)
                _precache_status["errors"].append(f"{d}: timeout after {PER_DATE_TIMEOUT}s")
                _precache_status["done"] += 1
            except Exception as e:
                logger.warning("Pre-cache: failed %s: %s", d, e)
                _precache_status["errors"].append(f"{d}: {e}")
                _precache_status["done"] += 1

            # Free memory aggressively between fetches
            sst = disk_payload = None
            gc.collect()

            # Pause between fetches to avoid ERDDAP rate limits
            # and to leave the worker available for health checks
            time.sleep(delay_s)

        _precache_status["running"] = False
        logger.info("Pre-cache: complete. %d/%d cached, %d errors",
                     _precache_status["done"], _precache_status["total"],
                     len(_precache_status["errors"]))

    threading.Thread(target=_run_precache, args=(uncached, delay), daemon=True).start()

    return json.dumps({
        "message": f"Started pre-caching {len(uncached)} dates ({len(target_dates) - len(uncached)} already cached)",
        "uncached": [str(d) for d in uncached[:10]],  # show first 10
        "total": len(uncached),
    }), 200, {"Content-Type": "application/json"}


@server.route("/api/precache/status")
def precache_status():
    """Check pre-cache progress, including cache directory diagnostics."""
    from data.cache import CACHE_DIR
    status = dict(_precache_status)
    # Debug info for verifying persistent disk setup
    status["cache_dir"] = str(CACHE_DIR)
    try:
        files = list(CACHE_DIR.glob("*.json.gz"))
        status["cached_files"] = len(files)
    except Exception:
        status["cached_files"] = -1
    return json.dumps(status), 200, {"Content-Type": "application/json"}


@server.route("/api/cache/upload", methods=["POST"])
def cache_upload():
    """Receive a pre-built SST cache file from an off-Render fetcher.

    NOAA's firewall blocks ERDDAP from Render's datacenter IP (connections
    are refused/unreachable), so recent cache files are built where ERDDAP
    IS reachable (a residential IP / CI runner) and pushed here.

    Auth: shared secret in the X-Upload-Token header, compared against the
    CACHE_UPLOAD_TOKEN env var. If that env var is unset the endpoint is
    disabled (403). The body is the raw gzip cache file; X-Cache-Filename
    names it (strictly validated to prevent path traversal).
    """
    import hmac
    import re as _re

    from data.cache import CACHE_DIR

    token = os.environ.get("CACHE_UPLOAD_TOKEN")
    if not token:
        return (json.dumps({"error": "upload disabled (CACHE_UPLOAD_TOKEN not set)"}),
                403, {"Content-Type": "application/json"})

    supplied = request.headers.get("X-Upload-Token", "")
    if not (supplied and hmac.compare_digest(supplied, token)):
        return (json.dumps({"error": "unauthorized"}),
                401, {"Content-Type": "application/json"})

    fname = request.headers.get("X-Cache-Filename", "")
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}_(adaptive|locked)\.json\.gz", fname):
        return (json.dumps({"error": f"invalid filename: {fname!r}"}),
                400, {"Content-Type": "application/json"})

    data = request.get_data()
    if not data:
        return (json.dumps({"error": "empty body"}),
                400, {"Content-Type": "application/json"})
    if len(data) > 8 * 1024 * 1024:  # 8 MB ceiling per cache file
        return (json.dumps({"error": "file too large"}),
                413, {"Content-Type": "application/json"})

    # Validate it really is our gzip-JSON cache format before writing
    try:
        import gzip as _gz
        payload = json.loads(_gz.decompress(data).decode("utf-8"))
        if "raw_days" not in payload or "dates" not in payload:
            raise ValueError("missing expected cache keys")
    except Exception as e:
        return (json.dumps({"error": f"not a valid cache file: {e}"}),
                400, {"Content-Type": "application/json"})

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / fname
    tmp = CACHE_DIR / (fname + ".uploading")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, target)
    size_kb = target.stat().st_size / 1024
    logger.info("Cache UPLOAD: %s (%.0f KB)", fname, size_kb)
    return (json.dumps({"ok": True, "file": fname, "size_kb": round(size_kb, 1)}),
            200, {"Content-Type": "application/json"})


# ---- Currents page data (/currents) ----
# Prebuilt by tools/build_currents.py (off-Render) and stored on the persistent
# disk (data.currents.CURRENTS_DIR). Served as-is — no request-time processing
# on the single worker. Frame/meta files are stored gzip-compressed and sent
# with Content-Encoding: gzip so the browser inflates them transparently.
_JSON = {"Content-Type": "application/json"}


@server.route("/api/currents/manifest.json")
def currents_manifest():
    if not (currents_data.CURRENTS_DIR / "manifest.json").is_file():
        return (json.dumps({"version": 1, "weeks": [], "stations": []}),
                200, {**_JSON, "Cache-Control": "no-cache"})
    return send_from_directory(currents_data.CURRENTS_DIR, "manifest.json",
                               mimetype="application/json", max_age=60)


@server.route("/api/currents/<fname>")
def currents_file(fname):
    if fname == "manifest.json" or not currents_data.FILE_RE.fullmatch(fname):
        return "not found", 404
    if not (currents_data.CURRENTS_DIR / fname).is_file():
        return "not found", 404
    mimetype = ("application/json" if fname.endswith(".json.gz")
                else "application/octet-stream")
    resp = send_from_directory(currents_data.CURRENTS_DIR, fname,
                               mimetype=mimetype, max_age=86400)
    resp.headers["Content-Encoding"] = "gzip"
    return resp


@server.route("/api/currents/upload", methods=["POST"])
def currents_upload():
    """Receive a prebuilt currents file from tools/build_currents.py.

    Same auth as /api/cache/upload (CACHE_UPLOAD_TOKEN; 403 if unset, 401 on
    mismatch). X-Currents-Filename is validated against data.currents.FILE_RE
    and the body against the GOCUR1 / meta / manifest formats before an
    atomic write. The builder uploads manifest.json last, so a partial run
    never goes live.
    """
    import hmac

    token = os.environ.get("CACHE_UPLOAD_TOKEN")
    if not token:
        return (json.dumps({"error": "upload disabled (CACHE_UPLOAD_TOKEN not set)"}),
                403, _JSON)
    supplied = request.headers.get("X-Upload-Token", "")
    if not (supplied and hmac.compare_digest(supplied, token)):
        return json.dumps({"error": "unauthorized"}), 401, _JSON

    fname = request.headers.get("X-Currents-Filename", "")
    if not currents_data.FILE_RE.fullmatch(fname):
        return json.dumps({"error": f"invalid filename: {fname!r}"}), 400, _JSON
    # Check the declared size BEFORE buffering the body (512 MB instance).
    if (request.content_length or 0) > currents_data.MAX_UPLOAD_BYTES:
        return json.dumps({"error": "file too large"}), 413, _JSON
    data = request.get_data()
    if len(data) > currents_data.MAX_UPLOAD_BYTES:
        return json.dumps({"error": "file too large"}), 413, _JSON
    err = currents_data.validate_upload(fname, data)
    if err:
        return json.dumps({"error": err}), 400, _JSON

    target_dir = currents_data.CURRENTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp = target_dir / (fname + ".uploading")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target_dir / fname)
    finally:
        tmp.unlink(missing_ok=True)
    size_kb = len(data) / 1024
    logger.info("Currents UPLOAD: %s (%.0f KB)", fname, size_kb)
    return (json.dumps({"ok": True, "file": fname, "size_kb": round(size_kb, 1)}),
            200, _JSON)


# ---- Server-side raw data cache ----
# Raw float arrays are too large (~18 MB) for dcc.Store / browser transport.
# Keep them server-side; click callbacks read from here instead.
_raw_data_cache = {}  # key → {"raw_days": [...], "lats": np.array, "lons": np.array}
_RAW_CACHE_MAX = 2  # max entries (~25 MB each) — keep tight on 512 MB Render


def _put_raw_cache(key, value):
    """Store raw data with eviction to stay within memory budget."""
    _raw_data_cache[key] = value
    while len(_raw_data_cache) > _RAW_CACHE_MAX:
        oldest = next(iter(_raw_data_cache))
        if oldest != key:
            del _raw_data_cache[oldest]
        else:
            break


def _cache_key(end_date, locked):
    mode = "locked" if locked else "adaptive"
    return f"{end_date}_{mode}"


def _get_raw_data(data_key):
    """Retrieve raw grid data, falling back to disk cache if needed.

    This handles multi-worker gunicorn (each worker has its own memory),
    server restarts, and any other case where in-memory cache is empty.
    """
    if data_key and data_key in _raw_data_cache:
        return _raw_data_cache[data_key]

    if not data_key:
        return None

    # Fall back to disk cache — parse key to get end_date and locked
    # data_key format: "2026-03-25_adaptive" or "2026-03-25_locked"
    logger.info("_get_raw_data: memory miss for %s, trying disk cache", data_key)
    try:
        parts = data_key.rsplit("_", 1)
        if len(parts) != 2:
            logger.warning("_get_raw_data: invalid key format: %s", data_key)
            return None
        end_date_str, mode = parts
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        locked = mode == "locked"
        cached = get_cached(end_date, locked)
        if cached:
            logger.info("_get_raw_data: disk cache HIT for %s, rebuilding raw data", data_key)
            _, raw_data = _build_payload_from_disk_cache(cached, raw_only=True)
            _put_raw_cache(data_key, raw_data)
            logger.info("Raw data loaded from disk cache: %s", data_key)
            return raw_data
        else:
            logger.warning("_get_raw_data: disk cache MISS for %s", data_key)
    except Exception:
        logger.warning("Failed to load raw data from disk for %s", data_key, exc_info=True)

    return None


# ---- Shared helper: build SST payload from raw ERDDAP result ----
def _build_payload(sst: dict, locked: bool) -> dict:
    """Process raw ERDDAP result into the full payload.

    Runs orient → mask_aoi → mask_land → color bounds → pre-render PNGs.
    Returns (store_payload, raw_data) where:
      - store_payload goes to dcc.Store (frames + metadata, ~7 MB)
      - raw_data stays server-side (float arrays for click lookups, ~18 MB)
    """
    lats_raw = sst["lats"]
    lons_raw = sst["lons"]

    processed_days = []
    all_finite = []

    for day_data in sst["days"]:
        arrF = day_data["arrF"]
        arrF, lats, lons = orient_to_leaflet(arrF, lats_raw.copy(), lons_raw.copy())
        arrF = mask_aoi_rasterized(arrF, lats, lons, CFG)
        arrF = mask_land_rasterized(arrF, lats, lons)
        processed_days.append({"arrF": arrF, "date": day_data["date"]})
        finite = arrF[np.isfinite(arrF)]
        if finite.size > 0:
            all_finite.append(finite)

    # Re-orient once to get correct lat/lon arrays
    _, lats, lons = orient_to_leaflet(
        sst["days"][0]["arrF"], lats_raw.copy(), lons_raw.copy()
    )

    # Compute resolution
    res_km = abs(float(lats[1] - lats[0])) * 111.0 if len(lats) > 1 else None

    # Unified color bounds across all days
    if locked:
        vmin, vmax = 30.0, 90.0
    elif all_finite:
        stacked = np.concatenate(all_finite)
        vmin, vmax = compute_color_bounds(
            np.array(stacked, dtype=np.float64), locked=False
        )
    else:
        vmin, vmax = 30.0, 90.0

    # Pre-render each day's PNG
    frames = []
    dates = []
    for pd_item in processed_days:
        arrF_vis = upsample_visual(pd_item["arrF"], 1)
        png_url = sst_to_base64_png(arrF_vis, vmin, vmax)
        frames.append(png_url)
        dates.append(pd_item["date"])

    bounds = [
        [float(np.min(lats)), float(np.min(lons))],
        [float(np.max(lats)), float(np.max(lons))],
    ]

    # Store payload — goes to browser via dcc.Store (~7 MB)
    store_payload = {
        "frames": frames,
        "dates": dates,
        "bounds": bounds,
        "vmin": float(vmin),
        "vmax": float(vmax),
        "res_km": res_km,
        "server": sst["server"],
        "dataset_id": sst["dataset_id"],
        "dataset_title": sst["dataset_title"],
    }

    # Raw data — stays server-side for click lookups
    raw_data = {
        "raw_days": processed_days,  # list of {"arrF": np.array, "date": str}
        "lats": lats,
        "lons": lons,
    }

    return store_payload, raw_data


def _serialize_array(arr: np.ndarray) -> str:
    """Serialize numpy array to base64 string (memory-efficient)."""
    buf = io.BytesIO()
    np.save(buf, arr)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _deserialize_array(s: str) -> np.ndarray:
    """Deserialize base64 string to numpy array."""
    buf = io.BytesIO(base64.b64decode(s))
    return np.load(buf)


def _precache_single_date(sst: dict, locked: bool) -> dict:
    """Build a raw-only disk cache payload (no PNG frames).

    Unlike _build_payload, this:
    - Never renders PNGs (saves ~5-7 MB peak)
    - Processes and serializes each day individually (never holds all
      7 arrays + their base64 representations simultaneously)
    - Uses running percentiles instead of np.concatenate (saves ~16 MB)

    Returns a dict ready for put_cache() with frames=None.
    """
    import gc
    lats_raw = sst["lats"]
    lons_raw = sst["lons"]

    # Orient once for lat/lon arrays
    _, lats, lons = orient_to_leaflet(
        sst["days"][0]["arrF"], lats_raw.copy(), lons_raw.copy()
    )
    serialized_lats = _serialize_array(lats)
    serialized_lons = _serialize_array(lons)
    res_km = abs(float(lats[1] - lats[0])) * 111.0 if len(lats) > 1 else None
    bounds = [
        [float(np.min(lats)), float(np.min(lons))],
        [float(np.max(lats)), float(np.max(lons))],
    ]

    # Process each day individually — serialize and free before next
    serialized_days = []
    dates = []
    running_p5_min = float("inf")
    running_p95_max = float("-inf")

    for day_data in sst["days"]:
        arrF = day_data["arrF"]
        arrF, _, _ = orient_to_leaflet(arrF, lats_raw.copy(), lons_raw.copy())
        arrF = mask_aoi_rasterized(arrF, lats, lons, CFG)
        arrF = mask_land_rasterized(arrF, lats, lons)

        # Running percentiles (avoids 16 MB concatenation)
        finite = arrF[np.isfinite(arrF)]
        if finite.size >= 50:
            running_p5_min = min(running_p5_min, float(np.nanpercentile(finite, 5)))
            running_p95_max = max(running_p95_max, float(np.nanpercentile(finite, 95)))
        del finite

        # Serialize immediately, then free numpy array
        serialized_days.append({
            "arrF": _serialize_array(arrF),
            "date": day_data["date"],
        })
        dates.append(day_data["date"])
        del arrF

    # Free lats/lons now that all days are processed
    del lats, lons
    gc.collect()

    # Apply color bounds logic (matches compute_color_bounds)
    if locked:
        vmin, vmax = 30.0, 90.0
    elif running_p5_min < float("inf"):
        vmin = max(running_p5_min, 28.0)
        vmax = min(running_p95_max, 95.0)
        if vmax - vmin < 5.0:
            mid = (vmin + vmax) / 2
            vmin, vmax = mid - 3.0, mid + 3.0
    else:
        vmin, vmax = 30.0, 90.0

    return {
        "frames": None,  # sentinel: raw-only, render PNGs on demand
        "dates": dates,
        "bounds": bounds,
        "vmin": float(vmin),
        "vmax": float(vmax),
        "res_km": res_km,
        "server": sst["server"],
        "dataset_id": sst["dataset_id"],
        "dataset_title": sst["dataset_title"],
        "raw_days": serialized_days,
        "lats": serialized_lats,
        "lons": serialized_lons,
    }


def _rebuild_payload_from_raw(raw_data: dict, locked: bool) -> dict:
    """Re-render PNGs from existing raw arrays with new color bounds.

    Used when toggling lock-scale: the raw SST data is identical between
    adaptive and locked modes — only vmin/vmax and PNGs differ.
    """
    raw_days = raw_data["raw_days"]
    lats = raw_data["lats"]
    lons = raw_data["lons"]

    if locked:
        vmin, vmax = 30.0, 90.0
    else:
        all_finite = []
        for rd in raw_days:
            finite = rd["arrF"][np.isfinite(rd["arrF"])]
            if finite.size > 0:
                all_finite.append(finite)
        if all_finite:
            stacked = np.concatenate(all_finite)
            vmin, vmax = compute_color_bounds(stacked, locked=False)
        else:
            vmin, vmax = 30.0, 90.0

    frames = []
    dates = []
    for rd in raw_days:
        arrF_vis = upsample_visual(rd["arrF"], 1)
        png_url = sst_to_base64_png(arrF_vis, vmin, vmax)
        frames.append(png_url)
        dates.append(rd["date"])

    res_km = abs(float(lats[1] - lats[0])) * 111.0 if len(lats) > 1 else None
    bounds = [
        [float(np.min(lats)), float(np.min(lons))],
        [float(np.max(lats)), float(np.max(lons))],
    ]

    return {
        "frames": frames,
        "dates": dates,
        "bounds": bounds,
        "vmin": float(vmin),
        "vmax": float(vmax),
        "res_km": res_km,
        "server": "",
        "dataset_id": "",
        "dataset_title": "",
    }


def _build_payload_from_disk_cache(cached: dict, raw_only: bool = False, locked_override=None):
    """Split a disk-cached payload into store_payload + raw_data.

    Supports three formats:
      - v3 (raw-only pre-cache): frames is None, raw arrays are base64
      - v2: raw_days[i]["arrF"] is a base64-encoded numpy string
      - v1: raw_days[i]["arrF"] is a nested JSON list of floats

    If raw_only=True, skip PNG rendering even for raw-only entries
    (used by _get_raw_data which only needs arrays for click lookups).
    """
    raw_days = []
    dates = []
    for rd in cached["raw_days"]:
        arr_data = rd["arrF"]
        if isinstance(arr_data, str):
            arrF = _deserialize_array(arr_data)
        else:
            arrF = np.array(arr_data, dtype=np.float64)
        raw_days.append({"arrF": arrF, "date": rd["date"]})
        dates.append(rd["date"])

    lats_data = cached["lats"]
    lons_data = cached["lons"]
    raw_data = {
        "raw_days": raw_days,
        "lats": _deserialize_array(lats_data) if isinstance(lats_data, str) else np.array(lats_data, dtype=np.float64),
        "lons": _deserialize_array(lons_data) if isinstance(lons_data, str) else np.array(lons_data, dtype=np.float64),
    }

    # Determine color bounds — use locked_override if provided (cross-mode reuse)
    need_rerender = locked_override is not None
    if need_rerender:
        if locked_override:
            vmin, vmax = 30.0, 90.0
        else:
            all_finite = []
            for rd in raw_days:
                finite = rd["arrF"][np.isfinite(rd["arrF"])]
                if finite.size > 0:
                    all_finite.append(finite)
            if all_finite:
                stacked = np.concatenate(all_finite)
                vmin, vmax = compute_color_bounds(stacked, locked=False)
            else:
                vmin, vmax = 30.0, 90.0
    else:
        vmin, vmax = cached["vmin"], cached["vmax"]

    # Handle raw-only pre-cache entries (frames=None) or cross-mode rerender
    frames = cached.get("frames")
    if (frames is None or need_rerender) and not raw_only:
        logger.info("Rendering PNGs on the fly (rerender=%s)", need_rerender)
        frames = []
        for rd in raw_days:
            arrF_vis = upsample_visual(rd["arrF"], 1)
            png_url = sst_to_base64_png(arrF_vis, vmin, vmax)
            frames.append(png_url)
    elif frames is None:
        frames = []  # raw_only mode — caller doesn't need PNGs

    store_payload = {
        "frames": frames,
        "dates": dates,
        "bounds": cached["bounds"],
        "vmin": float(vmin),
        "vmax": float(vmax),
        "res_km": cached.get("res_km"),
        "server": cached.get("server", ""),
        "dataset_id": cached.get("dataset_id", ""),
        "dataset_title": cached.get("dataset_title", ""),
    }

    return store_payload, raw_data


# NOTE: layout is a callable so date.today() (and the date picker's
# max_date_allowed / default date) are recomputed on every page load.
# A static layout freezes "today" at server startup — on the long-running
# Render worker that capped the date picker at the server's start date.
def build_sst_page():
    return html.Div(
    [
        # GotOne branded header
        html.Div(
            [
                html.Img(src=app.get_asset_url("gotone-logo.png")),
                html.Div(
                    [
                        html.H1("Offshore SST Analyzer"),
                        html.P(
                            "7-day sea-surface temperatures. "
                            "Click map to read temps. Click spots for details.",
                            className="subtitle",
                        ),
                    ]
                ),
                # No public link to the catch map — it's reachable only by
                # navigating to /catches directly, then entering the access key.
                dcc.Link("← GotOne Labs", href=labs_href(), refresh=labs_href().startswith("http"),
                         className="header-nav-link"),
            ],
            className="gotone-header",
        ),
        dbc.Container(
            [
                dbc.Row([build_sidebar(), build_map()]),
                # Backdrop overlay for mobile drawer (hidden on desktop via CSS)
                html.Div(id="sidebar-backdrop", className="sidebar-backdrop"),
                dcc.Store(id="sst-store"),
                dcc.Store(id="click-pos"),
                html.Div(id="fetch-spinner-target", style={"display": "none"}),
                html.Div(id="sst-events-sink", style={"display": "none"}),
                # Auto-fetch SST on page load (fires once after 500ms)
                dcc.Interval(id="auto-fetch", interval=500, max_intervals=1),
            ],
            fluid=True,
            style={"padding": "0"},
        ),
    ]
    )


# ---- /catches access gate (shared-secret link) ----
# Set CATCHES_ACCESS_KEY (preferred — matches the CATCHES_UPLOAD_TOKEN naming)
# or CATCH_ACCESS_KEY in production -> the catch map requires ?key=<key>.
# Unset -> open in local dev, but fail closed on Render. Trimmed so a stray
# space/newline from pasting into the dashboard doesn't break the match.
CATCH_ACCESS_KEY = (os.environ.get("CATCHES_ACCESS_KEY")
                    or os.environ.get("CATCH_ACCESS_KEY") or "").strip() or None


def _catch_authorized(search):
    """True if the catch map may be shown for this URL query string."""
    if not CATCH_ACCESS_KEY:
        # No key configured: open for local dev, but FAIL CLOSED in production
        # (Render sets RENDER) so a forgotten env var can't expose /catches.
        return not os.environ.get("RENDER")
    if not search:
        return False
    import hmac
    from urllib.parse import parse_qs

    supplied = parse_qs(search.lstrip("?")).get("key", [])
    return any(hmac.compare_digest(k, CATCH_ACCESS_KEY) for k in supplied)


def _catch_restricted_page(attempted=False):
    msg = ("Incorrect access key — try again."
           if attempted else "Enter your access key to continue.")
    return html.Div(
        [
            html.Div(
                [
                    html.Img(src=app.get_asset_url("gotone-logo.png")),
                    html.Div(
                        [
                            html.H1("Catch Map"),
                            html.P("Restricted access", className="subtitle"),
                        ]
                    ),
                    dcc.Link("← GotOne Labs", href=labs_href(), refresh=labs_href().startswith("http"), style={
                        "marginLeft": "auto", "alignSelf": "center",
                        "color": "#0183fe", "fontSize": "0.85rem", "paddingLeft": "1rem"}),
                ],
                className="gotone-header",
            ),
            html.Div(
                html.Div(
                    [
                        html.Div(msg, style={
                            "color": "#f87171" if attempted else "#e2e8f0",
                            "fontSize": "0.9rem", "fontWeight": "600",
                            "marginBottom": "0.75rem", "textAlign": "center"}),
                        dcc.Input(
                            id="catch-key-input", type="password",
                            placeholder="Access key", n_submit=0,
                            style={"width": "100%", "padding": "0.55rem 0.7rem",
                                   "borderRadius": "8px", "border": "1px solid #334155",
                                   "backgroundColor": "#ffffff", "color": "#0a1628",
                                   "fontSize": "0.9rem", "marginBottom": "0.6rem",
                                   "boxSizing": "border-box"},
                        ),
                        html.Button("Enter", id="catch-key-submit", n_clicks=0, style={
                            "width": "100%", "padding": "0.55rem", "borderRadius": "100px",
                            "border": "none", "backgroundColor": "#0183fe",
                            "color": "#ffffff", "fontWeight": "600",
                            "fontSize": "0.9rem", "cursor": "pointer"}),
                        html.Div(id="catch-key-sink", style={"display": "none"}),
                    ],
                    style={"maxWidth": "320px", "margin": "0 auto"},
                ),
                style={"padding": "3rem 1.5rem", "display": "flex",
                       "justifyContent": "center"},
            ),
        ]
    )


def serve_layout():
    # Shell: one Location + the page container the router swaps. Callable so
    # date.today() stays fresh on the long-running worker (see build_sst_page).
    return html.Div(
        [
            dcc.Location(id="url", refresh=False),
            html.Div(id="page-content"),
            html.Div(id="page-sink", style={"display": "none"}),
        ]
    )


app.layout = serve_layout


@app.callback(
    Output("page-content", "children"),
    Input("url", "pathname"),
    State("url", "search"),
)
def route_page(pathname, search):
    """/ = the GotOne Labs home, /sst, /currents, /catches (gated). On the old
    sst.gotoneapp.com host "/" (and anything unknown) is still the SST map."""
    path = (pathname or "/").rstrip("/") or "/"
    if path.endswith("/catches"):
        if not _catch_authorized(search):
            return _catch_restricted_page(attempted=bool(search and "key=" in search))
        return build_catch_page()
    if path == "/currents":
        return build_currents_page(labs_href=labs_href())
    if path == "/sst":
        return build_sst_page()
    if path == "/labs" or _request_host() not in LEGACY_HOSTS:
        return build_labs_page()
    return build_sst_page()


# Tab title + anonymous page count for every page view (assets/labs.js).
app.clientside_callback(
    """
    function(path) {
        if (window.GotOneLabs) { window.GotOneLabs.page(path); }
        return "";
    }
    """,
    Output("page-sink", "children"),
    Input("url", "pathname"),
)


# Restricted page: submit the access key -> reload /catches?key=... so the gate
# re-validates server-side (a wrong key just lands back on the prompt).
app.clientside_callback(
    """
    function(n_clicks, n_submit) {
        var el = document.getElementById("catch-key-input");
        var val = el ? el.value : "";
        if ((n_clicks || n_submit) && val) {
            window.location.href = "/catches?key=" + encodeURIComponent(val);
        }
        return "";
    }
    """,
    Output("catch-key-sink", "children"),
    Input("catch-key-submit", "n_clicks"),
    Input("catch-key-input", "n_submit"),
    prevent_initial_call=True,
)


_LOADING_OVERLAY_VISIBLE = {
    "position": "absolute",
    "top": 0, "left": 0, "right": 0, "bottom": 0,
    "backgroundColor": "rgba(255,255,255,0.7)",
    "display": "flex",
    "alignItems": "center",
    "justifyContent": "center",
    "zIndex": 1000,
}
_LOADING_OVERLAY_HIDDEN = {"display": "none"}


# ---- Callback 1a: Show loading overlay immediately on fetch ----
# Clientside so it runs instantly in the browser — no server round-trip.
# With 1 gunicorn worker, a server-side callback could be blocked behind
# the slow fetch callback, leaving the user with no visual feedback.
app.clientside_callback(
    """
    function(n_clicks, n_intervals, lock_scale) {
        return [
            true,
            "Loading...",
            {
                "position": "absolute",
                "top": 0, "left": 0, "right": 0, "bottom": 0,
                "backgroundColor": "rgba(255,255,255,0.7)",
                "display": "flex",
                "alignItems": "center",
                "justifyContent": "center",
                "zIndex": 1000
            }
        ];
    }
    """,
    Output("fetch-btn", "disabled"),
    Output("fetch-btn", "children"),
    Output("map-loading-overlay", "style"),
    Input("fetch-btn", "n_clicks"),
    Input("auto-fetch", "n_intervals"),
    Input("lock-scale", "value"),
    prevent_initial_call=True,
)


# ---- Callback 1b: Fetch 7-day SST data ----
@app.callback(
    output=[
        Output("sst-store", "data"),
        Output("fetch-status", "children"),
        Output("frame-slider", "marks"),
        Output("frame-slider", "value"),
        Output("frame-slider", "max"),
        Output("anim-controls", "style"),
        Output("fetch-spinner-target", "children"),
        Output("fetch-btn", "disabled", allow_duplicate=True),
        Output("fetch-btn", "children", allow_duplicate=True),
        Output("sidebar-col", "className", allow_duplicate=True),
        Output("sidebar-backdrop", "style", allow_duplicate=True),
        Output("gfw-layer", "url", allow_duplicate=True),
        Output("map-loading-overlay", "style", allow_duplicate=True),
    ],
    inputs=[
        Input("fetch-btn", "n_clicks"),
        Input("auto-fetch", "n_intervals"),
        Input("lock-scale", "value"),
    ],
    state=[
        State("end-date-picker", "date"),
    ],
    prevent_initial_call=True,
)
def fetch_sst_data(n_clicks, n_intervals, lock_scale, end_date_str):

    # Parse the date string from the date picker
    if end_date_str:
        end_date = datetime.strptime(end_date_str[:10], "%Y-%m-%d").date()
    else:
        end_date = date.today() - timedelta(days=4)

    locked = "lock" in (lock_scale or [])
    data_key = _cache_key(end_date, locked)

    try:
        # Check disk cache first — exact match, then nearby dates (±3 days)
        cached_hit = False
        cross_mode_hit = False
        actual_end_date = end_date
        cached, actual_end_date_found = find_nearest_cached(end_date, locked)
        if cached:
            actual_end_date = actual_end_date_found
            logger.info("Serving from cache: %s (requested %s)", actual_end_date, end_date)
            store_payload, raw_data = _build_payload_from_disk_cache(cached)
            cached_hit = True
            # Update data_key to match actual cached date
            data_key = _cache_key(actual_end_date, locked)
        # Cross-mode fallback: raw data is identical between adaptive/locked,
        # only vmin/vmax and PNG rendering differ
        if not cached_hit:
            alt_locked = not locked
            alt_key = _cache_key(end_date, alt_locked)

            # Check memory for alt mode — try exact date and ±3 fuzzy
            alt_mem_key = None
            for offset in range(4):
                for sign in ([0] if offset == 0 else [1, -1]):
                    candidate = end_date + timedelta(days=offset * sign)
                    ck = _cache_key(candidate, alt_locked)
                    if ck in _raw_data_cache:
                        alt_mem_key = ck
                        if offset != 0:
                            actual_end_date = candidate
                            data_key = _cache_key(candidate, locked)
                        break
                if alt_mem_key:
                    break

            if alt_mem_key:
                logger.info("Cross-mode reuse from memory: %s → %s", alt_mem_key, data_key)
                raw_data = _raw_data_cache[alt_mem_key]
                store_payload = _rebuild_payload_from_raw(raw_data, locked)
                cached_hit = True
                cross_mode_hit = True
            else:
                alt_cached, alt_end_date = find_nearest_cached(end_date, alt_locked)
                if alt_cached:
                    logger.info("Cross-mode reuse from disk: %s (locked=%s → %s)",
                                alt_end_date, alt_locked, locked)
                    store_payload, raw_data = _build_payload_from_disk_cache(
                        alt_cached, locked_override=locked)
                    actual_end_date = alt_end_date
                    cached_hit = True
                    cross_mode_hit = True
                    data_key = _cache_key(actual_end_date, locked)

        if not cached_hit:
            # Cache miss — fetch from ERDDAP
            logger.info("Cache miss, fetching from ERDDAP: %s", end_date)
            sst = get_sst_multiday(end_date, CFG)
            store_payload, raw_data = _build_payload(sst, locked)

        # Store raw data server-side FIRST — must happen before disk write
        # so click callbacks work even if the disk write crashes or OOMs
        _put_raw_cache(data_key, raw_data)

        # Update GFW date range to match the SST window
        # Skip if cross-mode toggle — dates haven't changed, tiles are still valid
        global _gfw_date_range, _gfw_style_cache
        sst_dates = store_payload.get("dates", [])
        if sst_dates and not cross_mode_hit:
            _gfw_date_range = f"{sst_dates[0]},{sst_dates[-1]}"
            _gfw_style_cache = {"url_template": None, "date_range": None}
            _gfw_tile_cache.clear()  # new date range → tiles change

        if cross_mode_hit:
            # Cross-mode reuse — write the re-rendered payload to disk
            # under the new mode's key so future loads are instant
            def _write_cross_mode_cache(sp, rd, ed, lk):
                try:
                    dp = dict(sp)
                    dp["raw_days"] = [
                        {"arrF": _serialize_array(d["arrF"]), "date": d["date"]}
                        for d in rd["raw_days"]
                    ]
                    dp["lats"] = _serialize_array(rd["lats"])
                    dp["lons"] = _serialize_array(rd["lons"])
                    put_cache(ed, lk, dp)
                    logger.info("Cross-mode cache written: %s locked=%s", ed, lk)
                except Exception:
                    logger.warning("Cross-mode cache write failed", exc_info=True)

            threading.Thread(
                target=_write_cross_mode_cache,
                args=(store_payload, raw_data, actual_end_date, locked),
                daemon=True,
            ).start()
        elif cached_hit and cached.get("frames") is None:
            # Raw-only pre-cache entry — upgrade with rendered PNGs in
            # background so subsequent loads are instant
            def _upgrade_cache(sp, rd, ed, lk):
                try:
                    dp = dict(sp)
                    dp["raw_days"] = [
                        {"arrF": _serialize_array(d["arrF"]), "date": d["date"]}
                        for d in rd["raw_days"]
                    ]
                    dp["lats"] = _serialize_array(rd["lats"])
                    dp["lons"] = _serialize_array(rd["lons"])
                    put_cache(ed, lk, dp)
                    logger.info("Cache upgraded with PNGs: %s", ed)
                except Exception:
                    logger.warning("Cache upgrade failed", exc_info=True)

            threading.Thread(
                target=_upgrade_cache,
                args=(store_payload, raw_data, actual_end_date, locked),
                daemon=True,
            ).start()
        elif not cached_hit:
            # Write to disk cache in background thread to avoid blocking
            # the callback response. Uses base64-encoded numpy arrays
            # instead of .tolist() to avoid 3x memory spike from Python
            # float objects (critical on Render's 512MB RAM).
            def _write_disk_cache(sp, rd, ed, lk):
                try:
                    disk_payload = dict(sp)
                    disk_payload["raw_days"] = [
                        {"arrF": _serialize_array(day["arrF"]), "date": day["date"]}
                        for day in rd["raw_days"]
                    ]
                    disk_payload["lats"] = _serialize_array(rd["lats"])
                    disk_payload["lons"] = _serialize_array(rd["lons"])
                    put_cache(ed, lk, disk_payload)
                except Exception:
                    logger.warning("Background cache write failed", exc_info=True)

            threading.Thread(
                target=_write_disk_cache,
                args=(store_payload, raw_data, end_date, locked),
                daemon=True,
            ).start()

        # Add data_key to store payload so click callbacks can look up raw data
        store_payload["data_key"] = data_key

        num_days = len(store_payload["frames"])

        # Build slider marks — just day-of-month
        marks = {}
        for i, d_str in enumerate(store_payload["dates"]):
            d = datetime.strptime(d_str, "%Y-%m-%d")
            marks[i] = str(d.day)

        status_text = f"MUR 1km \u2022 {num_days} days loaded"
        if cached_hit and actual_end_date != end_date:
            offset = (actual_end_date - end_date).days
            status_text += f" (nearest cache: {offset:+d}d)"
        status = html.Div(
            status_text,
            className="text-success",
            style={"fontSize": "0.75rem", "fontWeight": "500"},
        )

        anim_visible = {"display": "block"}

        # Cache-bust GFW tile URL so browser re-fetches for new date range
        gfw_url = f"/api/gfw/{{z}}/{{x}}/{{y}}.png?dr={_gfw_date_range or ''}"

        return (
            store_payload, status,
            marks, num_days - 1, num_days - 1,
            anim_visible, "",
            False, "Fetch SST",
            "gotone-sidebar", {"display": "none"},
            gfw_url,
            {"display": "none"},
        )

    except Exception as e:
        logger.exception("SST fetch failed")
        return (
            dash.no_update,
            dbc.Alert(
                f"Error: {e}",
                color="danger",
                className="py-2 px-3 mb-0",
                style={"fontSize": "0.8rem"},
            ),
            dash.no_update, dash.no_update, dash.no_update,
            dash.no_update, "",
            False, "Fetch SST",
            "gotone-sidebar", {"display": "none"},
            dash.no_update,
            {"display": "none"},
        )


# ---- Callback 2: Render static layers (POIs, legend, AOI, initial overlay) ----
# Fires on data load or POI selection change. Also sets the SST overlay
# on initial data load (clientside callback handles frame changes only).
@app.callback(
    Output("aoi-outline", "data"),
    Output("poi-layer", "children"),
    Output("legend-container", "children"),
    Output("map-loading-overlay", "style", allow_duplicate=True),
    Output("sst-overlay", "url"),
    Output("sst-overlay", "bounds"),
    Input("sst-store", "data"),
    Input("poi-picker", "value"),
    State("frame-slider", "value"),
    prevent_initial_call="initial_duplicate",
)
def render_static_layers(sst_data, selected_pois, frame_idx):
    aoi_geojson = build_aoi_geojson(CFG)
    hidden = {"display": "none"}

    if not sst_data or "frames" not in sst_data:
        return aoi_geojson, build_poi_markers(selected=selected_pois), "", dash.no_update, "", [[0, 0], [0, 0]]

    vmin = sst_data["vmin"]
    vmax = sst_data["vmax"]
    res_km = sst_data.get("res_km")

    legend = build_legend_component(vmin, vmax, res_km=res_km)
    poi_markers = build_poi_markers(selected=selected_pois)

    # Set overlay for current frame
    idx = frame_idx if frame_idx is not None else len(sst_data["frames"]) - 1
    if idx >= len(sst_data["frames"]):
        idx = len(sst_data["frames"]) - 1
    overlay_url = sst_data["frames"][idx]
    overlay_bounds = sst_data["bounds"]

    return aoi_geojson, poi_markers, legend, hidden, overlay_url, overlay_bounds


# ---- Clientside: Swap overlay PNG by frame index (instant, no server trip) ----
# sst-store is State (not Input) — initial overlay is set by render_static_layers.
# This callback only fires on frame slider changes (user interaction / animation).
app.clientside_callback(
    """
    function(frame_idx, sst_data) {
        if (!sst_data || !sst_data.frames) {
            return [window.dash_clientside.no_update, window.dash_clientside.no_update];
        }
        var idx = frame_idx || 0;
        if (idx >= sst_data.frames.length) idx = sst_data.frames.length - 1;
        return [sst_data.frames[idx], sst_data.bounds];
    }
    """,
    Output("sst-overlay", "url", allow_duplicate=True),
    Output("sst-overlay", "bounds", allow_duplicate=True),
    Input("frame-slider", "value"),
    State("sst-store", "data"),
    prevent_initial_call=True,
)


# ---- Clientside: Play/Pause toggle (no server trip) ----
app.clientside_callback(
    """
    function(n_clicks, currently_disabled) {
        if (currently_disabled) return [false, 'Pause'];
        return [true, 'Play'];
    }
    """,
    Output("anim-interval", "disabled"),
    Output("play-pause-btn", "children"),
    Input("play-pause-btn", "n_clicks"),
    State("anim-interval", "disabled"),
    prevent_initial_call=True,
)


# ---- Clientside: Auto-advance frame on interval tick (no server trip) ----
app.clientside_callback(
    """
    function(n_intervals, current_val, max_val) {
        if (current_val == null || max_val == null) return window.dash_clientside.no_update;
        var next_val = current_val + 1;
        if (next_val > max_val) next_val = 0;
        return next_val;
    }
    """,
    Output("frame-slider", "value", allow_duplicate=True),
    Input("anim-interval", "n_intervals"),
    State("frame-slider", "value"),
    State("frame-slider", "max"),
    prevent_initial_call=True,
)


# ---- Clientside: Step forward/back buttons (no server trip) ----
app.clientside_callback(
    """
    function(back_clicks, fwd_clicks, current_val, max_val) {
        if (current_val == null || max_val == null) return window.dash_clientside.no_update;
        var ctx = window.dash_clientside.callback_context;
        if (!ctx.triggered.length) return window.dash_clientside.no_update;
        var triggered_id = ctx.triggered[0].prop_id.split('.')[0];
        if (triggered_id === 'step-back-btn') return Math.max(0, current_val - 1);
        if (triggered_id === 'step-fwd-btn') return Math.min(max_val, current_val + 1);
        return window.dash_clientside.no_update;
    }
    """,
    Output("frame-slider", "value", allow_duplicate=True),
    Input("step-back-btn", "n_clicks"),
    Input("step-fwd-btn", "n_clicks"),
    State("frame-slider", "value"),
    State("frame-slider", "max"),
    prevent_initial_call=True,
)


# ---- Clientside: Day indicator text (no server trip) ----
app.clientside_callback(
    """
    function(frame_idx, sst_data) {
        if (!sst_data || !sst_data.dates) return '';
        var idx = frame_idx || 0;
        var num_days = sst_data.dates.length;
        if (idx >= num_days) idx = num_days - 1;
        var day_date = sst_data.dates[idx];
        var d = new Date(day_date + 'T12:00:00');
        var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        var label = months[d.getMonth()] + ' ' +
                    String(d.getDate()).padStart(2, '0') + ', ' +
                    d.getFullYear();
        return label + '  (Day ' + (idx + 1) + ' of ' + num_days + ')';
    }
    """,
    Output("day-indicator", "children"),
    Input("frame-slider", "value"),
    State("sst-store", "data"),
)


# ---- Callback 3a: Route map clicks (SST reading vs measure vs POI) ----
@app.callback(
    Output("click-pos", "data"),
    Output("measure-state", "data"),
    Output("measure-readout", "children"),
    Output("click-marker", "children", allow_duplicate=True),
    Input("sst-map", "clickData"),
    State("measure-state", "data"),
    State("poi-picker", "value"),
    State("sst-store", "data"),
    State("frame-slider", "value"),
    prevent_initial_call=True,
)
def handle_map_click(click_data, measure, selected_pois, sst_data, frame_idx):
    if not click_data:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update

    lat = click_data["latlng"]["lat"]
    lng = click_data["latlng"]["lng"]

    # Check if click is near a POI
    poi = find_nearest_poi(lat, lng, selected=selected_pois)

    # In measure mode, use POI coordinates if clicked near one
    click_lat = poi[1] if poi else lat
    click_lng = poi[2] if poi else lng

    if measure and measure.get("mode") == "a":
        # Set point A, wait for B
        label_a = poi[0] if poi else f"{click_lat:.3f}\u00b0N, {abs(click_lng):.3f}\u00b0W"
        new_state = {"mode": "b", "a": {"lat": click_lat, "lng": click_lng, "label": label_a}, "b": None}
        readout = html.Div(
            [
                html.Div(f"A: {label_a}", style={
                    "fontWeight": "600", "color": "#334155",
                }),
                html.Div("Click point B", style={
                    "fontSize": "0.8rem", "color": "#0183fe", "marginTop": "2px",
                }),
            ]
        )
        marker = [
            dl.CircleMarker(
                center=[click_lat, click_lng], radius=5,
                pathOptions={"color": "#0183fe", "weight": 2, "fillOpacity": 0.4},
                children=[dl.Tooltip(
                    f"A: {label_a}" if poi else "A",
                    permanent=True, direction="top",
                    offset=[0, -8], pane="tooltipPane")],
            )
        ]
        return dash.no_update, new_state, readout, marker

    elif measure and measure.get("mode") == "b":
        # Set point B, compute distance
        a = measure["a"]
        label_b = poi[0] if poi else f"{click_lat:.3f}\u00b0N, {abs(click_lng):.3f}\u00b0W"
        m = format_measurement(a["lat"], a["lng"], click_lat, click_lng)
        new_state = {"mode": "done", "a": a, "b": {"lat": click_lat, "lng": click_lng, "label": label_b}}
        readout = html.Div(
            [
                html.Div(m["label"], style={
                    "fontWeight": "700", "fontSize": "0.9rem", "color": "#1e293b",
                }),
                html.Div(
                    f"A: {a.get('label', '')}",
                    style={"fontSize": "0.7rem", "color": "#64748b", "marginTop": "4px"},
                ),
                html.Div(
                    f"B: {label_b}",
                    style={"fontSize": "0.7rem", "color": "#64748b"},
                ),
            ]
        )
        marker = [
            dl.Polyline(
                positions=[[a["lat"], a["lng"]], [click_lat, click_lng]],
                pathOptions={"color": "#0183fe", "weight": 2, "dashArray": "6 4"},
            ),
            dl.CircleMarker(
                center=[a["lat"], a["lng"]], radius=5,
                pathOptions={"color": "#0183fe", "weight": 2, "fillOpacity": 0.4},
                children=[dl.Tooltip(
                    f"A: {a.get('label', 'A')}", permanent=True, direction="top",
                    offset=[0, -8], pane="tooltipPane")],
            ),
            dl.CircleMarker(
                center=[click_lat, click_lng], radius=5,
                pathOptions={"color": "#0183fe", "weight": 2, "fillOpacity": 0.4},
                children=[dl.Tooltip(
                    f"B: {label_b}", permanent=True, direction="top",
                    offset=[0, -8], pane="tooltipPane")],
            ),
            dl.CircleMarker(
                center=[(a["lat"] + click_lat) / 2, (a["lng"] + click_lng) / 2],
                radius=0,
                pathOptions={"opacity": 0},
                children=[dl.Tooltip(
                    m["label"], permanent=True, direction="top",
                    offset=[0, -12], pane="tooltipPane",
                    className="measure-tooltip",
                )],
            ),
        ]
        return dash.no_update, new_state, readout, marker

    else:
        # Normal mode — show POI info or SST reading
        if poi:
            # Clicked near a POI — show POI tooltip in click-marker layer
            poi_name, poi_lat, poi_lon = poi
            temp = None
            data_key = sst_data.get("data_key") if sst_data else None
            raw = _get_raw_data(data_key) if data_key else None
            if raw:
                fi = min(frame_idx or 0, len(raw["raw_days"]) - 1)
                arrF = raw["raw_days"][fi]["arrF"]
                temp = _lookup_temp(poi_lat, poi_lon, arrF, raw["lats"], raw["lons"])

            tooltip_content = build_poi_tooltip(poi_name, poi_lat, poi_lon, temp)
            marker = [
                dl.CircleMarker(
                    center=[poi_lat, poi_lon],
                    radius=6,
                    pathOptions={"color": "#475569", "weight": 2, "fillOpacity": 0.3,
                                 "className": "sst-poi-pick"},   # SST feature use: "poi"
                    children=[
                        dl.Tooltip(
                            tooltip_content,
                            permanent=True, direction="top",
                            offset=[0, -10], pane="tooltipPane",
                        )
                    ],
                )
            ]
            return dash.no_update, dash.no_update, dash.no_update, marker
        else:
            # SST reading — pass to click-pos store (triggers render_click_marker)
            pos = {"lat": lat, "lng": lng}
            return pos, dash.no_update, dash.no_update, dash.no_update


# ---- Callback 3b: Render click marker (fires on click, frame change, or data change) ----
@app.callback(
    Output("click-marker", "children"),
    Input("click-pos", "data"),
    Input("sst-store", "data"),
    Input("frame-slider", "value"),
    prevent_initial_call=True,
)
def render_click_marker(click_pos, sst_data, frame_idx):
    if not click_pos or not sst_data:
        return []

    data_key = sst_data.get("data_key")
    raw = _get_raw_data(data_key) if data_key else None
    if not raw:
        logger.warning("render_click_marker: raw data unavailable for key=%s "
                       "(in_memory=%s, cache_keys=%s)",
                       data_key,
                       data_key in _raw_data_cache if data_key else False,
                       list(_raw_data_cache.keys()))
        return []

    frame_idx = frame_idx or 0
    num_frames = len(raw["raw_days"])
    if frame_idx >= num_frames:
        frame_idx = num_frames - 1

    lat = click_pos["lat"]
    lng = click_pos["lng"]
    arrF = raw["raw_days"][frame_idx]["arrF"]
    lats = raw["lats"]
    lons = raw["lons"]

    # Find nearest grid point
    lat_idx = np.argmin(np.abs(lats - lat))
    lon_idx = np.argmin(np.abs(lons - lng))

    temp = arrF[lat_idx, lon_idx]

    if not np.isfinite(temp):
        return [
            dl.CircleMarker(
                center=[lat, lng],
                radius=6,
                pathOptions={"color": "#e11d48", "weight": 2, "fillOpacity": 0.3},
                children=[
                    dl.Tooltip(
                        html.Div(
                            [
                                html.Div("No data", style={
                                    "fontSize": "0.95rem", "fontWeight": "600",
                                    "color": "#888",
                                }),
                                html.Div(f"{lat:.3f}\u00b0N, {abs(lng):.3f}\u00b0W", style={
                                    "fontSize": "0.7rem", "color": "#999",
                                    "marginTop": "2px",
                                }),
                            ],
                            style={"textAlign": "center"},
                        ),
                        permanent=True, direction="top", offset=[0, -8],
                        className="sst-tooltip",
                    )
                ],
            )
        ]

    return [
        dl.CircleMarker(
            center=[lat, lng],
            radius=6,
            pathOptions={"color": "#e11d48", "weight": 2, "fillOpacity": 0.3},
            children=[
                dl.Tooltip(
                    html.Div(
                        [
                            html.Div(f"{temp:.1f}\u00b0F", style={
                                "fontSize": "1.15rem", "fontWeight": "700",
                                "color": "#1e293b", "lineHeight": "1.2",
                            }),
                            html.Div(f"{lat:.3f}\u00b0N, {abs(lng):.3f}\u00b0W", style={
                                "fontSize": "0.7rem", "color": "#94a3b8",
                                "marginTop": "2px",
                            }),
                        ],
                        style={"textAlign": "center"},
                    ),
                    permanent=True, direction="top", offset=[0, -8],
                    className="sst-tooltip",
                )
            ],
        )
    ]


# ---- Callback 8: Measure toggle ----
@app.callback(
    Output("measure-state", "data", allow_duplicate=True),
    Output("measure-btn", "children"),
    Output("measure-btn", "color"),
    Output("measure-btn", "outline"),
    Output("measure-readout", "children", allow_duplicate=True),
    Output("click-marker", "children", allow_duplicate=True),
    Input("measure-btn", "n_clicks"),
    State("measure-state", "data"),
    prevent_initial_call=True,
)
def toggle_measure(n_clicks, measure):
    mode = measure.get("mode", "off") if measure else "off"
    if mode == "off":
        # Activate — waiting for point A
        return (
            {"mode": "a", "a": None, "b": None},
            "\U0001F4CF Measuring...",
            "primary",
            False,
            html.Div("Click point A on the map", style={
                "fontWeight": "500", "color": "#0183fe", "fontSize": "0.8rem",
            }),
            [],  # clear any existing markers
        )
    else:
        # Deactivate
        return (
            {"mode": "off", "a": None, "b": None},
            "\U0001F4CF Measure",
            "secondary",
            True,
            "",
            [],
        )


# ---- Callback 9: POI count label ----
@app.callback(
    Output("poi-count", "children"),
    Input("poi-picker", "value"),
)
def update_poi_count(selected):
    n = len(selected) if selected else 0
    total = len(get_all_poi_names())
    if n == total:
        return "All"
    return f"{n}/{total}"


# ---- POI checklist: collapse toggle ----
app.clientside_callback(
    """
    function(n_clicks, is_open) {
        return [!is_open, is_open ? '\\u25BE' : '\\u25B4'];
    }
    """,
    Output("poi-collapse", "is_open"),
    Output("poi-chevron", "children"),
    Input("poi-collapse-toggle", "n_clicks"),
    State("poi-collapse", "is_open"),
    prevent_initial_call=True,
)


# ---- POI checklist: select all / deselect all ----
@app.callback(
    Output("poi-picker", "value"),
    Input("poi-select-all", "n_clicks"),
    Input("poi-deselect-all", "n_clicks"),
    prevent_initial_call=True,
)
def poi_select_deselect(select_clicks, deselect_clicks):
    if ctx.triggered_id == "poi-select-all":
        return get_all_poi_names()
    return []


# ---- Chart layer toggles ----


@app.callback(
    Output("contours-layer", "opacity"),
    Output("gebco-layer", "opacity"),
    Output("gfw-layer", "opacity"),
    Input("layer-toggles", "value"),
)
def toggle_layers(active_layers):
    """Show/hide nautical chart, bathymetry, and fishing activity layers."""
    active = active_layers or []
    return (0.6 if "contours" in active else 0,
            0.5 if "gebco" in active else 0,
            0.7 if "gfw" in active else 0)


# ---- SST feature use (anonymous; GotOneLabs.event in assets/labs.js) ----
# One clientside callback on the controls and stores that mark real use. Dash 4 passes an
# empty `triggered` on initial calls and prevent_initial_call skips them anyway, so building
# the page counts nothing; GotOneLabs sends each name once per page view, on the live hosts
# only. Names: data/labstats.EVENTS["sst"]. Dragging the day slider ("step") is counted by
# the data-labs-event wrapper in layout/sidebar.py.
app.clientside_callback(
    """
    function() {
        var L = window.GotOneLabs;
        var t = (window.dash_clientside.callback_context || {}).triggered || [];
        if (!L) { return ""; }
        function ev(name) { L.event(name, {tool: "sst"}); }
        t.forEach(function (x) {
            var id = x.prop_id || "", v = x.value;
            if (id === "fetch-btn.n_clicks") { ev("load"); }
            else if (id === "anim-interval.disabled") { if (v === false) { ev("play"); } }
            else if (id === "step-back-btn.n_clicks" || id === "step-fwd-btn.n_clicks") { ev("step"); }
            else if (id === "click-pos.data") { if (v) { ev("readout"); } }
            else if (id === "click-marker.children") {
                var m = (v || [])[0], po = m && m.props && m.props.pathOptions;
                if (po && po.className === "sst-poi-pick") { ev("poi"); }
            }
            else if (id === "measure-state.data") { if (v && v.mode === "done") { ev("measure"); } }
            else if (id === "poi-collapse.is_open") { if (v) { ev("spots"); } }
            else if (id === "layer-toggles.value") {
                (v || []).forEach(function (k) {
                    var n = {contours: "chart", gebco: "bathymetry", gfw: "gfw"}[k];
                    if (n) { ev(n); }
                });
            }
            else if (id === "lock-scale.value") { if ((v || []).indexOf("lock") >= 0) { ev("lock_scale"); } }
            else if (id === "sst-opacity.value") { ev("opacity"); }
        });
        return "";
    }
    """,
    Output("sst-events-sink", "children"),
    Input("fetch-btn", "n_clicks"),
    Input("anim-interval", "disabled"),
    Input("step-back-btn", "n_clicks"),
    Input("step-fwd-btn", "n_clicks"),
    Input("click-pos", "data"),
    Input("click-marker", "children"),
    Input("measure-state", "data"),
    Input("poi-collapse", "is_open"),
    Input("layer-toggles", "value"),
    Input("lock-scale", "value"),
    Input("sst-opacity", "value"),
    prevent_initial_call=True,
)


# ---- GotOne catch map (route: /catches) ----


@app.callback(
    Output("catch-store", "data"),
    Output("catch-count", "children"),
    Input("catch-group-picker", "value"),
    Input("catch-species-picker", "value"),
    Input("catch-year-picker", "value"),
    Input("catch-grain", "value"),
    State("url", "search"),
)
def update_catch_store(groups, species_ids, years, grain, search):
    """Filter catches into a compact payload (points + windowed frames + grid)."""
    if not _catch_authorized(search):
        empty = {"points": [], "frames": [], "labels": [], "groups": [],
                 "species": [], "bounds": None, "count": 0, "grain": "season"}
        return empty, "restricted"
    payload = build_catch_payload(
        groups=groups or [],
        species_ids=species_ids or None,
        years=years or None,
        grain=grain or "season",
    )
    return payload, f"{payload['count']:,} catches shown"


@app.callback(
    Output("catch-group-picker", "value"),
    Input("catch-group-select-all", "n_clicks"),
    Input("catch-group-deselect-all", "n_clicks"),
    prevent_initial_call=True,
)
def catch_group_select_deselect(sel_clicks, des_clicks):
    if ctx.triggered_id == "catch-group-select-all":
        return get_all_groups()
    return []


@app.callback(
    Output("catch-contours-layer", "opacity"),
    Output("catch-gebco-layer", "opacity"),
    Output("catch-white-underlay", "fillOpacity"),
    Input("catch-contours-opacity", "value"),
    Input("catch-gebco-opacity", "value"),
)
def set_catch_layer_opacity(contours, gebco):
    """Dim the NOAA chart / bathymetry. The chart sits on a white backdrop so
    dimming it fades toward white (legible), not the grey basemap."""
    c = contours or 0
    white = 1.0 if c > 0 else 0   # full white whenever the chart is shown
    return c, (gebco or 0), white


# Dim the catches via pane CSS opacity (instant — no marker re-render). The
# heatmap blur is handled in the builder so it stays synced with the render.
app.clientside_callback(
    """
    function(op) {
        var el = document.querySelector('.leaflet-overlay-pane');
        if (el) el.style.opacity = (op == null ? 1 : op);
        return '';
    }
    """,
    Output("catch-opacity-sink", "children"),
    Input("catch-points-opacity", "value"),
)


# Slider max tracks the number of windows in the current payload.
app.clientside_callback(
    """
    function(store) {
        if (!store || !store.frames || !store.frames.length) return 0;
        return store.frames.length - 1;
    }
    """,
    Output("catch-frame-slider", "max"),
    Input("catch-store", "data"),
)


# Reset the scrub position when the payload changes (new grain / filters).
app.clientside_callback(
    "function(store) { return 0; }",
    Output("catch-frame-slider", "value", allow_duplicate=True),
    Input("catch-store", "data"),
    prevent_initial_call=True,
)


# Build the visible GeoJSON for the active time window. Points mode -> one
# haloed marker per catch. Heatmap mode -> bin the windowed points into a
# coarse grid and emit a density-ramped soft circle per cell (the pane blur,
# toggled separately, melts them into blobs). Season grain = full-year window
# = all catches. Pure clientside -> instant frame swaps.
app.clientside_callback(
    """
    function(store, mode, frameIdx) {
        // Keep the pane blur in lockstep with what we're about to render, so it
        // can never linger into points mode (which would smear dots into blobs).
        var pane = document.querySelector('.leaflet-overlay-pane');
        if (pane) pane.style.filter = (mode === "heat" ? "blur(4px)" : "none");
        var empty = {"type": "FeatureCollection", "features": []};
        if (!store) return empty;
        var pts = store.points || [];
        var groups = store.groups || [];
        var species = store.species || [];
        var lo = 1, hi = 365;
        if (store.frames && store.frames.length) {
            var f = store.frames[Math.min(frameIdx || 0, store.frames.length - 1)];
            lo = f[0]; hi = f[1];
        }
        if (mode === "heat") {
            function heatColor(t) {
                t = Math.max(0, Math.min(1, t));
                var r, g, b, u;
                if (t < 0.25) { u = t / 0.25; r = 0; g = Math.round(255 * u); b = 255; }
                else if (t < 0.5) { u = (t - 0.25) / 0.25; r = 0; g = 255; b = Math.round(255 * (1 - u)); }
                else if (t < 0.75) { u = (t - 0.5) / 0.25; r = Math.round(255 * u); g = 255; b = 0; }
                else { u = (t - 0.75) / 0.25; r = 255; g = Math.round(255 * (1 - u)); b = 0; }
                return "rgb(" + r + "," + g + "," + b + ")";
            }
            // Count local density on a fine grid for COLOR only, then draw a soft
            // circle at each catch's ACTUAL location (no grid lattice). Overlap +
            // the pane blur build the kernel-density surface.
            var cell = 0.03, bins = {}, win = [];
            for (var i = 0; i < pts.length; i++) {
                var p = pts[i];
                if (p[5] < lo || p[5] > hi) continue;
                win.push(p);
                var key = Math.round(p[0] / cell) + "_" + Math.round(p[1] / cell);
                bins[key] = (bins[key] || 0) + 1;
            }
            // Normalize against the ~90th percentile so hot cores read warm.
            var counts = [];
            for (var k in bins) counts.push(bins[k]);
            counts.sort(function(a, b) { return a - b; });
            var ref = counts.length ? counts[Math.floor(0.90 * (counts.length - 1))] : 1;
            if (ref < 1) ref = 1;
            var hfeats = [];
            for (var j = 0; j < win.length; j++) {
                var q = win[j];
                var t = Math.min(1, Math.sqrt(bins[Math.round(q[0] / cell) + "_" + Math.round(q[1] / cell)] / ref));
                hfeats.push({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [q[1], q[0]]},
                    // alpha scales with density: sparse -> transparent (map shows
                    // through, fades toward the light basemap), hot cores -> opaque.
                    "properties": {"heat": true, "color": heatColor(t), "alpha": 0.08 + 0.5 * t}
                });
            }
            return {"type": "FeatureCollection", "features": hfeats};
        }
        var feats = [];
        for (var i = 0; i < pts.length; i++) {
            var p = pts[i];
            if (p[5] < lo || p[5] > hi) continue;
            var g = groups[p[2]] || {};
            feats.push({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p[1], p[0]]},
                "properties": {
                    "color": g.color || "#9aa0a6",
                    "species": species[p[6]] || g.label || "",
                    "length": p[3], "temp": p[4], "date": p[7],
                    "group": g.key, "label": g.label
                }
            });
        }
        return {"type": "FeatureCollection", "features": feats};
    }
    """,
    Output("catches-layer", "data"),
    Input("catch-store", "data"),
    Input("catch-mode", "value"),
    Input("catch-frame-slider", "value"),
)


# Show playback only when there's a window to scrub (Month/Week/Day) — works in
# both points and heatmap modes. Always reveal paused ("Play").
app.clientside_callback(
    """
    function(grain) {
        var on = grain && grain !== "season";
        return [on ? {"display": "block"} : {"display": "none"}, true, "Play"];
    }
    """,
    Output("catch-anim-controls", "style"),
    Output("catch-anim-interval", "disabled", allow_duplicate=True),
    Output("catch-play-pause", "children", allow_duplicate=True),
    Input("catch-grain", "value"),
    prevent_initial_call=True,
)


# Play / pause the season animation.
app.clientside_callback(
    """
    function(n_clicks, currently_disabled) {
        if (currently_disabled) return [false, "Pause"];
        return [true, "Play"];
    }
    """,
    Output("catch-anim-interval", "disabled", allow_duplicate=True),
    Output("catch-play-pause", "children", allow_duplicate=True),
    Input("catch-play-pause", "n_clicks"),
    State("catch-anim-interval", "disabled"),
    prevent_initial_call=True,
)


# Auto-advance the frame on each interval tick (wraps at the end).
app.clientside_callback(
    """
    function(n_intervals, current_val, max_val) {
        if (current_val == null || max_val == null) return window.dash_clientside.no_update;
        var next_val = current_val + 1;
        if (next_val > max_val) next_val = 0;
        return next_val;
    }
    """,
    Output("catch-frame-slider", "value", allow_duplicate=True),
    Input("catch-anim-interval", "n_intervals"),
    State("catch-frame-slider", "value"),
    State("catch-frame-slider", "max"),
    prevent_initial_call=True,
)


# Step forward / back buttons.
app.clientside_callback(
    """
    function(back_clicks, fwd_clicks, current_val, max_val) {
        if (current_val == null || max_val == null) return window.dash_clientside.no_update;
        var ctx = window.dash_clientside.callback_context;
        if (!ctx.triggered.length) return window.dash_clientside.no_update;
        var t = ctx.triggered[0].prop_id.split(".")[0];
        if (t === "catch-step-back") return Math.max(0, current_val - 1);
        if (t === "catch-step-fwd") return Math.min(max_val, current_val + 1);
        return window.dash_clientside.no_update;
    }
    """,
    Output("catch-frame-slider", "value", allow_duplicate=True),
    Input("catch-step-back", "n_clicks"),
    Input("catch-step-fwd", "n_clicks"),
    State("catch-frame-slider", "value"),
    State("catch-frame-slider", "max"),
    prevent_initial_call=True,
)


# Season-window label, e.g. "Apr 3 – Apr 16". Store is an Input (not State) so
# the label appears as soon as the payload arrives, not only on slider moves.
app.clientside_callback(
    """
    function(frameIdx, store) {
        if (!store || !store.labels || !store.labels.length) return "";
        return store.labels[Math.min(frameIdx || 0, store.labels.length - 1)];
    }
    """,
    Output("catch-window-label", "children"),
    Input("catch-frame-slider", "value"),
    Input("catch-store", "data"),
)


@app.callback(
    Output("sst-overlay", "opacity"),
    Input("sst-opacity", "value"),
)
def update_sst_opacity(opacity):
    """Adjust SST overlay transparency via sidebar slider."""
    return opacity


# ---- Currents page (route: /currents) ----
# All animation, station markers and the chart run in assets/currents.js
# (window.Currents). These clientside callbacks only forward control changes —
# no server work and nothing per frame. Each writes "" to its own hidden sink.
# Initial calls are wanted: they hand the default control values to the engine,
# which queues them until the map is ready. A real change (Dash 4 passes an empty
# `triggered` on initial calls) also goes to Currents.noteUse: anonymous feature use.
_CURRENTS_FORWARD = """
function(value) {
    var C = window.Currents;
    if (C) {
        C.%s(value);
        var t = (window.dash_clientside.callback_context || {}).triggered || [];
        if (t.length && C.noteUse) { C.noteUse("%s", value); }
    }
    return "";
}
"""
for _ctrl, _method in (
    ("week", "setWeek"),
    ("depth", "setDepth"),
    ("rate", "setRate"),
    ("station", "selectStation"),
    ("options", "setOptions"),
    ("strength", "setStrength"),
):
    app.clientside_callback(
        _CURRENTS_FORWARD % (_method, _ctrl),
        Output(f"currents-sink-{_ctrl}", "children"),
        Input(f"currents-{_ctrl}", "value"),
    )

app.clientside_callback(
    _CURRENTS_FORWARD % ("setDate", "date"),
    Output("currents-sink-date", "children"),
    Input("currents-date", "date"),
)

# Harbor-inlet estimates (data/inlets.json, a dcc.Store) -> extra spots.
app.clientside_callback(
    _CURRENTS_FORWARD % ("setInlets", "inlets"),
    Output("currents-sink-inlets", "children"),
    Input("currents-inlets", "data"),
)

# View switch: show that view's panel and hand the engine the mode.
app.clientside_callback(
    """
    function(mode) {
        var C = window.Currents;
        if (C) {
            C.setMode(mode);
            var t = (window.dash_clientside.callback_context || {}).triggered || [];
            if (t.length && C.noteUse) { C.noteUse("mode", mode); }
        }
        var on = {display: "block"}, off = {display: "none"};
        return [mode === "date" ? on : off, mode === "cycle" ? on : off,
                mode === "week" ? on : off];
    }
    """,
    Output("currents-panel-date", "style"),
    Output("currents-panel-cycle", "style"),
    Output("currents-panel-week", "style"),
    Input("currents-mode", "value"),
)

# "Now": back to today (Eastern), clock at the current time.
app.clientside_callback(
    """
    function(n) {
        if (window.Currents) { window.Currents.goNow(); }
        return new Intl.DateTimeFormat("en-CA", {timeZone: "America/New_York",
            year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date());
    }
    """,
    Output("currents-date", "date"),
    Input("currents-now", "n_clicks"),
    prevent_initial_call=True,
)

# Tablets / phones: the sidebar is a drawer there (assets/currents.css), so
# picking a view, date, week, strength or spot closes it to show the result.
# Harmless on desktop, where the sidebar never carries "drawer-open".
app.clientside_callback(
    """
    function() { return ["gotone-sidebar", {"display": "none"}]; }
    """,
    Output("sidebar-col", "className", allow_duplicate=True),
    Output("sidebar-backdrop", "style", allow_duplicate=True),
    Input("currents-mode", "value"),
    Input("currents-date", "date"),
    Input("currents-week", "value"),
    Input("currents-strength", "value"),
    Input("currents-station", "value"),
    prevent_initial_call=True,
)

# "How it works": opens by itself on someone's first visit, then on request.
app.clientside_callback(
    """
    function(open1, open2, close) {
        var t = ((window.dash_clientside.callback_context.triggered || [])[0] || {}).prop_id || "";
        if (t.indexOf("currents-about-open") === 0) {
            if (window.GotOneLabs) { window.GotOneLabs.event("how_it_works", {tool: "currents"}); }
            return true;
        }
        if (t.indexOf("currents-about-close") === 0) { return false; }
        var KEY = "gotone-currents-intro-v1", seen = true;
        try {
            seen = !!window.localStorage.getItem(KEY);
            window.localStorage.setItem(KEY, "1");
        } catch (e) { seen = true; }        // storage blocked: don't nag every visit
        return !seen;
    }
    """,
    Output("currents-about", "is_open"),
    Input("currents-about-open", "n_clicks"),
    Input("currents-about-open-2", "n_clicks"),
    Input("currents-about-close", "n_clicks"),
)

app.clientside_callback(
    """
    function(n) {
        if (!window.Currents) { return window.dash_clientside.no_update; }
        return window.Currents.togglePlay() ? "Pause" : "Play";
    }
    """,
    Output("currents-play", "children"),
    Input("currents-play", "n_clicks"),
    prevent_initial_call=True,
)

app.clientside_callback(
    """
    function(back, fwd) {
        var t = window.dash_clientside.callback_context.triggered[0];
        if (window.Currents && t) {
            window.Currents.step(t.prop_id.indexOf("back") >= 0 ? -3600 : 3600);
        }
        return "";
    }
    """,
    Output("currents-sink-step", "children"),
    Input("currents-step-back", "n_clicks"),
    Input("currents-step-fwd", "n_clicks"),
    prevent_initial_call=True,
)

app.clientside_callback(
    """
    function(week, lessons) {
        var w = (lessons || []).find(function(x) { return x.id === week; });
        if (!w) { return ""; }
        return (w.subtitle ? w.subtitle + " — " : "") + w.lesson;
    }
    """,
    Output("currents-lesson", "children"),
    Input("currents-week", "value"),
    State("currents-weeks", "data"),
)


# ---- Mobile drawer toggle (clientside — no server round-trip) ----
app.clientside_callback(
    """
    function(open_clicks, close_clicks, backdrop_clicks) {
        var trigger = dash_clientside.callback_context.triggered[0];
        if (!trigger) return [dash_clientside.no_update, dash_clientside.no_update];
        if (trigger.prop_id === "sidebar-open.n_clicks") {
            return ["gotone-sidebar drawer-open", {"display": "block"}];
        }
        return ["gotone-sidebar", {"display": "none"}];
    }
    """,
    Output("sidebar-col", "className"),
    Output("sidebar-backdrop", "style"),
    Input("sidebar-open", "n_clicks"),
    Input("sidebar-close", "n_clicks"),
    Input("sidebar-backdrop", "n_clicks"),
    prevent_initial_call=True,
)


# ---- Startup pre-warm: populate server-side cache from disk ----
# Only loads from DISK cache (fast, no ERDDAP). Heavy ERDDAP fetches happen
# on first user request. This keeps startup lightweight so Render's health
# check passes quickly.
def _prewarm_cache():
    time.sleep(15)  # Let gunicorn fully start and pass health check
    end_date = date.today() - timedelta(days=4)
    for locked in [False, True]:
        data_key = _cache_key(end_date, locked)
        if data_key in _raw_data_cache:
            continue
        cached = get_cached(end_date, locked)
        if cached and not is_stale(end_date):
            try:
                _, raw_data = _build_payload_from_disk_cache(cached, raw_only=True)
                _put_raw_cache(data_key, raw_data)
                logger.info("Pre-warm: loaded from disk cache %s (locked=%s)", end_date, locked)
            except Exception:
                logger.warning("Pre-warm: disk cache parse failed for %s", end_date, exc_info=True)
        else:
            logger.info("Pre-warm: no disk cache for %s (locked=%s), will fetch on first request", end_date, locked)


if not os.environ.get("_SST_PREWARM_STARTED"):
    os.environ["_SST_PREWARM_STARTED"] = "1"
    threading.Thread(target=_prewarm_cache, daemon=True).start()


if __name__ == "__main__":
    app.run(debug=True, port=8050)
