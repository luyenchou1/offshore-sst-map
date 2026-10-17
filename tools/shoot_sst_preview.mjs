// Screenshot of the SST map for the Labs preview images (tools/build_labs_previews.py turns it into
// assets/labs/sst.jpg, og-sst.jpg and og.jpg). The map draws itself, so the previews always match the
// live look: the spectrum colors, auto range, depth lines and canyon names, smooth coastlines.
//
// No basemap tiles end up in our images (Esri's terms want attribution on every use): tile requests are
// blocked and the map's background is the dark basemap's land color. The controls, legend, playbar and
// spot dots are hidden; the canyon names stay. One page load, headless Chrome's own user agent (the
// visit counter ignores it).
//
//   node tools/shoot_sst_preview.mjs [--base https://labs.gotoneapp.com] [--out build/sst-shot.png]
//                                    [--view 39.9,-71.0,8] [--size 540x430 [--scale 2]]
//   (--base http://127.0.0.1:8050 for a local server with a cached week)
//   --size: the map alone fills a window of that many CSS pixels (the social images in
//   tools/build_social.py: zoom 7 at 2x keeps the names readable on a phone)
import { mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : dflt; };
const BASE = opt('--base', 'https://labs.gotoneapp.com').replace(/\/$/, '');
const OUT = path.resolve(opt('--out', path.join(ROOT, 'build', 'sst-shot.png')));
// Whole zoom levels only. Zoom 8 at this size: New Jersey to Nantucket, the canyons and the Gulf Stream.
const [LAT, LNG, Z] = opt('--view', '39.9,-71.0,8').split(',').map(Number);
const SIZE = opt('--size', null), SCALE = Number(opt('--scale', 2));
const LAND = '#474749';        // World_Dark_Gray_Base land (the sea there is #232227, all covered by SST)

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

const browser = await chromium.launch({ headless: true });
const [VW, VH] = SIZE ? SIZE.split('x').map(Number) : [1760, 1040];
const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, deviceScaleFactor: SCALE });
await ctx.route(/arcgisonline\.com/, r => r.abort());
await ctx.addInitScript(([lat, lng, z]) => {
    try {
        localStorage.setItem('gotone-sst-intro-v1', '1');                  // no "How it works" on open
        localStorage.setItem('gotone-sst-view-v1', JSON.stringify({ lat, lng, z }));
    } catch (e) { /* storage blocked: the default view */ }
}, [LAT, LNG, Z]);
if (SIZE) {      // the map fills the window from the start, so Leaflet sizes itself to it
    await ctx.addInitScript(() => document.addEventListener('DOMContentLoaded', () => {
        const st = document.createElement('style');
        st.textContent = '#sst-map { position: fixed !important; inset: 0 !important; width: 100vw !important; ' +
                         'height: 100vh !important; z-index: 3000 !important; }';
        document.head.appendChild(st);
    }));
}
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
await page.goto(`${BASE}/sst`, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForFunction(() => window.SSTEngine && SSTEngine.state && SSTEngine.state().ready, null,
                           { timeout: 60000 });
await page.waitForTimeout(6000);                   // depth lines, names and the coast tiles
const info = await page.evaluate(() => {
    const s = SSTEngine.state(), d = s.week && s.week.dates;
    return { day: d ? d[d.length - 1] : null, range: SSTEngine.getRange(), coast: s.coast };
});
await page.addStyleTag({ content: `
    .leaflet-control-container, #sst-playbar, #sst-legend-live, .sst-legend, .leaflet-poi-pane-pane { display: none !important; }
    #sst-map, #sst-map.leaflet-container { background: ${LAND} !important; }` });
await page.waitForTimeout(500);
mkdirSync(path.dirname(OUT), { recursive: true });
await page.locator('#sst-map').screenshot({ path: OUT });
await browser.close();
console.log(`${OUT}: ${info.day}, ${info.range.min}-${info.range.max} F (${info.range.mode}), ` +
            `coast ${info.coast && info.coast.land ? 'on' : 'off'}${errors.length ? ', page errors: ' + errors.join(' | ') : ''}`);
if (errors.length) { process.exit(1); }
