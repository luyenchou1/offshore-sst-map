/*
 * GotOne Offshore SST: page helpers for /sst (window.SST).
 *
 * Dash serves every /assets/*.js on every page, so this stays inert until a
 * Leaflet map whose container id is "sst-map" is created (L.Map.addInitHook,
 * as in currents.js).
 *
 * Setup (relaunch, Oct 16 2026): window.SST is defined at once, whatever else
 * is on the page (none of classify / status / marks / fetchError needs Leaflet
 * to exist yet), and only once (a second copy of this file keeps the first
 * one's state and hooks). The Leaflet hooks go in as soon as window.L exists
 * (dash-leaflet's bundle), retried every 50 ms until it does; a map or a
 * fishing-activity layer made before that is adopted, not missed. Page
 * changes: react-leaflet removes the map ("unload"), the next one is hooked
 * as it's made. (The "window.SST missing after a boot" bug was the server:
 * Dash sent pages with no /assets at all; app._finish_dash_setup.)
 *
 * The SST itself is drawn by assets/sstengine.js (window.SSTEngine, sprint 2);
 * attachMap() hands it the map. What runs here, called from small clientside
 * callbacks in app.py:
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
 *   gfwRetry()  fishing-activity tiles the server was too busy for (503) are
 *               asked for again after a short pause.
 *   selection() the tapped reading, spot or point in The Dump: a marker and
 *               a card with that day's temperature and the week's, read from
 *               SSTEngine's grid in the browser (no server trip on a tap or a
 *               day change: review R1). Same markup and classes as the card
 *               the server used to build (map/readout.selection_tip).
 */
(function () {
    "use strict";
    if (window.SST && window.SST._setup) { return; }       // already set up by an earlier copy

    var VIEW_KEY = "gotone-sst-view-v1";
    var AREA = [[37.8, -76.0], [44.0, -64.8]];            // the whole mapped area (sprint 2 coverage box)
    // Small maps (phones, landscape phones, short windows), where the whole
    // area only fits at zoom 6 or less: the NY Bight to the canyons (about 39.0
    // to 41.4 N, 74.2 to 69.0 W) at zoom 7. Whole zoom levels only: fractional
    // zooms showed seams between the basemap tiles.
    var GROUNDS = { center: [40.15, -71.6], zoom: 7 };
    var HIT_TOUCH = 16, HIT_MOUSE = 9;                     // spot hit radius, px
    var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    var DAY_MS = 86400000;
    var S = { map: null, pointer: null, statusHtml: "", saveTimer: null, hooked: false, waits: 0 };

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
    function ago(stamp, short) {
        var mins = (Date.now() - stamp.getTime()) / 60000;
        if (mins < 0) { mins = 0; }
        if (mins < 50) { return mins < 2 ? "updated just now" : "updated " + Math.round(mins) + " min ago"; }
        var h = Math.round(mins / 60);
        if (h < 36) { return "updated " + h + (short ? " h ago" : (h === 1 ? " hour ago" : " hours ago")); }
        return "updated " + fmtShort(stamp);
    }
    // A segment with a long form (wide strip) and a short one (phones, assets/sst.css).
    function dual(long, short, cls) {
        if (long === short) { return '<span class="' + cls + '">' + esc(long) + "</span>"; }
        return '<span class="' + cls + '"><span class="st-long">' + esc(long) + '</span><span class="st-short">' +
               esc(short) + "</span></span>";
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
        var sig = key ? (key.data_key || "") + "|" + (key.dates || []).join(",") : "";
        if (sig !== S.lastSig) { S.error = null; }       // a new week arrived
        S.lastSig = sig;
        S.lastKey = key;
        S.lastIdx = idx;
        var dates = (key && key.dates) || [];
        if (!dates.length) { return; }
        var meta = key.meta || {};
        var i = Math.max(0, Math.min(idx == null ? dates.length - 1 : idx, dates.length - 1));
        var day = parseDay(dates[i]);
        if (!day) { return; }

        var l1 = ['<b class="sst-st-date">' + esc(fmtLong(day)) + "</b>",
                  '<span class="sst-st-sub">day ' + (i + 1) + " of " + dates.length + "</span>"];
        // Missing days: meta.missing_days (lane D: the asked-for days NOAA didn't send), else
        // gaps inside the served range (normally none)
        var first = parseDay(dates[0]), last = parseDay(dates[dates.length - 1]);
        var gaps = [];
        if (Array.isArray(meta.missing_days)) {
            meta.missing_days.forEach(function (s) { var d = parseDay(s); if (d) { gaps.push(fmtShort(d)); } });
        } else if (first && last) {
            var have = {};
            dates.forEach(function (s) { var d = parseDay(s); if (d) { have[dayNum(d)] = 1; } });
            for (var n = dayNum(first); n <= dayNum(last); n++) {
                if (!have[n]) { gaps.push(fmtShort(fromNum(n))); }
            }
        }
        if (gaps.length) {
            l1.push('<span class="sst-st-warn">no data for ' + esc(gaps.join(", ")) +
                    (meta.complete === false ? " yet" : "") + "</span>");
        }
        // Preliminary (D4): MUR's final version comes about 4 days after the day itself.
        // meta.preliminary_days lists the days that were that new when fetched; without it,
        // count from built_at (or today).
        var built = parseStamp(meta.built_at), prelim;
        if (Array.isArray(meta.preliminary_days)) {
            prelim = meta.preliminary_days.indexOf(dates[i]) >= 0;
        } else {
            var ref = built ? etDay(built) : etDay(new Date());
            prelim = !!ref && dayNum(ref) - dayNum(day) < 4;
        }
        if (prelim) {
            l1.push('<span class="sst-st-prelim" title="MUR revises its newest days for about four days">preliminary</span>');
        }

        // Line 2, most important first (phones show what fits, in shorter words):
        // source, freshness, the newest day NOAA has, then which week this is.
        var src = source(key), l2 = [];
        // (On phones the legend carries the OISST "broad pattern only" warning.)
        l2.push('<span class="sst-st-src"><span class="st-long">' + esc(src.long) +
                (src.coarse ? '<span class="sst-st-dot"> \u00b7 </span><span class="sst-st-warn">broad pattern only</span>' : "") +
                '</span><span class="st-short">' + esc(src.short) + "</span></span>");
        if (built) { l2.push(dual(ago(built), ago(built, true), "")); }
        var latest = parseDay(meta.latest_available);
        var req = parseDay(meta.requested_end), served = parseDay(meta.served_end || dates[dates.length - 1]);
        if (latest) {
            var newer = served && dayNum(latest) > dayNum(served);
            l2.push(dual("newest available " + fmtShort(latest), "newest " + fmtShort(latest),
                         newer ? "sst-st-new" : ""));
        }
        // Which week this is (lane D's meta.week_match; without it, requested vs served end)
        var match = meta.week_match || (req && served && dayNum(req) !== dayNum(served) ? "closest_saved" : "exact");
        if (match === "closest_saved" && req && served && first) {
            var sameMonth = first.getMonth() === served.getMonth();
            var range = fmtShort(first) + " to " + (sameMonth ? served.getDate() : fmtShort(served));
            l2.push(dual(range + ", the closest saved week to " + fmtShort(req),
                         "closest week to " + fmtShort(req), "sst-st-warn"));
        } else if (match === "older_copy") {
            l2.push(dual("saved copy: NOAA didn't send a newer one", "saved copy", "sst-st-warn"));
        }
        if (meta.queued) { l2.push(dual("full week due within a day", "full week due", "")); }

        if (S.error) {                                   // the last Load week failed
            l2.unshift('<span class="sst-st-warn">Couldn\'t load that week: ' + esc(S.error) + "</span>");
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

    // Day-slider marks: weekday over day of the month ("Tu" / "6"; assets/sst.css
    // shows the two lines). Fewer than 7 days is fine.
    function marks(dates) {
        var out = {}, wd = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
        (dates || []).forEach(function (s, k) {
            var d = parseDay(s);
            out[k] = d ? wd[d.getDay()] + "\n" + d.getDate() : String(k + 1);
        });
        return out;
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

    // Returns [click-pos, measure-state]; null means "no change", {clear: true}
    // drops the selection (a second tap on the open reading or spot closes it).
    function classify(click, pois, selected, measure, current) {
        if (!click || !click.latlng) { return [null, null]; }
        var lat = click.latlng.lat, lng = click.latlng.lng;
        var spot = nearestSpot(click, pois, selected);
        var mode = (measure && measure.mode) || "off";
        if (mode !== "a" && mode !== "b" && current && S.map && click.containerPoint) {
            var q = S.map.latLngToContainerPoint([current.lat, current.lng]);
            var r = coarse() ? HIT_TOUCH : HIT_MOUSE;
            if (Math.hypot(q.x - click.containerPoint.x, q.y - click.containerPoint.y) <= r) {
                return [{ clear: true }, null];
            }
        }
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
            saved.lat > 36 && saved.lat < 46 && saved.lng > -78 && saved.lng < -63) {
            map.setView([saved.lat, saved.lng], saved.z, { animate: false });
        } else {
            if (map.getBoundsZoom(AREA, false, window.L.point(8, 8)) >= GROUNDS.zoom) {
                map.fitBounds(AREA, { animate: false, padding: [4, 4] });
            } else {
                map.setView(GROUNDS.center, GROUNDS.zoom, { animate: false });
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

    // ---------------------------------------------------- Leaflet hooks --
    // Every Leaflet map goes through here once (as it's made, or adopted later); only "sst-map" is ours.
    function attachMap(map) {
        if (map._gotoneSST) { return; }
        var c = map.getContainer && map.getContainer();
        if (!c || !c.id) { return; }                         // no id yet: look again on its next event
        map._gotoneSST = c.id === "sst-map" ? "sst" : "other";
        if (map._gotoneSST !== "sst") { return; }
        S.map = map;
        map.whenReady(function () { setTimeout(function () { if (S.map === map) { firstView(map); } }, 0); });
        map.on("unload", function () {
            if (S.map === map) { S.map = null; S.statusHtml = ""; SEL.marker = null; SEL.where = null; }
        });
        try { if (window.SSTEngine) { window.SSTEngine.attach(map); } }  // draws the SST on this map
        catch (e) { if (window.console) { console.error("SSTEngine.attach", e); } }   // never let it break the map
        // The page's flex layout and the drawer can resize the map without a window resize.
        if (window.ResizeObserver) {
            var ro = new ResizeObserver(function () { if (S.map === map) { map.invalidateSize({ pan: false }); } });
            ro.observe(c);
            map.on("unload", function () { ro.disconnect(); });
        }
        if (map.eachLayer) { map.eachLayer(hookTiles); }     // a fishing-activity layer made before we were
    }

    // Fishing-activity tiles the server was too busy for (HTTP 503: it serves at most a few GFW calls at once
    // so a zoom's 30-50 tiles can't take every request thread; incident 2026-10-16) are asked for again after a
    // short, jittered pause, up to 4 times. A GFW failure comes back as an empty 200 tile and isn't retried.
    var GFW_RETRIES = 4;
    function gfwRetry(e) {
        var tile = e && e.tile;
        if (!tile || !tile.src || tile.src.indexOf("/api/gfw/") < 0) { return; }
        var n = (tile._gfwTries || 0) + 1;
        if (n > GFW_RETRIES) { return; }
        tile._gfwTries = n;
        var base = tile.src.replace(/([?&])retry=\d+(&|$)/, function (m, a, b) { return b ? a : ""; });
        setTimeout(function () {
            if (!tile.isConnected) { return; }                  // panned or zoomed away meanwhile
            tile.src = base + (base.indexOf("?") >= 0 ? "&" : "?") + "retry=" + n;
        }, 500 * n + Math.random() * 700);
    }
    function hookTiles(layer) {
        var L = window.L;
        if (!layer || layer._gotoneGfw || !(L.TileLayer && layer instanceof L.TileLayer)) { return; }
        if (typeof layer._url !== "string" || layer._url.indexOf("/api/gfw/") < 0) { return; }
        layer._gotoneGfw = true;
        layer.on("tileerror", gfwRetry);
    }

    // Once window.L (dash-leaflet's bundle) exists. Assets load after dash-leaflet and before the Dash renderer,
    // so normally that's at once and no map exists yet. If not, a map already on the page is adopted the next time
    // it fires an event: while it's still being set up, or at the reader's first pan, zoom or tap (Leaflet has no
    // way to find a map from its container).
    function install() {
        var L = window.L;
        if (!L || !L.Map || !L.Map.addInitHook || !L.TileLayer || !L.TileLayer.addInitHook) { return false; }
        if (L._gotoneSST) { S.hooked = true; return true; }
        L._gotoneSST = true;
        L.Map.addInitHook(function () { attachMap(this); });
        L.TileLayer.addInitHook(function () { hookTiles(this); });
        if (S.waits > 0 || document.querySelector(".leaflet-container")) {
            var fire = L.Map.prototype.fire;
            L.Map.prototype.fire = function () {
                if (!this._gotoneSST) { attachMap(this); }
                return fire.apply(this, arguments);
            };
        }
        S.hooked = true;
        return true;
    }
    (function retry() {
        if (install()) { return; }
        S.waits++;
        setTimeout(retry, S.waits < 600 ? 50 : 1000);    // 30 s at 50 ms, then once a second
    })();

    // ------------------------------------------------- the selection card --
    // click-pos -> a marker and a permanent tooltip in the click pane (z 500), the
    // card's temperature from SSTEngine for the day on the map, refreshed when the
    // day changes or the week's grid arrives. Markup = map/readout.selection_tip.
    var SEL = { sel: null, key: null, idx: null, marker: null, where: null, hooked: false };
    var WDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    function fmtTemp(t) {
        if (t === null || t === undefined) { return "no data"; }
        return Math.abs(t - Math.round(t)) < 0.05 ? Math.round(t) + " \u00b0F" : t.toFixed(1) + " \u00b0F";
    }
    function fmtPos(lat, lng) {
        return lat.toFixed(3) + "\u00b0N, " + Math.abs(lng).toFixed(3) + "\u00b0" + (lng < 0 ? "W" : "E");
    }
    function fmtDay(iso) {
        var d = parseDay(iso);
        return d ? WDAYS[d.getDay()] + ", " + MONTHS[d.getMonth()] + " " + d.getDate() : "";
    }
    function cardHtml(sel, temps, dates, idx) {
        var kind = sel.kind || "reading", t = temps && idx >= 0 && idx < temps.length ? temps[idx] : null;
        var out = '<div class="sst-tip' + (kind === "poi" ? " sst-tip-poi" : "") + '">';
        if (kind === "poi" || kind === "dump") { out += '<div class="sst-tip-name">' + esc(sel.name || "") + "</div>"; }
        if (temps === undefined) { out += '<div class="sst-tip-none">Loading the temperature\u2026</div>'; }
        else if (t === null) {                       // the coast fill (sstengine.js) colors water MUR doesn't cover
            var E = window.SSTEngine, shore = E && E.nearShore && E.nearShore(sel.lat, sel.lng);
            out += '<div class="sst-tip-none">' + (shore ? "No satellite reading this close to shore" : "No data here") + "</div>";
        }
        else { out += '<div class="sst-tip-temp">' + fmtTemp(t) + "</div>"; }
        if (idx >= 0 && idx < dates.length) { out += '<div class="sst-tip-date">' + esc(fmtDay(dates[idx])) + "</div>"; }
        out += '<div class="sst-tip-pos">' + fmtPos(sel.lat, sel.lng) + "</div>";
        if (temps && temps.some(function (x) { return x !== null; })) {
            out += '<div class="sst-week" title="This spot each day of the week, \u00b0F">';
            temps.forEach(function (x, k) {
                var day = dates[k] ? String(+dates[k].slice(8, 10)) : "";
                out += '<div class="sst-wk-cell' + (k === idx ? " cur" : "") + '"><div class="sst-wk-t">' +
                       (x === null ? "" : Math.round(x)) + '</div><div class="sst-wk-d">' + day + "</div></div>";
            });
            out += "</div>";
        }
        return out + "</div>";
    }
    function drawSelection() {
        var map = S.map, L = window.L, E = window.SSTEngine, sel = SEL.sel;
        if (!map || !L) { return; }
        var where = sel ? [sel.kind || "reading", sel.lat, sel.lng, sel.name || ""].join("|") : null;
        if (SEL.marker && (where !== SEL.where || !map.hasLayer(SEL.marker))) {
            // Leaflet fades a removed tooltip out over 200 ms; the next card is drawn at once, so drop the old one now
            var tt = SEL.marker.getTooltip && SEL.marker.getTooltip(), el = tt && tt.getElement && tt.getElement();
            try { map.removeLayer(SEL.marker); } catch (e) { /* gone */ }
            if (tt) { clearTimeout(tt._removeTimeout); }
            if (el && el.parentNode) { el.parentNode.removeChild(el); }
            SEL.marker = null;
        }
        SEL.where = where;
        if (!sel) { return; }
        var st = E ? E.state() : null, ready = !!(st && st.ready);
        var dates = (ready && st.dates.length ? st.dates : (SEL.key && SEL.key.dates)) || [];
        var idx = SEL.idx !== null && SEL.idx !== undefined ? SEL.idx : (ready ? st.day : dates.length - 1);
        idx = Math.max(0, Math.min(idx, dates.length - 1));
        var html = cardHtml(sel, ready ? E.weekValuesAt(sel.lat, sel.lng) : undefined, dates, idx);
        if (!SEL.marker) {
            var poi = sel.kind === "poi";
            // A tap on the open card's own marker closes it (taps near it go through SST.classify, which does the
            // same). Handled here because a tap on exactly the same spot gives Dash the same clickData, which
            // doesn't fire the tap callback again.
            SEL.marker = L.circleMarker([sel.lat, sel.lng], {
                pane: map.getPane("click-pane") ? "click-pane" : "markerPane", radius: poi ? 9 : 6,
                color: poi ? "#0183fe" : "#e11d48", weight: 2.5, fillOpacity: poi ? 0 : 0.3,
                interactive: true, bubblingMouseEvents: false
            }).addTo(map);
            SEL.marker.on("click", function (ev) {
                if (ev && ev.originalEvent) { L.DomEvent.stop(ev.originalEvent); }
                try { window.dash_clientside.set_props("click-pos", { data: null }); } catch (e) { /* no Dash */ }
            });
            SEL.marker.bindTooltip(html, { permanent: true, direction: "top", offset: [0, -10], pane: "tooltipPane",
                                           className: "sst-tooltip", interactive: false }).openTooltip();
        } else {
            SEL.marker.setTooltipContent(html);
        }
    }
    function selection(sel, key, idx) {
        SEL.sel = sel && isFinite(sel.lat) && isFinite(sel.lng) ? sel : null;
        if (key) { SEL.key = key; }
        SEL.idx = idx === undefined ? SEL.idx : idx;
        var E = window.SSTEngine;
        if (E && !SEL.hooked) {                          // the grid arrived or the day changed: refresh the card
            SEL.hooked = true;
            E.on("ready", drawSelection);
            E.on("day", function (ev) { SEL.idx = ev.day; drawSelection(); });
        }
        drawSelection();
    }

    // A failed Load week (the sidebar's red message, which phones don't see:
    // the drawer closes). The map still shows the last week, so the strip keeps
    // its date and leads line 2 with the error until a week loads.
    function fetchError(msg) {
        var el = $("sst-status-line");
        S.error = msg ? String(msg).replace(/^Error:\s*/, "") : null;
        if (S.lastKey && S.lastKey.dates && S.lastKey.dates.length) {
            S.statusHtml = "";
            status(S.lastKey, S.lastIdx);
        } else if (el && S.error) {
            el.innerHTML = '<div class="sst-st-l1 sst-st-warn">Couldn\'t load the week</div>' +
                           '<div class="sst-st-l2">' + esc(S.error) + "</div>";
        }
    }

    window.SST = {
        classify: classify,
        status: status,
        marks: marks,
        fetchError: fetchError,
        selection: selection,
        ready: function () { return S.hooked; },        // Leaflet hooks installed
        _setup: true,
        _state: S
    };
})();
