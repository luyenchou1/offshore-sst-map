/*
 * GotOne Offshore SST: the client-side SST engine (window.SSTEngine), sprint 2 (docs/sst-review.md E1, R1, T4, T6,
 * R4, D3).
 *
 * The page gives it the week (SSTEngine.setWeek(sst-store data)); it downloads the week's grid once
 * (payload.grid.url = /api/sst/week/<end>.bin, data/sstgrid.py "GOSST1": 0.1 F codes per day and a land mask; the
 * browser inflates it, Content-Encoding: gzip) and draws it on a Leaflet canvas in the "sst-pane" (z 410: above the
 * basemap and the chart layers, below fishing activity, depth contours and spots).
 *
 * API (BRIEF.md; lane V's controls call it):
 *   attach(map)                 once the dash-leaflet map exists (assets/sst.js does it; idempotent)
 *   setWeek(payload)            the sst-store data: {dates, meta, bounds, vmin, vmax, data_key, grid: {url, ...}}
 *   setDay(i)                   0..n-1, crossfade (FADE_MS) unless the reader prefers reduced motion
 *   setPalette(name)            "spectrum" (default), "classic" (the old 11 colors), "cvd" (cividis, color-blind safe)
 *   setRange({mode, min, max})  "auto" = 5th to 95th percentile of all water in the coverage over the week, rounded
 *                               out to whole degrees, at least 8 F (stable while panning); "fixed" = min..max F
 *   fitToView()                 a fixed range from the water on screen (5th to 95th, whole degrees, >= 3 F): {min, max}
 *   setOptions({stepped, isotherms, breaks, opacity})      ("haze" is accepted and ignored: no cloud layer, below)
 *   valueAt(lat, lon)           {f, day, date, estimate} | null       (estimate is always null: no cloud signal)
 *   weekValuesAt(lat, lon)      [f or null per day]
 *   nearShore(lat, lon)         true where the coast fill colors water the week has no value for (the tap card says
 *                               "No satellite reading this close to shore"); never a temperature
 *   histogram()                 {min, max, step, bins, below, above, total}: the week's water, for the color bar
 *   on(evt, fn)                 "ready" (a week drawn), "day", "change" (palette, range, options, auto range),
 *                               also "error" (the week's grid didn't download); returns an off() function
 * Extras (lane V's color bar uses the first two): getRange() -> {mode, min, max} (the range drawn now; Auto's
 * numbers too), colorFor(f) -> [r, g, b] as drawn (palette, range, stepped; its toString() is "rgb(r,g,b)", so it
 * also works as a CSS color), state(), palettes, _stats (timings: decode, recolor, draw, analysis).
 * The engine attaches itself to the "sst-map" map (an L.Map init hook); attach(map) is there for callers too.
 *
 * Drawing: per day a canvas the size of the grid (one pixel per cell, colored through a 65536-entry lookup table:
 * palette x range x stepped, rebuilt in well under a millisecond), blended toward white where the temperature
 * changes fast (breaks). It is drawn in strips of
 * STRIP_ROWS rows, each placed by Web Mercator at its cell edges (Leaflet draws in Web Mercator; rows evenly spaced
 * in latitude stretched linearly put the SST up to 5.3 km north of true: CLAUDE.md, ImageOverlay Is Stretched).
 * Within a strip the error is under 0.1 px at zoom 12. Strip ends are snapped to whole device pixels so strips
 * neither overlap nor leave gaps. The canvas is an L.Canvas renderer: Leaflet moves and scales it while panning and
 * zooming, and it is redrawn on moveend.
 * Isotherms (every 1 F, heavier every 2 F, sparse labels) and the break gradient are computed once per day in a Web
 * Worker (marching squares on the field blurred 1.3 cells; gradient on the field blurred 1 cell, F per km, zeroed
 * within 3 cells of land), cached, and drawn as Path2D in world coordinates: a redraw at another zoom is one stroke
 * per level. Whole-degree weeks (saved before sprint 2) are blurred more so their terraces don't read as fronts.
 * The coast (Oct 17 2026, docs/coast-progress.md): once the coastline (assets/sstland.js, OpenStreetMap) is in,
 * water values are carried up to 3 cells (within 3.5 km; none on OISST) into the masked cells next to water, for
 * drawing only (readouts, histogram, Fit to view, isotherms and breaks use the decoded week as it is), and the land
 * is cleared from the picture along that smooth coastline (small islands painted in land color, a thin rim from
 * zoom 11). Zoomed past
 * the grid (a cell 3 CSS px or wider: MUR from zoom 9) the visible map is interpolated bilinearly between cell
 * centres instead of drawn as cells. Each day is drawn once per view into a canvas of its own (colors,
 * isotherms, coastline), so a frame, also mid-crossfade, composites one or two images.
 * No cloud haze: MUR's analysis_error is 0.36 to 0.40 C everywhere on every date checked, so it can't show where the
 * value is an estimate; the layer was dropped (Oct 16 2026) until a real signal exists (CLAUDE.md, SST engine).
 */
(function () {
    "use strict";
    if (window.SSTEngine && window.SSTEngine._setup) { return; }

    var NODATA = 65535, MAX_CODES = 4001;
    var FADE_MS = 300;
    var STRIP_ROWS = 16;
    var BREAK_G0 = 0.27, BREAK_G1 = 0.54, BREAK_MAX = 0.62;   // F per km: 0.5 and 1 F per nautical mile
    var ISO_MIN_PX = 26;                                       // isotherm loops shorter than this on screen: dropped
    var AUTO_MIN_SPAN = 8, FIT_MIN_SPAN = 3;
    // The coast (docs/coast-progress.md): water values are carried FILL_KM (at most FILL_MAX cells; whole cells only,
    // so none on OISST) into the masked cells next to water, for drawing only, so the SST reaches under the coastline
    // (assets/sstland.js), which then clears it on land. Readouts, histogram, Fit to view, isotherms and breaks never
    // see these cells.
    var FILL_KM = 3.5, FILL_MAX = 3;
    // Past the grid's resolution (a cell INTERP_PX CSS px wide or more: MUR from zoom 9) the view is drawn with
    // bilinear interpolation between cell centres, in about INTERP_BUDGET output pixels (fewer on a slow device).
    var INTERP_PX = 3, INTERP_BUDGET = 1500000, INTERP_TARGET_MS = 10;

    function hexes(list) {
        return list.map(function (h) {
            return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
        });
    }
    var PALETTES = {
        // design-notes.md "Palette": Turbo sampled 5% to 100%, 13 even stops
        spectrum: ["#3d358b", "#476ee6", "#3ba0fd", "#1bd0d5", "#2aefa1", "#75fe5c", "#b4f836",
                   "#e3db38", "#fcb136", "#f9751d", "#e14109", "#b71d02", "#7a0403"],
        // the palette before sprint 2 (map/colorscale.SST_COLORS)
        classic: ["#2c7fb8", "#41b6c4", "#7fcdbb", "#c7e9b4", "#ffffcc", "#fde68a", "#fca35d", "#fb6a4a",
                  "#ef3b2c", "#cb181d", "#99000d"],
        // cividis (matplotlib), lightness rising monotonically: readable with any color vision
        cvd: ["#00224e", "#123570", "#3b496c", "#575d6d", "#707173", "#8a8779", "#a69d75", "#c4b56c",
              "#e4cf5b", "#fee838"]
    };
    var STOPS = {};
    Object.keys(PALETTES).forEach(function (k) { STOPS[k] = hexes(PALETTES[k]); });

    var LITTLE = new Uint8Array(new Uint32Array([0x11223344]).buffer)[0] === 0x44;
    function pack(r, g, b, a) {
        return LITTLE ? ((a << 24) | (b << 16) | (g << 8) | r) >>> 0 : ((r << 24) | (g << 16) | (b << 8) | a) >>> 0;
    }
    function unpack(c) {
        return LITTLE ? [c & 255, (c >>> 8) & 255, (c >>> 16) & 255, c >>> 24]
                      : [c >>> 24, (c >>> 16) & 255, (c >>> 8) & 255, c & 255];
    }
    function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
    function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
    function mercY(lat) { return Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)); }
    function worldY(lat) { return 256 * (0.5 - mercY(lat) / (2 * Math.PI)); }   // Leaflet EPSG:3857, zoom 0
    function worldX(lon) { return 256 * (lon + 180) / 360; }

    var S = {
        map: null, layer: null, week: null, payload: null, url: null, gen: 0,
        day: 0, wantDay: null, prevDay: null, fadeStart: 0, raf: 0,
        palette: "spectrum", range: { mode: "auto" }, vmin: 30, vmax: 90, auto: null,
        opts: { stepped: false, isotherms: false, breaks: false, opacity: 1 },
        lut: new Uint32Array(65536), lutSig: "",
        img: {}, imgOrder: [], analysis: {}, labels: null, comp: null,
        hist: null, histTotal: 0,
        worker: null, workerFailed: false, queued: {},
        plan: null, planGen: 0, fillOn: false, disp: {}, landGrid: null, landOff: null, landCache: null,
        interp: null, ival: null, interpNow: false, interpOff: false, nsPerPx: 25,
        listeners: { ready: [], day: [], change: [], error: [] },
        stats: { decodeMs: null, histMs: null, lutMs: [], recolorMs: [], drawMs: [], fadeFrameMs: [],
                 analysisMs: {}, bytes: null, planMs: null, fillMs: [], interpMs: [], interpPx: null, landMs: [],
                 landGridMs: null, compMs: [], compParts: null, interpColorMs: [] }
    };
    function stat(list, v) { list.push(Math.round(v * 100) / 100); if (list.length > 60) { list.shift(); } }

    // --------------------------------------------------------------- events --
    function on(evt, fn) {
        if (!S.listeners[evt]) { S.listeners[evt] = []; }
        S.listeners[evt].push(fn);
        return function () {
            var l = S.listeners[evt], k = l.indexOf(fn);
            if (k >= 0) { l.splice(k, 1); }
        };
    }
    function fire(evt, arg) {
        (S.listeners[evt] || []).slice().forEach(function (fn) {
            try { fn(arg); } catch (e) { if (window.console) { console.error("SSTEngine " + evt + " listener", e); } }
        });
    }

    // --------------------------------------------------------------- decode --
    function decode(buf) {
        var t0 = now();
        var dv = new DataView(buf), u8 = new Uint8Array(buf);
        if (String.fromCharCode(u8[0], u8[1], u8[2], u8[3], u8[4], u8[5]) !== "GOSST1") {
            throw new Error("not a GOSST1 week");
        }
        var h = {
            version: dv.getUint16(6, true), nx: dv.getUint16(8, true), ny: dv.getUint16(10, true),
            nd: dv.getUint16(12, true), flags: dv.getUint16(14, true),
            latN: dv.getFloat32(16, true), latS: dv.getFloat32(20, true),
            lonW: dv.getFloat32(24, true), lonE: dv.getFloat32(28, true),
            base: dv.getFloat32(32, true), step: dv.getFloat32(36, true)       // 40..63 reserved
        };
        if (h.version !== 1) { throw new Error("GOSST1 version " + h.version); }
        var nx = h.nx, ny = h.ny, n = nx * ny, off = 64;
        var mask = u8.slice(off, off + n);
        off += n;
        var days = [];
        for (var d = 0; d < h.nd; d++) {
            var nesc = dv.getUint32(off, true);
            off += 4;
            var b = u8.subarray(off, off + n), escOff = off + n, e = 0;
            off += n + 2 * nesc;
            var codes = new Uint16Array(n), col0 = 0, v = 0;
            for (var r = 0; r < ny; r++) {
                var row = r * nx;
                for (var c = 0; c < nx; c++) {
                    var i = row + c, x = b[i], res;
                    if (x === 255) { res = dv.getInt16(escOff + 2 * e, true); e++; }
                    else if (x === 254) { res = 0; }
                    else { res = (x >>> 1) ^ -(x & 1); }
                    if (c === 0) { col0 += res; v = col0; } else { v += res; }
                    codes[i] = (mask[i] === 0 && x !== 254) ? v : NODATA;
                }
            }
            days.push(codes);
        }
        if (off !== buf.byteLength) { throw new Error("GOSST1: " + (buf.byteLength - off) + " bytes left over"); }
        h.dlat = ny > 1 ? (h.latN - h.latS) / (ny - 1) : 0.01;
        h.dlon = nx > 1 ? (h.lonE - h.lonW) / (nx - 1) : 0.01;
        h.edgeN = h.latN + h.dlat / 2; h.edgeS = h.latS - h.dlat / 2;
        h.edgeW = h.lonW - h.dlon / 2; h.edgeE = h.lonE + h.dlon / 2;
        h.whole = !!(h.flags & 2);
        h.baseT = Math.round(h.base * 10);                   // tenths of a degree
        h.stepT = Math.round(h.step * 10) || 1;
        S.stats.decodeMs = Math.round((now() - t0) * 10) / 10;
        return { h: h, mask: mask, days: days };
    }

    function b64buf(s) {
        var bin = atob(s), out = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) { out[i] = bin.charCodeAt(i); }
        return out.buffer;
    }
    function inflateIfGzip(buf) {
        var b = new Uint8Array(buf, 0, Math.min(2, buf.byteLength));
        if (b[0] === 0x1f && b[1] === 0x8b) {           // something dropped Content-Encoding: inflate here
            if (typeof DecompressionStream === "undefined") { throw new Error("this browser can't unpack the week"); }
            return new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();
        }
        return buf;
    }

    // ------------------------------------------------------------ statistics --
    function histogramOf(w) {
        var t0 = now(), hist = new Uint32Array(MAX_CODES), total = 0;
        w.days.forEach(function (codes) {
            for (var i = 0; i < codes.length; i++) {
                var c = codes[i];
                if (c !== NODATA) { hist[c < MAX_CODES ? c : MAX_CODES - 1]++; total++; }
            }
        });
        S.stats.histMs = Math.round((now() - t0) * 10) / 10;
        return { hist: hist, total: total };
    }
    function rank(hist, total, p) {                       // = numpy searchsorted(cumsum, p * total, "left")
        var target = p * total, cum = 0;
        for (var c = 0; c < hist.length; c++) { cum += hist[c]; if (cum >= target) { return c; } }
        return hist.length - 1;
    }
    function pctRange(hist, total, minSpan, h) {          // data/sstgrid.auto_range, the same integer math
        if (!total) { return { min: 30, max: 90 }; }
        var t5 = h.baseT + rank(hist, total, 0.05) * h.stepT, t95 = h.baseT + rank(hist, total, 0.95) * h.stepT;
        var lo = Math.floor(t5 / 10), hi = Math.ceil(t95 / 10);
        if (hi - lo < minSpan) {
            lo = Math.floor((lo + hi - minSpan) / 2);
            hi = lo + minSpan;
        }
        return { min: lo, max: hi };
    }

    // -------------------------------------------------------------- palette --
    function effectiveRange() {
        if (S.range.mode === "fixed" && isFinite(S.range.min) && isFinite(S.range.max) && S.range.max > S.range.min) {
            return { min: +S.range.min, max: +S.range.max };
        }
        return S.auto || { min: 30, max: 90 };
    }
    function buildLut() {
        var r = effectiveRange(), h = S.week ? S.week.h : { baseT: 280, stepT: 1 };
        var sig = [S.palette, r.min, r.max, S.opts.stepped ? 1 : 0, h.baseT, h.stepT].join("|");
        S.vmin = r.min;
        S.vmax = r.max;
        if (sig === S.lutSig) { return false; }
        var t0 = now(), stops = STOPS[S.palette] || STOPS.spectrum, ns = stops.length, lut = S.lut;
        var span = r.max - r.min;
        for (var c = 0; c < MAX_CODES; c++) {
            var T = (h.baseT + c * h.stepT) / 10;
            if (S.opts.stepped) { T = Math.floor(T + 1e-6) + 0.5; }
            var t = clamp((T - r.min) / span, 0, 1), x = t * (ns - 1), k = Math.min(ns - 2, Math.floor(x)), f = x - k;
            var a = stops[k], b = stops[k + 1];
            lut[c] = pack(Math.round(a[0] + (b[0] - a[0]) * f), Math.round(a[1] + (b[1] - a[1]) * f),
                          Math.round(a[2] + (b[2] - a[2]) * f), 255);
        }
        lut.fill(lut[MAX_CODES - 1], MAX_CODES, NODATA);
        lut[NODATA] = 0;
        S.lutSig = sig;
        stat(S.stats.lutMs, now() - t0);
        dropImages();
        return true;
    }
    function rgbFor(f) {
        var w = S.week, h = w ? w.h : { baseT: 280, stepT: 1 };
        var c = clamp(Math.round((f * 10 - h.baseT) / h.stepT), 0, MAX_CODES - 1);
        return unpack(S.lut[c]);
    }
    function colorFor(f) {
        buildLut();
        var c = rgbFor(+f), out = [c[0], c[1], c[2]];
        out.toString = function () { return "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")"; };
        return out;
    }
    function getRange() {
        var r = effectiveRange();
        return { mode: S.range.mode === "fixed" ? "fixed" : "auto", min: r.min, max: r.max };
    }

    // ------------------------------------------------- fill under the coast --
    // Once per week (when the coastline is in): the masked cells within FILL_KM of water, ring by ring outward
    // (8-connected), each with the neighbours of earlier rings it takes the mean of. Per day that is one pass over
    // a few tens of thousands of cells (dispCodes). Display only: S.week.days stays as decoded.
    // Rings of cells within FILL_KM: 3 on MUR's 1 km grid, none on OISST's 25 km one (its gap to the shore is a
    // whole cell, too far to carry a value: How it works says OISST shows nothing near the coast).
    function fillRings(h) { return clamp(Math.floor(FILL_KM / Math.max(h.dlat * 111.2, 0.01)), 0, FILL_MAX); }
    function buildPlan(w) {
        var t0 = now(), h = w.h, nx = h.nx, ny = h.ny, n = nx * ny, mask = w.mask, N = fillRings(h);
        var ring = new Uint8Array(n), cells = [], i, r, c, k, dr, dc;
        for (i = 0; i < n; i++) { ring[i] = mask[i] === 0 ? 0 : 255; }
        var prev = [];
        for (r = 0; r < ny && N > 0; r++) {              // ring 1: masked cells touching water
            for (c = 0; c < nx; c++) {
                i = r * nx + c;
                if (ring[i] !== 255) { continue; }
                var hit = false;
                for (dr = -1; dr <= 1 && !hit; dr++) {
                    var rr = r + dr;
                    if (rr < 0 || rr >= ny) { continue; }
                    for (dc = -1; dc <= 1; dc++) {
                        var cc = c + dc;
                        if (cc >= 0 && cc < nx && ring[rr * nx + cc] === 0) { hit = true; break; }
                    }
                }
                if (hit) { prev.push(i); }
            }
        }
        prev.forEach(function (j) { ring[j] = 1; });
        cells = prev.slice();
        for (k = 2; k <= N; k++) {                       // rings 2..N: unreached neighbours of the last ring
            var next = [];
            for (var p = 0; p < prev.length; p++) {
                var j0 = prev[p], r0 = (j0 / nx) | 0, c0 = j0 - r0 * nx;
                for (dr = -1; dr <= 1; dr++) {
                    var r1 = r0 + dr;
                    if (r1 < 0 || r1 >= ny) { continue; }
                    for (dc = -1; dc <= 1; dc++) {
                        var c1 = c0 + dc, j1 = r1 * nx + c1;
                        if (c1 >= 0 && c1 < nx && ring[j1] === 255) { ring[j1] = k; next.push(j1); }
                    }
                }
            }
            cells = cells.concat(next);
            prev = next;
        }
        var T = cells.length, start = new Int32Array(T + 1), nb = [];
        for (var t = 0; t < T; t++) {
            i = cells[t];
            var ri = (i / nx) | 0, ci = i - ri * nx, kr = ring[i];
            start[t] = nb.length;
            for (dr = -1; dr <= 1; dr++) {
                var r2 = ri + dr;
                if (r2 < 0 || r2 >= ny) { continue; }
                for (dc = -1; dc <= 1; dc++) {
                    var c2 = ci + dc, j2 = r2 * nx + c2;
                    if ((dr || dc) && c2 >= 0 && c2 < nx && ring[j2] < kr) { nb.push(j2); }
                }
            }
        }
        start[T] = nb.length;
        S.stats.planMs = Math.round((now() - t0) * 10) / 10;
        return { cells: new Int32Array(cells), start: start, nb: new Int32Array(nb), rings: N, ring: ring };
    }
    function fillSig() { return S.fillOn && S.plan ? S.planGen : 0; }
    // The coast fill is drawn faded (Luyen, Oct 17): washed out toward gray and a little see-through, so water the
    // satellite has no reading for (narrow bays, the last km to the shore) reads as an estimate, not a measurement.
    var FILL_FADE = 0.62, FILL_ALPHA = 0.78;
    // How faded a fill cell is: graded by its ring (1 = next to a reading, lightly; the last ring fully), so the
    // fade blends into the measured water instead of stepping at the cell edges.
    function fillW(i) { var P = S.plan, r = P ? P.ring[i] : 0; return S.fillOn && P && r > 0 && r <= P.rings ? (r + 1) / (P.rings + 1) : 0; }
    function fade(col, f) {                              // f: 0 (as measured) .. 1 (all fill)
        if (!f) { return col; }
        var p = unpack(col), L = 0.3 * p[0] + 0.59 * p[1] + 0.11 * p[2], m = FILL_FADE * f;
        return pack((p[0] + (L - p[0]) * m) | 0, (p[1] + (L - p[1]) * m) | 0, (p[2] + (L - p[2]) * m) | 0,
                    (p[3] * (1 - (1 - FILL_ALPHA) * f)) | 0);
    }
    // The day's codes as drawn: water as measured, plus the fill under the coast while the coastline is on.
    function dispCodes(d) {
        var w = S.week;
        if (!S.fillOn || !S.plan) { return w.days[d]; }
        var hit = S.disp[d];
        if (hit) { return hit; }
        var t0 = now(), P = S.plan, out = new Uint16Array(w.days[d]), cells = P.cells, st = P.start, nb = P.nb;
        for (var t = 0; t < cells.length; t++) {
            var sum = 0, cnt = 0;
            for (var j = st[t]; j < st[t + 1]; j++) {
                var v = out[nb[j]];
                if (v !== NODATA) { sum += v; cnt++; }
            }
            out[cells[t]] = cnt ? Math.round(sum / cnt) : NODATA;
        }
        S.disp[d] = out;
        stat(S.stats.fillMs, now() - t0);
        return out;
    }
    // The coastline (assets/sstland.js) arrived, or a week did while it was in: fill on, land cover per cell (for
    // the isotherm labels), every cached picture redone.
    function landChanged() {
        var Land = window.SSTLand, w = S.week;
        if (!Land || !Land.ready() || !w) { return; }
        if (!S.plan || S.plan.week !== w) {
            S.plan = buildPlan(w);
            S.plan.week = w;
            S.planGen++;
            S.disp = {};
            dropImages();
            S.interp = null; S.ival = null;
            S.landGrid = null;
            landGridLater(w);
        }
        if (!S.fillOn) { S.fillOn = true; dropImages(); S.interp = null; S.ival = null; }
    }
    // Land cover per grid cell from the coastline (isotherm labels stay off land), when the browser is idle: it
    // takes ~15 ms (~65 ms on a slow phone), once per week. Until then labels only avoid the week's own mask.
    function landGridLater(w) {
        var idle = window.requestIdleCallback || function (fn) { return setTimeout(fn, 200); };
        idle(function () {
            var Land = window.SSTLand;
            if (S.week !== w || S.landGrid || !Land || !Land.ready()) { return; }
            var t0 = now();
            try { S.landGrid = Land.rasterize(w.h); } catch (e) { S.landGrid = null; }
            S.stats.landGridMs = Math.round((now() - t0) * 10) / 10;
            S.labels = null;
            if (S.opts.isotherms) { S.comp = null; redraw(); }
        }, { timeout: 3000 });
    }
    function hookLand() {
        var Land = window.SSTLand;
        if (!Land || S.landOff) { return; }
        S.landOff = Land.on("change", function () { landChanged(); redraw(); });
        Land.start();
        landChanged();
    }

    // ------------------------------------------------------- per-day images --
    function dropImages() { S.img = {}; S.imgOrder = []; }
    function dayImage(d) {
        var w = S.week, br = S.opts.breaks && S.analysis[d] ? S.analysis[d].breakA : null;
        var sig = S.lutSig + "|" + (br ? 1 : 0) + "|" + fillSig();
        var hit = S.img[d];
        if (hit && hit.sig === sig) { return hit.canvas; }
        var t0 = now(), h = w.h, nx = h.nx, ny = h.ny, n = nx * ny;
        var cv = hit ? hit.canvas : document.createElement("canvas");
        if (!hit) { cv.width = nx; cv.height = ny; }
        var g = cv.getContext("2d");
        if (!S.imgData || S.imgData.width !== nx || S.imgData.height !== ny) {
            S.imgData = g.createImageData(nx, ny);
            S.imgBuf = new Uint32Array(S.imgData.data.buffer);
        }
        var out = S.imgBuf, codes = dispCodes(d), lut = S.lut, i;
        var faded = fillSig() !== 0;
        if (!br) {
            for (i = 0; i < n; i++) { out[i] = lut[codes[i]]; }
            if (faded) { for (var t = 0, fc = S.plan.cells; t < fc.length; t++) { var j = fc[t]; if (out[j]) { out[j] = fade(out[j], fillW(j)); } } }
        } else {
            for (i = 0; i < n; i++) {
                var code = codes[i], a = br[i];
                if (code === NODATA) { out[i] = 0; continue; }
                if (!a) { out[i] = lut[code]; continue; }
                var p = unpack(lut[code]);              // a break: blend toward white, up to 62%
                out[i] = pack((p[0] + (255 - p[0]) * a / 255) | 0, (p[1] + (255 - p[1]) * a / 255) | 0,
                              (p[2] + (255 - p[2]) * a / 255) | 0, 255);
            }
            if (faded) { for (var t2 = 0, fc2 = S.plan.cells; t2 < fc2.length; t2++) { var j2 = fc2[t2]; if (out[j2]) { out[j2] = fade(out[j2], fillW(j2)); } } }
        }
        g.putImageData(S.imgData, 0, 0);
        S.img[d] = { canvas: cv, sig: sig };
        var k = S.imgOrder.indexOf(d);
        if (k >= 0) { S.imgOrder.splice(k, 1); }
        S.imgOrder.push(d);
        while (S.imgOrder.length > 4) { delete S.img[S.imgOrder.shift()]; }   // memory: at most 4 days kept
        stat(S.stats.recolorMs, now() - t0);
        return cv;
    }

    // ------------------------------------------------------------- drawing --
    function retina() { return (window.L && L.Browser && L.Browser.retina) ? 2 : 1; }
    function drawStrips(ctx, map, d, b0, rough) {
        var w = S.week, h = w.h, cv = dayImage(d);
        var k = 256 * Math.pow(2, map.getZoom()), po = map.getPixelOrigin(), m = retina();
        var x0 = worldX(h.edgeW) * k / 256 - po.x, x1 = worldX(h.edgeE) * k / 256 - po.x;
        var top = b0.min.y, bottom = b0.max.y;
        ctx.imageSmoothingEnabled = !S.opts.stepped && !rough;     // rough: the padding, seen only mid-pan
        var snap = function (y) { return Math.round((y - b0.min.y) * m) / m + b0.min.y; };
        var yPrev = snap(worldY(h.edgeN) * k / 256 - po.y);
        for (var r0 = 0; r0 < h.ny; r0 += STRIP_ROWS) {
            var r1 = Math.min(h.ny, r0 + STRIP_ROWS);
            var y1 = snap(worldY(h.edgeN - r1 * h.dlat) * k / 256 - po.y), y0 = yPrev;
            yPrev = y1;
            if (y1 <= top || y0 >= bottom || y1 <= y0) { continue; }
            ctx.drawImage(cv, 0, r0, h.nx, r1 - r0, x0, y0, x1 - x0, y1 - y0);
        }
    }
    // Zoomed past the grid: the part of the grid in the visible map, interpolated bilinearly between cell centres
    // (stepped bands then follow the temperature lines instead of the cells). Where some of the four cells around a
    // pixel are masked, the others are averaged by their weights, and the pixel is drawn where they weigh at least
    // a quarter (the edge of the data is then a smooth curve, antialiased). Two steps, each cached per view and day:
    // the values (interpValues: the bilinear work, ~10 ms on a slow phone), then the colors through the color table
    // (interpImage: a few ms), so a palette or range change only redoes the second.
    var IV_SCALE = 8, IV_NONE = 65535;                    // values in 1/8 code steps (0.0125 F); none = transparent
    function cellPx(map) { return S.week ? S.week.h.dlon * 256 * Math.pow(2, map.getZoom()) / 360 : 0; }
    function interpView(layer) {                          // the visible part of the grid, layer px; null if none
        var map = layer._map, h = S.week.h, k = 256 * Math.pow(2, map.getZoom()), po = map.getPixelOrigin();
        var size = map.getSize(), v0 = map.containerPointToLayerPoint([0, 0]);
        var gx0 = worldX(h.edgeW) * k / 256 - po.x, gx1 = worldX(h.edgeE) * k / 256 - po.x;
        var gy0 = worldY(h.edgeN) * k / 256 - po.y, gy1 = worldY(h.edgeS) * k / 256 - po.y;
        var X0 = Math.floor(Math.max(v0.x, gx0)), X1 = Math.ceil(Math.min(v0.x + size.x, gx1));
        var Y0 = Math.floor(Math.max(v0.y, gy0)), Y1 = Math.ceil(Math.min(v0.y + size.y, gy1));
        if (X1 <= X0 || Y1 <= Y0) { return null; }
        return { X0: X0, Y0: Y0, X1: X1, Y1: Y1, k: k, po: po, z: map.getZoom(),
                 key: [map.getZoom(), X0, Y0, X1, Y1, po.x, po.y].join("|") };
    }
    function interpValues(layer, d, vw) {
        var w = S.week, h = w.h, nx = h.nx, ny = h.ny, k = vw.k, po = vw.po;
        var br = S.opts.breaks && S.analysis[d] ? S.analysis[d].breakA : null;
        var key = [vw.key, S.opts.stepped ? 1 : 0, fillSig()].join("|");
        if (!S.ival || S.ival.key !== key) { S.ival = { key: key, days: {}, order: [] }; }
        var hit = S.ival.days[d];
        if (hit && hit.br === br) { return hit; }
        var t0 = now(), X0 = vw.X0, Y0 = vw.Y0, W = vw.X1 - X0, H = vw.Y1 - Y0;
        var budget = clamp(INTERP_TARGET_MS * 1e6 / S.nsPerPx, 150000, INTERP_BUDGET);
        var rs = Math.min(retina(), Math.sqrt(budget / (W * H)));     // output pixels per CSS px
        var wo = Math.max(1, Math.round(W * rs)), ho = Math.max(1, Math.round(H * rs));
        var sx = W / wo, sy = H / ho;
        var cols0 = new Int32Array(wo), colsF = new Float32Array(wo), colIn = new Uint8Array(wo);
        for (var ox = 0; ox < wo; ox++) {
            var lon = (X0 + (ox + 0.5) * sx + po.x) / k * 360 - 180, fc = (lon - h.lonW) / h.dlon;
            colIn[ox] = fc >= -0.5 && fc <= nx - 0.5 ? 1 : 0;
            var c0 = clamp(Math.floor(fc), 0, nx - 2);
            cols0[ox] = c0;
            colsF[ox] = clamp(fc - c0, 0, 1);
        }
        var rows0 = new Int32Array(ho), rowsF = new Float32Array(ho), rowIn = new Uint8Array(ho);
        for (var oy = 0; oy < ho; oy++) {
            var wyp = Y0 + (oy + 0.5) * sy + po.y;
            var lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * wyp / k))) * 180 / Math.PI;
            var fr = (h.latN - lat) / h.dlat;
            rowIn[oy] = fr >= -0.5 && fr <= ny - 0.5 ? 1 : 0;
            rows0[oy] = clamp(Math.floor(fr), 0, ny - 2);
            rowsF[oy] = clamp(fr - rows0[oy], 0, 1);
        }
        var codes = dispCodes(d), n = wo * ho;
        var vals = new Uint16Array(n), alpha = new Uint8Array(n), brk = br ? new Uint8Array(n) : null;
        var fadeOn = fillSig() !== 0, fadeW = fadeOn ? new Uint8Array(n) : null;
        vals.fill(IV_NONE);
        var aaK = h.dlon * k / 360 / sx;                     // output pixels per cell, for the edge's antialiasing
        // Stepped: the bands come from the field smoothed as the isotherms are (analysisWorker), over the cells in
        // view, so a band's edge is the temperature line itself instead of following 0.1 F noise cell by cell where
        // the water is nearly even. Smooth colors use the cells as they are.
        var sm = null, swR = 0, swC = 0, swW = 0;
        if (S.opts.stepped) {
            var sig = h.whole ? 3.0 : 1.3, R = Math.ceil(3 * sig) + 1;
            var cr0 = clamp(rows0[0] - R, 0, ny - 1), cr1 = clamp(rows0[ho - 1] + 1 + R, 0, ny - 1);
            var cc0 = clamp(cols0[0] - R, 0, nx - 1), cc1 = clamp(cols0[wo - 1] + 1 + R, 0, nx - 1);
            sm = smoothWindow(codes, h, cr0, cr1, cc0, cc1, sig);
            swR = cr0; swC = cc0; swW = cc1 - cc0 + 1;
        }
        for (oy = 0; oy < ho; oy++) {
            if (!rowIn[oy]) { continue; }
            var r0 = rows0[oy], fy = rowsF[oy], gy = 1 - fy;
            var rowA = r0 * nx, rowB = rowA + nx, o = oy * wo, srA = (r0 - swR) * swW - swC, srB = srA + swW;
            for (ox = 0; ox < wo; ox++) {
                if (!colIn[ox]) { continue; }
                var cA = cols0[ox], fx = colsF[ox], gx = 1 - fx;
                var ia = rowA + cA, ib = ia + 1, ic = rowB + cA, id = ic + 1;
                var a = codes[ia], b = codes[ib], c = codes[ic], e = codes[id];
                var wa = gx * gy, wb = fx * gy, wc = gx * fy, we = fx * fy;
                var sw = 0, sv = 0;
                if (sm) {
                    if (a !== NODATA) { sw += wa; sv += wa * sm[srA + cA]; }
                    if (b !== NODATA) { sw += wb; sv += wb * sm[srA + cA + 1]; }
                    if (c !== NODATA) { sw += wc; sv += wc * sm[srB + cA]; }
                    if (e !== NODATA) { sw += we; sv += we * sm[srB + cA + 1]; }
                } else {
                    if (a !== NODATA) { sw += wa; sv += wa * a; }
                    if (b !== NODATA) { sw += wb; sv += wb * b; }
                    if (c !== NODATA) { sw += wc; sv += wc * c; }
                    if (e !== NODATA) { sw += we; sv += we * e; }
                }
                if (sw <= 0) { continue; }
                var al = (sw - 0.25) * aaK + 0.5;
                if (al <= 0) { continue; }
                var v = (sv / sw) * IV_SCALE + 0.5;
                vals[o + ox] = v < 0 ? 0 : (v > 65534 ? 65534 : v | 0);
                alpha[o + ox] = al >= 1 ? 255 : (al * 255) | 0;
                if (brk) {
                    brk[o + ox] = ((a !== NODATA ? wa * br[ia] : 0) + (b !== NODATA ? wb * br[ib] : 0) +
                                   (c !== NODATA ? wc * br[ic] : 0) + (e !== NODATA ? we * br[id] : 0)) / sw;
                }
                if (fadeW) {
                    fadeW[o + ox] = 255 * ((a !== NODATA ? wa * fillW(ia) : 0) + (b !== NODATA ? wb * fillW(ib) : 0) +
                                          (c !== NODATA ? wc * fillW(ic) : 0) + (e !== NODATA ? we * fillW(id) : 0)) / sw;
                }
            }
        }
        var ms = now() - t0;
        S.nsPerPx = clamp(0.7 * S.nsPerPx + 0.3 * (ms * 1e6 / (wo * ho)), 3, 400);
        stat(S.stats.interpMs, ms);
        S.stats.interpPx = [wo, ho];
        var res = { vals: vals, alpha: alpha, brk: brk, fade: fadeW, wo: wo, ho: ho, x: X0, y: Y0, w: W, h: H, br: br };
        S.ival.days[d] = res;
        S.ival.order.push(d);
        while (S.ival.order.length > 3) { delete S.ival.days[S.ival.order.shift()]; }
        return res;
    }
    function interpImage(layer, d) {
        if (!S.week) { return null; }
        var vw = interpView(layer);
        if (!vw) { return null; }
        var iv = interpValues(layer, d, vw);
        var key = [vw.key, S.lutSig, S.opts.stepped ? 1 : 0, fillSig()].join("|");
        if (!S.interp || S.interp.key !== key) { S.interp = { key: key, days: {}, order: [] }; }
        var hit = S.interp.days[d];
        if (hit && hit.iv === iv) { return hit; }
        var t0 = now(), wo = iv.wo, ho = iv.ho, n = wo * ho, lut = S.lut, vals = iv.vals, alpha = iv.alpha, brk = iv.brk;
        var cv = document.createElement("canvas");
        cv.width = wo; cv.height = ho;
        var g = cv.getContext("2d"), img = g.createImageData(wo, ho), out = new Uint32Array(img.data.buffer);
        for (var i = 0; i < n; i++) {
            var v = vals[i];
            if (v === IV_NONE) { continue; }
            var col = lut[(v + IV_SCALE / 2) >> 3], al = alpha[i];
            if (brk && brk[i]) {
                var ba = brk[i], p = unpack(col);
                col = pack((p[0] + (255 - p[0]) * ba / 255) | 0, (p[1] + (255 - p[1]) * ba / 255) | 0,
                           (p[2] + (255 - p[2]) * ba / 255) | 0, 255);
            }
            if (al < 255) { var q = unpack(col); col = pack(q[0], q[1], q[2], al); }
            if (iv.fade && iv.fade[i]) { col = fade(col, iv.fade[i] / 255); }
            out[i] = col;
        }
        g.putImageData(img, 0, 0);
        stat(S.stats.interpColorMs, now() - t0);
        var res = { canvas: cv, x: iv.x, y: iv.y, w: iv.w, h: iv.h, iv: iv };
        S.interp.days[d] = res;
        S.interp.order.push(d);
        while (S.interp.order.length > 3) { delete S.interp.days[S.interp.order.shift()]; }
        return res;
    }
    // The codes in rows r0..r1, columns c0..c1 smoothed by a Gaussian of sigma cells, normalized over the cells
    // that have a value (so land doesn't drag the water toward it): code units, a cell without a value keeps its own.
    function smoothWindow(codes, h, r0, r1, c0, c1, sigma) {
        var W = c1 - c0 + 1, H = r1 - r0 + 1, n = W * H, nx = h.nx;
        var F = new Float32Array(n), M = new Float32Array(n), tF = new Float32Array(n), tM = new Float32Array(n);
        var R = Math.max(1, Math.ceil(3 * sigma)), K = new Float32Array(2 * R + 1), ks = 0, x, y, j, i, q, sf, sm2;
        for (j = -R; j <= R; j++) { K[j + R] = Math.exp(-j * j / (2 * sigma * sigma)); ks += K[j + R]; }
        for (j = 0; j < K.length; j++) { K[j] /= ks; }
        for (y = 0; y < H; y++) {
            for (x = 0; x < W; x++) {
                var v = codes[(r0 + y) * nx + c0 + x];
                if (v !== NODATA) { F[y * W + x] = v; M[y * W + x] = 1; }
            }
        }
        for (y = 0; y < H; y++) {
            var row = y * W;
            for (x = 0; x < W; x++) {
                sf = 0; sm2 = 0;
                for (j = -R; j <= R; j++) {
                    q = x + j; q = q < 0 ? 0 : (q >= W ? W - 1 : q);
                    sf += F[row + q] * K[j + R]; sm2 += M[row + q] * K[j + R];
                }
                tF[row + x] = sf; tM[row + x] = sm2;
            }
        }
        var out = new Float32Array(n);
        for (y = 0; y < H; y++) {
            for (x = 0; x < W; x++) {
                sf = 0; sm2 = 0;
                for (j = -R; j <= R; j++) {
                    q = y + j; q = q < 0 ? 0 : (q >= H ? H - 1 : q);
                    i = q * W + x;
                    sf += tF[i] * K[j + R]; sm2 += tM[i] * K[j + R];
                }
                i = y * W + x;
                out[i] = sm2 > 0.3 ? sf / sm2 : F[i];
            }
        }
        return out;
    }
    function drawDay(ctx, layer, d, alpha) {
        var map = layer._map, b0 = layer._bounds;
        var it = S.interpNow ? interpImage(layer, d) : null;
        ctx.globalAlpha = alpha;
        if (!it) {
            drawStrips(ctx, map, d, b0);
        } else {
            ctx.save();                                     // the padding around the visible map: cells, as before
            ctx.beginPath();
            ctx.rect(b0.min.x, b0.min.y, b0.max.x - b0.min.x, b0.max.y - b0.min.y);
            ctx.rect(it.x, it.y, it.w, it.h);
            ctx.clip("evenodd");
            drawStrips(ctx, map, d, b0, true);
            ctx.restore();
            ctx.globalAlpha = alpha;
            ctx.imageSmoothingEnabled = true;
            ctx.drawImage(it.canvas, it.x, it.y, it.w, it.h);
        }
        ctx.globalAlpha = 1;
    }

    // The coastline over the SST (assets/sstland.js). Per view, the land in view as one path (pieces overlap, so
    // filling them together leaves no seams), drawn into each day's picture after its colors and isotherms: the
    // land is cleared ("destination-out"), so the SST stops at a smooth, antialiased coastline at full resolution and
    // the basemap shows on land as it is (ponds, rivers, roads, lighter built-up areas). Islets (under 1 km2) are
    // painted in the basemap's land color over the SST instead: the basemap leaves many out. From zoom 11 (a pixel is
    // under 60 m there), where the basemap's coast and ours can differ by a pixel or two, a rim of the basemap's
    // land color RIM px wide is laid under what's left ("destination-over"), so it shows only where the SST isn't:
    // on land along the coast, covering any sliver of the basemap's water. It is stroked from the level below
    // (fewer vertices), widened by that level's tolerance.
    var RIM_ZOOM = 11;
    function landLayer(layer) {
        var Land = window.SSTLand;
        if (!S.fillOn || !Land || !Land.ready()) { return null; }
        var map = layer._map, b0 = layer._bounds, c = layer._container, z = map.getZoom(), k = Math.pow(2, z);
        var po = map.getPixelOrigin();
        var v = Land.view(z, [(b0.min.x + po.x) / k, (b0.min.y + po.y) / k, (b0.max.x + po.x) / k, (b0.max.y + po.y) / k]);
        if (!v) { return null; }
        var key = [z, b0.min.x, b0.min.y, po.x, po.y, c.width, c.height, v.key].join("|");
        if (S.landCache && S.landCache.key === key) { return S.landCache; }
        var t0 = now(), bw = b0.max.x - b0.min.x;
        var res = { key: key, level: v.level, rimLevel: null, tiles: v.tiles.length, color: Land.color(z),
                    m: c.width / Math.max(1, bw), k: k, ox: b0.min.x + po.x, oy: b0.min.y + po.y,
                    path: null, islets: null, coast: null, rimW: 0 };
        if (v.tiles.length) {
            res.path = new Path2D();
            v.tiles.forEach(function (t) {
                res.path.addPath(t.fill);
                if (t.islets) { res.islets = res.islets || new Path2D(); res.islets.addPath(t.islets); }
            });
            if (z >= (S.rimZoom || RIM_ZOOM)) {
                var mpp = 156543.03 * Math.cos(map.getCenter().lat * Math.PI / 180) / k;   // metres per CSS px
                res.coast = new Path2D();
                v.rim.tiles.forEach(function (t) { res.coast.addPath(t.coast); });
                res.rimW = 2 * (Land.rimPx + v.rim.tol / mpp) / k;
                res.rimLevel = v.rim.level;
            }
        }
        S.landCache = res;
        stat(S.stats.landMs, now() - t0);
        return res;
    }
    function drawLand(ctx, layer) {
        var land = landLayer(layer);
        if (!land || !land.path) { return; }
        ctx.save();
        ctx.setTransform(land.m * land.k, 0, 0, land.m * land.k, -land.ox * land.m, -land.oy * land.m);
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = "destination-out";
        ctx.fillStyle = "#000";
        ctx.fill(land.path, "nonzero");
        if (land.islets) {                              // small islands: land color over the SST
            ctx.globalCompositeOperation = "source-atop";
            ctx.fillStyle = land.color;
            ctx.fill(land.islets, "nonzero");
        }
        if (land.coast) {
            ctx.globalCompositeOperation = "destination-over";
            ctx.strokeStyle = land.color;
            ctx.lineWidth = land.rimW;
            ctx.lineJoin = "bevel";                     // its edge lies on land color or under the SST: the
            ctx.lineCap = "butt";                       // cheaper stroke doesn't show
            ctx.stroke(land.coast);
        }
        ctx.restore();
    }

    function isoColor(T) {
        var c = rgbFor(T), r = c[0] / 255, g = c[1] / 255, b = c[2] / 255;
        var lin = function (v) { return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
        var lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
        var line = lum > 0.22 ? [c[0] * 0.42, c[1] * 0.42, c[2] * 0.42]
                              : [c[0] + (255 - c[0]) * 0.6, c[1] + (255 - c[1]) * 0.6, c[2] + (255 - c[2]) * 0.6];
        return { base: c, line: line.map(Math.round), lum: lum };
    }
    function isoPaths(an) {
        if (an.paths) { return an.paths; }
        an.paths = an.iso.map(function (lv) {
            var buckets = {};
            for (var p = 0; p < lv.starts.length; p++) {
                var len = lv.lens[p], b = Math.floor(Math.log(Math.max(len, 1e-9)) / Math.LN2);
                var path = buckets[b] || (buckets[b] = new Path2D());
                var s = lv.starts[p] * 2, n = lv.counts[p];
                path.moveTo(lv.coords[s], lv.coords[s + 1]);
                for (var q = 1; q < n; q++) { path.lineTo(lv.coords[s + 2 * q], lv.coords[s + 2 * q + 1]); }
            }
            return { T: lv.T, buckets: buckets };
        });
        return an.paths;
    }
    // A day as drawn in one view (zoom, position, size, colors, coastline): its colors (cells, or interpolated when
    // zoomed past the grid), isotherms and labels, and the coastline over them, on a canvas the size of the layer's.
    // Built once per view and day (the next and previous days while the browser is idle), so every frame, also
    // mid-crossfade, only composites one or two images. At most three of these (two on a big retina screen: each is
    // the canvas's size, ~30 MB at 1440 x 900 @2x); a new view reuses the old canvases.
    function compKeep(c) { return c.width * c.height > 4e6 ? 2 : 3; }
    function compLayer(layer, d) {
        var map = layer._map, b0 = layer._bounds, c = layer._container;
        var an = S.analysis[d] || null;
        var br = S.opts.breaks && an && an.breakA ? an.breakA : null;
        var iso = S.opts.isotherms && an && an.iso ? an.iso : null;
        var land = landLayer(layer);
        var key = [map.getZoom(), b0.min.x, b0.min.y, c.width, c.height, S.lutSig, fillSig(), S.interpNow ? 1 : 0,
                   land ? land.key : "-", S.opts.isotherms ? 1 : 0, S.opts.breaks ? 1 : 0].join("|");
        if (!S.comp || S.comp.key !== key) {
            var spare = [];
            if (S.comp) {
                spare = S.comp.spare;
                S.comp.order.forEach(function (k) { spare.push(S.comp.days[k].canvas); });
            }
            S.comp = { key: key, days: {}, order: [], spare: spare.slice(0, compKeep(c)) };
        }
        var hit = S.comp.days[d];
        if (hit && hit.br === br && hit.iso === iso) { return hit.canvas; }
        var t0 = now();
        var cv = hit ? hit.canvas : (S.comp.spare.pop() || document.createElement("canvas"));
        if (cv.width !== c.width || cv.height !== c.height) { cv.width = c.width; cv.height = c.height; }
        var g = cv.getContext("2d"), m = c.width / Math.max(1, b0.max.x - b0.min.x);
        g.setTransform(1, 0, 0, 1, 0, 0);
        g.globalAlpha = 1;
        g.globalCompositeOperation = "source-over";
        g.clearRect(0, 0, cv.width, cv.height);
        g.setTransform(m, 0, 0, m, -b0.min.x * m, -b0.min.y * m);   // layer points, as the layer's own context
        drawDay(g, layer, d, 1);
        var t1 = now();
        if (iso) { drawIsotherms(g, map, d, 1); }
        var t2 = now();
        drawLand(g, layer);
        S.stats.compParts = { day: t1 - t0, iso: t2 - t1, land: now() - t2 };
        S.comp.days[d] = { canvas: cv, br: br, iso: iso };
        var at = S.comp.order.indexOf(d);
        if (at >= 0) { S.comp.order.splice(at, 1); }
        S.comp.order.push(d);
        while (S.comp.order.length > compKeep(c)) {
            var old = S.comp.order.shift();
            if (S.comp.spare.length < 1) { S.comp.spare.push(S.comp.days[old].canvas); }
            delete S.comp.days[old];
        }
        stat(S.stats.compMs, now() - t0);
        return cv;
    }
    function drawComp(ctx, layer, d, alpha) {
        var cv = compLayer(layer, d), b0 = layer._bounds;
        if (alpha <= 0) { return; }
        ctx.globalAlpha = alpha;
        ctx.drawImage(cv, b0.min.x, b0.min.y, b0.max.x - b0.min.x, b0.max.y - b0.min.y);
        ctx.globalAlpha = 1;
    }
    function drawIsotherms(ctx, map, d, alpha) {
        var an = S.analysis[d];
        if (!an || !an.iso) { return; }
        var z = Math.pow(2, map.getZoom()), po = map.getPixelOrigin(), minLen = ISO_MIN_PX / z;
        ctx.save();
        ctx.translate(-po.x, -po.y);
        ctx.scale(z, z);
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        isoPaths(an).forEach(function (lv) {
            var major = lv.T % 2 === 0, col = isoColor(lv.T).line;
            ctx.strokeStyle = "rgb(" + col.join(",") + ")";
            ctx.globalAlpha = alpha * (major ? 0.95 : 0.6);
            ctx.lineWidth = (major ? 0.9 : 0.5) / z;
            Object.keys(lv.buckets).forEach(function (b) {
                if (Math.pow(2, +b + 1) >= minLen) { ctx.stroke(lv.buckets[b]); }
            });
        });
        ctx.restore();
        drawLabels(ctx, map, d, alpha);
    }

    // Sparse labels on even degrees: straight stretches in water, at most 2 per level (1 on a phone-sized map),
    // 420 px apart (300 on a phone), never over another label. Placed per view, drawn crisp at the current zoom.
    function placeLabels(map, d) {
        var an = S.analysis[d], size = map.getSize(), z = Math.pow(2, map.getZoom()), po = map.getPixelOrigin();
        var key = [d, map.getZoom(), Math.round(po.x), Math.round(po.y), size.x, size.y].join("|");
        if (S.labels && S.labels.key === key && S.labels.an === an) { return S.labels.items; }
        var phone = size.x < 600, maxPer = phone ? 1 : 2, sep = phone ? 300 : 420, lw = 26, lh = 12;
        var origin = map.containerPointToLayerPoint([0, 0]), items = [], boxes = [];
        var w = S.week, h = w.h;
        function water(x, y) {                           // container px -> grid cell -> water?
            var ll = map.containerPointToLatLng([x, y]);
            var r = Math.round((h.latN - ll.lat) / h.dlat), c = Math.round((ll.lng - h.lonW) / h.dlon);
            return r >= 0 && r < h.ny && c >= 0 && c < h.nx && w.mask[r * h.nx + c] === 0 &&
                (!S.landGrid || S.landGrid[r * h.nx + c] < 96);      // and not on the coastline's land
        }
        an.iso.forEach(function (lv) {
            if (lv.T % 2 !== 0) { return; }
            var order = [];
            for (var p = 0; p < lv.starts.length; p++) { if (lv.lens[p] * z > lw * 4) { order.push(p); } }
            order.sort(function (a, b) { return lv.lens[b] - lv.lens[a]; });
            var placed = [];
            for (var oi = 0; oi < order.length && oi < 25 && placed.length < maxPer; oi++) {
                var p2 = order[oi], s = lv.starts[p2] * 2, n = lv.counts[p2];
                var pts = new Float32Array(n * 2), cum = new Float32Array(n), q;
                for (q = 0; q < n; q++) {
                    pts[2 * q] = lv.coords[s + 2 * q] * z - po.x - origin.x;
                    pts[2 * q + 1] = lv.coords[s + 2 * q + 1] * z - po.y - origin.y;
                    if (q) { cum[q] = cum[q - 1] + Math.hypot(pts[2 * q] - pts[2 * q - 2], pts[2 * q + 1] - pts[2 * q - 1]); }
                }
                var total = cum[n - 1];
                var at = function (dist) {
                    var lo = 0, hi = n - 1;
                    while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (cum[mid] < dist) { lo = mid; } else { hi = mid; } }
                    var f = cum[hi] > cum[lo] ? (dist - cum[lo]) / (cum[hi] - cum[lo]) : 0;
                    return [pts[2 * lo] + (pts[2 * hi] - pts[2 * lo]) * f, pts[2 * lo + 1] + (pts[2 * hi + 1] - pts[2 * lo + 1]) * f];
                };
                var cands = [];
                for (var dd = lw; dd < total - lw; dd += 18) { cands.push(dd); }
                cands.sort(function (a, b) { return Math.abs(a - total / 2) - Math.abs(b - total / 2); });
                for (var ci = 0; ci < cands.length && placed.length < maxPer; ci++) {
                    var c0 = at(cands[ci]), ca = at(cands[ci] - lw / 2), cb = at(cands[ci] + lw / 2);
                    if (c0[0] < 24 || c0[1] < 24 || c0[0] > size.x - 24 || c0[1] > size.y - 24) { continue; }
                    var a1 = Math.atan2(c0[1] - ca[1], c0[0] - ca[0]), a2 = Math.atan2(cb[1] - c0[1], cb[0] - c0[0]);
                    var turn = Math.abs(((a1 - a2) * 180 / Math.PI + 540) % 360 - 180);
                    if (turn > 20) { continue; }
                    if (placed.some(function (pp) { return Math.hypot(pp[0] - c0[0], pp[1] - c0[1]) < sep; })) { continue; }
                    if (!water(c0[0], c0[1]) || !water(ca[0], ca[1]) || !water(cb[0], cb[1])) { continue; }
                    var ang = Math.atan2(cb[1] - ca[1], cb[0] - ca[0]);
                    if (ang > Math.PI / 2) { ang -= Math.PI; }
                    if (ang < -Math.PI / 2) { ang += Math.PI; }
                    var hx = Math.abs(lw / 2 * Math.cos(ang)) + Math.abs(lh / 2 * Math.sin(ang));
                    var hy = Math.abs(lw / 2 * Math.sin(ang)) + Math.abs(lh / 2 * Math.cos(ang));
                    var box = [c0[0] - hx - 2, c0[1] - hy - 2, c0[0] + hx + 2, c0[1] + hy + 2];
                    if (boxes.some(function (bx) { return box[0] < bx[2] && box[2] > bx[0] && box[1] < bx[3] && box[3] > bx[1]; })) { continue; }
                    boxes.push(box);
                    placed.push(c0);
                    items.push({ x: c0[0] + origin.x, y: c0[1] + origin.y, ang: ang, T: lv.T });
                }
            }
        });
        S.labels = { key: key, an: an, items: items };
        return items;
    }
    function drawLabels(ctx, map, d, alpha) {
        var items = placeLabels(map, d);
        if (!items.length) { return; }
        ctx.save();
        ctx.font = "bold 9px 'Helvetica Neue', Helvetica, Arial, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.lineJoin = "round";
        items.forEach(function (it) {
            var c = isoColor(it.T);
            ctx.save();
            ctx.translate(it.x, it.y);
            ctx.rotate(it.ang);
            ctx.globalAlpha = alpha;
            ctx.lineWidth = 3;
            ctx.strokeStyle = "rgb(" + c.base.slice(0, 3).join(",") + ")";
            ctx.fillStyle = "rgb(" + c.line.join(",") + ")";
            ctx.strokeText(it.T + "°", 0, 0.5);
            ctx.fillText(it.T + "°", 0, 0.5);
            ctx.restore();
        });
        ctx.restore();
    }

    function fadeT() {
        if (S.prevDay === null) { return 1; }
        var t = (now() - S.fadeStart) / FADE_MS;
        return t >= 1 ? 1 : t;
    }
    function render(layer) {
        var map = layer._map, ctx = layer._ctx, w = S.week;
        if (!map || !ctx || !w) { return; }
        var t0 = now(), t = fadeT();
        if (t >= 1) { S.prevDay = null; }
        buildLut();
        hookLand();
        landChanged();
        S.interpNow = !S.interpOff && cellPx(map) >= INTERP_PX;      // interpOff: tests and tools/perf only
        if (S.prevDay !== null && S.prevDay < w.days.length) {
            drawComp(ctx, layer, S.prevDay, 1);
            drawComp(ctx, layer, S.day, t);
        } else {
            drawComp(ctx, layer, S.day, 1);
        }
        var dt = now() - t0;
        if (S.prevDay !== null) { stat(S.stats.fadeFrameMs, dt); } else { stat(S.stats.drawMs, dt); prewarm(layer); }
    }

    // After a still frame, when the browser is idle: the next and previous days' colors (and isotherm layers), so a
    // step or the animation (which wraps from the last day to the first) starts its crossfade without a long frame.
    function prewarm(layer) {
        clearTimeout(S.prewarmTimer);
        var idle = window.requestIdleCallback || function (fn) { return setTimeout(fn, 60); };
        var gen = (S.prewarmGen = (S.prewarmGen || 0) + 1);
        S.prewarmTimer = setTimeout(function () {
            var w = S.week;
            if (!w) { return; }
            var n = w.days.length, todo = [(S.day + 1) % n, (S.day + n - 1) % n, S.day];   // the day on the map last
            (function next() {                       // one day per idle slot: a slow phone takes ~40 ms for one
                if (gen !== S.prewarmGen || !todo.length) { return; }
                idle(function () {
                    if (gen !== S.prewarmGen || S.week !== w || S.prevDay !== null || layer !== S.layer || !layer._map) { return; }
                    var d = todo.shift();
                    if (d !== S.day || !todo.length) { compLayer(layer, d); }
                    next();
                });
            })();
        }, 120);
    }

    function makeLayerClass() {
        return L.Canvas.extend({
            _draw: function () {
                this._ctx.save();
                try { render(this); } catch (e) { if (window.console) { console.error("SSTEngine draw", e); } }
                this._ctx.restore();
            }
        });
    }
    function ensureLayer() {
        var map = S.map;
        if (!map || S.layer || !S.week || !window.L || !L.Canvas) { return; }
        if (!map._loaded) {                              // a map still being built (no view yet): once it has one
            if (!map._sstEngineWait) { map._sstEngineWait = true; map.whenReady(function () { if (S.map === map) { ensureLayer(); redraw(); } }); }
            return;
        }
        var pane = "sst-pane";
        if (!map.getPane(pane)) {
            pane = "sstEnginePane";
            if (!map.getPane(pane)) { map.createPane(pane).style.zIndex = 410; }
        }
        S.LayerClass = S.LayerClass || makeLayerClass();
        S.layer = new S.LayerClass({ pane: pane, padding: 0.1 });
        map.addLayer(S.layer);
        var c = S.layer._container;
        if (!c) { return; }
        c.style.pointerEvents = "none";                 // taps go to the map (SST.classify), never to the canvas
        c.style.opacity = String(S.opts.opacity);
        c.classList.add("sst-engine-canvas");
    }
    function redraw() {
        if (!S.layer || !S.layer._map) { ensureLayer(); }
        if (S.layer && S.layer._map && S.layer._ctx) { S.layer._redraw(); }
    }
    function animate() {
        cancelAnimationFrame(S.raf);
        var step = function () {
            redraw();
            if (S.prevDay !== null) { S.raf = requestAnimationFrame(step); }
        };
        S.raf = requestAnimationFrame(step);
    }

    // ------------------------------------------------------------ analysis --
    // Worker body: everything it needs is in this function's text (posted to a blob URL).
    function analysisWorker() {
        var W = null;
        function gaussian(sigma) {
            var r = Math.max(1, Math.ceil(3 * sigma)), k = new Float32Array(2 * r + 1), s = 0;
            for (var i = -r; i <= r; i++) { k[i + r] = Math.exp(-i * i / (2 * sigma * sigma)); s += k[i + r]; }
            for (i = 0; i < k.length; i++) { k[i] /= s; }
            return { k: k, r: r };
        }
        function blur(src, nx, ny, sigma) {              // separable, edges clamped
            var g = gaussian(sigma), k = g.k, r = g.r, tmp = new Float32Array(src.length), out = new Float32Array(src.length);
            var x, y, j, s, c;
            for (y = 0; y < ny; y++) {
                var row = y * nx;
                for (x = 0; x < nx; x++) {
                    s = 0;
                    for (j = -r; j <= r; j++) { c = x + j; c = c < 0 ? 0 : (c >= nx ? nx - 1 : c); s += src[row + c] * k[j + r]; }
                    tmp[row + x] = s;
                }
            }
            for (y = 0; y < ny; y++) {
                for (x = 0; x < nx; x++) {
                    s = 0;
                    for (j = -r; j <= r; j++) { c = y + j; c = c < 0 ? 0 : (c >= ny ? ny - 1 : c); s += tmp[c * nx + x] * k[j + r]; }
                    out[y * nx + x] = s;
                }
            }
            return out;
        }
        function nearLand(land, nx, ny, d) {             // within d cells (square) of a non-water cell
            var tmp = new Uint8Array(land.length), out = new Uint8Array(land.length), x, y, j, v;
            for (y = 0; y < ny; y++) {
                for (x = 0; x < nx; x++) {
                    v = 0;
                    for (j = -d; j <= d && !v; j++) { var c = x + j; if (c >= 0 && c < nx && land[y * nx + c]) { v = 1; } }
                    tmp[y * nx + x] = v;
                }
            }
            for (y = 0; y < ny; y++) {
                for (x = 0; x < nx; x++) {
                    v = 0;
                    for (j = -d; j <= d && !v; j++) { var rr = y + j; if (rr >= 0 && rr < ny && tmp[rr * nx + x]) { v = 1; } }
                    out[y * nx + x] = v;
                }
            }
            return out;
        }
        function smoothField(codes, mask, h, sigma) {     // normalized convolution: land doesn't drag values
            var n = h.nx * h.ny, F = new Float32Array(n), Wt = new Float32Array(n);
            for (var i = 0; i < n; i++) {
                if (codes[i] !== 65535) { F[i] = (h.baseT + codes[i] * h.stepT) / 10; Wt[i] = 1; }
            }
            var bf = blur(F, h.nx, h.ny, sigma), bw = blur(Wt, h.nx, h.ny, sigma);
            for (i = 0; i < n; i++) { bf[i] = (bw[i] > 0.3 && Wt[i]) ? bf[i] / bw[i] : NaN; }
            return bf;
        }
        function breaks(codes, mask, h, land3) {
            var nx = h.nx, ny = h.ny, n = nx * ny;
            var F = smoothField(codes, mask, h, h.whole ? 2.5 : 1.0), A = new Float32Array(n);
            var dy = h.dlat * 111.2;
            for (var y = 1; y < ny - 1; y++) {
                var lat = h.latN - y * h.dlat, dx = h.dlon * 111.32 * Math.cos(lat * Math.PI / 180);
                for (var x = 1; x < nx - 1; x++) {
                    var i = y * nx + x;
                    if (land3[i]) { continue; }
                    var a = F[i - 1], b = F[i + 1], c = F[i - nx], d = F[i + nx];
                    if (a !== a || b !== b || c !== c || d !== d) { continue; }
                    var g = Math.hypot((b - a) / (2 * dx), (c - d) / (2 * dy));
                    var t = (g - 0.27) / (0.54 - 0.27);
                    if (t > 0) { A[i] = Math.pow(t > 1 ? 1 : t, 0.8); }
                }
            }
            var B = blur(A, nx, ny, 0.8), out = new Uint8Array(n);
            for (var k = 0; k < n; k++) { if (!land3[k] && B[k] > 0.004) { out[k] = Math.round(255 * 0.62 * Math.min(1, B[k])); } }
            return out;
        }
        function isotherms(codes, mask, h, land2) {
            var nx = h.nx, ny = h.ny, F = smoothField(codes, mask, h, h.whole ? 3.0 : 1.3);
            for (var i = 0; i < F.length; i++) { if (land2[i]) { F[i] = NaN; } }
            var levels = {};                              // T -> {seg: [eA, eB, ...], pts: Map edge -> [x, y]}
            function lvl(T) { return levels[T] || (levels[T] = { seg: [], pts: new Map() }); }
            function pt(L, e, x, y) { if (!L.pts.has(e)) { L.pts.set(e, [x, y]); } }
            for (var r = 0; r < ny - 1; r++) {
                for (var c = 0; c < nx - 1; c++) {
                    var i0 = r * nx + c, tl = F[i0], tr = F[i0 + 1], bl = F[i0 + nx], br = F[i0 + nx + 1];
                    if (tl !== tl || tr !== tr || bl !== bl || br !== br) { continue; }
                    var lo = Math.min(tl, tr, bl, br), hi = Math.max(tl, tr, bl, br);
                    for (var T = Math.ceil(lo); T < hi; T++) {
                        var k = (tl > T ? 8 : 0) | (tr > T ? 4 : 0) | (br > T ? 2 : 0) | (bl > T ? 1 : 0);
                        if (k === 0 || k === 15) { continue; }
                        var L = lvl(T);
                        var eT = 2 * i0, eB = 2 * (i0 + nx), eL = 2 * i0 + 1, eR = 2 * (i0 + 1) + 1;
                        var cross = function (e) {
                            if (e === eT) { pt(L, e, c + (T - tl) / (tr - tl), r); }
                            else if (e === eB) { pt(L, e, c + (T - bl) / (br - bl), r + 1); }
                            else if (e === eL) { pt(L, e, c, r + (T - tl) / (bl - tl)); }
                            else { pt(L, e, c + 1, r + (T - tr) / (br - tr)); }
                            return e;
                        };
                        var segs;
                        switch (k) {
                            case 1: case 14: segs = [eL, eB]; break;
                            case 2: case 13: segs = [eB, eR]; break;
                            case 3: case 12: segs = [eL, eR]; break;
                            case 4: case 11: segs = [eT, eR]; break;
                            case 6: case 9: segs = [eT, eB]; break;
                            case 7: case 8: segs = [eL, eT]; break;
                            case 5: segs = ((tl + tr + bl + br) / 4 > T) ? [eL, eT, eB, eR] : [eT, eR, eL, eB]; break;
                            case 10: segs = ((tl + tr + bl + br) / 4 > T) ? [eT, eR, eL, eB] : [eL, eT, eB, eR]; break;
                        }
                        for (var s = 0; s < segs.length; s += 2) { L.seg.push(cross(segs[s]), cross(segs[s + 1])); }
                    }
                }
            }
            var R = 6378137, out = [];
            function wx(col) { return 256 * (h.lonW + col * h.dlon + 180) / 360; }
            function wy(row) {
                var lat = h.latN - row * h.dlat;
                return 256 * (0.5 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / (2 * Math.PI));
            }
            Object.keys(levels).map(Number).sort(function (a, b) { return a - b; }).forEach(function (T) {
                var L = levels[T], seg = L.seg, ns = seg.length / 2, adj = new Map(), used = new Uint8Array(ns);
                for (var q = 0; q < ns; q++) {
                    for (var e = 0; e < 2; e++) {
                        var id = seg[2 * q + e], a = adj.get(id);
                        if (a) { a.push(q); } else { adj.set(id, [q]); }
                    }
                }
                var coords = [], starts = [], counts = [], lens = [];
                for (q = 0; q < ns; q++) {
                    if (used[q]) { continue; }
                    used[q] = 1;
                    var chain = [seg[2 * q], seg[2 * q + 1]];
                    for (var dir = 0; dir < 2; dir++) {           // extend forward from the end, then the start
                        for (;;) {
                            var endE = dir === 0 ? chain[chain.length - 1] : chain[0];
                            var nb = adj.get(endE), nxt = -1;
                            for (var t2 = 0; nb && t2 < nb.length; t2++) { if (!used[nb[t2]]) { nxt = nb[t2]; break; } }
                            if (nxt < 0) { break; }
                            used[nxt] = 1;
                            var other = seg[2 * nxt] === endE ? seg[2 * nxt + 1] : seg[2 * nxt];
                            if (dir === 0) { chain.push(other); } else { chain.unshift(other); }
                        }
                    }
                    var start = coords.length / 2, len = 0, px = 0, py = 0;
                    for (var v = 0; v < chain.length; v++) {
                        var p = L.pts.get(chain[v]), X = wx(p[0]), Y = wy(p[1]);
                        if (v) { len += Math.hypot(X - px, Y - py); }
                        coords.push(X, Y);
                        px = X; py = Y;
                    }
                    starts.push(start); counts.push(chain.length); lens.push(len);
                }
                out.push({ T: T, coords: new Float32Array(coords), starts: new Uint32Array(starts),
                           counts: new Uint32Array(counts), lens: new Float32Array(lens) });
            });
            return out;
        }
        function analyze(d, want) {
            var t0 = Date.now(), h = W.h, n = h.nx * h.ny, land = new Uint8Array(n);
            for (var i = 0; i < n; i++) { land[i] = W.mask[i] !== 0 ? 1 : 0; }
            if (!W.land2) { W.land2 = nearLand(land, h.nx, h.ny, 2); W.land3 = nearLand(land, h.nx, h.ny, 3); }
            var res = { id: W.id, day: d, breakA: null, iso: null };
            if (want.breaks) { res.breakA = breaks(W.days[d], W.mask, h, W.land3); }
            if (want.iso) { res.iso = isotherms(W.days[d], W.mask, h, W.land2); }
            res.ms = Date.now() - t0;
            return res;
        }
        var handle = function (msg) {
            if (msg.cmd === "week") { W = msg; return null; }
            if (msg.cmd === "day" && W && msg.id === W.id) { return analyze(msg.day, msg.want); }
            return null;
        };
        if (typeof self !== "undefined" && typeof window === "undefined") {
            self.onmessage = function (ev) {
                var res = handle(ev.data);
                if (!res) { return; }
                var tr = [];
                if (res.breakA) { tr.push(res.breakA.buffer); }
                (res.iso || []).forEach(function (lv) { tr.push(lv.coords.buffer, lv.starts.buffer, lv.counts.buffer, lv.lens.buffer); });
                self.postMessage(res, tr);
            };
        }
        return handle;
    }

    function startWorker() {
        if (S.worker || S.workerFailed) { return; }
        try {
            var src = "(" + analysisWorker.toString() + ")();";
            var url = URL.createObjectURL(new Blob([src], { type: "application/javascript" }));
            S.worker = new Worker(url);
            S.worker.onmessage = function (ev) { gotAnalysis(ev.data); };
            S.worker.onerror = function () { S.worker = null; S.workerFailed = true; };
        } catch (e) {
            S.worker = null;
            S.workerFailed = true;                      // fall back to the main thread, a day at a time
        }
    }
    function sendWeek() {
        var w = S.week;
        if (!w) { return; }
        var msg = { cmd: "week", id: S.gen, h: w.h, mask: w.mask, days: w.days };
        if (S.worker) { S.worker.postMessage(msg); }
        else { S.localHandle = S.localHandle || analysisWorker(); S.localHandle(msg); }
        S.weekSent = S.gen;
    }
    function want() { return { breaks: !!S.opts.breaks, iso: !!S.opts.isotherms }; }
    function requestAnalysis() {
        var w = S.week, wn = want();
        if (!w || (!wn.breaks && !wn.iso)) { return; }
        startWorker();
        if (S.weekSent !== S.gen) { sendWeek(); }      // the worker gets the week once, when first needed
        var order = [S.day];
        for (var k = 1; k < w.days.length; k++) {      // the day on the map, then outward
            if (S.day + k < w.days.length) { order.push(S.day + k); }
            if (S.day - k >= 0) { order.push(S.day - k); }
        }
        order.forEach(function (d) {
            var have = S.analysis[d], q = S.queued[d];
            var need = { breaks: wn.breaks && !(have && have.breakA) && !(q && q.breaks),
                         iso: wn.iso && !(have && have.iso) && !(q && q.iso) };
            if (!need.breaks && !need.iso) { return; }
            S.queued[d] = { breaks: need.breaks || (q && q.breaks), iso: need.iso || (q && q.iso) };
            if (S.worker) {
                S.worker.postMessage({ cmd: "day", id: S.gen, day: d, want: need });
            } else {
                var gen = S.gen;
                setTimeout(function () {
                    if (gen !== S.gen) { return; }
                    if (!S.localHandle || S.weekSent !== gen) { sendWeek(); }
                    gotAnalysis(S.localHandle({ cmd: "day", id: gen, day: d, want: need }));
                }, 0);
            }
        });
    }
    function gotAnalysis(res) {
        if (!res || res.id !== S.gen || !S.week) { return; }
        var a = S.analysis[res.day] || (S.analysis[res.day] = {});
        if (res.breakA) { a.breakA = res.breakA; }
        if (res.iso) { a.iso = res.iso; a.paths = null; }
        S.stats.analysisMs[res.day] = res.ms;
        var q = S.queued[res.day];
        if (q) {
            if (res.breakA) { q.breaks = false; }
            if (res.iso) { q.iso = false; }
        }
        if (res.day === S.day || res.day === S.prevDay) { redraw(); }
    }

    // ------------------------------------------------------------- the API --
    function install(w, payload, url) {
        S.week = w;
        S.payload = payload;
        S.url = url;
        S.img = {}; S.imgOrder = []; S.analysis = {}; S.queued = {}; S.labels = null; S.comp = null;
        S.plan = null; S.disp = {}; S.interp = null; S.ival = null; S.landGrid = null;
        landChanged();
        var hs = histogramOf(w);
        S.hist = hs.hist;
        S.histTotal = hs.total;
        S.auto = pctRange(hs.hist, hs.total, AUTO_MIN_SPAN, w.h);
        S.lutSig = "";
        var d = S.wantDay !== null ? S.wantDay : w.days.length - 1;
        S.day = clamp(d | 0, 0, w.days.length - 1);
        S.prevDay = null;
        buildLut();
        ensureLayer();
        redraw();
        requestAnalysis();
        var st = state();
        fire("ready", st);
        fire("change", st);
    }

    function setWeek(payload) {
        if (!payload) { return; }
        var grid = payload.grid || {}, url = grid.url || null;
        if (S.week && url && url === S.url) {        // the same week again (new meta, or back on the page): keep it
            S.payload = payload;
            ensureLayer();
            redraw();
            setTimeout(function () { if (S.url === url) { fire("ready", state()); } }, 0);
            return;
        }
        var gen = ++S.gen;
        var t0 = now();
        var got = grid.data ? Promise.resolve(b64buf(grid.data))
            : fetch(url, { credentials: "same-origin" }).then(function (r) {
                if (!r.ok) { throw new Error("HTTP " + r.status); }
                return r.arrayBuffer();
            });
        got.then(inflateIfGzip).then(function (buf) {
            if (gen !== S.gen) { return; }
            S.stats.bytes = buf.byteLength;
            S.stats.fetchMs = Math.round(now() - t0);
            install(decode(buf), payload, url);
        }).catch(function (err) {
            if (gen !== S.gen) { return; }
            fire("error", err instanceof Error ? err : new Error(String(err)));
        });
    }

    function reducedMotion() {
        try { return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); }
        catch (e) { return false; }
    }
    function setDay(i) {
        i = +i;
        if (!isFinite(i)) { return; }
        S.wantDay = i | 0;
        var w = S.week;
        if (!w) { return; }
        i = clamp(i | 0, 0, w.days.length - 1);
        if (i === S.day && S.prevDay === null) { return; }
        if (i !== S.day && S.layer && S.layer._map && !reducedMotion()) {
            S.prevDay = S.day;
            S.fadeStart = now();
            S.day = i;
            animate();
        } else {
            S.prevDay = null;
            S.day = i;
            redraw();
        }
        requestAnalysis();
        fire("day", { day: S.day, date: (S.payload && S.payload.dates || [])[S.day] || null });
    }

    function changed() {
        buildLut();
        redraw();
        fire("change", state());
    }
    function setPalette(name) {
        if (!STOPS[name] || name === S.palette) { return; }
        S.palette = name;
        changed();
    }
    function setRange(r) {
        r = r || {};
        if (r.mode === "fixed") {
            var lo = +r.min, hi = +r.max;
            if (!isFinite(lo) || !isFinite(hi) || hi <= lo) { return; }
            S.range = { mode: "fixed", min: lo, max: hi };
        } else {
            S.range = { mode: "auto" };
        }
        changed();
    }
    function setOptions(o) {
        o = o || {};
        var redo = false;
        ["stepped", "isotherms", "breaks"].forEach(function (k) {      // "haze": ignored (no cloud layer)
            if (k in o && !!o[k] !== S.opts[k]) { S.opts[k] = !!o[k]; redo = true; }
        });
        if ("opacity" in o && isFinite(+o.opacity)) {
            S.opts.opacity = clamp(+o.opacity, 0, 1);
            if (S.layer && S.layer._container) { S.layer._container.style.opacity = String(S.opts.opacity); }
        }
        if (redo) { S.labels = null; requestAnalysis(); }
        changed();
    }

    function cellAt(lat, lon) {
        var w = S.week;
        if (!w || !isFinite(lat) || !isFinite(lon)) { return -1; }
        var h = w.h;
        if (lat > h.edgeN || lat < h.edgeS || lon < h.edgeW || lon > h.edgeE) { return -1; }
        var r = clamp(Math.round((h.latN - lat) / h.dlat), 0, h.ny - 1);
        var c = clamp(Math.round((lon - h.lonW) / h.dlon), 0, h.nx - 1);
        return r * h.nx + c;
    }
    function tempOf(d, i) {
        var code = S.week.days[d][i];
        if (code === NODATA) { return null; }
        var h = S.week.h;
        return (h.baseT + code * h.stepT) / 10;
    }
    function valueAt(lat, lon) {
        var i = cellAt(lat, lon);
        if (i < 0) { return null; }
        var f = tempOf(S.day, i);
        if (f === null) { return null; }
        return { f: f, day: S.day, date: (S.payload && S.payload.dates || [])[S.day] || null, estimate: null };
    }
    // A point the coast fill colors (water near shore MUR has no value for): the tap card says so instead of
    // "No data here". Never a temperature: the fill is for drawing only.
    function nearShore(lat, lon) {
        var i = cellAt(lat, lon);
        if (i < 0 || !S.fillOn || !S.plan || S.week.mask[i] === 0) { return false; }
        var g = S.landGrid;
        return S.plan.ring[i] > 0 && S.plan.ring[i] !== 255 && (!g || g[i] < 128);
    }
    function weekValuesAt(lat, lon) {
        var w = S.week;
        if (!w) { return []; }
        var i = cellAt(lat, lon), out = [];
        for (var d = 0; d < w.days.length; d++) { out.push(i < 0 ? null : tempOf(d, i)); }
        return out;
    }

    function histogram(o) {
        o = o || {};
        var r = effectiveRange(), lo = isFinite(o.min) ? +o.min : r.min, hi = isFinite(o.max) ? +o.max : r.max;
        var step = o.step || ((hi - lo) <= 24 ? 0.5 : 1);
        var nb = Math.max(1, Math.round((hi - lo) / step)), bins = new Array(nb).fill(0), below = 0, above = 0;
        var w = S.week;
        if (w && S.hist) {
            for (var c = 0; c < S.hist.length; c++) {
                var cnt = S.hist[c];
                if (!cnt) { continue; }
                var T = (w.h.baseT + c * w.h.stepT) / 10;
                if (T < lo) { below += cnt; }
                else if (T >= hi) { above += cnt; }
                else { bins[Math.min(nb - 1, Math.floor((T - lo) / step + 1e-9))] += cnt; }
            }
        }
        return { min: lo, max: hi, step: step, bins: bins, below: below, above: above, total: S.histTotal || 0 };
    }

    function fitToView() {
        var w = S.week, map = S.map;
        if (!w || !map) { return null; }
        var b = map.getBounds(), h = w.h;
        var r0 = clamp(Math.floor((h.latN - b.getNorth()) / h.dlat), 0, h.ny - 1);
        var r1 = clamp(Math.ceil((h.latN - b.getSouth()) / h.dlat), 0, h.ny - 1);
        var c0 = clamp(Math.floor((b.getWest() - h.lonW) / h.dlon), 0, h.nx - 1);
        var c1 = clamp(Math.ceil((b.getEast() - h.lonW) / h.dlon), 0, h.nx - 1);
        var hist = new Uint32Array(MAX_CODES), total = 0;
        w.days.forEach(function (codes) {
            for (var r = r0; r <= r1; r++) {
                for (var c = c0; c <= c1; c++) {
                    var v = codes[r * h.nx + c];
                    if (v !== NODATA) { hist[v < MAX_CODES ? v : MAX_CODES - 1]++; total++; }
                }
            }
        });
        if (!total) { return null; }
        var rg = pctRange(hist, total, FIT_MIN_SPAN, h);
        S.range = { mode: "fixed", min: rg.min, max: rg.max };
        changed();
        return { min: rg.min, max: rg.max };
    }

    function state() {
        var w = S.week, p = S.payload || {}, r = effectiveRange();
        return {
            ready: !!w,
            week: w ? { url: S.url, dates: p.dates || [], nd: w.days.length, nx: w.h.nx, ny: w.h.ny,
                        bounds: [[w.h.edgeS, w.h.edgeW], [w.h.edgeN, w.h.edgeE]], precision: w.h.whole ? 1 : 0.1,
                        coverage: (p.grid || {}).coverage || null } : null,
            dates: p.dates || [],
            day: S.day,
            palette: S.palette,
            range: S.range.mode === "fixed" ? { mode: "fixed", min: S.range.min, max: S.range.max } : { mode: "auto" },
            vmin: r.min, vmax: r.max,
            auto: S.auto ? { min: S.auto.min, max: S.auto.max } : null,
            options: { stepped: S.opts.stepped, isotherms: S.opts.isotherms, breaks: S.opts.breaks,
                       opacity: S.opts.opacity },
            // the coast (docs/coast-progress.md): coastline in, fill under it (rings of cells), the land level drawn,
            // and whether the view is interpolated (zoomed past the grid)
            coast: { land: !!(window.SSTLand && window.SSTLand.ready()), fill: S.fillOn && !!S.plan,
                     rings: S.plan ? S.plan.rings : 0, cells: S.plan ? S.plan.cells.length : 0,
                     level: S.landCache ? S.landCache.level : null },
            interp: S.interpNow
        };
    }

    function detach() {
        cancelAnimationFrame(S.raf);
        if (S.layer && S.layer._map) { try { S.layer._map.removeLayer(S.layer); } catch (e) { /* gone */ } }
        S.layer = null;
        S.map = null;
        S.labels = null;
    }
    function attach(map) {
        if (!map || S.map === map) { return; }
        detach();
        S.map = map;
        map.on("unload", function () { if (S.map === map) { detach(); } });
        try { hookLand(); } catch (e) { if (window.console) { console.error("SSTEngine coastline", e); } }
        try { ensureLayer(); redraw(); } catch (e) { if (window.console) { console.error("SSTEngine attach", e); } }
    }

    // Also attach by itself to the "sst-map" map, so the engine works whichever script loads first (sst.js calls
    // attach too; both are idempotent).
    (function hook() {
        var L = window.L;
        if (!L || !L.Map || !L.Map.addInitHook) { setTimeout(hook, 50); return; }
        if (L._gotoneSSTEngine) { return; }
        L._gotoneSSTEngine = true;
        L.Map.addInitHook(function () {
            var map = this, c = map.getContainer && map.getContainer();
            if (c && c.id === "sst-map") { map.whenReady(function () { attach(map); }); }
        });
    })();

    window.SSTEngine = {
        attach: attach,
        setWeek: setWeek,
        setDay: setDay,
        setPalette: setPalette,
        setRange: setRange,
        fitToView: fitToView,
        setOptions: setOptions,
        valueAt: valueAt,
        weekValuesAt: weekValuesAt,
        nearShore: nearShore,
        histogram: histogram,
        on: on,
        getRange: getRange,
        state: state,
        colorFor: colorFor,
        palettes: PALETTES,
        _stats: S.stats,
        _state: S,
        _debug: { landLayer: landLayer, drawLand: drawLand, compLayer: compLayer, interpImage: interpImage,
                  dispCodes: dispCodes },
        _setup: true
    };
})();
