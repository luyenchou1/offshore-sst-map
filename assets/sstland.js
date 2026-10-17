/*
 * GotOne Offshore SST: the coastline (window.SSTLand), Oct 2026 (docs/coast-progress.md).
 *
 * OpenStreetMap land polygons for the coverage box, built offline by tools/build_land.py into four levels
 * (assets/sst/land/, "GOLAND1", served by app.py /api/sst/land/<name>.json, gzip, cached a week), each cut into
 * small overlapping pieces that a view culls by:
 *   level 0  zoom 5 to 9  90 m   one file, 0.5-degree pieces, fetched as soon as the SST map exists (~115 KB)
 *   level 1  zoom 10      45 m   one file per 1-degree tile (0.25-degree pieces), fetched when a view needs it
 *   level 2  zoom 11      20 m   the same
 *   level 3  zoom 12      7 m    the same
 * A view uses the level for its zoom once every file it needs has arrived (so two levels never meet at a piece
 * edge), and the best complete lower level meanwhile.
 *
 * The SST engine (assets/sstengine.js) asks for the land in view and draws it into its own pictures: the SST is
 * cleared on land, islets (islands under 1 km2, which the basemap often leaves out) are painted in the basemap's
 * land color, and from zoom 11 a rim of land color (RIM_PX wide, on the land side) covers any sliver between our
 * coast and the basemap's. Each piece becomes Path2D in Leaflet world pixels at zoom 0: "fill" (its rings,
 * nonzero), "islets", and "coast" (the ring edges that are coastline: edges on the piece's cut rectangle are left
 * out, so the rim never runs along a cut). Pieces overlap by ~20 m and a view fills them as one path, so cuts never
 * show. The rim is stroked from the level below (fewer vertices, much cheaper), widened by that level's tolerance.
 *
 * API: start(url)  begin fetching level 0 (the engine calls it when it attaches; idempotent)
 *      ready()     level 0 is in
 *      view(zoom, bounds) -> {level, tiles: [{id, fill, islets, coast, bb}], rim: {level, tol, tiles}, key}
 *                  or null; bounds: world px at zoom 0, [x0, y0, x1, y1]; asks for the level's missing files
 *      color(zoom) the basemap's land color at that zoom (Esri Dark Gray Base, sampled from its tiles)
 *      rasterize(h) -> Uint8Array nx*ny: land cover per cell of a grid (0 water .. 255 land), from level 0
 *      on("change", fn)  a level or tile arrived (redraw); returns off()
 */
(function () {
    "use strict";
    if (window.SSTLand && window.SSTLand._setup) { return; }

    var DEFAULT_URL = "/api/sst/land/0.json";
    var RIM_PX = 2;                         // CSS px of land color on the land side of the coast
    // Esri World_Dark_Gray_Base land, sampled from its JPEG tiles (Oct 17 2026): #3f3f41 at zoom 5, #414143 at 6
    // and 7, #474749 from 8 (built-up areas are a lighter #4d4d4f: the rim is thin enough not to show it).
    function color(z) { return z <= 5 ? "#3f3f41" : (z <= 7 ? "#414143" : "#474749"); }

    var S = {
        url: null, base: null, failed: false, l0: null, index: {}, tiles: { 1: {}, 2: {}, 3: {} }, pending: {},
        listeners: [], version: 0, stats: { l0Ms: null, l0Bytes: null, tilesLoaded: 0, prepMs: [] }
    };

    function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
    function wx(lon) { return 256 * (lon + 180) / 360; }
    function wy(lat) {
        var s = Math.sin(lat * Math.PI / 180);
        return 256 * (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI));
    }
    function fire() {
        S.version++;
        S.listeners.slice().forEach(function (fn) {
            try { fn(); } catch (e) { if (window.console) { console.error("SSTLand listener", e); } }
        });
    }
    function on(evt, fn) {
        if (evt !== "change") { return function () {}; }
        S.listeners.push(fn);
        return function () { var k = S.listeners.indexOf(fn); if (k >= 0) { S.listeners.splice(k, 1); } };
    }

    // One GOLAND1 piece -> Path2D in world pixels (zoom 0) + its bounding box there. Islets (islands under 1 km2)
    // get a path of their own: the engine paints them in land color instead of clearing the SST over them (the
    // basemap leaves many small islands out, so a cleared one would show its water).
    function prep(t, q) {
        var t0 = now(), fill = new Path2D(), coast = new Path2D(), islets = null, verts = 0;
        var cx0 = t.clip[0], cy0 = t.clip[1], cx1 = t.clip[2], cy1 = t.clip[3];
        var bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
        function cut(ax, ay, bx, by) {                     // an edge on the tile's cut rectangle
            return (ax === bx && (ax === cx0 || ax === cx1)) || (ay === by && (ay === cy0 || ay === cy1));
        }
        for (var k = 0; k < t.rings.length; k++) {
            var r = t.rings[k], n = r.length >> 1;
            if (n < 3) { continue; }
            var X = new Float64Array(n), Y = new Float64Array(n), IX = new Int32Array(n), IY = new Int32Array(n);
            var x = 0, y = 0;
            for (var i = 0; i < n; i++) {
                x += r[2 * i]; y += r[2 * i + 1];
                IX[i] = x; IY[i] = y;
                X[i] = wx(x / q); Y[i] = wy(y / q);
                if (X[i] < bx0) { bx0 = X[i]; } if (X[i] > bx1) { bx1 = X[i]; }
                if (Y[i] < by0) { by0 = Y[i]; } if (Y[i] > by1) { by1 = Y[i]; }
            }
            verts += n;
            fill.moveTo(X[0], Y[0]);
            for (i = 1; i < n; i++) { fill.lineTo(X[i], Y[i]); }
            fill.closePath();
            var pen = false;                               // the coastline: every edge that isn't a cut
            for (i = 0; i < n; i++) {
                var j = (i + 1) % n;
                if (cut(IX[i], IY[i], IX[j], IY[j])) { pen = false; continue; }
                if (!pen) { coast.moveTo(X[i], Y[i]); pen = true; }
                coast.lineTo(X[j], Y[j]);
            }
        }
        (t.islets || []).forEach(function (r) {
            var n = r.length >> 1, x = 0, y = 0;
            if (n < 3) { return; }
            islets = islets || new Path2D();
            for (var i = 0; i < n; i++) {
                x += r[2 * i]; y += r[2 * i + 1];
                var X = wx(x / q), Y = wy(y / q);
                if (X < bx0) { bx0 = X; } if (X > bx1) { bx1 = X; }
                if (Y < by0) { by0 = Y; } if (Y > by1) { by1 = Y; }
                if (i) { islets.lineTo(X, Y); } else { islets.moveTo(X, Y); }
            }
            islets.closePath();
            verts += n;
        });
        S.stats.prepMs.push(Math.round((now() - t0) * 10) / 10);
        if (S.stats.prepMs.length > 40) { S.stats.prepMs.shift(); }
        return { id: t.id, fill: fill, coast: coast, islets: islets, bb: [bx0, by0, bx1, by1], verts: verts,
                 rings: (t.rings || []).concat(t.islets || []), q: q };
    }

    function getJSON(url) {
        return fetch(url, { credentials: "same-origin" }).then(function (r) {
            if (!r.ok) { throw new Error("HTTP " + r.status); }
            return r.json();
        });
    }
    function start(url) {
        if (S.l0 || S.loading || S.failed) { return; }
        if (!url) {
            var d = document.getElementById("sst-look-data");
            url = (d && d.getAttribute("data-land")) || DEFAULT_URL;
        }
        S.url = url;
        S.base = url.split("?")[0].replace(/[^/]*$/, "");      // .../api/sst/land/
        S.loading = true;
        var t0 = now();
        getJSON(url).then(function (doc) {
            if (doc.format !== "GOLAND1") { throw new Error("not GOLAND1"); }
            var tiles = (doc.tiles || []).map(function (t) { return prep(t, doc.q); });
            S.l0 = { q: doc.q, tiles: tiles, tol: doc.tol_m || 90, doc: { osm_date: doc.osm_date, built: doc.built } };
            S.index = doc.index || {};
            S.loading = false;
            S.stats.l0Ms = Math.round(now() - t0);
            fire();
        }).catch(function (e) {
            S.loading = false;
            S.failed = true;                               // the SST is drawn as before (no erase, no fill-in)
            S.error = String(e);
            if (window.console) { console.warn("SSTLand: the coastline didn't load (" + e + "); the SST is drawn without it"); }
        });
    }

    function levelFor(z) { return z >= 12 ? 3 : (z >= 11 ? 2 : (z >= 10 ? 1 : 0)); }
    function tileOf(id) {                                  // "w73n40" -> [x0, y0] in degrees
        var m = /^([we])(\d+)n(\d+)$/.exec(id);
        return m ? [(m[1] === "w" ? -1 : 1) * +m[2], +m[3]] : null;
    }
    function wanted(level, b) {                            // ids of the level's files whose tile meets world box b
        var ent = S.index[String(level)], out = [];
        if (!ent) { return out; }
        Object.keys(ent.tiles).forEach(function (id) {
            var xy = tileOf(id);
            if (!xy) { return; }
            var x0 = wx(xy[0]), x1 = wx(xy[0] + 1), y0 = wy(xy[1] + 1), y1 = wy(xy[1]);
            if (x1 >= b[0] && x0 <= b[2] && y1 >= b[1] && y0 <= b[3]) { out.push(id); }
        });
        return out;
    }
    function fetchTile(level, id) {
        var key = level + "-" + id;
        if (S.tiles[level][id] || S.pending[key]) { return; }
        var ent = S.index[String(level)].tiles[id];
        S.pending[key] = true;
        getJSON(S.base + key + ".json?v=" + ent[0]).then(function (doc) {
            S.tiles[level][id] = (doc.tiles || []).map(function (t) { return prep(t, doc.q); });
            S.stats.tilesLoaded++;
            delete S.pending[key];
            fire();
        }).catch(function () {
            setTimeout(function () { delete S.pending[key]; }, 30000);   // try again on a later view
        });
    }
    function meets(t, b) { return t.bb[2] >= b[0] && t.bb[0] <= b[2] && t.bb[3] >= b[1] && t.bb[1] <= b[3]; }
    // The pieces of a level in world box b, or null while one of its files is missing (fetch: ask for those).
    function pieces(level, b, fetch) {
        if (level === 0) { return S.l0.tiles.filter(function (t) { return meets(t, b); }); }
        if (!S.index[String(level)]) { return null; }      // a level 0 built without it: use a lower one
        var ids = wanted(level, b), out = [], missing = 0;
        ids.forEach(function (id) {
            var have = S.tiles[level][id];
            if (have) { have.forEach(function (t) { if (meets(t, b)) { out.push(t); } }); }
            else { missing++; if (fetch) { fetchTile(level, id); } }
        });
        return missing ? null : out;
    }
    function tolOf(level) {
        if (level === 0) { return S.l0.tol; }
        var ent = S.index[String(level)];
        return (ent && ent.tol_m) || 20;
    }
    function view(z, b) {
        if (!S.l0) { return null; }
        var want = levelFor(z), level = 0, list = null;
        for (var lv = want; lv >= 1 && !list; lv--) {
            list = pieces(lv, b, lv === want);
            if (list) { level = lv; }
        }
        if (!list) { list = pieces(0, b, false); }
        var rl = Math.max(0, level - 1), rim = level === 0 ? list : pieces(rl, b, true);   // the rim's level
        if (!rim) { rl = 0; rim = pieces(0, b, false); }
        var ids = function (a) { return a.map(function (t) { return t.id; }).join(","); };
        return { level: level, tiles: list, rim: { level: rl, tol: rl === level ? 0 : tolOf(rl), tiles: rim },
                 key: level + ":" + ids(list) + "|" + rl + ":" + (rl === level ? "=" : ids(rim)) };
    }

    // Land cover per cell of a grid (the SST week's: h = {nx, ny, edgeN, edgeW, dlat, dlon}), from level 0 drawn
    // on a canvas one pixel per cell (rows evenly spaced in latitude, as the grid's). 0 = water, 255 = land.
    function rasterize(h) {
        if (!S.l0) { return null; }
        var cv = document.createElement("canvas");
        cv.width = h.nx; cv.height = h.ny;
        var g = cv.getContext("2d"), q = S.l0.q, path = new Path2D();
        S.l0.tiles.forEach(function (t) {
            t.rings.forEach(function (r) {
                var n = r.length >> 1, x = 0, y = 0;
                for (var i = 0; i < n; i++) {
                    x += r[2 * i]; y += r[2 * i + 1];
                    var px = (x / q - h.edgeW) / h.dlon, py = (h.edgeN - y / q) / h.dlat;
                    if (i) { path.lineTo(px, py); } else { path.moveTo(px, py); }
                }
                path.closePath();
            });
        });
        g.fillStyle = "#000";
        g.fill(path, "nonzero");
        var a = g.getImageData(0, 0, h.nx, h.ny).data, out = new Uint8Array(h.nx * h.ny);
        for (var i = 0; i < out.length; i++) { out[i] = a[4 * i + 3]; }
        return out;
    }

    window.SSTLand = {
        start: start,
        ready: function () { return !!S.l0; },
        failed: function () { return S.failed; },
        view: view,
        color: color,
        rimPx: RIM_PX,
        rasterize: rasterize,
        on: on,
        version: function () { return S.version; },
        _stats: S.stats,
        _state: S,
        _setup: true
    };
})();
