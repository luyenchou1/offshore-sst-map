// Cold-boot check for the /sst page helpers (assets/sst.js, window.SST). Relaunch, Oct 16 2026.
//
//   node tools/boot_sst_pages.mjs                        # 6 boots x 4 pages, then the init checks
//   node tools/boot_sst_pages.mjs --rounds 10 --pages 4 --port 8072 --cache-dir /tmp/sst-cache
//
// The bug: on /sst loads right after a server boot, window.SST was sometimes missing (map taps and the
// fishing-activity tile retry did nothing). The cause was the server: Dash 4.0 builds the page's script list in a
// first-request hook that marks itself done before it starts, so pages requested together right after a boot came
// back with no /assets at all (app._finish_dash_setup now runs it at import). Before the fix: 8 of 32 such pages.
//
// 1. Boots gunicorn exactly as production (1 worker, 16 threads, timeout 180) --rounds times and, as soon as the
//    port accepts a TCP connection (no HTTP request first, so the pages are the first requests the app sees),
//    opens --pages /sst pages at once. Each page must list every /assets script and have window.SST hooked to the
//    map (SST.ready(), SST._state.map is #sst-map).
// 2. On the last boot: sst.js run before Leaflet exists (moved ahead of dash-leaflet), run only after the map and a
//    fishing-activity layer exist (adopted when the reader first pans, zooms or taps), run twice (set up once), and
//    across page changes (/sst -> / -> /sst: the old map is dropped, the new one hooked). Each ends with a map tap
//    that reads the water.
//
// Needs Playwright (as tools/smoke_sst.mjs) and the venv. LABS_STATS_DIR is a temp dir; the cache is $SST_CACHE_DIR
// or --cache-dir (default ./cache): the page opens on its newest week, so use a copy that has it. Exit 1 on a failure.

import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, openSync, readFileSync } from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, arr) => {
    if (v.startsWith('--')) { a.push([v.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : '1']); }
    return a;
}, []));
const PORT = +(args.port || 8072);
const BASE = `http://127.0.0.1:${PORT}`;
const ROUNDS = +(args.rounds || 6), PAGES = +(args.pages || 4);
const PY = args.python || path.join(ROOT, '.venv/bin/python');
const CACHE = args['cache-dir'] || process.env.SST_CACHE_DIR || '';
const ASSET_JS = ['currents.js', 'dashExtensions_default.js', 'labs.js', 'sst.js'];

async function loadPlaywright() {
    const tries = [process.env.PLAYWRIGHT, 'playwright',
                   `${os.homedir()}/Code/bf6-recon/node_modules/playwright/index.mjs`].filter(Boolean);
    for (const t of tries) {
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

// ---------------------------------------------------------------- server --
const tmp = mkdtempSync(path.join(os.tmpdir(), 'sst-boot-'));
let server = null;
function portOpen() {
    return new Promise(res => {
        const s = net.connect(PORT, '127.0.0.1');
        s.once('connect', () => { s.destroy(); res(true); });
        s.once('error', () => res(false));
    });
}
// Never leave a gunicorn behind (an interrupted run, a closed stdout pipe)
function killServer() {
    if (server) { try { process.kill(-server.pid, 'SIGKILL'); } catch (e) { /* gone */ } }
}
process.on('exit', killServer);
for (const sig of ['SIGINT', 'SIGTERM', 'SIGPIPE']) { process.on(sig, () => { killServer(); process.exit(130); }); }
process.stdout.on('error', () => { killServer(); process.exit(1); });

async function stopServer() {
    killServer();
    server = null;
    for (let i = 0; i < 100 && await portOpen(); i++) { await sleep(50); }
}
async function boot(n) {
    await stopServer();
    if (await portOpen()) { console.error(`port ${PORT} is in use`); process.exit(2); }
    const env = { ...process.env, OBJC_DISABLE_INITIALIZE_FORK_SAFETY: 'YES', LABS_STATS_DIR: path.join(tmp, 'stats') };
    if (CACHE) { env.SST_CACHE_DIR = CACHE; }
    const log = openSync(path.join(tmp, `server-${n}.log`), 'w');
    const cmd = ['-m', 'gunicorn', 'app:server', '--bind', `127.0.0.1:${PORT}`, '--workers', '1', '--threads', '16',
                 '--timeout', '180'];
    if (CONTROL_SOCKET) { cmd.push('--control-socket', path.join(tmp, 'gunicorn.ctl')); }   // >= 25: not in the repo
    server = spawn(PY, cmd, { cwd: ROOT, env, stdio: ['ignore', log, log], detached: true });
    const t0 = Date.now();
    while (!await portOpen()) {
        if (Date.now() - t0 > 30000) { console.error('gunicorn did not start; log in ' + tmp); process.exit(2); }
        await sleep(25);
    }
    return Date.now() - t0;
}

const CONTROL_SOCKET = /--control-socket/.test(spawnSync(PY, ['-m', 'gunicorn', '--help'], { cwd: ROOT, encoding: 'utf8' }).stdout || '');

// ----------------------------------------------------------------- pages --
const browser = await chromium.launch({ headless: true });
async function newPage() {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    await ctx.addInitScript(() => {
        try { localStorage.setItem('gotone-sst-intro-v1', '1'); } catch (e) { /* the modal would cover the map */ }
    });
    const page = await ctx.newPage();
    page._errors = [];
    page.on('pageerror', e => page._errors.push(String(e).slice(0, 200)));
    return page;
}
const pageState = page => page.evaluate(names => {
    const S = window.SST, m = S && S._state && S._state.map;
    const have = Array.from(document.scripts).map(s => (s.src || '').split('?')[0].split('/assets/')[1]).filter(Boolean);
    return { SST: !!S, ready: !!(S && S.ready && S.ready()), map: !!(m && m.getContainer().id === 'sst-map'),
             missing: names.filter(n => have.indexOf(n) < 0), waits: S ? S._state.waits : null };
}, ASSET_JS);
const weekShown = page => page.waitForFunction(() => {
    const i = document.querySelector('img.leaflet-image-layer');
    const o = document.getElementById('map-loading-overlay');
    return i && i.src.startsWith('data:') && o && getComputedStyle(o).display === 'none';
}, null, { timeout: 90000, polling: 200 }).then(() => true).catch(() => false);
async function tapReads(page) {
    await page.waitForFunction(() => window.SST && window.SST._state.map, null, { timeout: 10000 }).catch(() => {});
    const p = await page.evaluate(() => {
        const m = window.SST && window.SST._state.map;
        if (!m) { return null; }
        m.setView([40.4, -71.4], 7, { animate: false });
        const q = m.latLngToContainerPoint([40.45, -71.0]), b = m.getContainer().getBoundingClientRect();
        return { x: b.x + q.x, y: b.y + q.y };
    });
    if (!p) { return 'no map handle'; }
    await sleep(500);
    await page.mouse.click(p.x, p.y);
    return page.waitForFunction(() => [...document.querySelectorAll('.leaflet-tooltip.sst-tooltip')]
        .some(t => /°F/.test(t.innerText)), null, { timeout: 15000 })
        .then(() => page.evaluate(() => document.querySelector('.leaflet-tooltip.sst-tooltip').innerText.split('\n')[0]))
        .catch(() => '');
}
// The /sst page with its HTML changed by fn(html) (the page itself, not the scripts).
async function routeHtml(page, fn) {
    await page.route(u => new URL(u).pathname === '/sst', async route => {
        const resp = await route.fetch();
        await route.fulfill({ response: resp, body: fn(await resp.text()) });
    });
}
const SST_TAG = /<script src="\/assets\/sst\.js[^"]*"><\/script>\s*/;
const injectSst = page => page.evaluate(() => new Promise(res => {
    const s = document.createElement('script');
    s.src = '/assets/sst.js?again=' + Date.now();
    s.onload = res; s.onerror = res;
    document.body.appendChild(s);
}));
const dashNavigate = (page, to) => page.evaluate(href => {
    window.history.pushState({}, '', href);
    window.dispatchEvent(new CustomEvent('_dashprivate_pushstate'));
}, to);

// ------------------------------------------------------------ 1. boots --
let total = 0, good = 0;
for (let round = 1; round <= ROUNDS; round++) {
    const up = await boot(round);
    const pages = await Promise.all(Array.from({ length: PAGES }, () => newPage()));
    const res = await Promise.all(pages.map(async page => {
        let nav = '';
        await page.goto(BASE + '/sst', { waitUntil: 'load', timeout: 60000 }).catch(e => { nav = String(e).slice(0, 80); });
        await page.waitForFunction(() => window.SST && window.SST._state.map, null, { timeout: 15000 }).catch(() => {});
        const st = await pageState(page).catch(e => ({ err: String(e) }));
        return { nav, ...st, errors: page._errors };
    }));
    for (const r of res) {
        total++;
        const ok = r.SST && r.ready && r.map && r.missing && !r.missing.length && !r.nav;
        if (ok) { good++; }
        if (!ok) {
            check(`boot ${round}: a page right after the boot`, false, JSON.stringify(r).slice(0, 300));
        }
    }
    if (round === 1) {
        let v = '?';
        try { v = (/Starting gunicorn (\S+)/.exec(readFileSync(path.join(tmp, 'server-1.log'), 'utf8')) || [])[1] || '?'; } catch (e) { /* no log */ }
        console.log(`gunicorn ${v}, ${PAGES} pages at once after each of ${ROUNDS} boots`);
    }
    console.log(`boot ${round} (port open after ${up} ms): ${res.filter(r => r.SST && r.ready && r.map).length}/${res.length} pages set up`);
    await Promise.all(pages.map(p => p.context().close()));
}
check(`${total} /sst pages loaded right after a boot have every /assets script and window.SST on the map`,
      good === total, `${good}/${total}`);

// ------------------------------------------------- 2. init robustness --
{   // sst.js before dash-leaflet: window.L doesn't exist yet, the hooks go in when it does
    const page = await newPage();
    await routeHtml(page, html => {
        const tag = (html.match(SST_TAG) || [''])[0];
        return html.replace(SST_TAG, '').replace(/<script src="\/_dash-component-suites\/dash_leaflet\//, tag + '$&');
    });
    await page.goto(BASE + '/sst');
    await weekShown(page);
    const st = await pageState(page);
    check('sst.js before Leaflet: waits for window.L, then hooks the map', st.SST && st.ready && st.map && st.waits > 0,
          JSON.stringify(st));
    const tip = await tapReads(page);
    check('  ... and a map tap reads the water', /°F/.test(tip), tip);
    check('  ... no page errors', !page._errors.length, page._errors.join(' | '));
    await page.context().close();
}
{   // sst.js only after the map and a fishing-activity layer exist: both adopted
    const page = await newPage();
    await routeHtml(page, html => html.replace(SST_TAG, ''));
    await page.goto(BASE + '/sst');
    await weekShown(page);
    await page.getByLabel('Fishing activity', { exact: false }).check();
    await page.waitForSelector('.leaflet-gfw-pane img, [class*=gfw] img, img[src*="/api/gfw/"]', { timeout: 15000 }).catch(() => {});
    const before = await page.evaluate(() => typeof window.SST);
    await injectSst(page);
    const box = await page.locator('#sst-map').boundingBox();
    // (a bare mouse move isn't enough: Leaflet only fires the DOM events the map listens for; a pan always fires)
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 20, { steps: 5 });
    await page.mouse.up();
    await sleep(500);
    const st = await pageState(page);
    const gfw = await page.evaluate(() => {
        const m = window.SST && window.SST._state.map, out = [];
        if (m) { m.eachLayer(l => { if (typeof l._url === 'string' && l._url.indexOf('/api/gfw/') >= 0) { out.push(!!l._gotoneGfw && l.listens('tileerror')); } }); }
        return out;
    });
    check('sst.js after the map exists: the map is adopted', before === 'undefined' && st.SST && st.ready && st.map,
          JSON.stringify(st));
    check('  ... and its fishing-activity layer gets the 503 retry', gfw.length > 0 && gfw.every(Boolean), JSON.stringify(gfw));
    const tip = await tapReads(page);
    check('  ... and a map tap reads the water', /°F/.test(tip), tip);
    check('  ... no page errors', !page._errors.length, page._errors.join(' | '));
    await page.context().close();
}
{   // a second copy of sst.js, then page changes /sst -> / -> /sst
    const page = await newPage();
    await page.goto(BASE + '/sst');
    await weekShown(page);
    const hooks = () => page.evaluate(() => [window.L.Map.prototype._initHooks.length, window.L.TileLayer.prototype._initHooks.length]);
    const h0 = await hooks();
    await page.evaluate(() => { window.__firstSST = window.SST; });
    await injectSst(page);
    const same = await page.evaluate(() => window.SST === window.__firstSST);
    const h1 = await hooks();
    check('sst.js run twice: set up once (same window.SST, no extra Leaflet hooks)',
          same && h0[0] === h1[0] && h0[1] === h1[1], `hooks ${h0} -> ${h1}`);
    await page.evaluate(() => { window.__firstMap = window.SST._state.map; });
    await dashNavigate(page, '/');
    await page.waitForSelector('.labs-page', { timeout: 15000 }).catch(() => {});
    await sleep(500);
    const away = await page.evaluate(() => window.SST._state.map === null && !document.getElementById('sst-map'));
    check('page change /sst -> /: the old map is dropped', away);
    await dashNavigate(page, '/sst');
    await weekShown(page);
    await page.waitForFunction(() => window.SST._state.map && window.SST._state.map !== window.__firstMap, null,
                               { timeout: 15000 }).catch(() => {});
    const back = await page.evaluate(() => {
        const m = window.SST._state.map;
        return !!m && m !== window.__firstMap && m.getContainer().id === 'sst-map' && m.getContainer().isConnected;
    });
    check('page change / -> /sst: the new map is hooked', back);
    const tip = await tapReads(page);
    check('  ... and a map tap reads the water', /°F/.test(tip), tip);
    check('  ... no page errors', !page._errors.length, page._errors.join(' | '));
    await page.context().close();
}

await browser.close();
await stopServer();
console.log(failures ? `\n${failures} check(s) failed (server logs in ${tmp})` : '\nall checks passed');
process.exit(failures ? 1 : 0);
