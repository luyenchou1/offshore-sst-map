/*
 * GotOne Currents — client-side engine for the /currents page.
 *
 * Dash serves every /assets/*.js on every page, so this file stays inert until
 * a Leaflet map whose container id is "currents-map" is created (we hook map
 * construction with L.Map.addInitHook — Leaflet is a global loaded by
 * dash-leaflet before assets run). Everything that runs per frame lives here:
 * no Dash callbacks or server requests per frame. Dash controls talk to us
 * through window.Currents (see the "Currents page" callbacks in app.py).
 *
 * Three views, each a "timeline" with its own clock S.t (looping):
 *   date   typical currents for one day's real Montauk tide times and ranges:
 *          the tide-stage atlas evaluated from bundled NOAA high/lows. Opens
 *          on today at the current time. The selected spot's chart line is
 *          NOAA's own prediction, fetched live (falls back to typical).
 *   cycle  one typical Montauk tide cycle, LW -> HW -> LW, at a preset
 *          strength (Neap / Average / Spring).
 *   week   the example weeks: hourly model output incl. that week's weather.
 *
 * Data (app.py /api/currents/*, formats in data/currents.py):
 *   manifest.json            weeks, stations, "atlas" entry
 *   atlas-*_<depth>.bin.gz   basis fields per tide-phase bin (72 frames)
 *   atlas-*_meta.json.gz     phase/range model, presets, skill maps, station
 *                            curves, Montauk high/lows (tools/build_tide_atlas.py)
 *   <week>_<depth>.bin.gz    hourly u/v frames; <week>_meta.json.gz NOAA data
 */
(function () {
    "use strict";
    if (typeof window.L === "undefined") { return; }   // dash-leaflet not loaded

    // ---------------------------------------------------------- constants --
    var API = "/api/currents/";
    var NOAA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter";
    var TZ = "America/New_York";
    var KNOTS = 1.943844;
    var HOUR = 3600;
    var FADE = 0.92;              // trail alpha kept per frame
    var MAX_AGE = 100;            // frames before a particle respawns
    var FRAME_MS = 1000 / 30;     // ~30 fps cap
    var SLACK_KN = 0.2;           // |current| below this reads as slack
    // Speed buckets (m/s) -> colour: slow = dim blue, fast = gold / white.
    var EDGES = [0.05, 0.15, 0.3, 0.45, 0.6, 0.8, 1.0, 1.25, 1.6];
    var COLORS = [
        "rgba(40,80,150,0.30)", "rgba(45,105,175,0.45)", "rgba(50,135,195,0.55)",
        "rgba(60,165,205,0.65)", "rgba(85,195,200,0.72)", "rgba(130,215,175,0.78)",
        "rgba(195,228,125,0.85)", "rgba(245,212,92,0.90)", "rgba(250,175,70,0.95)",
        "rgba(255,240,205,1.0)"];
    var FLOOD = "#2dd4bf", EBB = "#fb923c", SLACK = "#e2e8f0";
    var MARGIN = { l: 44, r: 50, t: 24, b: 26 };   // chart plot-area margins (px)
    // Confidence bands for the tide-only views (share of the current the
    // typical cycle explains at a spot, from the atlas build).
    var CONF = [[0.85, "High"], [0.7, "Good"], [0.5, "Moderate"], [-1, "Low"]];

    // -------------------------------------------------------------- state --
    var S = {
        map: null, particles: null, stationLayer: null, markers: {}, haze: null,
        clock: null, legend: null, playbar: null, fsBtn: null,
        statusEl: null, cursorEl: null, readoutEl: null, readout: null, readoutTimer: 0,
        readoutKey: "", legendRoom: false,
        resizeObs: null, manifestP: null, manifest: null, inflight: {},
        mode: "date", week: null, depth: "surface", rate: 1800, station: null,
        strength: "average", date: null,
        opts: { stations: true, model: true },
        atlas: null, weekMeta: null, fields: {}, field: null, tl: null,
        stations: [], stationsById: {}, cur: null, curSig: "",
        t: 0, playing: false, wantPlay: true, fitted: false,
        raf: 0, lastFrame: 0, lastUi: 0, loadToken: 0, applyTimer: 0,
        chartReady: false, chartKind: null, scrubbing: false,
        preview: null,                      // chart time under the mouse, else null
        live: { data: {}, failed: {}, key: null, ctrl: null, timer: 0, blockedUntil: 0 }
    };

    // The moment the map shows: the chart time under the mouse while the user
    // points at the chart, otherwise the playback time.
    function viewT() { return S.preview != null ? S.preview : S.t; }

    // ---------------------------------------------------------- utilities --
    function $(id) { return document.getElementById(id); }

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    // Push a prop into Dash from outside a callback (low rate only).
    function setProps(id, props) {
        var dc = window.dash_clientside;   // created after assets load: look up lazily
        if (dc && dc.set_props) { try { dc.set_props(id, props); } catch (e) { /* page gone */ } }
    }

    var _off = {};
    var _parts = new Intl.DateTimeFormat("en-US", {
        timeZone: TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit" });
    function etOffsetMs(tSec) {
        var k = Math.floor(tSec / 3600);
        if (_off[k] !== undefined) { return _off[k]; }
        var p = {};
        _parts.formatToParts(new Date(k * 3600000)).forEach(function (x) { p[x.type] = x.value; });
        var asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, (+p.hour) % 24, +p.minute, +p.second);
        return (_off[k] = asUtc - k * 3600000);
    }
    // Eastern wall-clock time expressed as a UTC timestamp (ms).
    function wall(tSec) { return tSec * 1000 + etOffsetMs(tSec); }
    function unwall(ms) { var t = ms / 1000; return t - etOffsetMs(t) / 1000; }
    // Chart x values. Plotly reads numeric dates in the *browser's* zone, so
    // passing wall() numbers shifted every chart label (ticks, hover time) by
    // the viewer's UTC offset (4-5 h in ET) while the map clock was right.
    // Timezone-naive strings are taken as-is.
    function px(tSec) { return new Date(wall(tSec)).toISOString().slice(0, 19).replace("T", " "); }

    var fmtClock = new Intl.DateTimeFormat("en-US", {
        timeZone: TZ, weekday: "short", month: "short", day: "numeric",
        hour: "numeric", minute: "2-digit", timeZoneName: "short" });
    var fmtHM = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
    var fmtDay = new Intl.DateTimeFormat("en-US", {
        timeZone: TZ, weekday: "short", month: "short", day: "numeric" });
    var _ymd = new Intl.DateTimeFormat("en-CA", {
        timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
    function todayET() { return _ymd.format(new Date()); }          // "YYYY-MM-DD"

    function shiftDay(day, n) {
        var p = day.split("-"), d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2] + n));
        return d.toISOString().slice(0, 10);
    }
    // Epoch seconds of 00:00 Eastern on `day`.
    function etMidnight(day) {
        var p = day.split("-"), utc = Date.UTC(+p[0], +p[1] - 1, +p[2]) / 1000, t = utc + 5 * HOUR;
        for (var i = 0; i < 3; i++) { t = utc - etOffsetMs(t) / 1000; }
        return t;
    }

    function fmtDur(sec) {
        var m = Math.round(Math.abs(sec) / 60), h = Math.floor(m / 60), r = m % 60;
        return h ? h + "h " + (r < 10 ? "0" : "") + r + "m" : r + "m";
    }

    function mercY(lat) { return Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)); }

    function median(a) {
        if (!a.length) { return null; }
        a = a.slice().sort(function (x, y) { return x - y; });
        var m = a.length >> 1;
        return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
    }

    // Largest k with arr[k] <= x (-1 if none).
    function floorIndex(arr, x) {
        var lo = 0, hi = arr.length;
        while (lo < hi) {
            var m = (lo + hi) >> 1;
            if (arr[m] <= x) { lo = m + 1; } else { hi = m; }
        }
        return lo - 1;
    }

    function interpSeries(ts, vs, tSec) {       // linear, clamped at the ends
        var n = vs.length;
        if (!n) { return null; }
        if (tSec <= ts[0]) { return vs[0]; }
        if (tSec >= ts[n - 1]) { return vs[n - 1]; }
        var k = floorIndex(ts, tSec);
        return vs[k] + (vs[k + 1] - vs[k]) * (tSec - ts[k]) / (ts[k + 1] - ts[k]);
    }

    function b64bytes(s) {
        var bin = atob(s || ""), out = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) { out[i] = bin.charCodeAt(i); }
        return out;
    }

    // --------------------------------------------------------------- data --
    function fetchBuf(url) {
        if (S.inflight[url]) { return S.inflight[url]; }   // one download per file
        var p = fetch(url, { credentials: "same-origin" }).then(function (r) {
            if (!r.ok) { throw new Error("HTTP " + r.status); }
            return r.arrayBuffer();
        }).then(function (buf) {
            var b = new Uint8Array(buf, 0, Math.min(2, buf.byteLength));
            if (b[0] === 0x1f && b[1] === 0x8b) {
                // Still gzipped (something dropped Content-Encoding): inflate here.
                if (typeof DecompressionStream === "undefined") {
                    throw new Error("this browser cannot unpack the data");
                }
                return new Response(new Blob([buf]).stream()
                    .pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();
            }
            return buf;
        });
        S.inflight[url] = p;
        var done = function () { delete S.inflight[url]; };
        p.then(done, done);
        return p;
    }

    function fetchJson(url) {
        return fetchBuf(url).then(function (b) { return JSON.parse(new TextDecoder().decode(b)); });
    }

    function ensureManifest() {
        if (!S.manifestP) {
            S.manifestP = fetchJson(API + "manifest.json")
                .catch(function (e) { S.manifestP = null; throw e; });
        }
        return S.manifestP;
    }

    function decodeFrames(buf) {
        var dv = new DataView(buf);
        if (String.fromCharCode.apply(null, new Uint8Array(buf, 0, 6)) !== "GOCUR1") {
            throw new Error("unrecognized frame file");
        }
        var f = {
            nx: dv.getUint16(8, true), ny: dv.getUint16(10, true), nf: dv.getUint16(12, true),
            t0: dv.getFloat64(16, true), dt: dv.getFloat32(24, true),
            lon0: dv.getFloat32(28, true), lat0: dv.getFloat32(32, true),
            lon1: dv.getFloat32(36, true), lat1: dv.getFloat32(40, true),
            scale: dv.getFloat32(44, true)
        };
        f.cells = f.nx * f.ny;
        f.mask = new Uint8Array(buf, 64, f.cells);
        f.data = new Int8Array(buf, 64 + f.cells, f.nf * 2 * f.cells);
        f.dlon = (f.lon1 - f.lon0) / (f.nx - 1);
        f.dlat = (f.lat1 - f.lat0) / (f.ny - 1);
        f.tEnd = f.t0 + (f.nf - 1) * f.dt;
        var w = [];
        for (var i = 0; i < f.cells; i++) { if (f.mask[i]) { w.push(i); } }
        f.water = Uint32Array.from(w);
        return f;
    }

    // ------------------------------------------- tide-stage atlas (model) --
    // Mirrors tools/build_tide_atlas.py; its "selftest" block checks this.
    function prepAtlas(meta, builtAt) {
        var a = meta.atlas, h = meta.tide.hilo, n = h.t.length;
        var A = {
            meta: meta, a: a, builtAt: builtAt, NB: a.bins, c: a.centres_ft, clamp: a.clamp_ft,
            T: new Float64Array(n), V: new Float64Array(n), H: new Uint8Array(n),
            mid: new Float64Array(n - 1), rng: new Float64Array(n - 1),
            stations: meta.stations || [], tideTyp: meta.tide.typ,
            tEbb: a.t_ebb_s, tFlood: a.t_flood_s, presets: a.presets || [],
            skill: {}, hazeUrl: {}
        };
        for (var i = 0; i < n; i++) {
            A.T[i] = h.t[i];
            A.V[i] = h.v[i];
            A.H[i] = h.type.charCodeAt(i) === 72 ? 1 : 0;           // "H"
        }
        for (i = 0; i < n - 1; i++) {
            A.mid[i] = (A.T[i] + A.T[i + 1]) / 2;
            A.rng[i] = Math.abs(A.V[i + 1] - A.V[i]);
        }
        return A;
    }

    // Phase (0 = Montauk HW, 0.5 = LW) and tidal range at t; null outside
    // the bundled high/lows. Range: each half-cycle's |H - L| at its
    // mid-time, linear between mid-times (no step at HW/LW), clamped.
    function tideAt(A, t) {
        var k = floorIndex(A.T, t);
        if (k < 0 || k >= A.T.length - 1) { return null; }
        var frac = (t - A.T[k]) / (A.T[k + 1] - A.T[k]);
        var R = interpSeries(A.mid, A.rng, t);
        R = Math.max(A.clamp[0], Math.min(A.clamp[1], R));
        return { phi: A.H[k] ? 0.5 * frac : 0.5 + 0.5 * frac, R: R, k: k, frac: frac };
    }

    var W3 = [0, 0, 0];
    // Weights interpolating linearly between the three range centres,
    // continued linearly past the ends (as fitted by the builder).
    function tent(R, c) {
        var a;
        if (R <= c[1]) { a = (R - c[0]) / (c[1] - c[0]); W3[0] = 1 - a; W3[1] = a; W3[2] = 0; }
        else { a = (R - c[1]) / (c[2] - c[1]); W3[0] = 0; W3[1] = 1 - a; W3[2] = a; }
        return W3;
    }

    function binPos(phi, NB) {                  // bin centres at (b + 0.5) / NB
        var x = phi * NB - 0.5, f = Math.floor(x), b0 = ((f % NB) + NB) % NB;
        return { b0: b0, b1: (b0 + 1) % NB, a: x - f };
    }

    // Frame weights [[frame, weight], ...]; frame = basis * NB + bin.
    function atlasWeights(A, phi, R) {
        var w = tent(R, A.c), p = binPos(phi, A.NB), out = [];
        for (var j = 0; j < 3; j++) {
            if (w[j] === 0) { continue; }
            out.push([j * A.NB + p.b0, w[j] * (1 - p.a)], [j * A.NB + p.b1, w[j] * p.a]);
        }
        return out;
    }

    // A station / tide curve stored as typ[basis][bin], at (phi, R).
    function typAt(A, typ, phi, R) {
        var w = tent(R, A.c), p = binPos(phi, A.NB), s = 0;
        for (var j = 0; j < 3; j++) {
            if (w[j]) { s += w[j] * ((1 - p.a) * typ[j][p.b0] + p.a * typ[j][p.b1]); }
        }
        return s;
    }

    function presetFor(A, key) {
        var ps = A.presets;
        return ps.filter(function (p) { return p.key === key; })[0] || ps[1] || ps[0] ||
            { key: "average", label: "Average", range_ft: A.c[1] };
    }

    // Share of the current the typical cycle explains near a spot (0-1), or null.
    function confidenceAt(A, f, lat, lon) {
        if (!A || !f) { return null; }
        if (!A.skill[S.depth]) { A.skill[S.depth] = b64bytes((A.a.skill || {})[S.depth]); }
        var sk = A.skill[S.depth];
        if (sk.length !== f.cells) { return null; }
        var i = Math.round((lon - f.lon0) / f.dlon), j = Math.round((f.lat1 - lat) / f.dlat);
        var best = -1, bd = 1e9;
        for (var dj = -2; dj <= 2; dj++) {          // nearest water cell nearby
            for (var di = -2; di <= 2; di++) {
                var jj = j + dj, ii = i + di;
                if (jj < 0 || ii < 0 || jj >= f.ny || ii >= f.nx) { continue; }
                var c = jj * f.nx + ii;
                if (f.mask[c] && di * di + dj * dj < bd) { bd = di * di + dj * dj; best = c; }
            }
        }
        return best < 0 ? null : sk[best] / 100;
    }

    function confLabel(x) {
        if (x == null) { return null; }
        for (var k = 0; k < CONF.length; k++) { if (x >= CONF[k][0]) { return CONF[k][1]; } }
        return "Low";
    }

    // ------------------------------------------------------- field sampling --
    // Blend the frames of the moment into S.cur once per animation frame (only
    // when the weights change); particles then sample S.cur bilinearly.
    function blendField(f, wts) {
        var C = f.cells, W = f.water, d = f.data, n, c;
        if (!S.cur || S.cur.cells !== C) {
            S.cur = { cells: C, u: new Float32Array(C), v: new Float32Array(C) };
        }
        var u = S.cur.u, v = S.cur.v;
        for (n = 0; n < W.length; n++) { c = W[n]; u[c] = 0; v[c] = 0; }
        for (var k = 0; k < wts.length; k++) {
            var base = wts[k][0] * 2 * C, w = wts[k][1] * f.scale;
            if (!w) { continue; }
            for (n = 0; n < W.length; n++) {
                c = W[n];
                u[c] += d[base + c] * w;
                v[c] += d[base + C + c] * w;
            }
        }
    }

    function refreshField() {
        if (!S.tl || !S.field) { return false; }
        var wts = S.tl.weights(viewT());
        if (!wts) { S.curSig = ""; return false; }
        var sig = S.field.key;
        for (var k = 0; k < wts.length; k++) { sig += "|" + wts[k][0] + ":" + wts[k][1].toFixed(4); }
        if (sig !== S.curSig) { blendField(S.field, wts); S.curSig = sig; }
        return true;
    }

    // Mask-aware bilinear corners for a point, or null over land/outside.
    var CORN = { c: [0, 0, 0, 0], w: [0, 0, 0, 0], ws: 0 };
    function corners(f, lat, lon) {
        var fx = (lon - f.lon0) / f.dlon, fy = (f.lat1 - lat) / f.dlat;
        var i = Math.floor(fx), j = Math.floor(fy);
        if (i < 0 || j < 0 || i >= f.nx - 1 || j >= f.ny - 1) { return null; }
        var ax = fx - i, ay = fy - j, m = f.mask, c00 = j * f.nx + i;
        CORN.c[0] = c00; CORN.c[1] = c00 + 1; CORN.c[2] = c00 + f.nx; CORN.c[3] = c00 + f.nx + 1;
        CORN.w[0] = (1 - ax) * (1 - ay) * m[c00];
        CORN.w[1] = ax * (1 - ay) * m[c00 + 1];
        CORN.w[2] = (1 - ax) * ay * m[c00 + f.nx];
        CORN.w[3] = ax * ay * m[c00 + f.nx + 1];
        CORN.ws = CORN.w[0] + CORN.w[1] + CORN.w[2] + CORN.w[3];
        return CORN.ws < 0.5 ? null : CORN;
    }

    var OUT = [0, 0];
    function sampleCur(f, lat, lon) {
        var q = corners(f, lat, lon);
        if (!q) { return null; }
        var u = S.cur.u, v = S.cur.v, c = q.c, w = q.w;
        OUT[0] = (u[c[0]] * w[0] + u[c[1]] * w[1] + u[c[2]] * w[2] + u[c[3]] * w[3]) / q.ws;
        OUT[1] = (v[c[0]] * w[0] + v[c[1]] * w[1] + v[c[2]] * w[2] + v[c[3]] * w[3]) / q.ws;
        return OUT;
    }

    // At an arbitrary time (charts, tooltips): blend just the 4 corner cells.
    function samplePoint(f, lat, lon, wts) {
        var q = corners(f, lat, lon);
        if (!q) { return null; }
        var d = f.data, C = f.cells, c = q.c, w = q.w, u = 0, v = 0;
        for (var k = 0; k < wts.length; k++) {
            var b = wts[k][0] * 2 * C, ww = wts[k][1];
            u += ww * (d[b + c[0]] * w[0] + d[b + c[1]] * w[1] + d[b + c[2]] * w[2] + d[b + c[3]] * w[3]);
            v += ww * (d[b + C + c[0]] * w[0] + d[b + C + c[1]] * w[1] +
                       d[b + C + c[2]] * w[2] + d[b + C + c[3]] * w[3]);
        }
        OUT[0] = u * f.scale / q.ws;
        OUT[1] = v * f.scale / q.ws;
        return OUT;
    }

    // Model current projected onto the station's flood axis (kn, + flood).
    function modelAt(st, t) {
        if (!S.field || !S.tl || st.flood_dir == null || !st.in_model) { return null; }
        var wts = S.tl.weights(t);
        if (!wts) { return null; }
        var uv = samplePoint(S.field, st.lat, st.lon, wts);
        if (!uv) { return null; }
        var th = st.flood_dir * Math.PI / 180;
        return (uv[0] * Math.sin(th) + uv[1] * Math.cos(th)) * KNOTS;
    }

    function curveTimes(c) {
        if (c.t) { return c.t; }
        var out = new Array(c.v.length);
        for (var i = 0; i < c.v.length; i++) { out[i] = c.t0 + i * c.dt; }
        return out;
    }

    // NOAA predicted current (kn, + flood) from a week's stored curve.
    function noaaAt(st, tSec) {
        var c = st.curve;
        if (!c || !c.v.length) { return null; }
        if (!c.t) {
            var x = (tSec - c.t0) / c.dt, n = c.v.length;
            if (x <= 0) { return c.v[0]; }
            if (x >= n - 1) { return c.v[n - 1]; }
            var k = Math.floor(x);
            return c.v[k] + (c.v[k + 1] - c.v[k]) * (x - k);
        }
        return interpSeries(c.t, c.v, tSec);
    }

    // Slack (zero crossings) and peak flood / ebb of a signed series.
    function deriveEvents(ts, vs) {
        var out = [], xs = [], i;
        for (i = 0; i < vs.length - 1; i++) {
            var a = vs[i], b = vs[i + 1];
            if (a !== 0 && (a < 0) !== (b < 0)) {
                var tc = ts[i] + (ts[i + 1] - ts[i]) * a / (a - b);
                xs.push(tc);
                out.push({ t: tc, type: "slack", v: 0 });
            }
        }
        for (var s = 0; s < xs.length - 1; s++) {
            var best = -1, bv = 0;
            for (i = 0; i < vs.length; i++) {
                if (ts[i] > xs[s] && ts[i] < xs[s + 1] && Math.abs(vs[i]) > Math.abs(bv)) { bv = vs[i]; best = i; }
            }
            if (best >= 0) { out.push({ t: ts[best], type: bv > 0 ? "flood" : "ebb", v: bv }); }
        }
        return out.sort(function (p, q) { return p.t - q.t; });
    }

    function describe(kn) {
        if (kn == null) { return { cls: "", text: "–" }; }
        if (Math.abs(kn) < SLACK_KN) { return { cls: "slack", text: "slack" }; }
        return { cls: kn > 0 ? "flood" : "ebb",
                 text: Math.abs(kn).toFixed(1) + " kn " + (kn > 0 ? "flood" : "ebb") };
    }

    // --------------------------------------------------------- timelines --
    // Each view runs on its own clock S.t in [tl.t0, tl.t1) and loops.
    // tl.weights(t) -> frame weights; tl.fracOf/tOfFrac map time <-> chart x.
    function wallAxis(tl) {
        var w0 = wall(tl.t0), w1 = wall(tl.t1);
        tl.range = [px(tl.t0), px(tl.t1)];
        tl.fracOf = function (t) { return (wall(t) - w0) / (w1 - w0); };
        tl.tOfFrac = function (f) { return unwall(w0 + f * (w1 - w0)); };
    }

    function weekTimeline(meta, f) {
        var tl = { kind: "week", id: "week|" + meta.week.id, t0: f.t0, t1: f.tEnd,
                   stations: meta.stations || [], meta: meta, loop: true };
        wallAxis(tl);
        tl.weights = function (t) {
            var h = (t - f.t0) / f.dt;
            if (h < 0) { h = 0; } else if (h > f.nf - 1) { h = f.nf - 1; }
            var k0 = Math.floor(h), k1 = Math.min(k0 + 1, f.nf - 1), a = h - k0;
            return [[k0, 1 - a], [k1, a]];
        };
        tl.clockTitle = function (t) { return fmtClock.format(new Date(t * 1000)); };
        tl.tide = function (t) {
            var td = meta.tide;
            if (!td || td.t.length < 2) { return null; }
            var v = interpSeries(td.t, td.v, t), v2 = interpSeries(td.t, td.v, t + 360);
            return { v: v, rising: v2 >= v };
        };
        tl.stationKn = function (st, t) { return noaaAt(st, t); };
        tl.source = function () { return "NOAA"; };
        tl.chart = function (st) { return weekChart(meta, f, st); };
        tl.badge = function (st) { return weekBadge(meta, st); };
        return tl;
    }

    function cycleTimeline(A, key) {
        var p = presetFor(A, key), R = p.range_ft;
        var tl = { kind: "cycle", id: "cycle", t0: -A.tFlood, t1: A.tEbb, stations: A.stations,
                   preset: p, loop: true };
        var phiOf = function (t) { return t >= 0 ? 0.5 * t / A.tEbb : 1 + 0.5 * t / A.tFlood; };
        tl.phiOf = phiOf;
        tl.range = [tl.t0 / HOUR, tl.t1 / HOUR];
        tl.fracOf = function (t) { return (t - tl.t0) / (tl.t1 - tl.t0); };
        tl.tOfFrac = function (fr) { return tl.t0 + fr * (tl.t1 - tl.t0); };
        tl.weights = function (t) { return atlasWeights(A, phiOf(t), R); };
        tl.clockTitle = function (t) { return cycleLabel(A, t); };
        tl.clockSub = function () {
            return "Typical " + p.label.toLowerCase() + " tide (" + R.toFixed(1) + " ft range)";
        };
        tl.tide = function (t) { return { v: typAt(A, A.tideTyp, phiOf(t), R), rising: t < 0 }; };
        tl.stationKn = function (st, t) { return st.typ ? typAt(A, st.typ, phiOf(t), R) : null; };
        tl.source = function () { return "Typical"; };
        tl.chart = function (st) { return cycleChart(A, tl, st); };
        tl.badge = function (st) { return typicalBadge(A, st, "vs Montauk tide, typically:"); };
        return tl;
    }

    function cycleLabel(A, t) {            // t = seconds from Montauk high water
        if (Math.abs(t) < 5 * 60) { return "Montauk high water"; }
        if (t > A.tEbb - 5 * 60 || t < -A.tFlood + 5 * 60) { return "Montauk low water"; }
        return fmtDur(t) + (t > 0 ? " after" : " before") + " high water";
    }

    function dateTimeline(A, day) {
        var tl = { kind: "date", id: "date|" + day, day: day, t0: etMidnight(day),
                   t1: etMidnight(shiftDay(day, 1)), stations: A.stations, loop: true };
        wallAxis(tl);
        tl.weights = function (t) {
            var s = tideAt(A, t);
            return s ? atlasWeights(A, s.phi, s.R) : null;
        };
        tl.clockTitle = function (t) { return fmtClock.format(new Date(t * 1000)); };
        tl.clockSub = function (t) { return stageLabel(A, t); };
        // Height between high/lows by NOAA's cosine rule.
        tl.tide = function (t) {
            var s = tideAt(A, t);
            if (!s) { return null; }
            var k = s.k, v = A.V[k] + (A.V[k + 1] - A.V[k]) * (1 - Math.cos(Math.PI * s.frac)) / 2;
            return { v: v, rising: A.V[k + 1] > A.V[k] };
        };
        tl.stationKn = function (st, t) {
            var L = liveFor(st, tl);
            if (L) { return interpSeries(L.t, L.v, t); }
            var s = tideAt(A, t);
            return s && st.typ ? typAt(A, st.typ, s.phi, s.R) : null;
        };
        tl.source = function (st) { return liveFor(st, tl) ? "NOAA" : "Typical"; };
        tl.chart = function (st) { return dateChart(A, tl, st); };
        tl.badge = function (st) { return dateBadge(A, tl, st); };
        return tl;
    }

    function stageLabel(A, t) {            // "2h 40m after high water"
        var k = floorIndex(A.T, t);
        if (k < 0 || k >= A.T.length - 1) { return ""; }
        var name = A.H[k] ? "high" : "low", next = A.H[k] ? "low" : "high";
        if (t - A.T[k] < 5 * 60) { return "Montauk " + name + " water"; }
        if (A.T[k + 1] - t < 5 * 60) { return "Montauk " + next + " water"; }
        return fmtDur(t - A.T[k]) + " after " + name + " water";
    }

    function clampT(t) {
        var tl = S.tl;
        if (!tl) { return t; }
        if (tl.kind === "cycle") {
            var span = tl.t1 - tl.t0;
            return tl.t0 + ((((t - tl.t0) % span) + span) % span);
        }
        return Math.max(tl.t0, Math.min(tl.t1, t));
    }

    function startTime(tl) {
        if (tl.kind === "date") {
            var now = Date.now() / 1000;
            if (now >= tl.t0 && now < tl.t1) { return now; }
            return tl.t0 + 6 * HOUR;                        // 6 AM on other days
        }
        return tl.t0;
    }

    // ------------------------------------------------------ live NOAA data --
    // The selected spot's official prediction for the chosen day (date view).
    function liveKey(st, tl) { return st.id + "|" + tl.day; }
    function liveFor(st, tl) {
        return st && tl && tl.kind === "date" ? S.live.data[liveKey(st, tl)] || null : null;
    }

    function pad2(n) { return (n < 10 ? "0" : "") + n; }
    function ymdhmUTC(t) {
        var d = new Date(t * 1000);
        return d.getUTCFullYear() + pad2(d.getUTCMonth() + 1) + pad2(d.getUTCDate()) + " " +
            pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes());
    }
    // "YYYY-MM-DD HH:MM" in GMT. Parsed by hand: Safari's Date() rejects it.
    function parseNoaa(s) {
        return Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10),
                        +s.slice(11, 13), +s.slice(14, 16)) / 1000;
    }

    function requestLive() {
        var tl = S.tl, st = S.stationsById[S.station];
        if (!tl || tl.kind !== "date" || !st) { return; }
        var key = liveKey(st, tl);
        if (S.live.data[key] || S.live.failed[key] || S.live.key === key) { return; }
        if (Date.now() < S.live.blockedUntil) { return; }
        clearTimeout(S.live.timer);                         // debounce quick switching
        S.live.timer = setTimeout(function () {
            if (S.live.ctrl) { S.live.ctrl.abort(); }
            var ctrl = window.AbortController ? new AbortController() : null;
            S.live.ctrl = ctrl;
            S.live.key = key;
            var q = { product: "currents_predictions", station: st.id, interval: "6",
                      units: "english", time_zone: "gmt", format: "json",
                      application: "gotone_currents",
                      begin_date: ymdhmUTC(tl.t0 - 1800), end_date: ymdhmUTC(tl.t1 + 1800) };
            if (st.bin) { q.bin = st.bin; }
            var url = NOAA + "?" + Object.keys(q).map(function (k) {
                return k + "=" + encodeURIComponent(q[k]);
            }).join("&");
            fetch(url, ctrl ? { signal: ctrl.signal } : {}).then(function (r) {
                if (r.status === 403 || r.status === 429) {
                    S.live.blockedUntil = Date.now() + 10 * 60 * 1000;
                    throw new Error("NOAA rate limit");
                }
                if (!r.ok) { throw new Error("HTTP " + r.status); }
                return r.json();
            }).then(function (j) {
                if (j.error) { throw new Error(j.error.message || "no predictions"); }
                var cp = (j.current_predictions || {}).cp || [];
                if (cp.length < 10) { throw new Error("no predictions"); }
                var t = new Float64Array(cp.length), v = new Float64Array(cp.length);
                cp.forEach(function (r, i) { t[i] = parseNoaa(r.Time); v[i] = +r.Velocity_Major; });
                S.live.data[key] = { t: t, v: v, events: deriveEvents(t, v) };
                liveArrived(key);
            }).catch(function (e) {
                if (e && e.name === "AbortError") { return; }
                S.live.failed[key] = true;
                liveArrived(key);
            }).then(function () {
                if (S.live.key === key) { S.live.key = null; }
            });
        }, 300);
    }

    function liveArrived(key) {
        var st = S.stationsById[S.station];
        if (!st || !S.tl || S.tl.kind !== "date" || liveKey(st, S.tl) !== key) { return; }
        buildChart();
        updateStationStyles();
        updateClock();
    }

    // ------------------------------------------------------ particle layer --
    var LL = [0, 0], PX = [0, 0];
    var ParticleLayer = L.Layer.extend({
        onAdd: function (map) {
            this._map = map;
            this._canvas = L.DomUtil.create("canvas", "currents-canvas",
                                            map.getPane("currentsParticles"));
            this._ctx = this._canvas.getContext("2d");
            this._n = 0;
            map.on("movestart zoomstart", this._hide, this);
            map.on("moveend resize", this._reset, this);
            this._reset();
        },
        onRemove: function (map) {
            map.off("movestart zoomstart", this._hide, this);
            map.off("moveend resize", this._reset, this);
            L.DomUtil.remove(this._canvas);
            this._canvas = null;
            this._n = 0;
        },
        _hide: function () {
            if (this._canvas) { this._canvas.style.visibility = "hidden"; }
        },
        _reset: function () {
            var map = this._map, c = this._canvas;
            if (!map || !c) { return; }
            var size = map.getSize(), dpr = Math.min(window.devicePixelRatio || 1, 2);
            L.DomUtil.setPosition(c, map.containerPointToLayerPoint([0, 0]));
            c.width = Math.max(1, Math.round(size.x * dpr));
            c.height = Math.max(1, Math.round(size.y * dpr));
            c.style.width = size.x + "px";
            c.style.height = size.y + "px";
            this._ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            this._w = size.x;
            this._h = size.y;
            // Web Mercator view transform (exact for Leaflet's default CRS).
            var b = map.getBounds();
            this._lonW = b.getWest();
            this._lonE = b.getEast();
            this._yN = mercY(b.getNorth());
            this._yS = mercY(b.getSouth());
            this._k = 0.9 * Math.pow(1.5, map.getZoom() - 10);   // px per frame per m/s
            c.style.visibility = "visible";
            this._seed();
        },
        _toLatLon: function (x, y) {
            LL[1] = this._lonW + x / this._w * (this._lonE - this._lonW);
            var my = this._yN + y / this._h * (this._yS - this._yN);
            LL[0] = (2 * Math.atan(Math.exp(my)) - Math.PI / 2) * 180 / Math.PI;
            return LL;
        },
        _toPx: function (lat, lon) {
            PX[0] = (lon - this._lonW) / (this._lonE - this._lonW) * this._w;
            PX[1] = (mercY(lat) - this._yN) / (this._yS - this._yN) * this._h;
            return PX;
        },
        _seed: function () {
            var f = S.field;
            this._n = 0;
            this._ctx.clearRect(0, 0, this._w, this._h);
            if (!f) { return; }
            // Water cells currently on screen, for respawning particles.
            var vis = [], inView = 0, w = this._w, h = this._h;
            for (var cell = 0; cell < f.cells; cell++) {
                var j = (cell / f.nx) | 0, i = cell - j * f.nx;
                var p = this._toPx(f.lat1 - j * f.dlat, f.lon0 + i * f.dlon);
                if (p[0] >= -4 && p[0] <= w + 4 && p[1] >= -4 && p[1] <= h + 4) {
                    inView++;
                    if (f.mask[cell]) { vis.push(cell); }
                }
            }
            this._visible = Uint32Array.from(vis);
            if (!vis.length) { return; }
            var n = Math.round((w * h / 280) * (vis.length / Math.max(1, inView)));
            n = Math.max(300, Math.min(5000, n));
            this._x = new Float32Array(n);
            this._y = new Float32Array(n);
            this._age = new Uint16Array(n);
            this._seg = COLORS.map(function () { return new Float32Array(n * 4); });
            this._segN = new Uint32Array(COLORS.length);
            for (var q = 0; q < n; q++) {
                this._spawn(q);
                this._age[q] = (Math.random() * MAX_AGE) | 0;   // stagger lifetimes
            }
            this._n = n;
        },
        _spawn: function (q) {
            var f = S.field, vis = this._visible;
            var cell = vis[(Math.random() * vis.length) | 0];
            var j = (cell / f.nx) | 0, i = cell - j * f.nx;
            var p = this._toPx(f.lat1 - (j + Math.random() - 0.5) * f.dlat,
                               f.lon0 + (i + Math.random() - 0.5) * f.dlon);
            this._x[q] = p[0];
            this._y[q] = p[1];
            this._age[q] = 0;
        },
        step: function () {
            var n = this._n, f = S.field;
            if (!n || !f || !this._canvas || !S.cur || !S.curSig) { return; }
            var ctx = this._ctx, w = this._w, h = this._h, k = this._k;
            var segN = this._segN, nb = COLORS.length, b, q;
            segN.fill(0);
            for (q = 0; q < n; q++) {
                var x = this._x[q], y = this._y[q];
                if (this._age[q] > MAX_AGE || x < 0 || y < 0 || x > w || y > h) {
                    this._spawn(q);
                    continue;
                }
                var ll = this._toLatLon(x, y);
                var uv = sampleCur(f, ll[0], ll[1]);
                if (!uv) { this._spawn(q); continue; }
                var u = uv[0], v = uv[1], spd = Math.sqrt(u * u + v * v);
                var x2 = x + u * k, y2 = y - v * k;        // screen y grows downward
                b = 0;
                while (b < EDGES.length && spd > EDGES[b]) { b++; }
                var s = this._seg[b], o = segN[b] * 4;
                s[o] = x; s[o + 1] = y; s[o + 2] = x2; s[o + 3] = y2;
                segN[b]++;
                this._x[q] = x2;
                this._y[q] = y2;
                this._age[q]++;
            }
            // Fade the existing trails, then add this frame's segments (glow).
            ctx.globalCompositeOperation = "destination-in";
            ctx.fillStyle = "rgba(0,0,0," + FADE + ")";
            ctx.fillRect(0, 0, w, h);
            ctx.globalCompositeOperation = "lighter";
            for (b = 0; b < nb; b++) {
                var cnt = segN[b];
                if (!cnt) { continue; }
                var sg = this._seg[b];
                ctx.beginPath();
                for (q = 0; q < cnt; q++) {
                    var o2 = q * 4;
                    ctx.moveTo(sg[o2], sg[o2 + 1]);
                    ctx.lineTo(sg[o2 + 2], sg[o2 + 3]);
                }
                ctx.strokeStyle = COLORS[b];
                ctx.lineWidth = b >= 7 ? 1.6 : 1.1;
                ctx.stroke();
            }
            ctx.globalCompositeOperation = "source-over";
        }
    });

    // ------------------------------------------------------------ controls --
    var Clock = L.Control.extend({
        options: { position: "topleft" },
        onAdd: function () { return L.DomUtil.create("div", "currents-clock"); }
    });

    var Legend = L.Control.extend({
        options: { position: "bottomright" },
        onAdd: function () {
            var d = L.DomUtil.create("div", "currents-legend");
            var maxKn = 3.5, stops = [], prev = 0;
            for (var b = 0; b < COLORS.length; b++) {
                var end = b < EDGES.length ? Math.min(100, EDGES[b] * KNOTS / maxKn * 100) : 100;
                var col = COLORS[b].replace(/,[\d.]+\)$/, ",1)");
                stops.push(col + " " + prev.toFixed(1) + "%", col + " " + end.toFixed(1) + "%");
                prev = end;
            }
            d.innerHTML = "<div>Current speed</div>" +
                '<div class="bar" style="background:linear-gradient(90deg,' + stops.join(",") +
                ')"></div><div class="ticks"><span>0</span><span>1</span><span>2</span>' +
                "<span>3+ kn</span></div>" +
                '<div class="haze-note"><span class="swatch"></span>Hazed: wind and ocean ' +
                "currents often override the tide</div>";
            return d;
        }
    });

    // Play / step on the map, shown only when the sidebar is a drawer (CSS):
    // on tablets and phones the sidebar is hidden while you watch.
    var PlayBar = L.Control.extend({
        options: { position: "bottomleft" },
        onAdd: function () {
            var d = L.DomUtil.create("div", "currents-playbar");
            d.innerHTML = '<button type="button" data-a="back" title="Back 1 hour" aria-label="Back 1 hour">◀</button>' +
                '<button type="button" data-a="play" class="main">Play</button>' +
                '<button type="button" data-a="fwd" title="Forward 1 hour" aria-label="Forward 1 hour">▶</button>';
            L.DomEvent.disableClickPropagation(d);
            L.DomEvent.disableScrollPropagation(d);
            L.DomEvent.on(d, "click", function (ev) {
                var a = ev.target && ev.target.getAttribute("data-a");
                if (a === "play") {
                    window.Currents.togglePlay();
                    setProps("currents-play", { children: S.playing ? "Pause" : "Play" });
                } else if (a === "back" || a === "fwd") {
                    window.Currents.step(a === "back" ? -HOUR : HOUR);
                }
            });
            return d;
        }
    });

    function updatePlayBar() {
        var b = S.playbar && S.playbar.getContainer().querySelector(".main");
        if (b) { b.textContent = S.playing ? "Pause" : "Play"; }
    }

    // Full screen (whole page; the header hides while it's on). iPhone Safari
    // can't full-screen a page, so no button there; inside an iframe without
    // fullscreen permission (the Squarespace embed) it opens the page in a tab.
    var FS_IN = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    var FS_OUT = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    function fsElement() { return document.fullscreenElement || document.webkitFullscreenElement || null; }
    function fsEnabled() { return !!(document.fullscreenEnabled || document.webkitFullscreenEnabled); }
    function embedded() { try { return window.self !== window.top; } catch (e) { return true; } }

    function toggleFullscreen() {
        if (fsElement()) {
            var exit = document.exitFullscreen || document.webkitExitFullscreen;
            if (exit) { exit.call(document); }
        } else if (fsEnabled()) {
            var el = document.documentElement, req = el.requestFullscreen || el.webkitRequestFullscreen;
            var p = req && req.call(el);
            if (p && p.catch) { p.catch(function () { /* refused: stay windowed */ }); }
        } else if (embedded()) {
            window.open(window.location.href, "_blank", "noopener");
        }
    }

    function onFullscreenChange() {
        var on = !!fsElement();
        document.documentElement.classList.toggle("currents-fs", on);
        if (S.fsBtn) {
            S.fsBtn.innerHTML = on ? FS_OUT : FS_IN;
            S.fsBtn.title = on ? "Exit full screen" : "Full screen";
        }
        // The map/chart boxes change size; the ResizeObserver redraws both.
    }
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("webkitfullscreenchange", onFullscreenChange);

    var FullscreenCtl = L.Control.extend({
        options: { position: "topright" },
        onAdd: function () {
            var b = L.DomUtil.create("button", "currents-fs-btn");
            b.type = "button";
            b.title = fsEnabled() ? "Full screen" : "Open in a new tab";
            b.setAttribute("aria-label", b.title);
            b.innerHTML = FS_IN;
            L.DomEvent.disableClickPropagation(b);
            L.DomEvent.on(b, "click", toggleFullscreen);
            S.fsBtn = b;
            return b;
        }
    });

    function updateLegend() {
        if (!S.legend) { return; }
        var note = S.legend.getContainer().querySelector(".haze-note");
        if (note) { note.style.display = S.haze ? "block" : "none"; }
    }

    function showStatus(msg, isError) {
        if (!S.map) { return; }
        if (!S.statusEl) {
            S.statusEl = L.DomUtil.create("div", "currents-status", S.map.getContainer());
        }
        S.statusEl.className = "currents-status" + (isError ? " error" : "");
        S.statusEl.textContent = msg;
        S.statusEl.style.display = "block";
    }

    function hideStatus() {
        if (S.statusEl) { S.statusEl.style.display = "none"; }
    }

    function updateClock() {
        if (!S.clock || !S.tl) { return; }
        var t = viewT(), tl = S.tl;
        var html = '<div class="time">' + esc(tl.clockTitle(t)) + "</div>";
        var sub = tl.clockSub ? tl.clockSub(t) : "";
        if (sub) { html += '<div class="cur-row sub">' + esc(sub) + "</div>"; }
        var td = tl.tide(t);
        if (td) {
            html += '<div class="cur-row">Montauk tide ' + td.v.toFixed(1) + " ft, " +
                    (td.rising ? "rising" : "falling") + "</div>";
        }
        var st = S.stationsById[S.station];
        if (st) {
            var d = describe(tl.stationKn(st, t));
            html += '<div class="cur-row">' + esc(st.label) + ': <span class="' + d.cls + '">' +
                    d.text + "</span></div>";
        }
        S.clock.getContainer().innerHTML = html;
    }

    // ------------------------------------------------------------ stations --
    function stationTip(st) {
        var t = viewT(), tl = S.tl, nv = tl.stationKn(st, t), d = describe(nv);
        var m = describe(modelAt(st, t));
        var dir = nv == null || Math.abs(nv) < SLACK_KN ? null : (nv > 0 ? st.flood_dir : st.ebb_dir);
        var conf = tl.kind === "week" ? null : confLabel(confidenceAt(S.atlas, S.field, st.lat, st.lon));
        return "<b>" + esc(st.label) + "</b>" +
            (st.label !== st.name ? '<br><span style="color:#94a3b8">' + esc(st.name) + "</span>" : "") +
            "<br>" + tl.source(st) + ": " + d.text + (dir != null ? " → " + Math.round(dir) + "°" : "") +
            "<br>Model: " + (st.in_model ? m.text : "outside model") +
            (conf ? "<br>Confidence here: " + conf : "");
    }

    function buildStations() {
        if (!S.stationLayer) { return; }
        S.stationLayer.clearLayers();
        S.markers = {};
        S.stations.forEach(function (st) {
            var m = L.circleMarker([st.lat, st.lon], {
                pane: "currentsStations", radius: st.featured ? 5.5 : 3.5, weight: 1.2,
                color: "#0a1628", fillColor: SLACK, fillOpacity: 0.95,
                bubblingMouseEvents: false
            });
            m.bindTooltip("", { direction: "top", offset: [0, -4], className: "currents-tip" });
            m.on("tooltipopen", function () { m.setTooltipContent(stationTip(st)); });
            m.on("click", function () { selectStation(st.id, true); });
            m.addTo(S.stationLayer);
            S.markers[st.id] = m;
        });
        applyStationVisibility();
        updateStationStyles();
    }

    function updateStationStyles() {
        if (!S.tl) { return; }
        var t = viewT();
        for (var id in S.markers) {
            var st = S.stationsById[id], m = S.markers[id], v = S.tl.stationKn(st, t);
            var col = v == null ? "#64748b" : Math.abs(v) < SLACK_KN ? SLACK : (v > 0 ? FLOOD : EBB);
            var sel = id === S.station;
            m.setStyle({ fillColor: col, color: sel ? "#ffffff" : "#0a1628", weight: sel ? 2.5 : 1.2 });
            m.setRadius(sel ? 8 : (st.featured ? 5.5 : 3.5));
            if (m.isTooltipOpen()) { m.setTooltipContent(stationTip(st)); }
        }
    }

    function applyStationVisibility() {
        if (!S.map || !S.stationLayer) { return; }
        var on = S.map.hasLayer(S.stationLayer);
        if (S.opts.stations && !on) { S.stationLayer.addTo(S.map); }
        if (!S.opts.stations && on) { S.map.removeLayer(S.stationLayer); }
    }

    function selectStation(id, fromMap) {
        if (!id) { return; }
        if (!S.tl) { S.station = id; return; }              // applied once data loads
        if (!S.stationsById[id] || id === S.station) { return; }
        S.station = id;
        if (fromMap) { setProps("currents-station", { value: id }); }
        updateStationStyles();
        updateClock();
        buildChart();
        requestLive();
    }

    // ------------------------------------------- low-confidence haze (atlas) --
    function buildHaze() {
        if (S.haze && S.map) { S.map.removeLayer(S.haze); }
        S.haze = null;
        var A = S.atlas, f = S.field, d = S.depth;
        if (!S.map || !A || !f || !S.tl || S.tl.kind === "week") { updateLegend(); return; }
        if (!A.hazeUrl[d]) {
            var sk = b64bytes((A.a.skill || {})[d]), rs = b64bytes((A.a.resid || {})[d]);
            var unit = A.a.resid_unit || 0.005;
            if (sk.length !== f.cells || rs.length !== f.cells) { updateLegend(); return; }
            var cv = document.createElement("canvas");
            cv.width = f.nx;
            cv.height = f.ny;
            var ctx = cv.getContext("2d"), img = ctx.createImageData(f.nx, f.ny), px4 = img.data;
            for (var c = 0; c < f.cells; c++) {
                if (!f.mask[c]) { continue; }
                // Haze only where the tide explains < ~70% AND the leftover is
                // a real current (> ~0.1 m/s): weak-flow bays stay clear.
                var low = Math.min(1, Math.max(0, (0.7 - sk[c] / 100) / 0.3));
                var big = Math.min(1, Math.max(0, (rs[c] * unit - 0.06) / 0.08));
                // Fade out over the last ~12 cells (~5 km) so the haze doesn't
                // end in a hard rectangle where the model grid stops.
                var cj = (c / f.nx) | 0, ci = c - cj * f.nx;
                var edge = Math.min(1, Math.min(ci, cj, f.nx - 1 - ci, f.ny - 1 - cj) / 12);
                var a = low * big * edge;
                if (a <= 0) { continue; }
                px4[4 * c] = 148;
                px4[4 * c + 1] = 163;
                px4[4 * c + 2] = 184;
                px4[4 * c + 3] = Math.round(a * 0.42 * 255);
            }
            ctx.putImageData(img, 0, 0);
            A.hazeUrl[d] = cv.toDataURL("image/png");
        }
        var hx = f.dlon / 2, hy = f.dlat / 2;              // values sit on grid points
        S.haze = L.imageOverlay(A.hazeUrl[d],
            [[f.lat0 - hy, f.lon0 - hx], [f.lat1 + hy, f.lon1 + hx]],
            { pane: "currentsHaze", interactive: false, className: "currents-haze" }).addTo(S.map);
        updateLegend();
    }

    // --------------------------------------------------------------- chart --
    function waitForPlotly(cb, tries) {
        if (window.Plotly) { cb(); return; }
        if ((tries || 0) > 300) { return; }                 // give up after 30 s
        setTimeout(function () { waitForPlotly(cb, (tries || 0) + 1); }, 100);
    }

    // Slack / strongest-current markers; listed in the legend when it has room.
    function eventTraces(evs, xf) {
        var pick = function (type) { return evs.filter(function (e) { return e.type === type; }); };
        var sl = pick("slack"), fl = pick("flood"), eb = pick("ebb");
        return [{
            x: sl.map(function (e) { return xf(e.t); }), y: sl.map(function () { return 0; }),
            yaxis: "y2", mode: "markers", name: "slack", showlegend: S.legendRoom,
            marker: { symbol: "circle-open", size: 7, color: SLACK, line: { width: 1.5 } }
        }, {
            x: fl.map(function (e) { return xf(e.t); }), y: fl.map(function (e) { return e.v; }),
            yaxis: "y2", mode: "markers", name: "strongest flood / ebb", showlegend: S.legendRoom,
            marker: { symbol: "triangle-up", size: 7, color: FLOOD }
        }, {
            x: eb.map(function (e) { return xf(e.t); }), y: eb.map(function (e) { return e.v; }),
            yaxis: "y2", mode: "markers", showlegend: false,
            marker: { symbol: "triangle-down", size: 7, color: EBB }
        }];
    }

    function tideTrace(xs, ys, name) {
        return { x: xs, y: ys, name: name, type: "scatter", mode: "lines",
                 line: { color: "#60a5fa", width: 2 } };
    }

    function currentTrace(xs, ys, name) {
        return { x: xs, y: ys, yaxis: "y2", name: name, mode: "lines",
                 line: { color: "#fbbf24", width: 2 },
                 fill: "tozeroy", fillcolor: "rgba(251,191,36,0.10)" };
    }

    function modelTrace(xs, ys, st) {
        return { x: xs, y: ys, yaxis: "y2", mode: "lines",
                 name: st.label + " current, model" + (S.depth === "davg" ? " (depth-avg)" : ""),
                 line: { color: "#fbbf24", width: 1.3, dash: "dot" } };
    }

    function hiloTrace(xs, ys) {
        return { x: xs, y: ys, mode: "markers", showlegend: false, hoverinfo: "skip",
                 marker: { size: 5, color: "#60a5fa" } };
    }

    function maxAbs(arrs) {
        var m = 1;
        arrs.forEach(function (a) { for (var i = 0; i < a.length; i++) { m = Math.max(m, Math.abs(a[i])); } });
        return m;
    }

    // Model at the station over [t0, t1] every `step` s (x mapped by xf).
    function modelSeries(st, t0, t1, step, xf) {
        var mx = [], my = [];
        if (!S.opts.model || !st || !st.in_model || st.flood_dir == null) { return null; }
        for (var t = t0; t <= t1 + 1; t += step) {
            var mv = modelAt(st, t);
            if (mv != null) { mx.push(xf(t)); my.push(Math.round(mv * 100) / 100); }
        }
        return my.length ? { x: mx, y: my } : null;
    }

    function weekChart(meta, f, st) {
        var td = meta.tide, traces = [tideTrace(td.t.map(px), td.v, "Montauk tide, ft (NOAA)")];
        if (S.opts.model && td.model) {
            traces.push({
                x: td.model.v.map(function (_, i) { return px(td.model.t0 + i * td.model.dt); }),
                y: td.model.v, name: "Montauk water level, model", mode: "lines",
                line: { color: "#60a5fa", width: 1.2, dash: "dot" }
            });
        }
        var hilo = td.hilo || [];
        traces.push(hiloTrace(hilo.map(function (h) { return px(h.t); }),
                              hilo.map(function (h) { return h.v; })));
        var arrs = [];
        if (st && st.curve) {
            traces.push(currentTrace(curveTimes(st.curve).map(px), st.curve.v,
                                     st.label + " current, kn (NOAA)"));
            arrs.push(st.curve.v);
            var ms = modelSeries(st, f.t0, f.tEnd, f.dt, px);
            if (ms) { traces.push(modelTrace(ms.x, ms.y, st)); arrs.push(ms.y); }
            traces = traces.concat(eventTraces(st.events || [], px));
        }
        return { traces: traces, maxC: maxAbs(arrs),
                 xaxis: { type: "date", dtick: 86400000, tickformat: "%a %-d",
                          hoverformat: "%a %b %-d, %-I:%M %p" } };
    }

    function dateChart(A, tl, st) {
        var xs = [], ys = [], t;
        for (t = tl.t0; t <= tl.t1 + 1; t += 600) {
            var td = tl.tide(t);
            if (td) { xs.push(px(t)); ys.push(Math.round(td.v * 100) / 100); }
        }
        var traces = [tideTrace(xs, ys, "Montauk tide, ft (NOAA)")];
        var hx = [], hy = [];
        for (var k = Math.max(0, floorIndex(A.T, tl.t0)); k < A.T.length && A.T[k] <= tl.t1; k++) {
            if (A.T[k] >= tl.t0) { hx.push(px(A.T[k])); hy.push(A.V[k]); }
        }
        traces.push(hiloTrace(hx, hy));
        var arrs = [];
        if (st) {
            var L1 = liveFor(st, tl), cx = [], cy = [], evs;
            if (L1) {
                for (var i = 0; i < L1.t.length; i++) {
                    if (L1.t[i] >= tl.t0 - 1800 && L1.t[i] <= tl.t1 + 1800) {
                        cx.push(L1.t[i]); cy.push(L1.v[i]);
                    }
                }
                evs = L1.events;
            } else {
                for (t = tl.t0; t <= tl.t1 + 1; t += 600) { cx.push(t); cy.push(tl.stationKn(st, t)); }
                evs = deriveEvents(cx, cy);
            }
            traces.push(currentTrace(cx.map(px), cy,
                                     st.label + " current, kn (" + (L1 ? "NOAA" : "typical") + ")"));
            arrs.push(cy);
            var ms = modelSeries(st, tl.t0, tl.t1, 1200, px);
            if (ms) { traces.push(modelTrace(ms.x, ms.y, st)); arrs.push(ms.y); }
            traces = traces.concat(eventTraces(evs.filter(function (e) {
                return e.t >= tl.t0 && e.t <= tl.t1;
            }), px));
        }
        var shapes = [], annotations = [], now = Date.now() / 1000;
        if (now >= tl.t0 && now < tl.t1) {
            shapes.push({ type: "line", xref: "x", yref: "paper", x0: px(now), x1: px(now),
                          y0: 0, y1: 1, line: { color: "#f87171", width: 1, dash: "dot" } });
            annotations.push({ x: px(now), y: 1, xref: "x", yref: "paper", text: "now",
                               showarrow: false, yanchor: "top", xanchor: "left", xshift: 2,
                               font: { size: 9, color: "#f87171" } });
        }
        return { traces: traces, maxC: maxAbs(arrs), shapes: shapes, annotations: annotations,
                 xaxis: { type: "date", dtick: 3 * HOUR * 1000, tickformat: "%-I %p",
                          hoverformat: "%a %-I:%M %p" } };
    }

    function cycleChart(A, tl, st) {
        var N = 124, step = (tl.t1 - tl.t0) / N, xs = [], ys = [], t, i;
        var hx = function (tt) { return Math.round(tt / HOUR * 1000) / 1000; };
        for (i = 0; i <= N; i++) {
            t = tl.t0 + i * step;
            xs.push(hx(t));
            ys.push(Math.round(tl.tide(t).v * 100) / 100);
        }
        var traces = [tideTrace(xs, ys, "Montauk tide, ft (typical)")];
        traces.push(hiloTrace([hx(tl.t0), 0, hx(tl.t1)],
                              [ys[0], tl.tide(0).v, ys[ys.length - 1]]));
        var arrs = [];
        if (st && st.typ) {
            var cx = [], cy = [];
            for (i = 0; i <= N; i++) { t = tl.t0 + i * step; cx.push(t); cy.push(tl.stationKn(st, t)); }
            traces.push(currentTrace(cx.map(hx), cy, st.label + " current, kn (typical NOAA)"));
            arrs.push(cy);
            var ms = modelSeries(st, tl.t0, tl.t1, step, hx);
            if (ms) { traces.push(modelTrace(ms.x, ms.y, st)); arrs.push(ms.y); }
            traces = traces.concat(eventTraces(deriveEvents(cx, cy), hx));
        }
        var lo = tl.t0 / HOUR, hi = tl.t1 / HOUR, tv = [lo], tt = ["LW"];
        [-4, -2, 0, 2, 4].forEach(function (h) {
            if (h > lo + 0.6 && h < hi - 0.6) { tv.push(h); tt.push(h === 0 ? "HW" : (h > 0 ? "+" : "−") + Math.abs(h) + "h"); }
        });
        tv.push(hi);
        tt.push("LW");
        return { traces: traces, maxC: maxAbs(arrs),
                 xaxis: { type: "linear", tickvals: tv, ticktext: tt, hoverformat: "+.1f" } };
    }

    function buildChart() {
        var gd = $("currents-chart");
        if (!gd || !S.tl || !S.field) { return; }
        if (!window.Plotly) { waitForPlotly(buildChart); return; }
        var wide = gd.clientWidth >= 620;   // phones: no legend / axis titles
        S.legendRoom = gd.clientWidth >= 1000;   // room to also list the marker symbols
        var st = S.stationsById[S.station], spec = S.tl.chart(st), maxC = spec.maxC;
        var xaxis = { range: S.tl.range, fixedrange: true, automargin: false,
                      gridcolor: "#1e293b", linecolor: "#334155" };
        for (var k in spec.xaxis) { xaxis[k] = spec.xaxis[k]; }
        var layout = {
            paper_bgcolor: "#0a1628", plot_bgcolor: "#0a1628",
            font: { color: "#94a3b8", size: 10 },
            margin: MARGIN, showlegend: wide,
            legend: { orientation: "h", x: 0, y: 1, yanchor: "bottom", font: { size: 10 },
                      bgcolor: "rgba(0,0,0,0)" },
            // No Plotly hover: our readout rides on the white time line instead.
            hovermode: false, dragmode: false,
            xaxis: xaxis,
            yaxis: { fixedrange: true, automargin: false, zeroline: false,
                     gridcolor: "#1e293b", tickfont: { color: "#60a5fa" },
                     title: { text: wide ? "tide ft" : "", font: { size: 10, color: "#60a5fa" } } },
            // tickmode "auto": an overlaying axis otherwise synced its ticks to
            // the tide axis (5.25 / 1.81 / -1.62 kn on a tall iPad chart).
            yaxis2: { overlaying: "y", side: "right", fixedrange: true, automargin: false,
                      range: [-maxC * 1.15, maxC * 1.15], showgrid: false,
                      tickmode: "auto", nticks: 6,
                      zeroline: true, zerolinecolor: "#475569", tickfont: { color: "#fbbf24" },
                      title: { text: wide ? "current kn (+flood)" : "",
                               font: { size: 10, color: "#fbbf24" } } },
            shapes: spec.shapes || [], annotations: spec.annotations || []
        };
        // Switching between date and numeric x axes: start the plot fresh.
        if (S.chartKind && S.chartKind !== S.tl.kind) {
            try { window.Plotly.purge(gd); } catch (e) { /* not drawn */ }
        }
        S.chartKind = S.tl.kind;
        window.Plotly.react(gd, spec.traces, layout,
                            { displayModeBar: false, responsive: true, scrollZoom: false });
        S.chartReady = true;
        updateBadge(st);
        updateCursor();
    }

    // --------------------------------------------------------------- badge --
    function phrase(off, what) {
        if (off == null) { return null; }
        if (Math.abs(off) < 15 * 60) { return "<b>at " + what + "</b>"; }
        return "<b>" + fmtDur(off) + (off > 0 ? " after" : " before") + "</b> " + what;
    }

    function confSpan(st) {
        var c = confLabel(confidenceAt(S.atlas, S.field, st.lat, st.lon));
        return c ? ' · <span class="conf conf-' + c.toLowerCase() + '">Confidence here: ' + c + "</span>" : "";
    }

    function typicalBadge(A, st, lead) {
        var L = st.lags || {}, parts = [];
        var a = phrase(L.slack_ebb_vs_hw, "high water"), b = phrase(L.slack_flood_vs_lw, "low water");
        if (a || b) { parts.push("slack comes " + [a, b].filter(Boolean).join(" and ")); }
        var c = phrase(L.max_flood_vs_hw, "high water");
        if (c) { parts.push("strongest flood " + c); }
        return "<b>" + esc(st.label) + '</b> <span class="muted">' + esc(lead) + "</span> " +
            (parts.length ? parts.join(" · ") : '<span class="muted">no typical slack timing here</span>') +
            confSpan(st);
    }

    function dateBadge(A, tl, st) {
        var L1 = liveFor(st, tl);
        if (!L1) {
            var lead = S.live.failed[liveKey(st, tl)] ? "NOAA times unavailable; typically:"
                                                     : "vs Montauk tide, typically:";
            return typicalBadge(A, st, lead);
        }
        var ev = L1.events.filter(function (e) { return e.t >= tl.t0 && e.t < tl.t1; });
        var hm = function (t) { return fmtHM.format(new Date(t * 1000)).replace(":00", "").replace(" ", "").toLowerCase(); };
        var slack = ev.filter(function (e) { return e.type === "slack"; }).map(function (e) { return hm(e.t); });
        var big = function (type) {
            var x = ev.filter(function (e) { return e.type === type; })
                      .sort(function (p, q) { return Math.abs(q.v) - Math.abs(p.v); })[0];
            return x ? "<b>" + Math.abs(x.v).toFixed(1) + " kn</b> at " + hm(x.t) : null;
        };
        var parts = [];
        if (slack.length) { parts.push("slack <b>" + slack.join(" · ") + "</b>"); }
        var fl = big("flood"), eb = big("ebb");
        if (fl) { parts.push("max flood " + fl); }
        if (eb) { parts.push("max ebb " + eb); }
        return "<b>" + esc(st.label) + '</b> <span class="muted">' +
            esc(fmtDay.format(new Date((tl.t0 + 12 * HOUR) * 1000))) + ", NOAA:</span> " +
            (parts.length ? parts.join(" · ") : '<span class="muted">no slack listed</span>') +
            confSpan(st);
    }

    // Median offset from each reference time to the nearest event (within 5 h).
    function typicalOffset(refs, events) {
        var out = [];
        refs.forEach(function (r) {
            var best = null;
            events.forEach(function (e) {
                var d = e - r;
                if (Math.abs(d) <= 5 * 3600 && (best === null || Math.abs(d) < Math.abs(best))) { best = d; }
            });
            if (best !== null) { out.push(best); }
        });
        return median(out);
    }

    // The lesson: how this station's slack / max flood line up with Montauk's tide.
    function weekBadge(meta, st) {
        var ev = (st.events || []).slice().sort(function (a, b) { return a.t - b.t; });
        var endFlood = [], endEbb = [], floods = [], prev = null;
        ev.forEach(function (e) {
            if (e.type === "slack") {
                if (prev === "flood") { endFlood.push(e.t); }
                else if (prev === "ebb") { endEbb.push(e.t); }
            } else {
                prev = e.type;
                if (e.type === "flood") { floods.push(e.t); }
            }
        });
        var hilo = meta.tide.hilo || [];
        var hw = hilo.filter(function (x) { return x.type === "H"; }).map(function (x) { return x.t; });
        var lw = hilo.filter(function (x) { return x.type === "L"; }).map(function (x) { return x.t; });
        var parts = [];
        var a = phrase(typicalOffset(hw, endFlood), "high water");
        var b = phrase(typicalOffset(lw, endEbb), "low water");
        if (a || b) { parts.push("slack comes " + [a, b].filter(Boolean).join(" and ")); }
        var c = phrase(typicalOffset(hw, floods), "high water");
        if (c) { parts.push("strongest flood " + c); }
        return parts.length
            ? "<b>" + esc(st.label) + '</b> <span class="muted">vs Montauk tide this week:</span> ' +
              parts.join(" · ")
            : "<b>" + esc(st.label) + '</b> <span class="muted">— NOAA lists no slack/max ' +
              "times for this station.</span>";
    }

    function updateBadge(st) {
        var el = $("currents-badge");
        if (!el) { return; }
        el.innerHTML = st && S.tl ? S.tl.badge(st) : "";
    }

    // -------------------------------------------- chart cursor, hover, scrub --
    // One white line = the moment the map shows. While someone points at,
    // taps or drags on the chart, a readout beside it gives that moment's tide
    // and currents. (Plotly's own hover readout is off: on touch it stayed
    // where the finger first landed, so the chart showed two different times.)
    function updateCursor() {
        var layer = $("currents-cursor-layer"), gd = $("currents-chart");
        if (!layer || !gd || !S.tl || !S.chartReady) { return; }
        if (!S.cursorEl || !layer.contains(S.cursorEl)) {
            S.cursorEl = document.createElement("div");
            S.cursorEl.className = "currents-cursor";
            S.readoutEl = document.createElement("div");
            S.readoutEl.className = "currents-readout";
            layer.appendChild(S.cursorEl);
            layer.appendChild(S.readoutEl);
            S.readoutKey = "";
        }
        var w = gd.clientWidth - MARGIN.l - MARGIN.r;
        var x = MARGIN.l + Math.max(0, Math.min(1, S.tl.fracOf(viewT()))) * w;
        S.cursorEl.style.left = x + "px";
        S.cursorEl.style.top = MARGIN.t + "px";
        S.cursorEl.style.bottom = MARGIN.b + "px";
        updateReadout(x, gd);
    }

    function readoutTime(t) {
        if (S.tl.kind === "cycle") { return cycleLabel(S.atlas, t); }
        return (S.tl.kind === "date" ? fmtHM : fmtClock).format(new Date(t * 1000));
    }

    // S.readout: null (hidden) | "hover" (mouse over the chart) | "drag" |
    // "touch" (finger on the chart; lingers briefly after it lifts).
    function updateReadout(x, gd) {
        var el = S.readoutEl;
        if (!el) { return; }
        if (!S.readout) { el.style.display = "none"; S.readoutKey = ""; return; }
        var t = viewT(), tl = S.tl, st = S.stationsById[S.station];
        // Hint only while previewing a time other than the one that's set.
        var hint = S.readout === "hover" && S.preview != null && Math.abs(S.preview - S.t) > 60;
        var key = [Math.round(t / 30), S.station, tl.id, hint, S.opts.model, S.depth].join("|");
        if (key !== S.readoutKey) {
            S.readoutKey = key;
            var rows = ['<div class="rt">' + esc(readoutTime(t)) + "</div>"];
            var td = tl.tide(t);
            if (td) {
                rows.push('<div><span class="sw tide"></span>Tide ' + td.v.toFixed(1) + " ft, " +
                          (td.rising ? "rising" : "falling") + "</div>");
            }
            if (st) {
                var d = describe(tl.stationKn(st, t));
                rows.push('<div><span class="sw cur"></span>' + esc(st.label) + " (" + esc(tl.source(st)) +
                          '): <span class="' + d.cls + '">' + d.text + "</span></div>");
                if (S.opts.model && st.in_model) {
                    rows.push('<div><span class="sw mod"></span>Model: ' + describe(modelAt(st, t)).text + "</div>");
                }
            }
            if (hint) { rows.push('<div class="hint">Click to set this time</div>'); }
            el.innerHTML = rows.join("");
        }
        el.style.display = "block";
        var rw = el.offsetWidth, W = gd.clientWidth;
        el.style.left = (x + 10 + rw > W - 4 ? Math.max(4, x - 10 - rw) : x + 10) + "px";
        el.style.top = (MARGIN.t + 4) + "px";
    }

    function showReadout(mode) {
        clearTimeout(S.readoutTimer);
        S.readout = mode;
        updateCursor();
    }

    function timeFromEvent(ev) {
        var gd = $("currents-chart");
        if (!gd || !S.tl) { return null; }
        var pt = ev.touches ? ev.touches[0] : ev, r = gd.getBoundingClientRect();
        var cx = pt.clientX - r.left, cy = pt.clientY - r.top;
        if (cy < MARGIN.t - 4 || cy > r.height - MARGIN.b + 4) { return null; }   // legend / ticks
        var frac = (cx - MARGIN.l) / (r.width - MARGIN.l - MARGIN.r);
        return S.tl.tOfFrac(Math.max(0, Math.min(1, frac)));
    }

    // Pointing at the chart (mouse only; touch has no hover) previews that
    // moment on the map. Playback holds meanwhile, and leaving the chart
    // returns the map to the time that's set. People scrub by pointing, so a
    // hover that did nothing to the map read as broken scrubbing.
    function hoverMove(ev) {
        if (ev.pointerType !== "mouse" || ev.buttons || S.scrubbing || !S.tl) { return; }
        var t = timeFromEvent(ev);
        if (t === null) { hoverEnd(); return; }
        if (S.readout !== "hover") { showReadout("hover"); }
        setPreview(clampT(t));
    }

    function hoverEnd() {
        if (!S.scrubbing && S.readout === "hover") { S.readout = null; }
        setPreview(null);
        updateCursor();
    }

    function setPreview(t) {
        if (t === S.preview) { return; }
        var ended = t === null;
        S.preview = t;
        updateCursor();
        updateClock();
        if (ended) { updateStationStyles(); }       // during a hover the loop refreshes them
    }

    function pause() {
        S.playing = false;
        S.wantPlay = false;
        setProps("currents-play", { children: "Play" });
        updatePlayBar();
    }

    // Click / drag (or tap / drag) on the chart jumps there and pauses, so
    // the map stays on the moment the user picked until they press Play.
    // Window-level move/up listeners exist only while a scrub is in progress:
    // this file loads on every page, and a permanent non-passive touchmove
    // listener would make mobile map panning wait on JS everywhere.
    function scrubStart(ev) {
        var t = timeFromEvent(ev);
        if (t === null) { return; }
        S.scrubbing = true;
        S.preview = null;
        showReadout(ev.touches ? "touch" : "drag");
        pause();
        seek(t);
        window.addEventListener("mousemove", scrubMove);
        window.addEventListener("mouseup", scrubEnd);
        window.addEventListener("touchmove", scrubMove, { passive: false });
        window.addEventListener("touchend", scrubEnd);
        window.addEventListener("touchcancel", scrubEnd);
    }

    function scrubMove(ev) {
        if (!S.scrubbing) { return; }
        var t = timeFromEvent(ev);
        if (t !== null) { seek(t); }
        if (ev.cancelable && ev.touches) { ev.preventDefault(); }
    }

    function scrubEnd() {
        S.scrubbing = false;
        window.removeEventListener("mousemove", scrubMove);
        window.removeEventListener("mouseup", scrubEnd);
        window.removeEventListener("touchmove", scrubMove);
        window.removeEventListener("touchend", scrubEnd);
        window.removeEventListener("touchcancel", scrubEnd);
        if (S.readout === "touch") {
            // Leave the readout up a moment after the finger lifts.
            S.readoutTimer = setTimeout(function () { S.readout = null; updateCursor(); }, 1800);
        } else {
            S.readout = null;                    // the next mouse move brings it back
            updateCursor();
        }
    }

    function bindScrub() {
        var gd = $("currents-chart");
        if (!gd || gd.getAttribute("data-scrub")) { return; }
        gd.setAttribute("data-scrub", "1");
        gd.addEventListener("mousedown", scrubStart);
        gd.addEventListener("touchstart", scrubStart, { passive: true });
        gd.addEventListener("pointermove", hoverMove);
        gd.addEventListener("pointerleave", hoverEnd);
    }

    function seek(t) {
        if (!S.tl) { return; }
        S.t = clampT(t);
        updateCursor();
        updateClock();
        updateStationStyles();
    }

    // ------------------------------------------------------------- loading --
    // Setters only record state; one apply() then loads what the current view
    // needs (Dash fires the initial control callbacks in no particular order).
    function scheduleApply() {
        if (S.applyTimer) { return; }
        S.applyTimer = setTimeout(function () { S.applyTimer = 0; apply(); }, 0);
    }

    function apply() {
        if (!S.map) { return; }                              // attach() calls us
        var token = ++S.loadToken;
        ensureManifest().then(function (man) {
            if (token !== S.loadToken || !S.map) { return null; }
            S.manifest = man;
            var mode = S.mode;
            if (mode !== "week" && !man.atlas) { mode = "week"; }   // atlas not built yet
            if (mode === "week" && !(man.weeks || []).length) {
                showStatus("Current data is still being prepared — check back soon.");
                return null;
            }
            return (mode === "week" ? loadWeek(man) : loadAtlas(man)).then(function (field) {
                if (token !== S.loadToken || !S.map) { return; }
                install(mode, field);
            });
        }).catch(function (e) {
            if (token === S.loadToken) {
                showStatus("Couldn't load current data (" + e.message + ").", true);
            }
        });
    }

    function loadAtlas(man) {
        var e = man.atlas, v = "?v=" + encodeURIComponent(e.built_at || man.generated_at || "1");
        var depth = e.files[S.depth] ? S.depth : "surface";
        var key = "atlas|" + depth + "|" + e.built_at;
        var haveMeta = S.atlas && S.atlas.builtAt === e.built_at;
        if (!haveMeta || !S.fields[key]) { showStatus("Loading tide atlas…"); }
        return Promise.all([
            haveMeta ? Promise.resolve(S.atlas) : fetchJson(API + e.files.meta + v).then(function (m) {
                return prepAtlas(m, e.built_at);
            }),
            S.fields[key] ? Promise.resolve(S.fields[key])
                          : fetchBuf(API + e.files[depth] + v).then(decodeFrames)
        ]).then(function (res) {
            S.atlas = res[0];
            res[1].key = key;
            S.fields[key] = res[1];
            return res[1];
        });
    }

    function loadWeek(man) {
        var weeks = man.weeks;
        if (!weeks.some(function (w) { return w.id === S.week; })) { S.week = weeks[0].id; }
        var w = weeks.filter(function (x) { return x.id === S.week; })[0];
        var depth = w.files[S.depth] ? S.depth : (w.files.surface ? "surface" : "davg");
        var v = "?v=" + encodeURIComponent(w.built_at || man.generated_at || "1");
        var key = "week|" + w.id + "|" + depth;
        var haveMeta = S.weekMeta && S.weekMeta.week.id === w.id;
        if (!haveMeta || !S.fields[key]) { showStatus("Loading " + w.title + "…"); }
        return Promise.all([
            haveMeta ? Promise.resolve(S.weekMeta) : fetchJson(API + w.files.meta + v),
            S.fields[key] ? Promise.resolve(S.fields[key])
                          : fetchBuf(API + w.files[depth] + v).then(decodeFrames)
        ]).then(function (res) {
            S.weekMeta = res[0];
            res[1].key = key;
            S.fields[key] = res[1];
            Object.keys(S.fields).forEach(function (k) {        // bound memory: one week
                if (k.indexOf("week|") === 0 && k.split("|")[1] !== w.id) { delete S.fields[k]; }
            });
            return res[1];
        });
    }

    function validDay(day) {
        var d = (S.manifest && S.manifest.atlas && S.manifest.atlas.dates) || {};
        day = /^\d{4}-\d{2}-\d{2}$/.test(day || "") ? day : todayET();
        if (d.min && day < d.min) { day = d.min; }
        if (d.max && day > d.max) { day = d.max; }
        return day;
    }

    function install(mode, field) {
        var A = S.atlas, tl;
        if (mode === "week") { tl = weekTimeline(S.weekMeta, field); }
        else if (mode === "cycle") { tl = cycleTimeline(A, S.strength); }
        else { tl = dateTimeline(A, validDay(S.date)); }
        var prev = S.tl, fresh = !prev || prev.id !== tl.id;
        S.field = field;
        S.tl = tl;
        S.curSig = "";
        if (fresh) {
            S.preview = null;
            S.t = startTime(tl);
            // Today opens paused on "now"; the other views play if wanted.
            var play = tl.kind !== "date" && S.wantPlay;
            if (play !== S.playing) {
                S.playing = play;
                setProps("currents-play", { children: play ? "Pause" : "Play" });
            }
            updatePlayBar();
        } else {
            S.t = clampT(S.t);
        }
        if (!prev || prev.stations !== tl.stations) {
            S.stations = tl.stations;
            S.stationsById = {};
            S.stations.forEach(function (s) { S.stationsById[s.id] = s; });
            if (!S.stationsById[S.station]) {
                var dflt = (S.manifest && S.manifest.default_station) || "LIS1001";
                S.station = S.stationsById[dflt] ? dflt : (S.stations[0] || {}).id;
                if (S.station) { setProps("currents-station", { value: S.station }); }
            }
            buildStations();
        } else {
            updateStationStyles();
        }
        if (!S.fitted) {
            S.fitted = true;
            S.map.fitBounds([[field.lat0, field.lon0], [field.lat1, field.lon1]], { padding: [4, 4] });
        }
        if (S.particles) { S.particles._reset(); }
        buildHaze();
        refreshField();
        hideStatus();
        buildChart();
        updateClock();
        requestLive();
        startLoop();
    }

    // ---------------------------------------------------------- animation --
    function frame(now) {
        S.raf = 0;
        if (!S.map || !S.field || !S.tl || document.hidden) { return; }   // resumed on visibility
        if (S.lastFrame && now - S.lastFrame < FRAME_MS - 1) {
            S.raf = requestAnimationFrame(frame);
            return;
        }
        var dt = S.lastFrame ? Math.min((now - S.lastFrame) / 1000, 0.25) : 0;
        S.lastFrame = now;
        if (S.playing && !S.scrubbing && S.preview == null) {
            S.t += dt * S.rate;
            if (S.t >= S.tl.t1) { S.t = S.tl.t0 + (S.t - S.tl.t1) % (S.tl.t1 - S.tl.t0); }   // loop
        }
        refreshField();
        if (S.particles) { S.particles.step(); }
        updateCursor();
        if (now - S.lastUi > 200) {
            S.lastUi = now;
            updateClock();
            updateStationStyles();
        }
        S.raf = requestAnimationFrame(frame);
    }

    function startLoop() {
        if (!S.raf && S.map && S.field) {
            S.lastFrame = 0;
            S.raf = requestAnimationFrame(frame);
        }
    }

    function stopLoop() {
        if (S.raf) { cancelAnimationFrame(S.raf); S.raf = 0; }
    }

    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) { startLoop(); }
    });

    // ------------------------------------------------------ attach / detach --
    L.Map.addInitHook(function () {
        var c = this.getContainer && this.getContainer();
        if (!c || c.id !== "currents-map") { return; }
        var map = this;
        map.whenReady(function () { attach(map); });
    });

    function attach(map) {
        if (S.map === map) { return; }
        if (S.map) { detach(); }
        S.map = map;
        var pp = map.createPane("currentsParticles");
        pp.style.zIndex = 450;
        pp.style.pointerEvents = "none";
        var hz = map.createPane("currentsHaze");
        hz.style.zIndex = 455;
        hz.style.pointerEvents = "none";
        map.createPane("currentsStations").style.zIndex = 470;
        S.particles = new ParticleLayer().addTo(map);
        S.stationLayer = L.layerGroup();
        S.clock = new Clock().addTo(map);
        S.legend = new Legend().addTo(map);
        S.playbar = new PlayBar().addTo(map);
        updatePlayBar();
        if (fsEnabled() || embedded()) {
            new FullscreenCtl().addTo(map);
            onFullscreenChange();
        }
        map.on("unload", detach);           // router swap -> react-leaflet map.remove()
        if (window.ResizeObserver) {
            // The chart box and mobile drawer resize the map without a window resize.
            S.resizeObs = new ResizeObserver(function () {
                if (S.map) { S.map.invalidateSize({ pan: false }); }
                var gd = $("currents-chart");
                if (gd && window.Plotly && S.chartReady) {
                    try { window.Plotly.Plots.resize(gd); } catch (e) { /* not drawn yet */ }
                }
                updateCursor();
            });
            S.resizeObs.observe(map.getContainer());
            var box = $("currents-chart");
            if (box) { S.resizeObs.observe(box); }
        }
        bindScrub();
        if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            S.wantPlay = false;
        }
        apply();
    }

    function detach() {
        stopLoop();
        if (S.resizeObs) { S.resizeObs.disconnect(); S.resizeObs = null; }
        var gd = $("currents-chart");
        if (gd && window.Plotly) { try { window.Plotly.purge(gd); } catch (e) { /* gone */ } }
        if (S.map) { S.map.off("unload", detach); }
        S.map = null;
        S.particles = null;
        S.stationLayer = null;
        S.markers = {};
        S.haze = null;
        S.clock = null;
        S.legend = null;
        S.playbar = null;
        S.fsBtn = null;
        S.statusEl = null;
        S.cursorEl = null;
        S.readoutEl = null;
        S.readout = null;
        clearTimeout(S.readoutTimer);
        S.preview = null;
        S.chartReady = false;
        S.chartKind = null;
        S.fitted = false;
        S.playing = false;
        S.tl = null;                        // rebuild stations/chart on the next visit
        S.curSig = "";
    }

    // Recompute the builder's reference values (tools/build_tide_atlas.py
    // "selftest") with this file's atlas math; maxErr should be ~0.
    function selftest() {
        var A = S.atlas, man = S.manifest;
        if (!A || !man || !man.atlas) { return Promise.resolve("no atlas loaded"); }
        var e = man.atlas, v = "?v=" + encodeURIComponent(e.built_at);
        var get = function (d) {
            var key = "atlas|" + d + "|" + e.built_at;
            return S.fields[key] ? Promise.resolve(S.fields[key])
                : fetchBuf(API + e.files[d] + v).then(decodeFrames).then(function (f) {
                    f.key = key; S.fields[key] = f; return f;
                });
        };
        return Promise.all([get("surface"), get("davg")]).then(function (fs) {
            var byDepth = { surface: fs[0], davg: fs[1] }, worst = 0, n = 0;
            (A.a.selftest || []).forEach(function (x) {
                var s = tideAt(A, x.t), wts = atlasWeights(A, s.phi, s.R);
                var f = byDepth[x.depth], c = x.j * f.nx + x.i, u = 0, vv = 0;
                wts.forEach(function (w) {
                    var b = w[0] * 2 * f.cells;
                    u += w[1] * f.data[b + c];
                    vv += w[1] * f.data[b + f.cells + c];
                });
                worst = Math.max(worst, Math.abs(u * f.scale - x.u), Math.abs(vv * f.scale - x.v));
                n++;
            });
            return { checked: n, maxErr: worst };
        });
    }

    // ----------------------------------------------------------------- API --
    window.Currents = {
        setMode: function (m) {
            m = m === "cycle" || m === "week" ? m : "date";
            if (m === S.mode && S.tl && S.tl.kind === m) { return; }
            S.mode = m;
            scheduleApply();
        },
        setWeek: function (id) {
            if (!id || id === S.week) { return; }
            S.week = id;
            if (S.mode === "week") { scheduleApply(); }      // else just remember it
        },
        setDepth: function (d) {
            d = d === "davg" ? "davg" : "surface";
            if (d === S.depth && S.field) { return; }
            S.depth = d;
            scheduleApply();
        },
        setRate: function (r) {
            r = +r;
            if (r > 0) { S.rate = r; }
        },
        setStrength: function (k) {
            if (!k || k === S.strength) { return; }
            S.strength = k;
            if (S.mode === "cycle") { scheduleApply(); }
        },
        setDate: function (d) {
            if (!d) { return; }
            d = String(d).slice(0, 10);
            if (d === S.date) { return; }
            S.date = d;
            if (S.mode === "date") { scheduleApply(); }
        },
        // "Now" button: today's date (Eastern) with the clock at this moment.
        goNow: function () {
            var today = todayET();
            if (S.date !== today) {
                S.date = today;
                if (S.mode === "date") { scheduleApply(); }   // new day opens on "now"
            } else if (S.tl && S.tl.kind === "date") {
                pause();
                seek(Date.now() / 1000);
            }
        },
        selectStation: function (id) { selectStation(id, false); },
        setOptions: function (vals) {
            vals = vals || [];
            S.opts = { stations: vals.indexOf("stations") >= 0, model: vals.indexOf("model") >= 0 };
            applyStationVisibility();
            buildChart();
        },
        togglePlay: function () {
            S.playing = !S.playing;
            S.wantPlay = S.playing;
            if (S.playing) { startLoop(); }
            updatePlayBar();
            return S.playing;
        },
        step: function (sec) {
            pause();
            seek(S.t + (+sec || 0));
        },
        _state: S,                          // for poking at in the browser console
        _selftest: selftest
    };
})();
