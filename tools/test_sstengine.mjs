// Headless checks of the client-side SST engine (assets/sstengine.js, window.SSTEngine) in the real /sst page.
//
//   node tools/test_sstengine.mjs                  # boots its own server on --port (default 8081), NOAA blocked
//   node tools/test_sstengine.mjs --port 8083
//
// A synthetic week with known values is written into a temp cache as the page's default week (data/sstgrid, so the
// bytes are the real format): a sharp front at 40.0 N (62 F north, 72 F south, rising 0.5 F a day), land in the
// north-west, MUR's 0.02 deg grid over the coverage box; plus a week saved the old way (whole degrees, the old
// corridor). The Python decode of the same bytes gives the expected values. Checks: the week draws (canvas pixels =
// SSTEngine.colorFor of the value there), readouts at known points and land, palettes, ranges, stepped bands,
// isotherms and labels, the break highlight at the front only, "haze" ignored, the day crossfade (and none with
// reduced motion), histogram and Auto range = the server's, Fit to view, events, an old week on its own grid, and a
// week that can't download ("error"). Needs Playwright (as tools/smoke_sst.mjs) and the venv. Exit 1 on a failure.

import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, openSync, mkdirSync } from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, arr) => {
    if (v.startsWith('--')) { a.push([v.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : '1']); }
    return a;
}, []));
const PORT = +(args.port || 8081);
const BASE = `http://127.0.0.1:${PORT}`;
const PY = args.python || path.join(ROOT, '.venv/bin/python');

async function loadPlaywright() {
    for (const t of [process.env.PLAYWRIGHT, 'playwright', `${os.homedir()}/Code/bf6-recon/node_modules/playwright/index.mjs`].filter(Boolean)) {
        try { return await import(t); } catch (e) { /* next */ }
    }
    console.error('Playwright not found: npm i playwright, or set PLAYWRIGHT=/path/to/playwright/index.mjs');
    process.exit(2);
}
const { chromium } = await loadPlaywright();
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
function check(name, ok, detail = '') {
    if (!ok) { failures++; }
    console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? '  ' + detail : ''}`);
}

// ------------------------------------------------------------ fixtures --
const tmp = mkdtempSync(path.join(os.tmpdir(), 'sst-engine-test-'));
const FIXTURE = `
import gzip, json, os, sys, base64, io
from datetime import date, timedelta
import numpy as np
sys.path.insert(0, ${JSON.stringify(ROOT)})
from data import sstgrid, sstmeta
out = ${JSON.stringify(tmp)}
end = date.today() - timedelta(days=2)              # the page's default week when NOAA can't be asked
dates = [str(end - timedelta(days=6 - k)) for k in range(7)]
lats = np.round(np.linspace(44.0, 37.8, 311), 4)
lons = np.round(np.linspace(-76.0, -64.8, 561), 4)
la, lo = np.meshgrid(lats, lons, indexing="ij")
base = 62 + 10 / (1 + np.exp((la - 40.0) / 0.02)) + 0.3 * np.sin(lo * 2.0)
land = (la > 41.0) & (lo < -72.0)
days = []
for k in range(7):
    f = np.round(base + 0.5 * k, 1).astype(np.float32)
    f[land] = np.nan
    days.append(f)
meta = sstmeta.make_meta("MUR", "jplMURSST41", "https://coastwatch.pfeg.noaa.gov/erddap", dates)
entry = sstgrid.build_week(days, dates, lats, lons, meta=meta, coverage="box-2026-10", level=6)
d = os.path.join(out, "cache", "sst", "weeks")
os.makedirs(d, exist_ok=True)
with gzip.open(os.path.join(d, f"{end}_week.json.gz"), "wt") as fh:
    json.dump(entry, fh)
w = sstgrid.unpack_week(sstgrid.entry_blob(entry))
arr = sstgrid.week_arrays(sstgrid.entry_blob(entry), dates, w)
pts = [(42.5, -70.0), (41.0, -69.0), (39.5, -71.0), (38.2, -66.0), (40.6, -73.0), (39.95, -70.5), (40.05, -70.5),
       (43.5, -66.5), (41.5, -73.5), (36.0, -70.0), (40.0, -80.0)]
def at(lat, lon, k):
    if not (37.79 <= lat <= 44.01 and -76.01 <= lon <= -64.79):
        return None
    i, j = int(np.argmin(abs(arr["lats"] - lat))), int(np.argmin(abs(arr["lons"] - lon)))
    v = arr["raw_days"][k]["arrF"][i, j]
    return None if not np.isfinite(v) else round(float(v), 1)
expect = [{"lat": a, "lon": b, "f": [at(a, b, k) for k in range(7)]} for a, b in pts]
water = int(sum(int(v.sum()) for v in w["valid"]))
# a week saved before sprint 2: whole degrees over the old corridor, the "locked" variant only
olats = np.round(np.arange(43.8, 38.7999, -0.05), 4)
olons = np.round(np.arange(-74.96, -68.8001, 0.05), 4)
ola, olo = np.meshgrid(olats, olons, indexing="ij")
oend = date(2025, 7, 12)
odates = [str(oend - timedelta(days=6 - k)) for k in range(7)]
def npy(a):
    b = io.BytesIO(); np.save(b, a); return base64.b64encode(b.getvalue()).decode()
odays = []
for k in range(7):
    f = np.round(55 + (43.8 - ola) * 3 + k).astype(np.float64)
    f[(ola > 40.6) & (olo < -73.5)] = np.nan
    odays.append(f)
old = {"dates": odates, "vmin": 55.0, "vmax": 75.0, "lats": npy(olats), "lons": npy(olons), "frames": None,
       "raw_days": [{"arrF": npy(f), "date": dt} for f, dt in zip(odays, odates)], "dataset_id": "jplMURSST41"}
with gzip.open(os.path.join(out, "cache", "sst", f"{oend}_locked.json.gz"), "wt") as fh:
    json.dump(old, fh)
i, j = int(np.argmin(abs(olats - 41.0))), int(np.argmin(abs(olons - -70.0)))
print(json.dumps({"end": str(end), "dates": dates, "expect": expect, "water": water, "auto": [entry["vmin"], entry["vmax"]],
                  "old": {"end": str(oend), "dates": odates, "at": [41.0, -70.0], "f": [float(odays[k][i, j]) for k in range(7)],
                          "south": float(olats.min()) - 0.025}}))
`;
const fx = spawnSync(PY, ['-c', FIXTURE], { cwd: ROOT, encoding: 'utf8' });
if (fx.status !== 0) { console.error(fx.stderr); process.exit(2); }
const F = JSON.parse(fx.stdout.trim().split('\n').pop());

// -------------------------------------------------------------- server --
let server = null;
const portOpen = () => new Promise(res => {
    const s = net.connect(PORT, '127.0.0.1');
    s.once('connect', () => { s.destroy(); res(true); });
    s.once('error', () => res(false));
});
function killServer() { if (server) { try { process.kill(-server.pid, 'SIGKILL'); } catch (e) { /* gone */ } } }
process.on('exit', killServer);
for (const sig of ['SIGINT', 'SIGTERM']) { process.on(sig, () => { killServer(); process.exit(130); }); }
if (await portOpen()) { console.error(`port ${PORT} is in use`); process.exit(2); }
mkdirSync(path.join(tmp, 'stats'), { recursive: true });
server = spawn(PY, ['-c', `from app import app; app.run(debug=False, port=${PORT}, threaded=True)`], {
    cwd: ROOT, detached: true, stdio: ['ignore', openSync(path.join(tmp, 'server.log'), 'w'), openSync(path.join(tmp, 'server.log'), 'a')],
    env: { ...process.env, OBJC_DISABLE_INITIALIZE_FORK_SAFETY: 'YES', SST_CACHE_DIR: path.join(tmp, 'cache'),
           LABS_STATS_DIR: path.join(tmp, 'stats'), NETSIM: '*.noaa.gov=unreachable', PYTHONPATH: path.join(ROOT, 'tools/netsim') },
});
for (let t0 = Date.now(); !await portOpen(); await sleep(100)) {
    if (Date.now() - t0 > 60000) { console.error('server did not start; log in ' + tmp); process.exit(2); }
}

// --------------------------------------------------------------- tests --
const browser = await chromium.launch({ headless: true });
async function openPage(opts = {}) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1, ...opts });
    await ctx.addInitScript(() => { try { localStorage.setItem('gotone-sst-intro-v1', '1'); } catch (e) { /* */ } });
    const page = await ctx.newPage();
    page._errors = [];
    page.on('pageerror', e => page._errors.push(String(e)));
    await page.goto(BASE + '/sst');
    await page.waitForFunction(() => window.SSTEngine && window.SSTEngine.state().ready, null, { timeout: 60000 });
    await page.evaluate(() => window.SST._state.map.setView([41.0, -70.4], 7, { animate: false }));   // the whole box
    await sleep(600);
    return page;
}
// In the page: the drawn pixel under a lat/lon, and helpers
const HELPERS = () => {
    window.__px = (lat, lon) => {
        const E = window.SSTEngine, S = E._state, m = S.map, cv = S.layer._container;
        S.layer._redraw();
        const p = m.latLngToContainerPoint([lat, lon]), c = m.getContainer().getBoundingClientRect(), r = cv.getBoundingClientRect();
        const x = Math.floor((c.x + p.x - r.left) * cv.width / r.width), y = Math.floor((c.y + p.y - r.top) * cv.height / r.height);
        return [...cv.getContext('2d').getImageData(x, y, 1, 1).data];
    };
    window.__snap = () => {
        const S = window.SSTEngine._state, cv = S.layer._container;
        S.layer._redraw();
        return cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
    };
    // move the map as a reader would: one move at a time, waiting until Leaflet (and dash-leaflet's viewport sync,
    // which can start a zoom animation that swallows the next setView) has settled
    window.__go = async (lat, lon, z) => {
        const m = window.SST._state.map;
        for (let k = 0; k < 40; k++) {
            if (!m._animatingZoom) { m.setView([lat, lon], z, { animate: false }); }
            await new Promise(r => setTimeout(r, 60));
            const c = m.getCenter();
            if (!m._animatingZoom && m.getZoom() === z && Math.abs(c.lat - lat) < 1e-3 && Math.abs(c.lng - lon) < 1e-3) { return true; }
        }
        return false;
    };
    window.__diff = (a, b) => { let n = 0; for (let i = 0; i < a.length; i += 4) { if (Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]) > 6) { n++; } } return n; };
    window.__near = (a, b, tol) => Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]) <= tol;
};

const page = await openPage();
await page.evaluate(HELPERS);
const st = await page.evaluate(() => window.SSTEngine.state());
check('the week loads into the engine (the page\'s default week)', st.ready && st.week.nd === 7 && st.dates[6] === F.dates[6] &&
      st.week.precision === 0.1 && /\/api\/sst\/week\//.test(st.week.url), JSON.stringify(st.week).slice(0, 160));

// readouts = the Python decode of the same bytes
const reads = await page.evaluate(ex => ex.map(p => ({ v: window.SSTEngine.valueAt(p.lat, p.lon), w: window.SSTEngine.weekValuesAt(p.lat, p.lon) })), F.expect);
const badReads = [];
F.expect.forEach((p, k) => {
    const r = reads[k], want = p.f[6];
    const okNow = want === null ? r.v === null : (r.v && Math.abs(r.v.f - want) < 1e-6 && r.v.day === 6 && r.v.estimate === null);
    const okWeek = p.f.every((x, d) => x === null ? (r.w[d] === null || r.w.length === 0) : Math.abs(r.w[d] - x) < 1e-6);
    if (!okNow || !okWeek) { badReads.push(`${p.lat},${p.lon}: ${JSON.stringify(r)} want ${JSON.stringify(p.f)}`); }
});
check(`valueAt / weekValuesAt at ${F.expect.length} points (water, land, outside) match the data`, !badReads.length, badReads.join('; '));

// drawn colors = colorFor(value) at points away from the front
const drawn = await page.evaluate(ex => ex.filter(p => p.f[6] !== null && Math.abs(p.lat - 40) > 0.2).map(p => {
    const px = window.__px(p.lat, p.lon), c = window.SSTEngine.colorFor(p.f[6]);
    return { p: [p.lat, p.lon], px, c: [c[0], c[1], c[2]], css: String(c), ok: px[3] === 255 && window.__near(px, c, 9) };
}), F.expect);
check('the canvas shows colorFor(value) under each point (Web Mercator placement)', drawn.length >= 5 && drawn.every(d => d.ok),
      JSON.stringify(drawn.filter(d => !d.ok)).slice(0, 300));
check('colorFor(f) is [r, g, b] and prints as rgb(...)', /^rgb\(\d+,\d+,\d+\)$/.test(drawn[0].css), drawn[0].css);
// placement at zoom 11 across the front (cells 0.02 deg, ~22 px): at cell centres just north and south of 40.0 N the
// pixel must be that cell's color; drawn a cell (2 km) off, it would show the other side of the front
const front = await page.evaluate(async () => {
    const E = window.SSTEngine, m = window.SST._state.map, bad = [];
    for (const lon of [-73.0, -71.5, -70.5, -68.0, -66.0]) {
        await window.__go(40.0, lon, 11);
        for (const lat of [40.06, 40.02, 39.98, 39.94]) {
            const v = E.valueAt(lat, lon), px = window.__px(lat, lon), c = E.colorFor(v.f);
            if (!window.__near(px, c, 12)) { bad.push(`${lat},${lon}: ${v.f} F drawn ${px.slice(0, 3)} want ${[c[0], c[1], c[2]]}`); }
        }
    }
    await window.__go(41.0, -70.4, 7);
    return bad;
});
check('placement at zoom 11: across the front, each cell centre shows its own color (20 points)', !front.length, front.join('; '));
const landPx = await page.evaluate(() => window.__px(41.6, -73.6));
check('land is transparent (the basemap shows)', landPx[3] === 0, JSON.stringify(landPx));

// histogram + Auto range
const hist = await page.evaluate(() => ({ h: window.SSTEngine.histogram(), r: window.SSTEngine.getRange(),
    store: window.dash_component_api.getLayout('sst-store').props.data }));
const binsSum = hist.h.bins.reduce((a, b) => a + b, 0) + hist.h.below + hist.h.above;
check('histogram covers every water cell of every day; Auto range = the server\'s',
      hist.h.total === F.water && binsSum === F.water && hist.r.mode === 'auto' && hist.r.min === F.auto[0] && hist.r.max === F.auto[1] &&
      hist.store.vmin === F.auto[0] && hist.h.min === hist.r.min && hist.h.max === hist.r.max,
      `total ${hist.h.total} / ${F.water}, range ${JSON.stringify(hist.r)}, server ${F.auto}`);

// events, palette, range
const pal = await page.evaluate(() => {
    const E = window.SSTEngine, got = [];
    const off = E.on('change', ev => got.push(ev.palette + ':' + ev.vmin + '-' + ev.vmax));
    const p0 = window.__px(42.5, -70.0);
    E.setPalette('classic');
    const p1 = window.__px(42.5, -70.0), c1 = E.colorFor(E.valueAt(42.5, -70.0).f);
    E.setPalette('cvd');
    const p2 = window.__px(42.5, -70.0);
    E.setPalette('nonsense');
    const still = E.state().palette;
    E.setPalette('spectrum');
    const p3 = window.__px(42.5, -70.0);
    E.setRange({ mode: 'fixed', min: 50, max: 90 });
    const p4 = window.__px(42.5, -70.0), r4 = E.getRange();
    E.setRange({ mode: 'fixed', min: 70, max: 60 });           // ignored: min must be below max
    const r5 = E.getRange();
    E.setRange({ mode: 'auto' });
    off();
    return { p0, p1, c1: [c1[0], c1[1], c1[2]], p2, p3, p4, r4, r5, still, got, near: window.__near(p1, c1, 9), back: window.__near(p0, p3, 3) };
});
check('palette: classic and cvd recolor the map, an unknown name is ignored, spectrum comes back',
      pal.near && pal.p1.join() !== pal.p0.join() && pal.p2.join() !== pal.p1.join() && pal.still === 'cvd' && pal.back,
      JSON.stringify({ p0: pal.p0, p1: pal.p1, p2: pal.p2, still: pal.still }));
check('range: a fixed range recolors, a reversed one is ignored, Auto returns; "change" events fire',
      pal.p4.join() !== pal.p3.join() && pal.r4.mode === 'fixed' && pal.r4.min === 50 && pal.r5.min === 50 && pal.got.length >= 4,
      JSON.stringify({ r4: pal.r4, r5: pal.r5, events: pal.got }));

// stepped: two values in the same whole degree get the same color, a different degree a different one
const step = await page.evaluate(() => {
    const E = window.SSTEngine, h = E._state.week.h;
    E.setRange({ mode: 'fixed', min: 60, max: 80 });
    const c = f => { const x = E.colorFor(f); return [x[0], x[1], x[2]].join(); };
    const smooth = [c(64.2), c(64.8), c(65.2)];
    E.setOptions({ stepped: true });
    const banded = [c(64.2), c(64.8), c(65.2)];
    const snap = window.__snap(), px = window.__px(42.5, -70.0), want = c(E.valueAt(42.5, -70.0).f);
    E.setOptions({ stepped: false });
    const back = window.__snap();
    E.setRange({ mode: 'auto' });
    return { smooth, banded, drawn: px.slice(0, 3).join() === want, changed: window.__diff(snap, back) };
});
check('stepped: 1 F bands (64.2 and 64.8 share a color, 65.2 doesn\'t), drawn that way, smooth again after',
      step.smooth[0] !== step.smooth[1] && step.banded[0] === step.banded[1] && step.banded[1] !== step.banded[2] &&
      step.drawn && step.changed > 1000, JSON.stringify(step));

// isotherms + breaks (computed once per day in a worker), haze ignored
const iso = await page.evaluate(async () => {
    const E = window.SSTEngine, S = E._state;
    const before = window.__snap();
    const front0 = window.__px(40.0, -70.5), far0 = window.__px(42.5, -70.0);
    E.setOptions({ haze: true });
    const hazeChanged = window.__diff(before, window.__snap());
    E.setOptions({ breaks: true });
    await new Promise(r => { const iv = setInterval(() => { const a = S.analysis[S.day]; if (a && a.breakA) { clearInterval(iv); r(); } }, 30); setTimeout(r, 20000); });
    const front1 = window.__px(40.0, -70.5), far1 = window.__px(42.5, -70.0);
    E.setOptions({ breaks: false, isotherms: true });
    await new Promise(r => { const iv = setInterval(() => { const a = S.analysis[S.day]; if (a && a.iso) { clearInterval(iv); r(); } }, 30); setTimeout(r, 20000); });
    const withIso = window.__snap();
    const levels = S.analysis[S.day].iso.map(l => l.T);
    const labels = (S.labels && S.labels.items || []).map(i => i.T);
    E.setOptions({ isotherms: false });
    const after = window.__snap();
    return { hazeChanged, front0, front1, far0, far1, isoChanged: window.__diff(after, withIso), offAgain: window.__diff(after, before),
             levels, labels, opts: E.state().options, ms: S.stats.analysisMs };
});
const lighter = (a, b) => b[0] + b[1] + b[2] > a[0] + a[1] + a[2] + 40;
check('"haze" is ignored (no cloud layer): nothing changes', iso.hazeChanged === 0 && !('haze' in iso.opts), JSON.stringify(iso.opts));
check('breaks: the front at 40 N turns lighter, water far from it doesn\'t', lighter(iso.front0, iso.front1) &&
      iso.far0.join() === iso.far1.join(), JSON.stringify({ front: [iso.front0, iso.front1], far: [iso.far0, iso.far1] }));
check('isotherms: one line per whole degree across the front, labels on even degrees, gone when off',
      iso.isoChanged > 500 && [66, 67, 68, 69, 70, 71, 72, 73, 74].every(t => iso.levels.includes(t)) &&   // day 7: +3 F
      iso.labels.length > 0 && iso.labels.every(t => t % 2 === 0) && iso.offAgain === 0,
      JSON.stringify({ changed: iso.isoChanged, levels: iso.levels, labels: iso.labels, ms: iso.ms }));

// crossfade between days
const fade = await page.evaluate(async () => {
    const E = window.SSTEngine, S = E._state, days = [];
    const off = E.on('day', ev => days.push(ev));
    const c6 = window.__px(42.5, -70.0);
    E.setDay(6);
    E.setDay(0);
    const mid = await new Promise(r => setTimeout(() => {
        const S2 = window.SSTEngine._state, m = S2.map, cv = S2.layer._container;
        const p = m.latLngToContainerPoint([42.5, -70.0]), c = m.getContainer().getBoundingClientRect(), rr = cv.getBoundingClientRect();
        r({ px: [...cv.getContext('2d').getImageData(Math.floor(c.x + p.x - rr.left), Math.floor(c.y + p.y - rr.top), 1, 1).data],
            fading: S2.prevDay !== null });
    }, 120));
    await new Promise(r => setTimeout(r, 400));
    const c0 = window.__px(42.5, -70.0), want0 = E.colorFor(E.valueAt(42.5, -70.0).f);
    off();
    return { c6, mid, c0, want0: [want0[0], want0[1], want0[2]], days, day: S.day, prev: S.prevDay };
});
const between = (a, m, b) => [0, 1, 2].every(i => m[i] >= Math.min(a[i], b[i]) - 2 && m[i] <= Math.max(a[i], b[i]) + 2) &&
    m.slice(0, 3).join() !== a.slice(0, 3).join() && m.slice(0, 3).join() !== b.slice(0, 3).join();
check('crossfade: mid-fade the color is between the two days, then day 1\'s color; "day" event with the date',
      fade.mid.fading && between(fade.c6, fade.mid.px, fade.c0) && fade.day === 0 && fade.prev === null &&
      fade.c0.slice(0, 3).join() === fade.want0.join() && fade.days.length === 1 && fade.days[0].date === F.dates[0],
      JSON.stringify(fade));

// Fit to view
const fit = await page.evaluate(async () => {
    const E = window.SSTEngine;
    E.setDay(6);
    await window.__go(42.5, -68.5, 9);
    const r = E.fitToView(), g = E.getRange();
    E.setRange({ mode: 'auto' });
    await window.__go(41.0, -70.4, 7);
    return { r, g };
});
check('Fit to view: a fixed range from the water on screen, whole degrees, at least 3 F', fit.r && fit.g.mode === 'fixed' &&
      fit.g.min === fit.r.min && fit.r.max - fit.r.min >= 3 && Number.isInteger(fit.r.min) && fit.r.min >= 61 && fit.r.max <= 67,
      JSON.stringify(fit));

// an old week (saved before sprint 2): its own grid, whole degrees
const old = await page.evaluate(async O => {
    const E = window.SSTEngine;
    const ready = new Promise(r => { const off = E.on('ready', ev => { off(); r(ev); }); });
    E.setWeek({ dates: O.dates, grid: { url: `/api/sst/week/${O.end}.bin` } });
    const ev = await ready;
    return { ev: ev.week, v: E.weekValuesAt(O.at[0], O.at[1]), south: ev.week.bounds[0][0] };
}, F.old);
check('an old week loads on its own grid in whole degrees (old corridor)', old.ev.precision === 1 && old.ev.ny === 101 &&
      Math.abs(old.south - F.old.south) < 1e-3 && old.v.every((x, k) => Math.abs(x - F.old.f[k]) < 1e-6),
      JSON.stringify({ week: old.ev, v: old.v, want: F.old.f }));

// a week whose grid can't be fetched: "error", nothing else breaks
const err = await page.evaluate(async () => {
    const E = window.SSTEngine;
    const got = new Promise(r => { const off = E.on('error', e => { off(); r(String(e && e.message)); }); setTimeout(() => r('no error event'), 8000); });
    E.setWeek({ dates: [], grid: { url: '/api/sst/week/1999-01-01.bin' } });
    return { msg: await got, still: E.state().ready };
});
check('a grid that won\'t download fires "error" and leaves the last week drawn', /404/.test(err.msg) && err.still, JSON.stringify(err));
check('no page errors', !page._errors.length, page._errors.join(' | '));
await page.context().close();

// reduced motion: no crossfade
const rm = await openPage({ reducedMotion: 'reduce' });
const rmr = await rm.evaluate(() => { const E = window.SSTEngine; E.setDay(2); return { prev: E._state.prevDay, day: E._state.day }; });
check('reduced motion: days switch without a crossfade', rmr.prev === null && rmr.day === 2, JSON.stringify(rmr));
await rm.context().close();

await browser.close();
killServer();
console.log(failures ? `\n${failures} check(s) failed (server log in ${tmp})` : '\nall checks passed');
process.exit(failures ? 1 : 0);
