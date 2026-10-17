/*
 * TEST ONLY: a stand-in for lane E's window.SSTEngine (assets/sstengine.js), so the color panel, the legend,
 * the depth lines and the spot labels (assets/sstlook.js) can be tried before the real engine is merged.
 * Never served by the app (it lives in tools/, not assets/). tools/smoke_sst.mjs injects it into the page
 * only when the page has no SSTEngine of its own; by hand: paste it into the console on /sst.
 *
 * It implements the API agreed in the sprint 2 brief, roughly:
 *   attach(map), setWeek(payload), setDay(i), setPalette(name), setRange({mode, min, max}), fitToView(),
 *   setOptions({stepped, isotherms, breaks, haze, opacity}), valueAt(lat, lon), weekValuesAt(lat, lon),
 *   histogram(), on("ready" | "change" | "day", fn)
 * but its temperatures come from decoding today's server PNG frames back through the old 11-color palette
 * (whole degrees, the overlay's own range), not from lane E's 0.1 F grid; the "haze" is a made-up patch
 * (there is no analysis_error yet); breaks are a plain gradient threshold and isotherms are pixel edges. It
 * has no getRange(): the resolved range goes out in the "change" / "ready" events ({range}), which tests the
 * panel's fallback path. It watches the sst-store and the day slider itself (the real engine is fed by
 * lane E's callbacks).
 */
(function () {
    "use strict";
    if (window.__sstStub) { return; }
    window.__sstStub = true;
    var OLD = ["#2c7fb8", "#41b6c4", "#7fcdbb", "#c7e9b4", "#ffffcc", "#fde68a", "#fca35d", "#fb6a4a",
               "#ef3b2c", "#cb181d", "#99000d"];
    var STOPS = ["#3d358b", "#476ee6", "#3ba0fd", "#1bd0d5", "#2aefa1", "#75fe5c", "#b4f836",
                 "#e3db38", "#fcb136", "#f9751d", "#e14109", "#b71d02", "#7a0403"];
    function hex(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
    function lerpStops(st, t) {
        t = Math.max(0, Math.min(1, t)) * (st.length - 1);
        var k = Math.min(st.length - 2, Math.floor(t)), f = t - k;
        return [0, 1, 2].map(function (j) { return st[k][j] + (st[k + 1][j] - st[k][j]) * f; });
    }
    var OLDRGB = OLD.map(hex), SPEC = STOPS.map(hex);
    var OLDLUT = [];
    for (var k = 0; k < 256; k++) { OLDLUT.push(lerpStops(OLDRGB, k / 255)); }

    var E = { __stub: true }, H = {}, S = {
        map: null, week: null, days: [], w: 0, h: 0, bounds: null, day: 0, range: { mode: "auto" }, resolved: null,
        opts: { stepped: false, isotherms: false, breaks: false, haze: false, opacity: 1 }, layer: null, canvas: null,
        grad: {}, hazeMask: null, hist: null, key: null
    };
    function emit(name, arg) { (H[name] || []).forEach(function (fn) { try { fn(arg); } catch (e) { console.warn(e); } }); }
    function mercY(lat) { var s = Math.sin(lat * Math.PI / 180); return Math.log((1 + s) / (1 - s)) / 2; }

    function decodeFrame(url, vmin, vmax) {
        return new Promise(function (resolve) {
            var img = new Image();
            img.onload = function () {
                var c = document.createElement("canvas");
                c.width = img.naturalWidth; c.height = img.naturalHeight;
                var g = c.getContext("2d");
                g.drawImage(img, 0, 0);
                var d = g.getImageData(0, 0, c.width, c.height).data, n = c.width * c.height;
                var out = new Float32Array(n), cache = {};
                for (var i = 0; i < n; i++) {
                    if (d[4 * i + 3] < 128) { out[i] = NaN; continue; }
                    var key = (d[4 * i] << 16) | (d[4 * i + 1] << 8) | d[4 * i + 2], v = cache[key];
                    if (v === undefined) {
                        var best = 0, bd = 1e9;
                        for (var j = 0; j < 256; j++) {
                            var L = OLDLUT[j], dd = Math.abs(L[0] - d[4 * i]) + Math.abs(L[1] - d[4 * i + 1]) + Math.abs(L[2] - d[4 * i + 2]);
                            if (dd < bd) { bd = dd; best = j; }
                        }
                        v = cache[key] = Math.round((vmin + (best + 0.5) / 256 * (vmax - vmin)) * 10) / 10;
                    }
                    out[i] = v;
                }
                resolve({ w: c.width, h: c.height, t: out });
            };
            img.onerror = function () { resolve(null); };
            img.src = url;
        });
    }

    function pctl(vals, p) { return vals[Math.min(vals.length - 1, Math.max(0, Math.floor(p * (vals.length - 1))))]; }
    function autoRange() {
        var h = S.hist;
        if (!h) { return { min: 50, max: 70 }; }
        var tot = h.bins.reduce(function (a, b) { return a + b; }, 0), cum = 0, lo = null, hi = null;
        for (var i = 0; i < h.bins.length; i++) {
            cum += h.bins[i];
            var x = h.min + (i + 1) * (h.max - h.min) / h.bins.length;
            if (lo === null && cum >= 0.05 * tot) { lo = x - (h.max - h.min) / h.bins.length; }
            if (hi === null && cum >= 0.95 * tot) { hi = x; }
        }
        lo = Math.floor(lo); hi = Math.ceil(hi);
        if (hi - lo < 8) { var mid = (lo + hi) / 2; lo = Math.floor(mid - 4); hi = lo + 8; }
        return { min: lo, max: hi };
    }
    function resolve() {
        var r = S.range.mode === "fixed" ? { min: S.range.min, max: S.range.max } : autoRange();
        S.resolved = { mode: S.range.mode, min: r.min, max: r.max };
        return S.resolved;
    }

    var CanvasOverlay = null;
    function ensureLayer() {
        var L = window.L;
        if (!CanvasOverlay) {
            CanvasOverlay = L.ImageOverlay.extend({
                _initImage: function () {
                    var c = this._image = S.canvas;
                    L.DomUtil.addClass(c, "leaflet-image-layer");
                    L.DomUtil.addClass(c, "sst-stub-canvas");
                    if (this._zoomAnimated) { L.DomUtil.addClass(c, "leaflet-zoom-animated"); }
                    c.onselectstart = L.Util.falseFn;
                    c.onmousemove = L.Util.falseFn;
                    this._updateOpacity();
                    this._updateZIndex();
                }
            });
        }
        if (S.layer) { S.map.removeLayer(S.layer); }
        S.canvas = document.createElement("canvas");
        S.canvas.width = S.w; S.canvas.height = S.h;
        S.canvas.style.imageRendering = "auto";
        var b = S.bounds;
        S.layer = new CanvasOverlay("", [[b[0][0], b[0][1]], [b[1][0], b[1][1]]],
            { pane: S.map.getPane("sst-pane") ? "sst-pane" : "overlayPane", interactive: false, opacity: S.opts.opacity });
        S.layer.addTo(S.map);
    }

    function gradient(i) {
        if (S.grad[i]) { return S.grad[i]; }
        var t = S.days[i], w = S.w, h = S.h, g = new Float32Array(w * h);
        for (var y = 1; y < h - 1; y++) {
            for (var x = 1; x < w - 1; x++) {
                var p = y * w + x, dx = (t[p + 1] - t[p - 1]) / 2, dy = (t[p + w] - t[p - w]) / 4;  // rows ~0.5 km
                g[p] = Math.hypot(dx, dy);
            }
        }
        return (S.grad[i] = g);
    }
    function hazeMask() {
        if (S.hazeMask) { return S.hazeMask; }
        var w = S.w, h = S.h, m = new Uint8Array(w * h);
        for (var y = 0; y < h; y++) {
            for (var x = 0; x < w; x++) {
                var u = x / w, v = y / h;
                var f = Math.sin(u * 9.0 + 1.3) * Math.cos(v * 7.0 - 0.4) + 0.5 * Math.sin(u * 23 + v * 17);
                m[y * w + x] = f > 0.85 ? 1 : 0;
            }
        }
        return (S.hazeMask = m);
    }

    function draw() {
        if (!S.days.length || !S.canvas) { return; }
        var t0 = performance.now(), r = resolve(), span = Math.max(r.max - r.min, 0.1);
        var g = S.canvas.getContext("2d"), img = g.createImageData(S.w, S.h), d = img.data;
        var T = S.days[S.day], w = S.w, n = S.w * S.h, o = S.opts;
        var grad = o.breaks ? gradient(S.day) : null, haze = o.haze ? hazeMask() : null;
        var lut = [];
        for (var k = 0; k <= 1000; k++) {                 // 28 F + 0.1 F steps
            var f = 28 + k / 10;
            if (o.stepped) { f = Math.floor(f) + 0.5; }
            lut.push(lerpStops(SPEC, (f - r.min) / span));
        }
        for (var i = 0; i < n; i++) {
            var v = T[i];
            if (!(v === v)) { d[4 * i + 3] = 0; continue; }
            var c = lut[Math.max(0, Math.min(1000, Math.round((v - 28) * 10)))], R = c[0], G = c[1], B = c[2];
            if (o.isotherms && i % w < w - 1 && i + w < n) {
                var a = Math.floor(v), bR = T[i + 1], bD = T[i + w];
                if ((bR === bR && Math.floor(bR) !== a) || (bD === bD && Math.floor(bD) !== a)) {
                    var heavy = a % 2 === 0, s = heavy ? 0.45 : 0.7;
                    R *= s; G *= s; B *= s;
                }
            }
            if (grad && grad[i] > 0.27) {
                var aa = Math.min(1, (grad[i] - 0.27) / 0.27) * 0.62;
                R += (255 - R) * aa; G += (255 - G) * aa; B += (255 - B) * aa;
            }
            if (haze && haze[i]) { R += (255 - R) * 0.35; G += (255 - G) * 0.35; B += (255 - B) * 0.35; }
            d[4 * i] = R; d[4 * i + 1] = G; d[4 * i + 2] = B; d[4 * i + 3] = 255;
        }
        g.putImageData(img, 0, 0);
        E._lastDrawMs = performance.now() - t0;
    }

    function cell(lat, lon) {
        var b = S.bounds;
        if (!b || lat < b[0][0] || lat > b[1][0] || lon < b[0][1] || lon > b[1][1]) { return -1; }
        var yn = mercY(b[1][0]), ys = mercY(b[0][0]);
        var row = Math.floor((yn - mercY(lat)) / (yn - ys) * S.h), col = Math.floor((lon - b[0][1]) / (b[1][1] - b[0][1]) * S.w);
        if (row < 0 || row >= S.h || col < 0 || col >= S.w) { return -1; }
        return row * S.w + col;
    }

    E.attach = function (map) { S.map = map; };
    E.setWeek = function (payload) {
        if (!payload || !payload.frames || !payload.frames.length) { return; }
        S.week = payload;
        Promise.all(payload.frames.map(function (u) { return decodeFrame(u, payload.vmin, payload.vmax); })).then(function (fr) {
            if (S.week !== payload) { return; }
            fr = fr.filter(Boolean);
            if (!fr.length) { return; }
            S.w = fr[0].w; S.h = fr[0].h; S.days = fr.map(function (x) { return x.t; });
            S.bounds = payload.bounds; S.grad = {}; S.hazeMask = null;
            var lo = 1e9, hi = -1e9;
            S.days.forEach(function (t) { for (var i = 0; i < t.length; i++) { var v = t[i]; if (v === v) { if (v < lo) { lo = v; } if (v > hi) { hi = v; } } } });
            lo = Math.floor(lo); hi = Math.ceil(hi) + 0.5;
            var nb = Math.max(1, Math.round((hi - lo) / 0.5)), bins = new Array(nb).fill(0);
            S.days.forEach(function (t) { for (var i = 0; i < t.length; i++) { var v = t[i]; if (v === v) { bins[Math.min(nb - 1, Math.floor((v - lo) / 0.5))]++; } } });
            S.hist = { min: lo, max: lo + nb * 0.5, bins: bins };
            S.day = Math.min(S.day, S.days.length - 1);
            ensureLayer();
            draw();
            emit("ready", { range: resolve() });
        });
    };
    E.setDay = function (i) { if (i === S.day) { return; } S.day = Math.max(0, Math.min(S.days.length - 1, i | 0)); draw(); emit("day", S.day); };
    E.setPalette = function () { draw(); emit("change", { range: resolve() }); };
    E.setRange = function (r) { S.range = r && r.mode === "fixed" ? { mode: "fixed", min: +r.min, max: +r.max } : { mode: "auto" }; draw(); emit("change", { range: resolve() }); };
    E.setOptions = function (o) {
        Object.keys(o || {}).forEach(function (k) { if (o[k] !== undefined) { S.opts[k] = o[k]; } });
        if (S.layer && o && o.opacity !== undefined) { S.layer.setOpacity(o.opacity); }
        draw(); emit("change", { range: resolve() });
    };
    E.fitToView = function () {
        if (!S.days.length || !S.map) { return null; }
        var b = S.map.getBounds(), T = S.days[S.day], vals = [];
        var step = Math.max(1, Math.floor(S.w / 300));
        for (var y = 0; y < S.h; y += step) {
            for (var x = 0; x < S.w; x += step) {
                var v = T[y * S.w + x];
                if (!(v === v)) { continue; }
                var bb = S.bounds, yn = mercY(bb[1][0]), ys = mercY(bb[0][0]);
                var my = yn - (y + 0.5) / S.h * (yn - ys), lat = Math.atan(Math.sinh(my)) * 180 / Math.PI;
                var lon = bb[0][1] + (x + 0.5) / S.w * (bb[1][1] - bb[0][1]);
                if (b.contains([lat, lon])) { vals.push(v); }
            }
        }
        if (vals.length < 20) { return null; }
        vals.sort(function (a, c) { return a - c; });
        var r = { min: Math.floor(pctl(vals, 0.05)), max: Math.ceil(pctl(vals, 0.95)) };
        if (r.max - r.min < 2) { r.max = r.min + 2; }
        S.range = { mode: "fixed", min: r.min, max: r.max };
        draw();
        emit("change", { range: resolve() });
        return r;
    };
    E.valueAt = function (lat, lon) {
        var i = cell(lat, lon);
        if (i < 0 || !S.days.length) { return null; }
        var v = S.days[S.day][i];
        return v === v ? { f: v, day: S.day, estimate: !!(S.hazeMask && S.hazeMask[i]) } : null;
    };
    E.weekValuesAt = function (lat, lon) {
        var i = cell(lat, lon);
        return S.days.map(function (t) { var v = i < 0 ? NaN : t[i]; return v === v ? v : null; });
    };
    E.histogram = function () { return S.hist; };
    E.on = function (name, fn) { (H[name] = H[name] || []).push(fn); };
    E._state = S;

    function boot() {
        if (window.SSTEngine && !window.SSTEngine.__stub) { return; }       // the real engine is here
        var m = window.SST && window.SST._state && window.SST._state.map;
        var api = window.dash_component_api;
        if (!window.L || !m || !api) { setTimeout(boot, 100); return; }
        if (window.SSTEngine && !window.SSTEngine.__stub) { return; }
        var st = document.createElement("style");
        st.textContent = ".leaflet-sst-pane-pane img.leaflet-image-layer{visibility:hidden}";
        document.head.appendChild(st);
        window.SSTEngine = E;
        E.attach(m);
        setInterval(function () {
            var cur = window.SST && window.SST._state && window.SST._state.map;
            if (cur && cur !== S.map) { S.map = cur; S.layer = null; S.week = null; }
            var data = null, idx = null;
            try { data = api.getLayout("sst-store").props.data; idx = api.getLayout("frame-slider").props.value; } catch (e) { return; }
            if (data && data !== S.week && data.frames) { E.setWeek(data); }
            if (idx !== null && idx !== undefined && S.days.length) { E.setDay(idx); }
        }, 300);
    }
    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", boot); } else { boot(); }
})();
