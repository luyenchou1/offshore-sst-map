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
 * Data (built by tools/build_currents.py, served by app.py /api/currents/*):
 *   manifest.json          weeks + stations
 *   <week>_<depth>.bin.gz  GOCUR1 hourly u/v frames (format: data/currents.py)
 *   <week>_meta.json.gz    NOAA tide + current predictions for the week
 */
(function () {
    "use strict";
    if (typeof window.L === "undefined") { return; }   // dash-leaflet not loaded

    // ---------------------------------------------------------- constants --
    var API = "/api/currents/";
    var TZ = "America/New_York";
    var KNOTS = 1.943844;
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

    // -------------------------------------------------------------- state --
    var S = {
        map: null, particles: null, stationLayer: null, markers: {},
        clock: null, statusEl: null, cursorEl: null, resizeObs: null,
        manifestP: null, week: null, depth: "surface", rate: 1800, station: null,
        opts: { stations: true, model: true },
        meta: null, field: null, fields: {}, stationsById: {},
        t: 0, playing: false, wantPlay: true, fitted: false,
        raf: 0, lastFrame: 0, lastUi: 0, loadToken: 0,
        chartReady: false, xr: null, scrubbing: false
    };

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
    // Eastern wall-clock time expressed as a UTC timestamp. Plotly date axes
    // ignore time zones, so these make the chart read in local (ET) time.
    function wall(tSec) { return tSec * 1000 + etOffsetMs(tSec); }
    function unwall(ms) { var t = ms / 1000; return t - etOffsetMs(t) / 1000; }

    var fmtClock = new Intl.DateTimeFormat("en-US", {
        timeZone: TZ, weekday: "short", month: "short", day: "numeric",
        hour: "numeric", minute: "2-digit", timeZoneName: "short" });

    function fmtDur(sec) {
        var m = Math.round(Math.abs(sec) / 60), h = Math.floor(m / 60), r = m % 60;
        return h ? h + "h " + (r < 10 ? "0" : "") + r + "m" : r + "m";
    }

    function mercY(lat) { return Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)); }

    // --------------------------------------------------------------- data --
    function fetchBuf(url) {
        return fetch(url, { credentials: "same-origin" }).then(function (r) {
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

    // Mask-aware bilinear sample in space, linear between hourly frames.
    // Returns OUT = [u, v] in m/s (reused array), or null over land/outside.
    var OUT = [0, 0];
    function sample(f, lat, lon, tSec) {
        var fx = (lon - f.lon0) / f.dlon, fy = (f.lat1 - lat) / f.dlat;
        var i = Math.floor(fx), j = Math.floor(fy);
        if (i < 0 || j < 0 || i >= f.nx - 1 || j >= f.ny - 1) { return null; }
        var ax = fx - i, ay = fy - j, m = f.mask, nx = f.nx;
        var c00 = j * nx + i, c10 = c00 + 1, c01 = c00 + nx, c11 = c01 + 1;
        var w00 = (1 - ax) * (1 - ay) * m[c00], w10 = ax * (1 - ay) * m[c10];
        var w01 = (1 - ax) * ay * m[c01], w11 = ax * ay * m[c11];
        var ws = w00 + w10 + w01 + w11;
        if (ws < 0.5) { return null; }
        var h = (tSec - f.t0) / f.dt;
        if (h < 0) { h = 0; } else if (h > f.nf - 1) { h = f.nf - 1; }
        var k0 = Math.floor(h), k1 = Math.min(k0 + 1, f.nf - 1), a = h - k0;
        var d = f.data, C = f.cells;
        var u0 = k0 * 2 * C, v0 = u0 + C, u1 = k1 * 2 * C, v1 = u1 + C;
        var u = (d[u0 + c00] * w00 + d[u0 + c10] * w10 + d[u0 + c01] * w01 + d[u0 + c11] * w11) * (1 - a)
              + (d[u1 + c00] * w00 + d[u1 + c10] * w10 + d[u1 + c01] * w01 + d[u1 + c11] * w11) * a;
        var v = (d[v0 + c00] * w00 + d[v0 + c10] * w10 + d[v0 + c01] * w01 + d[v0 + c11] * w11) * (1 - a)
              + (d[v1 + c00] * w00 + d[v1 + c10] * w10 + d[v1 + c01] * w01 + d[v1 + c11] * w11) * a;
        OUT[0] = u * f.scale / ws;
        OUT[1] = v * f.scale / ws;
        return OUT;
    }

    function curveTimes(c) {
        if (c.t) { return c.t; }
        var out = new Array(c.v.length);
        for (var i = 0; i < c.v.length; i++) { out[i] = c.t0 + i * c.dt; }
        return out;
    }

    function interpSeries(ts, vs, tSec) {
        var n = vs.length;
        if (!n) { return null; }
        if (tSec <= ts[0]) { return vs[0]; }
        if (tSec >= ts[n - 1]) { return vs[n - 1]; }
        var lo = 0, hi = n - 1;
        while (hi - lo > 1) {
            var mid = (lo + hi) >> 1;
            if (ts[mid] <= tSec) { lo = mid; } else { hi = mid; }
        }
        return vs[lo] + (vs[hi] - vs[lo]) * (tSec - ts[lo]) / (ts[hi] - ts[lo]);
    }

    // NOAA predicted current (kn, + flood) at a station.
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

    // Model current projected onto the station's flood axis (kn, + flood).
    function modelAt(st, tSec) {
        if (!S.field || st.flood_dir == null || !st.in_model) { return null; }
        var uv = sample(S.field, st.lat, st.lon, tSec);
        if (!uv) { return null; }
        var th = st.flood_dir * Math.PI / 180;
        return (uv[0] * Math.sin(th) + uv[1] * Math.cos(th)) * KNOTS;
    }

    function tideState(tSec) {
        var td = S.meta && S.meta.tide;
        if (!td || td.t.length < 2) { return null; }
        var v = interpSeries(td.t, td.v, tSec);
        var v2 = interpSeries(td.t, td.v, tSec + 360);
        return { v: v, rising: v2 >= v };
    }

    function describe(kn) {
        if (kn == null) { return { cls: "", text: "–" }; }
        if (Math.abs(kn) < SLACK_KN) { return { cls: "slack", text: "slack" }; }
        return { cls: kn > 0 ? "flood" : "ebb",
                 text: Math.abs(kn).toFixed(1) + " kn " + (kn > 0 ? "flood" : "ebb") };
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
            if (!n || !f || !this._canvas) { return; }
            var ctx = this._ctx, w = this._w, h = this._h, k = this._k, t = S.t;
            var segN = this._segN, nb = COLORS.length, b, q;
            segN.fill(0);
            for (q = 0; q < n; q++) {
                var x = this._x[q], y = this._y[q];
                if (this._age[q] > MAX_AGE || x < 0 || y < 0 || x > w || y > h) {
                    this._spawn(q);
                    continue;
                }
                var ll = this._toLatLon(x, y);
                var uv = sample(f, ll[0], ll[1], t);
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
                "<span>3+ kn</span></div>";
            return d;
        }
    });

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
        if (!S.clock || !S.meta) { return; }
        var html = '<div class="time">' + esc(fmtClock.format(new Date(S.t * 1000))) + "</div>";
        var td = tideState(S.t);
        if (td) {
            html += '<div class="cur-row">Montauk tide ' + td.v.toFixed(1) + " ft, " +
                    (td.rising ? "rising" : "falling") + "</div>";
        }
        var st = S.stationsById[S.station];
        if (st) {
            var d = describe(noaaAt(st, S.t));
            html += '<div class="cur-row">' + esc(st.label) + ': <span class="' + d.cls + '">' +
                    d.text + "</span></div>";
        }
        S.clock.getContainer().innerHTML = html;
    }

    // ------------------------------------------------------------ stations --
    function stationTip(st) {
        var nv = noaaAt(st, S.t), d = describe(nv), m = describe(modelAt(st, S.t));
        var dir = nv == null || Math.abs(nv) < SLACK_KN ? null : (nv > 0 ? st.flood_dir : st.ebb_dir);
        return "<b>" + esc(st.label) + "</b>" +
            (st.label !== st.name ? '<br><span style="color:#94a3b8">' + esc(st.name) + "</span>" : "") +
            "<br>NOAA: " + d.text + (dir != null ? " → " + Math.round(dir) + "°" : "") +
            "<br>Model: " + (st.in_model ? m.text : "outside model");
    }

    function buildStations() {
        if (!S.stationLayer || !S.meta) { return; }
        S.stationLayer.clearLayers();
        S.markers = {};
        (S.meta.stations || []).forEach(function (st) {
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
        for (var id in S.markers) {
            var st = S.stationsById[id], m = S.markers[id], v = noaaAt(st, S.t);
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
        if (!S.meta) { S.station = id; return; }          // applied once data loads
        if (!S.stationsById[id] || id === S.station) { return; }
        S.station = id;
        if (fromMap) { setProps("currents-station", { value: id }); }
        updateStationStyles();
        updateClock();
        buildChart();
    }

    // --------------------------------------------------------------- chart --
    function waitForPlotly(cb, tries) {
        if (window.Plotly) { cb(); return; }
        if ((tries || 0) > 300) { return; }                 // give up after 30 s
        setTimeout(function () { waitForPlotly(cb, (tries || 0) + 1); }, 100);
    }

    function median(a) {
        if (!a.length) { return null; }
        a = a.slice().sort(function (x, y) { return x - y; });
        var m = a.length >> 1;
        return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
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

    function phrase(off, what) {
        if (off === null) { return null; }
        if (Math.abs(off) < 15 * 60) { return "<b>at " + what + "</b>"; }
        return "<b>" + fmtDur(off) + (off > 0 ? " after" : " before") + "</b> " + what;
    }

    // The lesson: how this station's slack / max flood line up with Montauk's tide.
    function updateBadge(st) {
        var el = $("currents-badge");
        if (!el) { return; }
        if (!st || !S.meta) { el.innerHTML = ""; return; }
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
        var hilo = S.meta.tide.hilo || [];
        var hw = hilo.filter(function (x) { return x.type === "H"; }).map(function (x) { return x.t; });
        var lw = hilo.filter(function (x) { return x.type === "L"; }).map(function (x) { return x.t; });
        var parts = [];
        var a = phrase(typicalOffset(hw, endFlood), "high water");
        var b = phrase(typicalOffset(lw, endEbb), "low water");
        if (a || b) { parts.push("slack comes " + [a, b].filter(Boolean).join(" and ")); }
        var c = phrase(typicalOffset(hw, floods), "high water");
        if (c) { parts.push("strongest flood " + c); }
        el.innerHTML = parts.length
            ? "<b>" + esc(st.label) + '</b> <span class="muted">vs Montauk tide this week:</span> ' +
              parts.join(" · ")
            : "<b>" + esc(st.label) + '</b> <span class="muted">— NOAA lists no slack/max ' +
              "times for this station.</span>";
    }

    function buildChart() {
        var gd = $("currents-chart");
        if (!gd || !S.meta || !S.field) { return; }
        if (!window.Plotly) { waitForPlotly(buildChart); return; }
        var st = S.stationsById[S.station], td = S.meta.tide, f = S.field;
        var traces = [{
            x: td.t.map(wall), y: td.v, name: "Montauk tide (NOAA)", type: "scatter",
            mode: "lines", line: { color: "#60a5fa", width: 2 },
            hovertemplate: "%{y:.1f} ft<extra>Tide</extra>"
        }];
        if (S.opts.model && td.model) {
            traces.push({
                x: td.model.v.map(function (_, i) { return wall(td.model.t0 + i * td.model.dt); }),
                y: td.model.v, name: "water level (model)", mode: "lines",
                line: { color: "#60a5fa", width: 1.2, dash: "dot" },
                hovertemplate: "%{y:.1f} ft<extra>Model level</extra>"
            });
        }
        var hilo = td.hilo || [];
        traces.push({
            x: hilo.map(function (h) { return wall(h.t); }), y: hilo.map(function (h) { return h.v; }),
            mode: "markers", showlegend: false, hoverinfo: "skip",
            marker: { size: 5, color: "#60a5fa" }
        });
        var maxC = 1;
        if (st) {
            st.curve.v.forEach(function (v) { maxC = Math.max(maxC, Math.abs(v)); });
            traces.push({
                x: curveTimes(st.curve).map(wall), y: st.curve.v, yaxis: "y2",
                name: st.label + " current (NOAA)", mode: "lines",
                line: { color: "#fbbf24", width: 2 },
                fill: "tozeroy", fillcolor: "rgba(251,191,36,0.10)",
                hovertemplate: "%{y:.1f} kn<extra>NOAA</extra>"
            });
            if (S.opts.model && st.in_model && st.flood_dir != null) {
                var mx = [], my = [];
                for (var i = 0; i < f.nf; i++) {
                    var tt = f.t0 + i * f.dt, mv = modelAt(st, tt);
                    if (mv != null) { mx.push(wall(tt)); my.push(Math.round(mv * 100) / 100); }
                }
                my.forEach(function (v) { maxC = Math.max(maxC, Math.abs(v)); });
                if (my.length) {
                    traces.push({
                        x: mx, y: my, yaxis: "y2", mode: "lines",
                        name: "model (" + (S.depth === "davg" ? "depth-avg" : "surface") + ")",
                        line: { color: "#fbbf24", width: 1.3, dash: "dot" },
                        hovertemplate: "%{y:.1f} kn<extra>Model</extra>"
                    });
                }
            }
            var ev = st.events || [];
            var pick = function (type) { return ev.filter(function (e) { return e.type === type; }); };
            var sl = pick("slack"), fl = pick("flood"), eb = pick("ebb");
            traces.push({
                x: sl.map(function (e) { return wall(e.t); }), y: sl.map(function () { return 0; }),
                yaxis: "y2", mode: "markers", showlegend: false,
                marker: { symbol: "circle-open", size: 7, color: SLACK, line: { width: 1.5 } },
                hovertemplate: "Slack<extra></extra>"
            });
            traces.push({
                x: fl.map(function (e) { return wall(e.t); }), y: fl.map(function (e) { return e.v; }),
                yaxis: "y2", mode: "markers", showlegend: false,
                marker: { symbol: "triangle-up", size: 7, color: FLOOD },
                hovertemplate: "Max flood %{y:.1f} kn<extra></extra>"
            });
            traces.push({
                x: eb.map(function (e) { return wall(e.t); }), y: eb.map(function (e) { return e.v; }),
                yaxis: "y2", mode: "markers", showlegend: false,
                marker: { symbol: "triangle-down", size: 7, color: EBB },
                hovertemplate: "Max ebb %{y:.1f} kn<extra></extra>"
            });
        }
        S.xr = [wall(f.t0), wall(f.tEnd)];
        var wide = gd.clientWidth >= 620;   // phones: no legend / axis titles
        var layout = {
            paper_bgcolor: "#0a1628", plot_bgcolor: "#0a1628",
            font: { color: "#94a3b8", size: 10 },
            margin: MARGIN, showlegend: wide,
            legend: { orientation: "h", x: 0, y: 1, yanchor: "bottom", font: { size: 10 },
                      bgcolor: "rgba(0,0,0,0)" },
            hovermode: "x unified", dragmode: false,
            hoverlabel: { bgcolor: "#0f1d32", bordercolor: "#334155",
                          font: { color: "#e2e8f0", size: 11 } },
            xaxis: { type: "date", range: S.xr, fixedrange: true, automargin: false,
                     gridcolor: "#1e293b", linecolor: "#334155", dtick: 86400000,
                     tickformat: "%a %-d", hoverformat: "%a %b %-d, %-I:%M %p" },
            yaxis: { fixedrange: true, automargin: false, zeroline: false,
                     gridcolor: "#1e293b", tickfont: { color: "#60a5fa" },
                     title: { text: wide ? "tide ft" : "", font: { size: 10, color: "#60a5fa" } } },
            yaxis2: { overlaying: "y", side: "right", fixedrange: true, automargin: false,
                      range: [-maxC * 1.15, maxC * 1.15], showgrid: false,
                      zeroline: true, zerolinecolor: "#475569", tickfont: { color: "#fbbf24" },
                      title: { text: wide ? "current kn (+flood)" : "",
                               font: { size: 10, color: "#fbbf24" } } }
        };
        window.Plotly.react(gd, traces, layout,
                            { displayModeBar: false, responsive: true, scrollZoom: false });
        S.chartReady = true;
        updateBadge(st);
        updateCursor();
    }

    function updateCursor() {
        var layer = $("currents-cursor-layer"), gd = $("currents-chart");
        if (!layer || !gd || !S.xr || !S.chartReady) { return; }
        if (!S.cursorEl || !layer.contains(S.cursorEl)) {
            S.cursorEl = document.createElement("div");
            S.cursorEl.className = "currents-cursor";
            layer.appendChild(S.cursorEl);
        }
        var w = gd.clientWidth - MARGIN.l - MARGIN.r;
        var frac = (wall(S.t) - S.xr[0]) / (S.xr[1] - S.xr[0]);
        S.cursorEl.style.left = (MARGIN.l + Math.max(0, Math.min(1, frac)) * w) + "px";
        S.cursorEl.style.top = MARGIN.t + "px";
        S.cursorEl.style.bottom = MARGIN.b + "px";
    }

    // Click / drag across the chart to scrub time.
    function timeFromEvent(ev) {
        var gd = $("currents-chart");
        if (!gd || !S.xr) { return null; }
        var pt = ev.touches ? ev.touches[0] : ev, r = gd.getBoundingClientRect();
        var cx = pt.clientX - r.left, cy = pt.clientY - r.top;
        if (cy < MARGIN.t - 4 || cy > r.height - MARGIN.b + 4) { return null; }   // legend / ticks
        var frac = (cx - MARGIN.l) / (r.width - MARGIN.l - MARGIN.r);
        frac = Math.max(0, Math.min(1, frac));
        return unwall(S.xr[0] + frac * (S.xr[1] - S.xr[0]));
    }

    // Window-level move/up listeners exist only while a scrub is in progress:
    // this file loads on every page, and a permanent non-passive touchmove
    // listener would make mobile map panning wait on JS everywhere.
    function scrubStart(ev) {
        var t = timeFromEvent(ev);
        if (t === null) { return; }
        S.scrubbing = true;
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
    }

    function bindScrub() {
        var gd = $("currents-chart");
        if (!gd || gd.getAttribute("data-scrub")) { return; }
        gd.setAttribute("data-scrub", "1");
        gd.addEventListener("mousedown", scrubStart);
        gd.addEventListener("touchstart", scrubStart, { passive: true });
    }

    function seek(t) {
        var f = S.field;
        if (!f) { return; }
        S.t = Math.max(f.t0, Math.min(f.tEnd, t));
        updateCursor();
        updateClock();
        updateStationStyles();
    }

    // ------------------------------------------------------------- loading --
    function load() {
        if (!S.map) { return; }                              // attach() calls us
        var token = ++S.loadToken;
        ensureManifest().then(function (man) {
            if (token !== S.loadToken || !S.map) { return null; }
            var weeks = man.weeks || [];
            if (!weeks.length) {
                showStatus("Current data is still being prepared — check back soon.");
                return null;
            }
            if (!weeks.some(function (w) { return w.id === S.week; })) { S.week = weeks[0].id; }
            var w = weeks.filter(function (x) { return x.id === S.week; })[0];
            var depth = w.files[S.depth] ? S.depth : (w.files.surface ? "surface" : "davg");
            var v = "?v=" + encodeURIComponent(w.built_at || man.generated_at || "1");
            var key = w.id + "|" + depth;
            var weekChanged = !S.meta || S.meta.week.id !== w.id;
            if (weekChanged || !S.fields[key]) { showStatus("Loading " + w.title + "…"); }
            return Promise.all([
                weekChanged ? fetchJson(API + w.files.meta + v) : Promise.resolve(S.meta),
                S.fields[key] ? Promise.resolve(S.fields[key])
                              : fetchBuf(API + w.files[depth] + v).then(decodeFrames)
            ]).then(function (res) {
                if (token !== S.loadToken || !S.map) { return; }
                S.meta = res[0];
                S.field = res[1];
                S.fields[key] = res[1];
                Object.keys(S.fields).forEach(function (k) {
                    if (k.split("|")[0] !== w.id) { delete S.fields[k]; }   // bound memory
                });
                S.stationsById = {};
                (S.meta.stations || []).forEach(function (s) { S.stationsById[s.id] = s; });
                if (!S.stationsById[S.station]) {
                    S.station = S.stationsById[S.meta.default_station]
                        ? S.meta.default_station : ((S.meta.stations || [])[0] || {}).id;
                }
                if (weekChanged) {
                    S.t = S.field.t0;
                    buildStations();
                }
                if (!S.fitted) {
                    S.fitted = true;
                    S.map.fitBounds([[S.field.lat0, S.field.lon0], [S.field.lat1, S.field.lon1]],
                                    { padding: [4, 4] });
                }
                if (S.particles) { S.particles._reset(); }
                hideStatus();
                buildChart();
                updateClock();
                updateStationStyles();
                if (weekChanged && S.wantPlay) {
                    S.playing = true;
                    setProps("currents-play", { children: "Pause" });
                }
                startLoop();
            });
        }).catch(function (e) {
            if (token === S.loadToken) {
                showStatus("Couldn't load current data (" + e.message + ").", true);
            }
        });
    }

    // ---------------------------------------------------------- animation --
    function frame(now) {
        S.raf = 0;
        if (!S.map || !S.field || document.hidden) { return; }   // resumed on visibility
        if (S.lastFrame && now - S.lastFrame < FRAME_MS - 1) {
            S.raf = requestAnimationFrame(frame);
            return;
        }
        var dt = S.lastFrame ? Math.min((now - S.lastFrame) / 1000, 0.25) : 0;
        S.lastFrame = now;
        if (S.playing && !S.scrubbing) {
            S.t += dt * S.rate;
            if (S.t > S.field.tEnd) { S.t = S.field.t0; }        // loop the week
        }
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
        map.createPane("currentsStations").style.zIndex = 470;
        S.particles = new ParticleLayer().addTo(map);
        S.stationLayer = L.layerGroup();
        S.clock = new Clock().addTo(map);
        new Legend().addTo(map);
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
        load();
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
        S.clock = null;
        S.statusEl = null;
        S.cursorEl = null;
        S.chartReady = false;
        S.fitted = false;
        S.playing = false;
        S.meta = null;                      // rebuild stations/chart on the next visit
    }

    // ----------------------------------------------------------------- API --
    window.Currents = {
        setWeek: function (id) {
            if (!id || (id === S.week && S.meta && S.meta.week.id === id)) { return; }
            S.week = id;
            load();
        },
        setDepth: function (d) {
            d = d === "davg" ? "davg" : "surface";
            if (d === S.depth && S.field) { return; }
            S.depth = d;
            load();
        },
        setRate: function (r) {
            r = +r;
            if (r > 0) { S.rate = r; }
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
            return S.playing;
        },
        step: function (sec) {
            S.playing = false;
            S.wantPlay = false;
            setProps("currents-play", { children: "Play" });
            seek(S.t + (+sec || 0));
        },
        _state: S                           // for poking at in the browser console
    };
})();
