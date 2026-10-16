/*
 * GotOne Offshore SST: page helpers for /sst (window.SST).
 *
 * Dash serves every /assets/*.js on every page, so this stays inert until a
 * Leaflet map whose container id is "sst-map" is created (L.Map.addInitHook,
 * as in currents.js). This is not the client-side engine (docs/sst-review.md
 * E1, sprint 2): temperatures still come from the server (app.py
 * render_selection). What runs here, called from small clientside callbacks
 * in app.py:
 *
 *   classify()  a map tap -> a spot, The Dump, open water or a measure point.
 *               Spots are hit-tested in screen pixels (about 16 px for a
 *               finger, 9 px for a mouse), so a tap next to a spot reads the
 *               water there, and a tap inside The Dump reads that point.
 *   status()    the strip under the map (day shown, source, freshness) and
 *               the floating playbar's date, from the week's "meta" (any
 *               field may be missing: old cache entries have none).
 *   the first view: the fishing grounds (phones) or the whole area, then the
 *               reader's last view (localStorage, per browser).
 */
(function () {
    "use strict";
    if (typeof window.L === "undefined") { return; }

    var VIEW_KEY = "gotone-sst-view-v1";
    var AREA = [[38.8, -74.6], [43.8, -68.8]];            // the whole mapped area
    // Phones: the NY Bight to the canyons (about 39.0 to 41.4 N, 74.2 to 69.0 W).
    // A set view, not fitBounds: whole zoom levels only (fractional zooms showed
    // seams between the basemap tiles), and zoom 6 would fit but is too far out.
    var GROUNDS = { center: [40.15, -71.6], zoom: 7 };
    var HIT_TOUCH = 16, HIT_MOUSE = 9;                     // spot hit radius, px
    var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    var DAY_MS = 86400000;
    var S = { map: null, pointer: null, statusHtml: "", saveTimer: null };

    // The last press's pointer type: a finger gets a bigger spot target than a mouse.
    document.addEventListener("pointerdown", function (ev) { S.pointer = ev.pointerType || null; }, true);
    function coarse() {
        if (S.pointer) { return S.pointer !== "mouse"; }
        return !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
    }

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s).replace(/[&<>"]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
        });
    }
    function store(key, val) {
        try {
            if (val === undefined) { return window.localStorage.getItem(key); }
            window.localStorage.setItem(key, val);
        } catch (e) { /* storage blocked: the page works without it */ }
        return null;
    }

    // ------------------------------------------------------------ dates --
    function parseDay(s) {                               // "2026-10-12" -> local midnight
        var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || "");
        return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
    }
    function dayNum(d) { return Math.round(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / DAY_MS); }
    function fromNum(n) { var u = new Date(n * DAY_MS); return new Date(u.getUTCFullYear(), u.getUTCMonth(), u.getUTCDate()); }
    function fmtLong(d) { return DAYS[d.getDay()] + ", " + MONTHS[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear(); }
    function fmtShort(d) { return MONTHS[d.getMonth()] + " " + d.getDate(); }
    function etDay(when) {                               // the calendar day in New York
        try {
            return parseDay(new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York",
                year: "numeric", month: "2-digit", day: "2-digit" }).format(when));
        } catch (e) { return new Date(when.getFullYear(), when.getMonth(), when.getDate()); }
    }
    function parseStamp(s) {                             // ISO time; no zone given = UTC
        if (!s) { return null; }
        var t = String(s);
        if (/T\d{2}:\d{2}/.test(t) && !/(Z|[+-]\d{2}:?\d{2})$/.test(t)) { t += "Z"; }
        var ms = Date.parse(t);
        return isNaN(ms) ? null : new Date(ms);
    }
    function ago(stamp) {
        var mins = (Date.now() - stamp.getTime()) / 60000;
        if (mins < 0) { mins = 0; }
        if (mins < 50) { return mins < 2 ? "updated just now" : "updated " + Math.round(mins) + " min ago"; }
        var h = Math.round(mins / 60);
        if (h < 36) { return "updated " + h + (h === 1 ? " hour ago" : " hours ago"); }
        return "updated " + fmtShort(stamp);
    }

    // ---------------------------------------------------------- source ---
    function source(key) {
        var m = key.meta || {};
        var id = String(m.dataset_id || key.dataset_id || "").toLowerCase();
        var res = m.resolution_km || key.res_km;
        var name = m.source || (/oisst/.test(id) ? "OISST" : (/mur/.test(id) ? "MUR" : ""));
        if (!name && res) { name = res < 5 ? "MUR" : "OISST"; }
        var km = m.resolution_km || (name === "OISST" ? 25 : (name === "MUR" ? 1 : null));
        var short = name ? name + (km ? " " + km + " km" : "") : "Sea-surface temperature";
        var long = m.source_label || (name === "MUR" ? "MUR 1 km (NASA JPL, via NOAA CoastWatch)" :
                   name === "OISST" ? "OISST 25 km (NOAA NCEI)" : short);
        return { long: long, short: short, coarse: name === "OISST" || (km && km >= 5) };
    }

    // ---------------------------------------------------- status strip ---
    // Line 1: the day on the map. Line 2: source, which week, freshness.
    // Fixed height (assets/sst.css) so the map never resizes as text changes.
    function status(key, idx) {
        var el = $("sst-status-line"), pb = $("sst-pb-date");
        if (!el) { return; }
        var dates = (key && key.dates) || [];
        if (!dates.length) { return; }
        var meta = key.meta || {};
        var i = Math.max(0, Math.min(idx == null ? dates.length - 1 : idx, dates.length - 1));
        var day = parseDay(dates[i]);
        if (!day) { return; }

        var l1 = ['<b class="sst-st-date">' + esc(fmtLong(day)) + "</b>",
                  '<span class="sst-st-sub">day ' + (i + 1) + " of " + dates.length + "</span>"];
        // Missing days inside the served range (normally none)
        var first = parseDay(dates[0]), last = parseDay(dates[dates.length - 1]);
        if (first && last) {
            var have = {}, gaps = [];
            dates.forEach(function (s) { var d = parseDay(s); if (d) { have[dayNum(d)] = 1; } });
            for (var n = dayNum(first); n <= dayNum(last); n++) {
                if (!have[n]) { gaps.push(fmtShort(fromNum(n))); }
            }
            if (gaps.length) { l1.push('<span class="sst-st-warn">no data for ' + esc(gaps.join(", ")) + "</span>"); }
        }
        // Preliminary: MUR's final version comes about 4 days after the day itself.
        var built = parseStamp(meta.built_at);
        var ref = built ? etDay(built) : etDay(new Date());
        if (ref && dayNum(ref) - dayNum(day) < 4) {
            l1.push('<span class="sst-st-prelim" title="MUR revises its newest days for about four days">preliminary</span>');
        }

        var src = source(key), l2 = [];
        l2.push('<span class="sst-st-src"><span class="src-long">' + esc(src.long) +
                '</span><span class="src-short">' + esc(src.short) + "</span></span>");
        if (src.coarse) { l2.push('<span class="sst-st-warn">broad pattern only</span>'); }
        var req = parseDay(meta.requested_end), served = parseDay(meta.served_end || dates[dates.length - 1]);
        if (req && served && dayNum(req) !== dayNum(served)) {
            l2.push('<span class="sst-st-warn">' + esc(fmtShort(first) + " to " + fmtShort(served)) +
                    ", the closest week to " + esc(fmtShort(req)) + "</span>");
        }
        if (built) { l2.push("<span>" + esc(ago(built)) + "</span>"); }
        var latest = parseDay(meta.latest_available);
        if (latest) {
            var newer = served && dayNum(latest) > dayNum(served);
            l2.push('<span class="' + (newer ? "sst-st-new" : "") + '">newest available ' +
                    esc(fmtShort(latest)) + "</span>");
        }

        var dot = '<span class="sst-st-dot"> · </span>';
        var html = '<div class="sst-st-l1">' + l1.join(dot) + '</div><div class="sst-st-l2">' +
                   l2.join(dot) + "</div>";
        if (html !== S.statusHtml || !el.innerHTML) {
            S.statusHtml = html;
            el.innerHTML = html;
        }
        if (pb) { pb.textContent = DAYS[day.getDay()] + ", " + fmtShort(day); }
    }

    // ------------------------------------------------------- tap -> what --
    function inDump(d, lat, lng) {
        return d && lat >= d.s && lat <= d.n && lng >= d.w && lng <= d.e;
    }
    function nearestSpot(click, pois, selected) {
        var map = S.map, sel = selected ? {} : null, best = null;
        if (selected) { selected.forEach(function (n) { sel[n] = 1; }); }
        var r = coarse() ? HIT_TOUCH : HIT_MOUSE, bestD = r + 0.001;
        var cp = click.containerPoint;
        var pts = (pois && pois.points) || [];
        for (var k = 0; k < pts.length; k++) {
            var p = pts[k];
            if (sel && !sel[p[0]]) { continue; }
            var dist;
            if (map && cp) {
                var q = map.latLngToContainerPoint([p[1], p[2]]);
                dist = Math.hypot(q.x - cp.x, q.y - cp.y);
            } else {                                     // no map handle: about 7 px at zoom 7
                dist = Math.hypot(p[1] - click.latlng.lat, p[2] - click.latlng.lng) / 0.01;
            }
            if (dist < bestD) { bestD = dist; best = p; }
        }
        return best;
    }

    // Returns [click-pos, measure-state]; null means "no change".
    function classify(click, pois, selected, measure) {
        if (!click || !click.latlng) { return [null, null]; }
        var lat = click.latlng.lat, lng = click.latlng.lng;
        var spot = nearestSpot(click, pois, selected);
        var mode = (measure && measure.mode) || "off";
        if (mode === "a" || mode === "b") {
            var pt = spot ? { lat: spot[1], lng: spot[2], label: spot[0] } : { lat: lat, lng: lng, label: null };
            if (mode === "a") { return [null, { mode: "b", a: pt, b: null }]; }
            return [null, { mode: "done", a: measure.a, b: pt }];
        }
        if (spot) { return [{ kind: "poi", name: spot[0], lat: spot[1], lng: spot[2] }, null]; }
        var d = pois && pois.dump;
        var dumpOn = d && (!selected || selected.indexOf(d.name) >= 0);
        if (dumpOn && inDump(d, lat, lng)) { return [{ kind: "dump", name: d.name, lat: lat, lng: lng }, null]; }
        return [{ kind: "reading", lat: lat, lng: lng }, null];
    }

    // ------------------------------------------------------- first view --
    function firstView(map) {
        var saved = null;
        try { saved = JSON.parse(store(VIEW_KEY) || "null"); } catch (e) { saved = null; }
        map.invalidateSize({ pan: false });
        if (saved && isFinite(saved.lat) && isFinite(saved.lng) && isFinite(saved.z) &&
            saved.lat > 36 && saved.lat < 46 && saved.lng > -78 && saved.lng < -65) {
            map.setView([saved.lat, saved.lng], saved.z, { animate: false });
        } else {
            if (map.getSize().x < 600) {
                map.setView(GROUNDS.center, GROUNDS.zoom, { animate: false });
            } else {
                map.fitBounds(AREA, { animate: false, padding: [4, 4] });
            }
        }
        map.on("moveend", function () {
            clearTimeout(S.saveTimer);
            S.saveTimer = setTimeout(function () {
                if (S.map !== map) { return; }
                var c = map.getCenter();
                store(VIEW_KEY, JSON.stringify({ lat: +c.lat.toFixed(4), lng: +c.lng.toFixed(4), z: map.getZoom() }));
            }, 400);
        });
    }

    L.Map.addInitHook(function () {
        var c = this.getContainer && this.getContainer();
        if (!c || c.id !== "sst-map") { return; }
        var map = this;
        S.map = map;
        map.whenReady(function () { setTimeout(function () { if (S.map === map) { firstView(map); } }, 0); });
        map.on("unload", function () { if (S.map === map) { S.map = null; S.statusHtml = ""; } });
        // The page's flex layout and the drawer can resize the map without a window resize.
        if (window.ResizeObserver) {
            var ro = new ResizeObserver(function () { if (S.map === map) { map.invalidateSize({ pan: false }); } });
            ro.observe(c);
            map.on("unload", function () { ro.disconnect(); });
        }
    });

    window.SST = {
        classify: classify,
        status: status,
        _state: S
    };
})();
