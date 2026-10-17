/*
 * GotOne Offshore SST: the look and the color controls (window.SSTLook), sprint 2 lane V.
 *
 * Dash serves every /assets/*.js on every page; this stays inert until the
 * /sst map exists (window.SST._state.map, set up by assets/sst.js).
 *
 *   The color panel (sidebar on laptops, inside the drawer on phones and
 *   tablets): range chips Auto / Tuna / Marlin / Gulf Stream / Custom (with a
 *   min/max editor), Fit to view, and the toggles Stepped, Breaks, Haze and
 *   Depth lines; the numbered color bar with the week's histogram. It is built
 *   into the empty Dash div #sst-color-panel (layout/sidebar.py; Dash never
 *   renders children there, as with labs.js's Helpful div).
 *   The map legend: #sst-legend-live (layout/mapview.py), a compact color bar
 *   that says which range is on.
 *   The depth contours: assets/sst/contours.json.gz (tools/build_contours.py,
 *   served by app.py /api/sst/contours.json), drawn on our own canvas in a pane
 *   above the SST (z 430) and below the place names (440) and spots (450),
 *   with labels placed along straight stretches.
 *   Spot labels (review R6): names beside the spot circles from zoom 8
 *   (canyons) and 9 (all), on a canvas above the circles (z 455), never on top
 *   of each other or of the map's controls.
 *
 * The colors themselves are drawn by the SST engine (window.SSTEngine, lane E,
 * assets/sstengine.js), through the API agreed in the sprint brief:
 *   setRange({mode: "auto"|"fixed", min, max}), fitToView() -> {min, max},
 *   setOptions({stepped, isotherms, breaks, haze}), histogram() -> {min, max,
 *   bins}, valueAt(lat, lon) -> {f, day, estimate} | null, on("ready"|"change"|"day", fn).
 * Optional extras used when present: getRange() -> {mode, min, max} (else the
 * range in a "change" event, else the panel's own rule from the histogram) and
 * colorFor(f) -> [r, g, b] (else the spectrum stops below). Until an engine is
 * on the page the color part of the panel and the live legend stay hidden
 * (html.sst-engine-on, assets/sstlook.css) and the server-drawn legend shows.
 *
 * Choices are remembered per browser (gotone-sst-colors-v1). Feature use is
 * counted only when the reader changes something (GotOneLabs.event), never for
 * the defaults or a restored choice.
 */
(function () {
    "use strict";
    if (window.SSTLook && window.SSTLook._setup) { return; }

    var PREF_KEY = "gotone-sst-colors-v1";
    // The spectrum (design-notes.md "Palette"): Turbo from 5% to 100%, 13 even stops.
    var STOPS = ["#3d358b", "#476ee6", "#3ba0fd", "#1bd0d5", "#2aefa1", "#75fe5c", "#b4f836",
                 "#e3db38", "#fcb136", "#f9751d", "#e14109", "#b71d02", "#7a0403"];
    var PRESETS = [
        { id: "auto", label: "Auto" },
        { id: "tuna", label: "Tuna", min: 62, max: 76 },
        { id: "marlin", label: "Marlin", min: 70, max: 84 },
        { id: "gulfstream", label: "Gulf Stream" },
        { id: "custom", label: "Custom" }
    ];
    var EVENT = { auto: "preset_auto", tuna: "preset_tuna", marlin: "preset_marlin",
                  gulfstream: "preset_gulfstream", custom: "preset_custom" };
    var LIMITS = [28, 90], MIN_SPAN = 2;              // Custom: 1 F steps between these
    var GULF_SPAN = 18, GULF_FALLBACK = { min: 64, max: 84 };
    var AUTO_MIN_SPAN = 8;
    var DEFAULTS = { preset: "auto", custom: null, view: null,
                     stepped: false, breaks: false, haze: true, contours: true };
    var TOGGLES = [
        { id: "stepped", label: "Stepped", hint: "1 °F color bands", engine: true },
        { id: "breaks", label: "Breaks", hint: "lines every degree, bright where it changes fast", engine: true },
        { id: "haze", label: "Haze", hint: "where clouds hid the water", engine: true },
        { id: "contours", label: "Depth lines", hint: "30, 40, 100 fathoms; 500, 1000, 2000 m", engine: false }
    ];
    var LABEL_FONT = "600 12px \"Helvetica Neue\", Helvetica, Arial, sans-serif";
    var DEPTH_FONT = "700 10px \"Helvetica Neue\", Helvetica, Arial, sans-serif";
    var DEPTH_ATTR = 'Depth: <a href="https://www.ncei.noaa.gov/products/etopo-global-relief-model" ' +
                     'target="_blank" rel="noopener">NOAA ETOPO 2022</a>';
    var PAD = 0.3;                                    // canvas margin around the view, fraction of its size

    var S = {
        prefs: null, engine: null, bound: false, applied: false, ready: false,
        hist: null, histSig: "", evRange: null, setRange: null,
        map: null, panes: null, cv: {}, frame: 0, depth: null, depthLoading: false,
        spots: null, attr: false, occ: [], debug: {}
    };

    // ------------------------------------------------------------- utils --
    function $(id) { return document.getElementById(id); }
    function esc(s) {
        return String(s).replace(/[&<>"]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
        });
    }
    function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
    function track(name) {
        try { if (window.GotOneLabs) { window.GotOneLabs.event(name, { tool: "sst" }); } } catch (e) { /* never break the page */ }
    }
    function hex(h) { return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]; }
    var RGB = STOPS.map(hex);
    function ramp(t) {                                // t in [0, 1] -> [r, g, b]
        t = clamp(isFinite(t) ? t : 0, 0, 1) * (RGB.length - 1);
        var k = Math.min(RGB.length - 2, Math.floor(t)), f = t - k, a = RGB[k], b = RGB[k + 1];
        return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
    }
    function css(c) { return "rgb(" + Math.round(c[0]) + "," + Math.round(c[1]) + "," + Math.round(c[2]) + ")"; }
    function lum(c) {                                 // relative luminance, 0..1
        var v = c.map(function (x) { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
    }
    // The color the map shows for f F with range r (stepped: the band's middle).
    function colorFor(f, r) {
        var E = S.engine;
        if (E && typeof E.colorFor === "function") {
            try { var c = E.colorFor(f); if (c && c.length >= 3) { return c; } } catch (e) { /* fall back */ }
        }
        if (!r) { return null; }
        if (S.prefs.stepped) { f = Math.floor(f) + 0.5; }
        return ramp((f - r.min) / Math.max(r.max - r.min, 0.1));
    }
    function fmt(v) { return (Math.round(v * 10) / 10).toString().replace(/\.0$/, ""); }

    // ------------------------------------------------------------- prefs --
    function loadPrefs() {
        var p = {}, raw = null;
        try { raw = window.localStorage.getItem(PREF_KEY); } catch (e) { raw = null; }
        try { p = JSON.parse(raw || "{}") || {}; } catch (e) { p = {}; }
        var out = {};
        Object.keys(DEFAULTS).forEach(function (k) { out[k] = DEFAULTS[k]; });
        if (["auto", "tuna", "marlin", "gulfstream", "custom", "view"].indexOf(p.preset) >= 0) { out.preset = p.preset; }
        ["stepped", "breaks", "haze", "contours"].forEach(function (k) { if (typeof p[k] === "boolean") { out[k] = p[k]; } });
        out.custom = okRange(p.custom, true);
        out.view = okRange(p.view, false);
        if (out.preset === "view" && !out.view) { out.preset = "auto"; }
        return out;
    }
    function okRange(r, whole) {
        if (!r || !isFinite(r.min) || !isFinite(r.max)) { return null; }
        var a = +r.min, b = +r.max;
        if (whole) { a = Math.round(a); b = Math.round(b); }
        a = clamp(a, LIMITS[0], LIMITS[1] - MIN_SPAN);
        b = clamp(b, a + MIN_SPAN, LIMITS[1]);
        return { min: a, max: b };
    }
    function savePrefs() {
        try { window.localStorage.setItem(PREF_KEY, JSON.stringify(S.prefs)); } catch (e) { /* storage blocked */ }
    }
    S.prefs = loadPrefs();

    // ------------------------------------------------------ the week's water --
    // Percentile of the week's histogram (linear inside a bin).
    function pct(h, p) {
        if (!h || !h.bins || !h.bins.length) { return null; }
        var n = h.bins.length, w = (h.max - h.min) / n, total = 0, i;
        for (i = 0; i < n; i++) { total += h.bins[i] || 0; }
        if (!total) { return null; }
        var target = p * total, cum = 0;
        for (i = 0; i < n; i++) {
            var c = h.bins[i] || 0;
            if (cum + c >= target) { return h.min + w * (i + (c ? (target - cum) / c : 0)); }
            cum += c;
        }
        return h.max;
    }
    // design-notes.md "Range presets": Auto = 5th to 95th percentile of the coverage area over the
    // week, rounded outward, at least 8 F (the engine's own rule; this is only the fallback for the label).
    function autoFromHist(h) {
        var a = pct(h, 0.05), b = pct(h, 0.95);
        if (a === null || b === null) { return null; }
        a = Math.floor(a); b = Math.ceil(b);
        if (b - a < AUTO_MIN_SPAN) { var mid = (a + b) / 2; a = Math.floor(mid - AUTO_MIN_SPAN / 2); b = a + AUTO_MIN_SPAN; }
        return { min: a, max: b };
    }
    // Gulf Stream: top = the week's 99.5th percentile rounded up, bottom 18 F below it.
    function gulfFromHist(h) {
        var top = pct(h, 0.995);
        if (top === null) { return { min: GULF_FALLBACK.min, max: GULF_FALLBACK.max }; }
        top = Math.ceil(top);
        return { min: top - GULF_SPAN, max: top };
    }
    function histogram() {
        var E = S.engine, h = null;
        if (E && typeof E.histogram === "function") {
            try { h = E.histogram(); } catch (e) { h = null; }
        }
        return h && h.bins && h.bins.length && isFinite(h.min) && isFinite(h.max) && h.max > h.min ? h : null;
    }

    // The range the reader asked for, in the engine's terms.
    function wantRange() {
        var p = S.prefs;
        switch (p.preset) {
            case "tuna": case "marlin":
                var d = PRESETS.filter(function (x) { return x.id === p.preset; })[0];
                return { mode: "fixed", min: d.min, max: d.max };
            case "gulfstream":
                var g = gulfFromHist(S.hist);
                return { mode: "fixed", min: g.min, max: g.max };
            case "custom":
                var c = p.custom || DEFAULT_CUSTOM();
                return { mode: "fixed", min: c.min, max: c.max };
            case "view":
                return { mode: "fixed", min: p.view.min, max: p.view.max };
            default:
                return { mode: "auto" };
        }
    }
    function DEFAULT_CUSTOM() {
        var r = shownRange();
        return okRange(r ? { min: Math.floor(r.min), max: Math.ceil(r.max) } : { min: 62, max: 76 }, true);
    }
    // The range the map shows now.
    function shownRange() {
        var E = S.engine, r = null;
        if (E && typeof E.getRange === "function") {
            try { r = E.getRange(); } catch (e) { r = null; }
            if (r && isFinite(r.min) && isFinite(r.max)) { return { min: +r.min, max: +r.max }; }
        }
        if (S.prefs.preset !== "auto" && S.setRange && S.setRange.mode === "fixed") {
            return { min: S.setRange.min, max: S.setRange.max };
        }
        if (S.evRange && isFinite(S.evRange.min) && isFinite(S.evRange.max)) { return { min: +S.evRange.min, max: +S.evRange.max }; }
        return autoFromHist(S.hist);
    }

    // ------------------------------------------------------- engine glue --
    function sendRange() {
        var E = S.engine;
        if (!E) { return; }
        var r = wantRange();
        if (S.setRange && S.setRange.mode === r.mode && S.setRange.min === r.min && S.setRange.max === r.max) { return; }
        S.setRange = r;
        try { E.setRange(r); } catch (e) { /* engine not ready for it yet */ }
    }
    function sendOptions() {
        var E = S.engine, p = S.prefs;
        if (!E || typeof E.setOptions !== "function") { return; }
        try { E.setOptions({ stepped: p.stepped, isotherms: p.breaks, breaks: p.breaks, haze: p.haze }); } catch (e) { /* ignore */ }
    }
    function onEngine(ev) {
        if (ev && typeof ev === "object") {
            var r = ev.range || (ev.min !== undefined && ev.max !== undefined ? ev : null);
            if (r && isFinite(r.min) && isFinite(r.max)) { S.evRange = { min: +r.min, max: +r.max }; }
        }
        var h = histogram();
        var sig = h ? [h.min, h.max, h.bins.length, h.bins.reduce(function (a, b) { return a + (b || 0); }, 0)].join("|") : "";
        var newWeek = sig !== S.histSig;
        S.hist = h;
        S.histSig = sig;
        if (h && !S.ready) {
            S.ready = true;
            document.documentElement.classList.add("sst-engine-on");
        }
        if (!S.applied && S.ready) {                  // the reader's remembered choices, once (not counted)
            S.applied = true;
            S.setRange = null;
            sendOptions();
        }
        if (newWeek && S.prefs.preset === "gulfstream") { S.setRange = null; }
        if (S.ready) { sendRange(); }
        render();
        schedule();                                   // depth / spot labels pick up the new colors
    }
    function bindEngine() {
        var E = window.SSTEngine;
        if (!E || typeof E.setRange !== "function" || typeof E.on !== "function") { return false; }
        if (S.engine === E) { return true; }
        S.engine = E;
        S.applied = false;
        S.setRange = null;
        try {
            E.on("ready", function (ev) { S.applied = false; onEngine(ev); });
            E.on("change", onEngine);
            E.on("day", function () { schedule(); });
        } catch (e) { /* an engine without events: polled below */ }
        onEngine();
        return true;
    }
    (function pollEngine() {
        if (!bindEngine()) { setTimeout(pollEngine, 250); return; }
        // An engine that has no data yet fires "ready" later; until then check now and then.
        (function waitData() {
            if (S.ready || S.engine !== window.SSTEngine) { return; }
            onEngine();
            setTimeout(waitData, 500);
        })();
    })();

    // ------------------------------------------------------- user actions --
    function pickPreset(id, viaKey) {
        var p = S.prefs;
        if (id === "custom" && !p.custom) { p.custom = DEFAULT_CUSTOM(); }
        if (p.preset === id && id !== "custom") { return; }
        p.preset = id;
        savePrefs();
        track(EVENT[id]);
        sendRange();
        render(viaKey ? id : null);
        schedule();
    }
    function stepCustom(end, delta) {
        var p = S.prefs, c = p.custom || DEFAULT_CUSTOM();
        var a = c.min, b = c.max;
        if (end === "min") { a = clamp(a + delta, LIMITS[0], b - MIN_SPAN); }
        else { b = clamp(b + delta, a + MIN_SPAN, LIMITS[1]); }
        if (a === c.min && b === c.max) { return; }
        p.custom = { min: a, max: b };
        p.preset = "custom";
        savePrefs();
        track("custom_range");
        sendRange();
        render();
        schedule();
    }
    function fitView() {
        var E = S.engine, r = null;
        if (!E || typeof E.fitToView !== "function") { return; }
        try { r = E.fitToView(); } catch (e) { r = null; }
        r = r && isFinite(r.min) && isFinite(r.max) ? { min: +r.min, max: +r.max } : null;
        if (!r) { return; }
        S.prefs.preset = "view";
        S.prefs.view = r;
        S.setRange = { mode: "fixed", min: r.min, max: r.max };   // the engine set it already
        savePrefs();
        track("fit_view");
        render();
        schedule();
    }
    function toggle(id, on) {
        if (S.prefs[id] === on) { return; }
        S.prefs[id] = on;
        savePrefs();
        track(id);
        if (id === "contours") { depthVisible(); } else { sendOptions(); }
        render();
        schedule();
    }

    // -------------------------------------------------------------- panel --
    function rangeNote(r) {
        var p = S.prefs.preset;
        if (p === "auto") { return "auto: 5 to 95% of this week's water"; }
        if (p === "tuna") { return "Tuna preset"; }
        if (p === "marlin") { return "Marlin preset"; }
        if (p === "gulfstream") { return "Gulf Stream preset: the week's warmest water and " + GULF_SPAN + "° below"; }
        if (p === "custom") { return "your range"; }
        if (p === "view") { return "fitted to the map view"; }
        return "";
    }
    function scaleWord() {
        return { auto: "auto range", tuna: "Tuna preset", marlin: "Marlin preset", gulfstream: "Gulf Stream preset",
                 custom: "your range", view: "fit to view" }[S.prefs.preset] || "";
    }
    function tickStep(px, span) {
        var steps = [1, 2, 4, 5, 10, 20];
        for (var i = 0; i < steps.length; i++) { if (steps[i] * px / span >= 22) { return steps[i]; } }
        return 20;
    }
    // The color bar: an optional histogram above it, triangles for "and beyond", numbers every few degrees.
    function barHtml(r, width, withHist) {
        var span = Math.max(r.max - r.min, 0.1);
        var stops = [], k, f;
        if (S.prefs.stepped) {
            var a = Math.floor(r.min), b = Math.ceil(r.max);
            for (k = a; k < b; k++) {
                var c = css(colorFor(k + 0.5, r));
                var p0 = clamp((k - r.min) / span * 100, 0, 100), p1 = clamp((k + 1 - r.min) / span * 100, 0, 100);
                stops.push(c + " " + p0.toFixed(2) + "%", c + " " + p1.toFixed(2) + "%");
            }
        } else {
            for (k = 0; k <= 24; k++) {
                f = r.min + span * k / 24;
                stops.push(css(colorFor(f, r)) + " " + (k / 24 * 100).toFixed(1) + "%");
            }
        }
        var lo = css(colorFor(r.min - 1, r)), hi = css(colorFor(r.max + 1, r));
        var inner = Math.max(60, width - 22);
        var step = tickStep(inner, span), ticks = "";
        for (k = Math.ceil(r.min); k <= Math.floor(r.max); k++) {
            var x = (k - r.min) / span * 100;
            var major = k % step === 0;
            if (!major && inner / span < 5) { continue; }
            ticks += '<span class="sstc-tick' + (major ? " major" : "") + '" style="left:' + x.toFixed(2) + '%"></span>';
            if (major && x > 3 && x < 97) {
                ticks += '<span class="sstc-num" style="left:' + x.toFixed(2) + '%">' + k + "</span>";
            }
        }
        var hist = "";
        if (withHist && S.hist) {
            var h = S.hist, n = h.bins.length, bw = (h.max - h.min) / n;
            var cols = Math.max(20, Math.min(90, Math.round(inner / 3))), acc = [];
            for (k = 0; k < cols; k++) { acc.push(0); }
            for (k = 0; k < n; k++) {
                var cen = h.min + bw * (k + 0.5);
                if (cen < r.min || cen > r.max) { continue; }
                var j = Math.min(cols - 1, Math.floor((cen - r.min) / span * cols));
                acc[j] += h.bins[k] || 0;
            }
            // a light smoothing (Gaussian, 0.4 F): whole-degree weeks would otherwise draw a comb
            var sg = 0.4 / (span / cols), rad = Math.ceil(sg * 2.5), sm = [];
            for (k = 0; k < cols; k++) {
                var sum = 0, wsum = 0;
                for (var q2 = -rad; q2 <= rad; q2++) {
                    if (k + q2 < 0 || k + q2 >= cols) { continue; }
                    var wt = Math.exp(-q2 * q2 / (2 * sg * sg));
                    sum += acc[k + q2] * wt; wsum += wt;
                }
                sm.push(wsum ? sum / wsum : 0);
            }
            acc = sm;
            var top = Math.max.apply(null, acc) || 1, pts = ["0,20"];
            for (k = 0; k < cols; k++) {
                var y = 20 - acc[k] / top * 18;
                pts.push(((k + 0.5) / cols * 100).toFixed(2) + "," + y.toFixed(1));
            }
            pts.push("100,20");
            hist = '<svg class="sstc-hist" viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden="true">' +
                   '<polygon points="' + pts.join(" ") + '"/></svg>';
        }
        return hist +
            '<div class="sstc-ramprow"><span class="sstc-tri lo" style="border-right-color:' + lo + '"></span>' +
            '<span class="sstc-ramp" style="background:linear-gradient(to right,' + stops.join(",") + ')"></span>' +
            '<span class="sstc-tri hi" style="border-left-color:' + hi + '"></span></div>' +
            '<div class="sstc-ticks">' + ticks + "</div>";
    }

    function buildPanel(el) {
        var chips = PRESETS.map(function (p) {
            return '<button type="button" class="sstc-chip" role="radio" data-p="' + p.id + '">' + esc(p.label) + "</button>";
        }).join("");
        function stepper(end, label) {
            return '<div class="sstc-step"><span class="sstc-step-label" id="sstc-' + end + '-l">' + label + "</span>" +
                '<button type="button" class="sstc-pm" data-end="' + end + '" data-d="-1" tabindex="-1" aria-label="' + label + ' colder">−</button>' +
                '<span class="sstc-val" role="spinbutton" tabindex="0" data-end="' + end + '" aria-labelledby="sstc-' + end + '-l" ' +
                'aria-valuemin="' + LIMITS[0] + '" aria-valuemax="' + LIMITS[1] + '"></span>' +
                '<button type="button" class="sstc-pm" data-end="' + end + '" data-d="1" tabindex="-1" aria-label="' + label + ' warmer">+</button></div>';
        }
        var toggles = TOGGLES.map(function (t) {
            return '<div class="form-check sstc-toggle' + (t.engine ? " sstc-engine" : "") + '">' +
                '<input type="checkbox" class="form-check-input" id="sstc-' + t.id + '" data-t="' + t.id + '">' +
                '<label class="form-check-label" for="sstc-' + t.id + '">' + esc(t.label) +
                ' <span class="sstc-hint">' + esc(t.hint) + "</span></label></div>";
        }).join("");
        el.innerHTML =
            '<div class="sstc" role="group" aria-labelledby="sstc-title">' +
            '<div class="sidebar-section-label" id="sstc-title">Colors</div>' +
            '<div class="sstc-engine">' +
            '<div class="sstc-chips" role="radiogroup" aria-label="Color range">' + chips + "</div>" +
            '<div class="sstc-custom" hidden>' + stepper("min", "Coldest") + stepper("max", "Warmest") + "</div>" +
            '<div class="sstc-range" aria-live="polite"><b class="sstc-range-val"></b> <span class="sstc-range-note"></span></div>' +
            '<div class="sstc-bar" role="img"></div>' +
            '<button type="button" class="btn btn-outline-secondary btn-sm sstc-fit" ' +
            'title="Spread the colors over the water on the map now">Fit to view</button>' +
            "</div>" +
            '<div class="sstc-toggles">' + toggles + "</div>" +
            "</div>";
        el.addEventListener("click", function (ev) {
            var t = ev.target.closest ? ev.target.closest("button") : null;
            if (!t || !el.contains(t)) { return; }
            if (t.classList.contains("sstc-chip")) { pickPreset(t.getAttribute("data-p")); }
            else if (t.classList.contains("sstc-pm")) { stepCustom(t.getAttribute("data-end"), +t.getAttribute("data-d")); }
            else if (t.classList.contains("sstc-fit")) { fitView(); }
        });
        el.addEventListener("change", function (ev) {
            var t = ev.target;
            if (t && t.getAttribute && t.getAttribute("data-t")) { toggle(t.getAttribute("data-t"), !!t.checked); }
        });
        el.addEventListener("keydown", function (ev) {
            var t = ev.target;
            if (t.classList && t.classList.contains("sstc-chip")) {
                var ids = PRESETS.map(function (p) { return p.id; }), i = ids.indexOf(t.getAttribute("data-p"));
                var k = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[ev.key];
                if (ev.key === "Home") { i = 0; } else if (ev.key === "End") { i = ids.length - 1; }
                else if (k) { i = (i + k + ids.length) % ids.length; } else { return; }
                ev.preventDefault();
                pickPreset(ids[i], true);
            } else if (t.classList && t.classList.contains("sstc-val")) {
                var d = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 5, PageDown: -5,
                          Home: -99, End: 99 }[ev.key];
                if (!d) { return; }
                ev.preventDefault();
                stepCustom(t.getAttribute("data-end"), d);
            }
        });
        el._sstc = true;
    }

    function renderPanel(focusId) {
        var el = $("sst-color-panel");
        if (!el) { return; }
        if (!el._sstc) { buildPanel(el); }
        var p = S.prefs, r = shownRange();
        el.querySelectorAll(".sstc-chip").forEach(function (b) {
            var id = b.getAttribute("data-p"), on = id === p.preset;
            b.setAttribute("aria-checked", on ? "true" : "false");
            b.classList.toggle("active", on);
            b.tabIndex = on || (p.preset === "view" && id === "auto") ? 0 : -1;
            if (focusId && id === focusId) { b.focus(); }
        });
        var cust = el.querySelector(".sstc-custom");
        if (cust) {
            cust.hidden = p.preset !== "custom";
            var c = p.custom || DEFAULT_CUSTOM();
            el.querySelectorAll(".sstc-val").forEach(function (v) {
                var end = v.getAttribute("data-end"), val = c[end];
                v.textContent = val + " °F";
                v.setAttribute("aria-valuenow", val);
                v.setAttribute("aria-valuetext", val + " degrees Fahrenheit");
            });
        }
        var rv = el.querySelector(".sstc-range-val"), rn = el.querySelector(".sstc-range-note");
        var bar = el.querySelector(".sstc-bar");
        if (r) {
            var txt = fmt(r.min) + " to " + fmt(r.max) + " °F";
            if (rv.textContent !== txt) { rv.textContent = txt; }
            rn.textContent = rangeNote(r);
            var w = bar.clientWidth || 200;
            bar.innerHTML = barHtml(r, w, true);
            bar.setAttribute("aria-label", "Color scale " + txt + ", " + rangeNote(r) +
                ". Colder water shows in the coldest color, warmer in the warmest.");
        } else {
            rv.textContent = "";
            rn.textContent = "Colors load with the week";
            bar.innerHTML = "";
        }
        var fit = el.querySelector(".sstc-fit");
        if (fit) {
            fit.classList.toggle("active", p.preset === "view");
            fit.setAttribute("aria-pressed", p.preset === "view" ? "true" : "false");
        }
        TOGGLES.forEach(function (t) {
            var i = $("sstc-" + t.id);
            if (i && i.checked !== !!p[t.id]) { i.checked = !!p[t.id]; }
        });
    }

    // ---------------------------------------------------------- map legend --
    function coarseWeek() {
        var k = window.SST && window.SST._state && window.SST._state.lastKey;
        var m = (k && k.meta) || {};
        var res = m.resolution_km || (k && k.res_km);
        return m.source === "OISST" || !!(res && res >= 5);
    }
    function renderLegend() {
        var el = $("sst-legend-live");
        if (!el) { return; }
        var r = shownRange();
        if (!S.ready || !r) { if (el.innerHTML) { el.innerHTML = ""; } return; }
        var narrow = (window.innerWidth || 1000) < 768 || (window.innerHeight || 800) <= 500;
        var w = narrow ? 180 : 214;
        var notes = "";
        if (!narrow) {
            notes += '<div class="sst-legend-note">Colder than ' + fmt(r.min) + " or warmer than " + fmt(r.max) +
                     " °F shows in the end colors.</div>";
        }
        if (S.prefs.haze) {
            notes += '<div class="sstl-haze"><span class="sstl-haze-sw"></span>haze: filled in under cloud</div>';
        }
        if (coarseWeek()) { notes += '<div class="sst-legend-warn">25 km grid: broad pattern only</div>'; }
        var html = '<div class="sst-legend sstl">' +
            '<div class="sst-legend-head"><span class="sst-legend-title">Water °F</span>' +
            '<span class="sst-legend-scale">' + esc(scaleWord()) + "</span></div>" +
            '<div class="sstl-bar">' + barHtml(r, w, false) + "</div>" + notes + "</div>";
        if (el.innerHTML !== html) { el.innerHTML = html; }
    }

    function render(focusId) {
        renderPanel(focusId);
        renderLegend();
    }

    // Fill the panel and legend whenever Dash puts the /sst page in (or back).
    function watchDom() {
        var pending = false;
        var mo = new MutationObserver(function () {
            if (pending) { return; }
            pending = true;
            setTimeout(function () {
                pending = false;
                var el = $("sst-color-panel");
                if (el && !el._sstc) { render(); }
                var lg = $("sst-legend-live");
                if (lg && !lg.firstChild && S.ready) { renderLegend(); }
            }, 50);
        });
        mo.observe(document.body, { childList: true, subtree: true });
        render();
    }
    if (document.body) { watchDom(); } else { document.addEventListener("DOMContentLoaded", watchDom); }
    var resizeT = null;
    window.addEventListener("resize", function () {
        clearTimeout(resizeT);
        resizeT = setTimeout(render, 150);
    });

    // ------------------------------------------------------ map: canvases --
    function lookData() {
        var d = $("sst-look-data"), out = { contours: "/api/sst/contours.json", spots: [] };
        if (d) {
            out.contours = d.getAttribute("data-contours") || out.contours;
            try { out.spots = JSON.parse(d.getAttribute("data-spots") || "[]"); } catch (e) { out.spots = []; }
        }
        return out;
    }
    function makePane(map, name, z) {
        var p = map.getPane(name) || map.createPane(name);
        p.style.zIndex = String(z);
        p.style.pointerEvents = "none";
        return p;
    }
    function makeCanvas(pane, cls) {
        var el = document.createElement("canvas");
        el.className = "leaflet-zoom-animated " + cls;
        el.style.position = "absolute";
        el.style.pointerEvents = "none";
        pane.appendChild(el);
        return { el: el, ctx: el.getContext("2d") };
    }
    function placeCanvas(c) {
        var m = S.map, L = window.L, size = m.getSize();
        var px = Math.round(size.x * PAD), py = Math.round(size.y * PAD);
        var tl = m.containerPointToLayerPoint([-px, -py]).round();
        var w = size.x + 2 * px, h = size.y + 2 * py, dpr = Math.min(window.devicePixelRatio || 1, 3);
        c.el.width = Math.round(w * dpr);
        c.el.height = Math.round(h * dpr);
        c.el.style.width = w + "px";
        c.el.style.height = h + "px";
        L.DomUtil.setPosition(c.el, tl);
        c.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        c.ctx.clearRect(0, 0, w, h);
        c.tl = tl; c.px = px; c.py = py; c.w = w; c.h = h;
        c.bounds = L.latLngBounds(m.containerPointToLatLng([-px, -py]), m.containerPointToLatLng([size.x + px, size.y + py]));
        var po = m.getPixelOrigin();
        c.ox = po.x + tl.x;                           // world pixel of the canvas's top-left
        c.oy = po.y + tl.y;
        c.world = 256 * Math.pow(2, m.getZoom());
    }
    function animZoom(e) {
        var m = S.map, L = window.L;
        if (!m) { return; }
        ["depth", "spots"].forEach(function (k) {
            var c = S.cv[k];
            if (!c || !c.bounds) { return; }
            var scale = m.getZoomScale(e.zoom), off = m._latLngBoundsToNewLayerBounds(c.bounds, e.zoom, e.center).min;
            L.DomUtil.setTransform(c.el, off, scale);
        });
    }
    function attachMap(map) {
        S.map = map;
        S.panes = {
            depth: makePane(map, "sst-depth-pane", 430),
            spots: makePane(map, "sst-spotlabel-pane", 455)
        };
        S.cv.depth = makeCanvas(S.panes.depth, "sst-depth-canvas");
        S.cv.spots = makeCanvas(S.panes.spots, "sst-spotlabel-canvas");
        S.attr = false;
        map.on("moveend resize viewreset", redraw);
        map.on("zoomanim", animZoom);
        map.on("layeradd layerremove", function (e) {
            if (e.layer && e.layer.options && e.layer.options.pane === "poi-pane") { schedule(); }
        });
        map.on("unload", function () {
            if (S.map === map) { S.map = null; S.cv = {}; S.attr = false; }
        });
        S.spots = lookData().spots;
        loadDepth();
        depthVisible();
        redraw();
    }
    (function pollMap() {
        var m = window.SST && window.SST._state && window.SST._state.map;
        if (m && m !== S.map && m.getContainer && document.body.contains(m.getContainer())) {
            try { attachMap(m); } catch (e) { S.debug.attachError = String(e); }
        }
        setTimeout(pollMap, S.map ? 1000 : 200);
    })();

    var queued = false;
    function schedule() {
        if (queued) { return; }
        queued = true;
        (window.requestAnimationFrame || setTimeout)(function () { queued = false; redraw(); });
    }
    function uiRects() {
        var m = S.map, out = [];
        if (!m) { return out; }
        var cr = m.getContainer().getBoundingClientRect();
        var wrap = m.getContainer().parentNode;
        var sel = [".sst-legend-box", "#sst-playbar", ".leaflet-control-zoom", ".hamburger-btn",
                   ".leaflet-control-attribution", "#sst-map-hint"];
        sel.forEach(function (s) {
            var nodes = (wrap || document).querySelectorAll(s);
            for (var i = 0; i < nodes.length; i++) {
                var b = nodes[i].getBoundingClientRect();
                if (b.width < 1 || b.height < 1 || getComputedStyle(nodes[i]).display === "none") { continue; }
                out.push([b.left - cr.left - 4, b.top - cr.top - 4, b.right - cr.left + 4, b.bottom - cr.top + 4]);
            }
        });
        return out;
    }
    function free(occ, b, pad) {
        pad = pad || 0;
        for (var i = 0; i < occ.length; i++) {
            var o = occ[i];
            if (b[0] < o[2] + pad && b[2] > o[0] - pad && b[1] < o[3] + pad && b[3] > o[1] - pad) { return false; }
        }
        return true;
    }
    function redraw() {
        var m = S.map;
        if (!m || !S.cv.depth) { return; }
        var t0 = performance.now();
        placeCanvas(S.cv.spots);
        placeCanvas(S.cv.depth);
        var occ = uiRects();
        drawSpots(S.cv.spots, occ);
        if (S.prefs.contours) { drawDepth(S.cv.depth, occ); }
        S.occ = occ;
        S.debug.redrawMs = performance.now() - t0;
    }

    // --------------------------------------------------- the SST under a point --
    function sstColorAt(lat, lng) {
        var E = S.engine;
        if (!E || !S.ready || typeof E.valueAt !== "function") { return null; }
        var v = null;
        try { v = E.valueAt(lat, lng); } catch (e) { v = null; }
        if (!v || !isFinite(v.f)) { return null; }
        return colorFor(v.f, shownRange());
    }

    // ------------------------------------------------------- spot labels --
    function shortName(n) { return n.replace(/ Canyon$/, ""); }
    function visibleSpots() {
        var m = S.map, L = window.L, names = S.spots || [], out = [];
        m.eachLayer(function (l) {
            if (!l.options || l.options.pane !== "poi-pane") { return; }
            if (L.Rectangle && l instanceof L.Rectangle) {
                var b = l.getBounds();
                out.push({ name: "The Dump", box: b, kind: "box", order: 50 });
            } else if (L.CircleMarker && l instanceof L.CircleMarker) {
                var ll = l.getLatLng(), name = null;
                for (var i = 0; i < names.length; i++) {
                    if (Math.abs(names[i][1] - ll.lat) < 1e-5 && Math.abs(names[i][2] - ll.lng) < 1e-5) { name = names[i][0]; break; }
                }
                if (!name) { return; }
                var canyon = / Canyon$/.test(name);
                out.push({ name: name, lat: ll.lat, lng: ll.lng, r: (l.getRadius && l.getRadius()) || 6,
                           kind: canyon ? "canyon" : "spot", order: canyon ? 0 : (/Bank|Ledge/.test(name) ? 10 : 20) + i / 100 });
            }
        });
        out.sort(function (a, b) { return a.order - b.order; });
        return out;
    }
    function drawSpots(c, occ) {
        var m = S.map, z = m.getZoom(), ctx = c.ctx, size = m.getSize();
        var list = visibleSpots();
        S.debug.spotLabels = [];
        // the circles themselves are taken first, so no label covers a spot
        list.forEach(function (s) {
            if (s.kind === "box") { return; }
            var q = m.latLngToContainerPoint([s.lat, s.lng]);
            occ.push([q.x - s.r - 1, q.y - s.r - 1, q.x + s.r + 1, q.y + s.r + 1]);
        });
        if (z < 8) { return; }
        ctx.font = LABEL_FONT;
        ctx.textBaseline = "middle";
        ctx.lineJoin = "round";
        var H = 15;
        list.forEach(function (s) {
            if (z < 9 && s.kind !== "canyon") { return; }
            var text = z < 9 ? shortName(s.name) : s.name;
            var w = Math.ceil(ctx.measureText(text).width);
            var cands;
            if (s.kind === "box") {
                var nw = m.latLngToContainerPoint(s.box.getNorthWest()), ne = m.latLngToContainerPoint(s.box.getNorthEast());
                var cx = (nw.x + ne.x) / 2;
                cands = [[cx - w / 2, nw.y - 4 - H]];
            } else {
                var q = m.latLngToContainerPoint([s.lat, s.lng]), d = s.r + 5;
                cands = [[q.x + d, q.y - H / 2], [q.x - d - w, q.y - H / 2],
                         [q.x - w / 2, q.y - d - H + 1], [q.x - w / 2, q.y + d - 1]];
            }
            for (var i = 0; i < cands.length; i++) {
                var x = cands[i][0], y = cands[i][1], b = [x - 1, y, x + w + 1, y + H];
                if (b[0] < 4 || b[1] < 4 || b[2] > size.x - 4 || b[3] > size.y - 4) { continue; }
                if (!free(occ, b, 1)) { continue; }
                occ.push(b);
                var cx2 = x + c.px, cy2 = y + H / 2 + c.py;
                ctx.lineWidth = 3;
                ctx.strokeStyle = "rgba(11,15,20,0.92)";
                ctx.strokeText(text, cx2, cy2);
                ctx.fillStyle = "#ffffff";
                ctx.fillText(text, cx2, cy2);
                S.debug.spotLabels.push({ name: s.name, text: text, x: x, y: y, w: w, h: H });
                break;
            }
        });
    }

    // ----------------------------------------------------- depth contours --
    function loadDepth() {
        if (S.depth || S.depthLoading) { return; }
        S.depthLoading = true;
        var url = lookData().contours;
        fetch(url, { credentials: "same-origin" }).then(function (r) {
            if (!r.ok) { throw new Error("HTTP " + r.status); }
            return r.json();
        }).then(function (doc) {
            S.depth = prepDepth(doc);
            S.depthLoading = false;
            schedule();
        }).catch(function (e) {
            S.depthLoading = false;
            S.debug.depthError = String(e);
        });
    }
    function mercX(lon) { return (lon + 180) / 360; }
    function mercY(lat) {
        var s = Math.sin(lat * Math.PI / 180);
        return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
    }
    // GOCONT1 (tools/build_contours.py): per piece [len_m, lon0*q, lat0*q, dlon, dlat, ...].
    function prepDepth(doc) {
        var q = doc.q || 10000, levels = [], verts = 0;
        (doc.levels || []).forEach(function (lv) {
            var pieces = [];
            (lv.lines || []).forEach(function (a) {
                var n = (a.length - 1) >> 1;
                if (n < 2) { return; }
                var xs = new Float64Array(n), ys = new Float64Array(n), x = 0, y = 0;
                var x0 = 1, y0 = 1, x1 = 0, y1 = 0, latSum = 0;
                for (var i = 0; i < n; i++) {
                    x += a[1 + 2 * i]; y += a[2 + 2 * i];
                    var lon = x / q, lat = y / q;
                    xs[i] = mercX(lon); ys[i] = mercY(lat);
                    if (xs[i] < x0) { x0 = xs[i]; } if (xs[i] > x1) { x1 = xs[i]; }
                    if (ys[i] < y0) { y0 = ys[i]; } if (ys[i] > y1) { y1 = ys[i]; }
                    latSum += lat;
                }
                verts += n;
                pieces.push({ xs: xs, ys: ys, bb: [x0, y0, x1, y1], len: a[0], lat: latSum / n });
            });
            levels.push({ id: lv.id, label: lv.label, unit: lv.unit, width: lv.width || 0.9,
                          alpha: lv.alpha || 0.8, minzoom: lv.minzoom || 7, pieces: pieces });
        });
        return { levels: levels, verts: verts, built: doc.built, checks: doc.checks || {} };
    }
    function depthVisible() {
        var m = S.map;
        if (!m || !S.cv.depth) { return; }
        var on = !!S.prefs.contours;
        S.cv.depth.el.style.display = on ? "" : "none";
        var ac = m.attributionControl;
        if (ac) {
            if (on && !S.attr) { ac.addAttribution(DEPTH_ATTR); S.attr = true; }
            else if (!on && S.attr) { ac.removeAttribution(DEPTH_ATTR); S.attr = false; }
        }
    }
    var LABEL_ORDER = ["100fa", "1000m", "500m", "2000m", "30fa", "40fa"];
    function drawDepth(c, occ) {
        var D = S.depth, m = S.map;
        if (!D || !m) { return; }
        var z = m.getZoom(), ctx = c.ctx, W = c.world;
        var bx0 = c.ox / W, by0 = c.oy / W, bx1 = (c.ox + c.w) / W, by1 = (c.oy + c.h) / W;
        var lines = [];                                   // visible pieces per level, in canvas pixels
        D.levels.forEach(function (lv) {
            if (z < lv.minzoom) { return; }
            var minPx = /fa$/.test(lv.id) && lv.id !== "100fa" ? 55 : 30;
            var vis = [];
            lv.pieces.forEach(function (p) {
                if (p.bb[2] < bx0 || p.bb[0] > bx1 || p.bb[3] < by0 || p.bb[1] > by1) { return; }
                var mpp = 156543.03 * Math.cos(p.lat * Math.PI / 180) / Math.pow(2, z);
                if (p.len / mpp < minPx) { return; }
                vis.push(p);
            });
            lines.push({ lv: lv, vis: vis });
        });
        var wb = z >= 10 ? 0.25 : 0;
        function path(set) {
            ctx.beginPath();
            set.vis.forEach(function (p) {
                var xs = p.xs, ys = p.ys, n = xs.length;
                ctx.moveTo(xs[0] * W - c.ox, ys[0] * W - c.oy);
                for (var i = 1; i < n; i++) { ctx.lineTo(xs[i] * W - c.ox, ys[i] * W - c.oy); }
            });
        }
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        lines.forEach(function (set) {                // halos first, so every line stays visible on dark colors
            path(set);
            ctx.strokeStyle = "rgba(255,255,255,0.16)";
            ctx.lineWidth = set.lv.width + wb + 1.6;
            ctx.stroke();
        });
        lines.forEach(function (set) {
            path(set);
            ctx.strokeStyle = "rgba(8,10,15," + set.lv.alpha + ")";
            ctx.lineWidth = Math.max(0.8, set.lv.width + wb);
            ctx.stroke();
        });
        if (z >= 7) { depthLabels(c, lines, occ); }
    }
    function depthLabels(c, lines, occ) {
        var m = S.map, ctx = c.ctx, W = c.world, size = m.getSize();
        var phone = size.x < 600;
        var byId = {};
        lines.forEach(function (s) { byId[s.lv.id] = s; });
        ctx.font = DEPTH_FONT;
        ctx.textBaseline = "middle";
        ctx.textAlign = "center";
        var fh = 12, placedAll = [];
        S.debug.depthLabels = [];
        LABEL_ORDER.forEach(function (id) {
            var set = byId[id];
            if (!set) { return; }
            var lv = set.lv, label = lv.label;
            var lw = Math.ceil(ctx.measureText(label).width) + 8;
            var maxN = phone ? 2 : (id === "100fa" ? 4 : 3), sep = phone ? 240 : 380, mine = [];
            var spacing = sep, edge = 24;
            var vis = set.vis.slice().sort(function (a, b) { return b.len - a.len; });
            for (var pi = 0; pi < vis.length && mine.length < maxN; pi++) {
                var p = vis[pi], n = p.xs.length;
                // cumulative length in world pixels; candidates at fixed distances from the piece's start,
                // so labels stay put while the map pans
                var cum = new Float64Array(n), X = new Float64Array(n), Y = new Float64Array(n);
                for (var i = 0; i < n; i++) {
                    X[i] = p.xs[i] * W - c.ox - c.px;      // container pixels
                    Y[i] = p.ys[i] * W - c.oy - c.py;
                    if (i) { cum[i] = cum[i - 1] + Math.hypot(X[i] - X[i - 1], Y[i] - Y[i - 1]); }
                }
                var total = cum[n - 1];
                if (total < lw * 3) { continue; }
                var at = function (d) {
                    var lo = 0, hi = n - 1;
                    while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (cum[mid] < d) { lo = mid; } else { hi = mid; } }
                    var f = (d - cum[lo]) / Math.max(cum[hi] - cum[lo], 1e-9);
                    return [X[lo] + (X[hi] - X[lo]) * f, Y[lo] + (Y[hi] - Y[lo]) * f];
                };
                for (var d = spacing / 2; d < total - lw && mine.length < maxN; d += spacing / 4) {
                    if (d < lw) { continue; }
                    var pt = at(d), pa = at(d - lw / 2), pb = at(d + lw / 2);
                    if (pt[0] < edge || pt[1] < edge || pt[0] > size.x - edge || pt[1] > size.y - edge) { continue; }
                    var a1 = Math.atan2(pt[1] - pa[1], pt[0] - pa[0]), a2 = Math.atan2(pb[1] - pt[1], pb[0] - pt[0]);
                    var da = Math.abs(((a1 - a2) * 180 / Math.PI + 540) % 360 - 180);
                    if (da > 16) { continue; }
                    var close = false;
                    for (var k = 0; k < mine.length; k++) {
                        if (Math.hypot(mine[k][0] - pt[0], mine[k][1] - pt[1]) < sep) { close = true; break; }
                    }
                    if (close) { continue; }
                    var ang = Math.atan2(pb[1] - pa[1], pb[0] - pa[0]);
                    if (ang > Math.PI / 2) { ang -= Math.PI; } else if (ang < -Math.PI / 2) { ang += Math.PI; }
                    var hx = Math.abs(lw / 2 * Math.cos(ang)) + Math.abs(fh / 2 * Math.sin(ang));
                    var hy = Math.abs(lw / 2 * Math.sin(ang)) + Math.abs(fh / 2 * Math.cos(ang));
                    var box = [pt[0] - hx, pt[1] - hy, pt[0] + hx, pt[1] + hy];
                    if (!free(occ, box, 2)) { continue; }
                    occ.push(box);
                    mine.push([pt[0], pt[1], ang]);
                }
            }
            mine.forEach(function (q) {
                var ll = m.containerPointToLatLng([q[0], q[1]]);
                var col = sstColorAt(ll.lat, ll.lng);
                var dark = col ? lum(col) > 0.28 : false;
                ctx.save();
                ctx.translate(q[0] + c.px, q[1] + c.py);
                ctx.rotate(q[2]);
                ctx.globalCompositeOperation = "destination-out";   // the line breaks around its label
                ctx.fillStyle = "#000";
                ctx.beginPath();
                ctx.rect(-lw / 2, -fh / 2, lw, fh);
                ctx.fill();
                ctx.globalCompositeOperation = "source-over";
                if (col) {
                    ctx.lineWidth = 2.5;
                    ctx.strokeStyle = css(col);
                    ctx.strokeText(label, 0, 0.5);
                } else {
                    ctx.lineWidth = 2.5;
                    ctx.strokeStyle = "rgba(11,15,20,0.85)";
                    ctx.strokeText(label, 0, 0.5);
                }
                ctx.fillStyle = col && dark ? "#0b0f14" : "#f4f6f8";
                ctx.fillText(label, 0, 0.5);
                ctx.restore();
                placedAll.push(label);
                S.debug.depthLabels.push({ level: id, label: label, x: q[0], y: q[1], deg: q[2] * 180 / Math.PI });
            });
        });
    }

    window.SSTLook = {
        prefs: function () { return JSON.parse(JSON.stringify(S.prefs)); },
        range: shownRange,
        redraw: redraw,
        _colorFor: colorFor,
        _ramp: ramp,
        _pct: pct,
        _autoFromHist: autoFromHist,
        _gulfFromHist: gulfFromHist,
        _setup: true,
        _state: S
    };
})();
