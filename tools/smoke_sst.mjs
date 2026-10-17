// Smoke test for the Offshore SST page (/sst). Run it against a local dev server before pushing
// (Render deploys every push to main):
//
//   OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES LABS_STATS_DIR=$(mktemp -d) \
//     .venv/bin/python -c 'from app import app; app.run(debug=False, port=8062, threaded=True)' &
//   node tools/smoke_sst.mjs --base http://127.0.0.1:8062            # all three sizes
//   node tools/smoke_sst.mjs --sizes phone --shots /tmp/sst-shots     # one size, with screenshots
//
// Needs Playwright (npm i playwright, or set PLAYWRIGHT=/path/to/node_modules/playwright/index.mjs;
// it falls back to ~/Code/bf6-recon/node_modules). Headless Chrome keeps its default user agent, so
// the Labs counter treats it as a bot and records nothing. The page should open on a week that's in
// the local cache (cache/sst), or the first load waits on NOAA.
//
// Per size (laptop 1440x900, phone 390x664 touch, iPad 820x1180 touch) it checks: no page or console
// errors; the header, How it works (opens once on a first visit, not on the next), the status strip
// (date, day n of m), the legend; the page isn't wider or taller than the screen and a map tap doesn't
// scroll it; sidebar vs drawer and the floating playbar; a water reading that survives a day change
// and shows that day; a spot card; a tap inside The Dump reads that point; a tap next to a spot at
// zoom 10 reads the water; measure (true + magnetic, survives a day change, Clear); the status strip
// with a fake payload "meta" (source, freshness, closest week) and an OISST legend warning; and the
// analytics events the page sends (GotOneLabs._log). Also window.SST set up on the map, and (laptop) the SST image
// on its bounds with the color drawn under 10 taps matching each tap's reading.
//
// Sprint 2 look (assets/sstlook.js): the dark basemap; the Colors panel at each size (in the sidebar, or in the
// drawer and off the map on phones and tablets; 44 px targets on touch; Auto / Tuna / Marlin / Gulf Stream /
// Custom with + and - and the keyboard, Fit to view, Stepped / Breaks reaching the engine, the color bar
// with numbers and the week's histogram, the live legend); choices remembered after a reload without sending
// events; the depth contours asset, drawn and labelled, and its toggle (and attribution); spot labels (none at
// zoom 7, short canyon names at 8, all at 9, never overlapping); Hudson Canyon at the canyon head; the new
// analytics events. Until lane E's engine is merged the page has no window.SSTEngine, so the test injects
// tools/sst_engine_stub.js (it stands aside when a real engine is there; --no-stub skips it).
// Exit code 1 on any failure.

import { execSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import os from 'node:os';

const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, arr) => {
    if (v.startsWith('--')) { a.push([v.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : '1']); }
    return a;
}, []));
const BASE = (args.base || 'http://127.0.0.1:8062').replace(/\/$/, '');
const SHOTS = args.shots || '';
const SIZES = {
    desk: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
    phone: { viewport: { width: 390, height: 664 }, deviceScaleFactor: 1, hasTouch: true, isMobile: true },
    ipad: { viewport: { width: 820, height: 1180 }, deviceScaleFactor: 1, hasTouch: true },
};
const sizes = (args.sizes || 'desk,phone,ipad').split(',').filter(s => SIZES[s]);
const STUB = new URL('./sst_engine_stub.js', import.meta.url).pathname;
const HUDSON = [39.642, -72.44];                         // the canyon head (map/pois.py)
const LOOK_EVENTS = ['preset_tuna', 'preset_marlin', 'preset_gulfstream', 'preset_custom', 'custom_range',
                     'fit_view', 'preset_auto', 'stepped', 'breaks', 'contours'];

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
// Tile and layer requests depend on outside servers; they don't fail the test.
const NOISE = /api\/gfw|gebco|charttools|arcgisonline|favicon/i;

let failures = 0;
function check(size, name, ok, detail = '') {
    if (!ok) { failures++; }
    console.log(`${ok ? 'ok  ' : 'FAIL'} [${size}] ${name}${detail ? '  ' + detail : ''}`);
}

async function shot(page, size, name) {
    if (!SHOTS) { return; }
    mkdirSync(SHOTS, { recursive: true });
    const p = `${SHOTS}/sst-${size}-${name}.jpg`;
    await page.screenshot({ path: p, type: 'jpeg', quality: 70 });
    try { execSync(`sips -Z 1000 "${p}" --out "${p}" >/dev/null 2>&1`); } catch (e) { /* not on a Mac */ }
}

async function run(size) {
    const touch = !!SIZES[size].hasTouch;
    const browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext(SIZES[size]);
    await ctx.addInitScript(() => {
        window.GotOneLabsCountHere = true;              // log what would be sent (server ignores bots)
        window.__maps = [];
        const iv = setInterval(() => {
            if (window.L && window.L.Map && !window.L.__smoke) {
                window.L.__smoke = true;
                window.L.Map.addInitHook(function () { window.__maps.push(this); });
                clearInterval(iv);
            }
        }, 2);
    });
    if (!args['no-stub']) { await ctx.addInitScript({ path: STUB }); }
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push('pageerror: ' + String(e)));
    page.on('console', m => { if (m.type() === 'error' && !NOISE.test(m.text() + (m.location().url || ''))) { errors.push(m.text()); } });

    // Sprint 2: the week is drawn by assets/sstengine.js (window.SSTEngine) on a canvas in the SST pane, not by a PNG
    // overlay; "ready" = the engine has the week and the loading overlay is gone.
    const ready = () => page.waitForFunction(() => {
        const E = window.SSTEngine, o = document.getElementById('map-loading-overlay');
        return E && !E.__stub && E.state().ready && document.querySelector('canvas.sst-engine-canvas') &&
            o && getComputedStyle(o).display === 'none';
    }, null, { timeout: 90000, polling: 200 });

    // ---- first visit: How it works opens by itself ----
    await page.goto(`${BASE}/sst`);
    const modal = await page.waitForSelector('.modal.show', { timeout: 20000 }).then(() => true).catch(() => false);
    check(size, 'How it works opens on a first visit', modal);
    if (modal) {
        const txt = await page.innerText('.modal.show .modal-body');
        check(size, 'How it works has no em dashes', !/\u2014/.test(txt));
        await page.click('#sst-about-close');
        await sleep(600);
    }
    await ready().catch(() => {});
    await sleep(1200);
    check(size, 'week loads (drawn by SSTEngine on its canvas, loading hidden)', await page.evaluate(() =>
        !!(window.SSTEngine && window.SSTEngine.state().ready && window.__maps[0].getPane('sst-pane') &&
           window.__maps[0].getPane('sst-pane').querySelector('canvas.sst-engine-canvas'))));
    check(size, 'window.SST set up and hooked to the map', await page.evaluate(() => !!(window.SST && window.SST.ready &&
        window.SST.ready() && window.SST._state.map === window.__maps[0])));
    await shot(page, size, 'first');

    // ---- chrome: header, strip, legend, layout ----
    const ui = await page.evaluate(() => {
        const q = s => document.querySelector(s);
        const vis = s => { const e = q(s); if (!e) { return false; } const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 && getComputedStyle(e).display !== 'none' && b.right > 0 && b.left < innerWidth && b.bottom > 0 && b.top < innerHeight; };
        const sb = q('#sidebar-col').getBoundingClientRect();
        const mc = q('.map-col').getBoundingClientRect();
        return {
            h1: q('.gotone-header h1')?.innerText, about: vis('#sst-about-open'),
            strip: q('#sst-status-line')?.innerText || '', stripVisible: vis('#sst-status-line'),
            legend: (document.documentElement.classList.contains('sst-engine-on') ? q('#sst-legend-live') : q('#legend-container'))?.innerText || '',
            playbar: vis('#sst-playbar'),
            tile: [...document.querySelectorAll('#sst-map img.leaflet-tile')].map(i => i.src).find(u => /arcgisonline/.test(u)) || '',
            pbDate: q('#sst-pb-date')?.innerText || '',
            sidebarLeft: Math.round(sb.left), sidebarW: Math.round(sb.width), mapRight: Math.round(mc.right),
            sw: document.documentElement.scrollWidth, sh: document.documentElement.scrollHeight,
            iw: innerWidth, ih: innerHeight,
        };
    });
    check(size, 'header says Offshore SST + How it works', ui.h1 === 'Offshore SST' && ui.about, ui.h1);
    check(size, 'status strip shows the date and day n of m',
        ui.stripVisible && /\w{3}, \w{3} \d{1,2}, \d{4}/.test(ui.strip) && /day \d of \d/.test(ui.strip), ui.strip.replace(/\n/g, ' / '));
    check(size, 'status strip names the source', /MUR|OISST/.test(ui.strip));
    check(size, "legend says which range is on", /°F/.test(ui.legend) && /this week's range|fixed scale|auto range/.test(ui.legend), ui.legend.replace(/\n/g, ' '));
    check(size, 'dark basemap on /sst', /World_Dark_Gray_Base/.test(ui.tile), ui.tile.slice(0, 90));
    check(size, 'page fits the screen (no scroll)', ui.sw <= ui.iw && ui.sh <= ui.ih, `${ui.sw}x${ui.sh} in ${ui.iw}x${ui.ih}`);
    check(size, 'map ends at the screen edge', Math.abs(ui.mapRight - ui.iw) <= 1, `right ${ui.mapRight}`);
    if (size === 'desk') {
        check(size, 'sidebar beside the map, playbar hidden', ui.sidebarLeft === 0 && ui.sidebarW > 180 && !ui.playbar);
    } else {
        check(size, 'sidebar is a closed drawer, playbar on the map with the date',
            ui.sidebarLeft < 0 && ui.playbar && /\w{3}, \w{3} \d/.test(ui.pbDate), `drawer x ${ui.sidebarLeft}, "${ui.pbDate}"`);
    }

    // ---- helpers ----
    const pt = (lat, lng) => page.evaluate(([lat, lng]) => {
        const m = window.__maps[0], p = m.latLngToContainerPoint([lat, lng]), b = m.getContainer().getBoundingClientRect();
        return { x: b.x + p.x, y: b.y + p.y };
    }, [lat, lng]);
    const tap = async (lat, lng) => { const p = await pt(lat, lng); if (touch) { await page.touchscreen.tap(p.x, p.y); } else { await page.mouse.click(p.x, p.y); } };
    const tipText = () => page.evaluate(() => [...document.querySelectorAll('.leaflet-tooltip.sst-tooltip')].map(t => t.innerText.replace(/\n+/g, ' | ')).join(' || '));
    const waitTip = re => page.waitForFunction(r => [...document.querySelectorAll('.leaflet-tooltip.sst-tooltip')].some(t => new RegExp(r).test(t.innerText)), re, { timeout: 10000 }).then(() => true).catch(() => false);
    const waitNewTip = prev => page.waitForFunction(p => {
        const t = [...document.querySelectorAll('.leaflet-tooltip.sst-tooltip')].map(x => x.innerText.replace(/\n+/g, ' | ')).join(' || ');
        return t && t !== p;
    }, prev, { timeout: 10000 }).then(() => true).catch(() => false);
    const press = async (deskSel, touchSel) => { if (touch && touchSel) { await page.tap(touchSel); } else { await page.click(deskSel); } };
    const openDrawer = async () => { if (size !== 'desk') { await page.tap('#sidebar-open'); await sleep(500); } };
    const closeDrawer = async () => { if (size !== 'desk') { await page.tap('#sidebar-close').catch(() => {}); await sleep(500); } };
    const setView = (lat, lng, z) => page.evaluate(([a, b, c]) => window.__maps[0].setView([a, b], c, { animate: false }), [lat, lng, z]);

    await look(page, size, touch, { openDrawer, closeDrawer, setView, ready });

    // ---- a water reading follows the day ----
    await setView(40.4, -71.4, 7); await sleep(800);
    const t0 = Date.now();
    await tap(40.45, -71.0);
    const got = await waitTip('°F');
    check(size, 'tap the water: a temperature', got, `${Date.now() - t0} ms ${await tipText()}`);
    const scrolled = await page.evaluate(() => document.scrollingElement.scrollTop + window.scrollY);
    check(size, 'a map tap does not scroll the page', scrolled === 0, `scrollTop ${scrolled}`);
    const before = await tipText();
    await press('#step-back-btn', '#sst-pb-back'); await sleep(1500);
    const after = await tipText();
    check(size, 'the reading survives a day change and shows that day',
        /°F/.test(after) && after.split(' | ')[1] !== before.split(' | ')[1], after.split(' | ').slice(0, 2).join(' | '));

    // ---- spots and The Dump ----
    await tap(...HUDSON);
    check(size, 'tap a spot: its card (Hudson Canyon at the canyon head)',
        await waitTip('Hudson Canyon') && /39\.642°N, 72\.440°W/.test(await tipText()), await tipText());
    await press('#step-fwd-btn', '#sst-pb-fwd'); await sleep(1500);
    check(size, 'the spot card survives a day change', /Hudson Canyon/.test(await tipText()));
    const prevCard = await tipText();
    await tap(40.70, -70.95); await waitNewTip(prevCard);
    const dump = await tipText();
    check(size, 'inside The Dump: reads the tapped point', /The Dump/.test(dump) && /40\.(69|70)\d°N, 70\.9[45]\d°W/.test(dump), dump);
    await sleep(800);                                    // (two quick taps are a double-tap zoom)
    await tap(40.70, -70.95);
    const closed = await page.waitForFunction(() => !document.querySelector('.leaflet-tooltip.sst-tooltip'), null, { timeout: 8000 }).then(() => true).catch(() => false);
    check(size, 'a second tap on the open reading closes it', closed);
    await setView(41.05, -71.10, 10); await sleep(1000);
    const prevTip = await tipText();
    await tap(41.05, -71.08);                            // ~4 nm east of Coxes Ledge, ~58 px at zoom 10
    await waitNewTip(prevTip);
    const near = await tipText();
    const zinfo = await page.evaluate(() => { const m = window.__maps[0]; const a = m.latLngToContainerPoint([41.05, -71.08]), c = m.latLngToContainerPoint([41.05, -71.158]); return `zoom ${m.getZoom()}, ${Math.round(Math.hypot(a.x - c.x, a.y - c.y))} px from the spot; click ${JSON.stringify(window.dash_component_api.getLayout('sst-map').props.clickData.containerPoint)}`; });
    check(size, 'next to a spot at zoom 10: reads the water', /°F/.test(near) && !/Coxes/.test(near), near.split(' | ').slice(0, 1).join('') + '; ' + zinfo);
    await setView(40.4, -71.6, 7); await sleep(1000);

    // ---- the colors are where the readings are (laptop only) ----
    // At water points on MUR cell centres (2-decimal lat/lon), at zoom 9: the tap readout vs the color SSTEngine draws
    // under the tap, decoded through the engine's own colors (SSTEngine.colorFor over the range drawn). The engine
    // places the grid by Web Mercator strip by strip (before the relaunch fix the image sat up to 5.3 km north of
    // true). Also: the canvas covers the map, and the week's grid sits on the payload's bounds.
    if (size === 'desk') {
        const PTS = [[40.20, -71.00], [39.80, -72.40], [40.60, -72.80], [41.30, -69.60], [41.25, -71.40],
                     [41.20, -70.75], [42.60, -70.40], [43.00, -69.40], [39.30, -73.50], [40.90, -70.70]];
        const placed = await page.evaluate(() => {
            const E = window.SSTEngine, st = E.state(), m = window.__maps[0];
            const b = window.dash_component_api.getLayout('sst-store').props.data.bounds;
            const cv = document.querySelector('canvas.sst-engine-canvas').getBoundingClientRect();
            const c = m.getContainer().getBoundingClientRect();
            const off = Math.max(Math.abs(st.week.bounds[0][0] - b[0][0]), Math.abs(st.week.bounds[1][0] - b[1][0]),
                                 Math.abs(st.week.bounds[0][1] - b[0][1]), Math.abs(st.week.bounds[1][1] - b[1][1]));
            return { off, covers: cv.left <= c.left + 1 && cv.top <= c.top + 1 && cv.right >= c.right - 1 && cv.bottom >= c.bottom - 1 };
        });
        check(size, 'the engine draws the week on its bounds, over the whole map', placed.off < 1e-4 && placed.covers,
            JSON.stringify(placed));
        const bad = [];
        let last = '';
        for (const [lat, lng] of PTS) {
            await setView(lat, lng, 9); await sleep(900);
            const box = await page.locator('#sst-map').boundingBox();
            const prev = await tipText();
            await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
            await waitNewTip(prev);
            last = await tipText();
            const t = /(-?\d+(?:\.\d)?) °F/.exec(last);
            const got = await page.evaluate(() => {
                const E = window.SSTEngine, m = window.__maps[0], cv = document.querySelector('canvas.sst-engine-canvas');
                const ll = window.dash_component_api.getLayout('sst-map').props.clickData.latlng;
                const c = m.getContainer().getBoundingClientRect(), r = cv.getBoundingClientRect();
                const p = m.latLngToContainerPoint([ll.lat, ll.lng]);
                const x = Math.floor((c.x + p.x - r.left) * cv.width / r.width), y = Math.floor((c.y + p.y - r.top) * cv.height / r.height);
                const px = cv.getContext('2d').getImageData(x, y, 1, 1).data;
                const rg = E.getRange();
                let best = null, bestD = 1e9;
                for (let f = rg.min - 0.5; f <= rg.max + 0.5; f += 0.05) {
                    const col = E.colorFor(f), dd = Math.hypot(col[0] - px[0], col[1] - px[1], col[2] - px[2]);
                    if (dd < bestD) { bestD = dd; best = f; }
                }
                return { rgba: [...px], f: best, dist: bestD, vmin: rg.min, vmax: rg.max, read: E.valueAt(ll.lat, ll.lng) };
            });
            const T = t ? +t[1] : null;
            const ok = T !== null && got.rgba[3] === 255 && got.dist < 8 && got.read && Math.abs(got.read.f - T) < 0.05 &&
                (Math.abs(got.f - T) <= 0.35 || (T <= got.vmin && got.f <= got.vmin + 0.1) || (T >= got.vmax && got.f >= got.vmax - 0.1));
            if (!ok) { bad.push(`${lat},${lng}: reads ${T} °F, drawn ~${got.f && got.f.toFixed(2)} (${got.rgba.join(',')}, d ${got.dist.toFixed(1)})`); }
        }
        check(size, `the colors drawn under ${PTS.length} taps match their readings`, !bad.length, bad.join('; '));
        if (last) {                                      // a second tap on the open reading closes it
            const box = await page.locator('#sst-map').boundingBox();
            await sleep(800);
            await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        }
        await setView(40.4, -71.6, 7); await sleep(1000);
    }

    // ---- measure ----
    await openDrawer();
    await page.click('#measure-btn'); await sleep(700);
    check(size, 'Measure: button turns into Cancel and the map shows a hint',
        (await page.innerText('#measure-btn')) === 'Cancel' && await page.evaluate(() => getComputedStyle(document.getElementById('sst-map-hint')).display !== 'none'));
    if (size !== 'desk') {
        check(size, 'Measure closes the drawer', await page.evaluate(() => document.querySelector('#sidebar-col').getBoundingClientRect().left < 0));
    }
    await tap(41.0, -71.8083); await sleep(1200);          // Cartwright
    await tap(...HUDSON); await sleep(1500);               // Hudson Canyon
    const lab = () => page.evaluate(() => document.querySelector('.leaflet-tooltip.measure-tooltip')?.innerText || '');
    const m1 = await lab();
    check(size, 'measure label: distance, true and magnetic', /nm/.test(m1) && /true/.test(m1) && /magnetic/.test(m1), m1);
    const ro = await page.innerText('#measure-readout');
    check(size, 'measure panel: both ends with temperatures', /A: Cartwright/.test(ro) && /B: Hudson Canyon/.test(ro) && /°F/.test(ro) && /magnetic/.test(ro));
    await shot(page, size, 'measure');
    await press('#step-back-btn', '#sst-pb-back'); await sleep(1500);
    check(size, 'the measurement survives a day change', (await lab()) === m1);
    await openDrawer();
    await page.click('#measure-clear'); await sleep(1000);
    check(size, 'Clear removes the measurement', !(await lab()) && (await page.innerText('#measure-btn')) === 'Measure');
    await closeDrawer();

    // ---- the strip with a fake payload "meta" (lane D's contract) ----
    await page.evaluate(() => {
        const d = window.dash_component_api.getLayout('sst-store').props.data;
        const days = d.dates.slice();
        const last = days[days.length - 1];
        window.dash_clientside.set_props('sst-store', { data: Object.assign({}, d, { meta: {
            source: 'OISST', source_label: 'OISST 25 km (NOAA NCEI)', resolution_km: 25,
            dataset_id: 'ncdcOisst21NrtAgg_LonPM180', server: 'https://coastwatch.pfeg.noaa.gov/erddap',
            days: days, requested_end: '2099-01-01', served_end: last, latest_available: '2099-01-02',
            built_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString(), from_cache: true,
            missing_days: ['2099-01-03'], complete: false, preliminary_days: days.slice(-2),
            week_match: 'closest_saved', queued: true,
        } }) });
    });
    await sleep(2500);
    const fake = await page.evaluate(() => [document.getElementById('sst-status-line').innerText,
        document.getElementById(document.documentElement.classList.contains('sst-engine-on') ? 'sst-legend-live' : 'legend-container').innerText]);
    check(size, 'strip with meta: source, freshness, closest week, newest',
        /OISST/.test(fake[0]) && /updated 3 (hours|h) ago/.test(fake[0]) && /closest (saved )?week to Jan 1/.test(fake[0]) &&
        /newest (available )?Jan 2/.test(fake[0]) && /no data for Jan 3 yet/.test(fake[0]),
        fake[0].replace(/\n/g, ' / '));
    // (phones: the legend carries the warning, the strip keeps the short source name)
    if (look || fake[1]) {
        check(size, 'OISST week: legend (and the strip, if wide) say broad pattern only',
            /broad pattern only/.test(fake[1]) && (size === 'phone' || /broad pattern only/.test(fake[0])));
    } else {
        check(size, 'OISST week: the strip says broad pattern only (the legend is the Colors panel\'s)',
            size === 'phone' || /broad pattern only/.test(fake[0]));
    }
    await shot(page, size, 'meta');

    // ---- the newest days are marked preliminary; a failed load shows in the strip ----
    const prelim = await page.evaluate(() => {
        const k = window.dash_component_api.getLayout('sst-key').props.data;
        const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date());
        window.SST.status({ dates: [today], meta: {} }, 0);
        const t = document.getElementById('sst-status-line').innerText;
        window.SST.status(k, window.dash_component_api.getLayout('frame-slider').props.value);
        return t;
    });
    check(size, 'a day from today is marked preliminary', /preliminary/.test(prelim), prelim.replace(/\n/g, ' / '));
    await page.evaluate(() => window.dash_clientside.set_props('fetch-status', { children: {
        type: 'Alert', namespace: 'dash_bootstrap_components',
        props: { children: 'Error: smoke-test failure', color: 'danger' } } }));
    await sleep(600);
    const errStrip = await page.innerText('#sst-status-line');
    check(size, "a failed load: the strip says so and keeps the day shown",
        /Couldn't load that week: smoke-test failure/.test(errStrip) && /day \d of \d/.test(errStrip), errStrip.replace(/\n/g, ' / '));
    await page.evaluate(() => window.dash_clientside.set_props('fetch-status', { children: 'ok' }));
    await sleep(400);

    const engineOn = await page.evaluate(() => document.documentElement.classList.contains('sst-engine-on'));
    if (size === 'desk' && engineOn) {
        check(size, 'engine on: "Same colors every week" steps aside for the presets',
            await page.evaluate(() => getComputedStyle(document.getElementById('lock-scale')).display === 'none'));
    }
    // ---- the range: the engine's Auto = the server's (whole coverage, the week), no reload for colors (laptop) ----
    // ("Same colors every week" went with the PNG frames: the Colors panel's presets replace it, lane V.)
    if (size === 'desk') {
        const rg = await page.evaluate(() => {
            const E = window.SSTEngine, d = window.dash_component_api.getLayout('sst-store').props.data;
            const url = E.state().week.url, before = E.getRange();
            E.setRange({ mode: 'fixed', min: 62, max: 76 });
            const fixed = E.getRange();
            E.setRange({ mode: 'auto' });
            return { before, fixed, after: E.getRange(), server: [d.vmin, d.vmax], same: E.state().week.url === url,
                     overlay: getComputedStyle(document.getElementById('map-loading-overlay')).display };
        });
        check(size, "engine Auto range = the server's; a fixed range recolors in place (no reload)",
            rg.before.mode === 'auto' && rg.before.min === rg.server[0] && rg.before.max === rg.server[1] &&
            rg.fixed.min === 62 && rg.fixed.max === 76 && rg.after.min === rg.server[0] && rg.same && rg.overlay === 'none',
            JSON.stringify(rg));
    }

    // ---- How it works from the header counts; a reload doesn't reopen it ----
    await page.click('#sst-about-open');
    check(size, 'How it works opens from the header', await page.waitForSelector('.modal.show', { timeout: 5000 }).then(() => true).catch(() => false));
    await page.click('#sst-about-close'); await sleep(600);
    const sent = await page.evaluate(() => window.GotOneLabs._log.filter(x => /\/api\/event/.test(x.url)).map(x => x.body.e));
    for (const e of ['readout', 'poi', 'step', 'measure', 'how_it_works']) {
        check(size, `analytics event "${e}" sent`, sent.includes(e), sent.join(','));
    }
    if (engineOn) {
        const missing = LOOK_EVENTS.filter(e => !(look.sent || []).includes(e));
        check(size, 'analytics: every Colors-panel event sent on a real change', !missing.length, 'missing ' + missing.join(','));
    }
    await page.reload();
    await ready().catch(() => {});
    await sleep(1500);
    check(size, 'How it works stays closed on the next visit', !(await page.evaluate(() => !!document.querySelector('.modal.show'))));

    check(size, 'no page or console errors', errors.length === 0, errors.slice(0, 5).join(' | '));
    await browser.close();
}

// ---- Sprint 2 look: the Colors panel, live legend, depth contours, spot labels (assets/sstlook.js) ----
async function look(page, size, touch, h) {
    look.sent = [];
    const engine = await page.waitForFunction(() => document.documentElement.classList.contains('sst-engine-on'),
        null, { timeout: 30000 }).then(() => true).catch(() => false);
    check(size, 'SST engine on the page (real or the test stub) and the Colors panel on', engine,
        await page.evaluate(() => window.SSTEngine ? (window.SSTEngine.__stub ? 'stub engine' : 'real engine') : 'no engine'));
    const P = '#sst-color-panel';
    const panelBox = () => page.evaluate(() => {
        const r = e => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
        const p = document.getElementById('sst-color-panel'), sb = document.getElementById('sidebar-col');
        return { p: r(p), sb: r(sb), sw: p.scrollWidth, cw: p.clientWidth, iw: innerWidth };
    });
    // the depth contours asset: served gzip, six levels, loaded and drawn
    const asset = await page.evaluate(async () => {
        const url = document.getElementById('sst-look-data').getAttribute('data-contours');
        const r = await fetch(url); const d = await r.json();
        return { url, ok: r.ok, enc: r.headers.get('content-encoding'), levels: d.levels.map(l => l.id), fmt: d.format };
    });
    check(size, 'depth contours asset: GOCONT1, six levels, versioned URL',
        asset.ok && asset.fmt === 'GOCONT1' && asset.levels.join() === '30fa,40fa,100fa,500m,1000m,2000m' && /\?v=[0-9a-f]{12}$/.test(asset.url),
        `${asset.url} ${asset.levels.join(',')}`);
    if (!engine) { return; }

    // ---- placement: sidebar on the laptop; in the drawer (off the map until opened) on phones and tablets ----
    let b = await panelBox();
    if (size === 'desk') {
        check(size, 'Colors panel sits in the sidebar, nothing cut off',
            b.p[0] >= b.sb[0] && b.p[2] <= b.sb[2] + 1 && b.sw <= b.cw + 1, `panel ${b.p.map(Math.round)} in ${b.sb.map(Math.round)}`);
    } else {
        check(size, 'Colors panel is off the map while the drawer is closed', b.p[2] <= 0, `panel right ${Math.round(b.p[2])}`);
        await h.openDrawer();
        await page.locator(P).scrollIntoViewIfNeeded();
        b = await panelBox();
        check(size, 'Colors panel fits the drawer', b.p[0] >= 0 && b.p[2] <= b.sb[2] + 1 && b.sw <= b.cw + 1,
            `panel ${b.p.map(Math.round)} drawer ${b.sb.map(Math.round)}`);
    }
    if (touch) {
        const small = await page.evaluate(() => [...document.querySelectorAll('#sst-color-panel .sstc-chip, #sst-color-panel .sstc-fit, #sst-color-panel .form-check-label')]
            .filter(e => e.offsetParent).map(e => [e.innerText.split('\n')[0], Math.round(e.getBoundingClientRect().height)]).filter(x => x[1] < 44));
        check(size, 'Colors panel: 44 px touch targets', !small.length, JSON.stringify(small));
    }
    const range = () => page.innerText(`${P} .sstc-range`);
    const legend = () => page.innerText('#sst-legend-live');
    const pick = async p => { await page.click(`${P} .sstc-chip[data-p="${p}"]`); await sleep(500); };

    // ---- Auto: range, bar with numbers, histogram, live legend ----
    const auto = await page.evaluate(() => ({ r: window.SSTLook.range(), want: window.SSTLook._autoFromHist(window.SSTEngine.histogram()),
        nums: [...document.querySelectorAll('#sst-color-panel .sstc-num')].map(e => +e.innerText),
        hist: !!document.querySelector('#sst-color-panel .sstc-hist polygon'),
        legendNums: document.querySelectorAll('#sst-legend-live .sstc-num').length }));
    check(size, 'Auto: 5 to 95% of the week, whole degrees, at least 8 F wide',
        /auto: 5 to 95% of this week's water/.test(await range()) && auto.r && auto.r.max - auto.r.min >= 8 &&
        Number.isInteger(auto.r.min) && Number.isInteger(auto.r.max), `${JSON.stringify(auto.r)} (rule ${JSON.stringify(auto.want)}) ${(await range()).replace(/\n/g, ' ')}`);
    check(size, 'color bar: numbers, the week\'s histogram; live legend with numbers',
        auto.nums.length >= 3 && auto.nums.every(n => n >= auto.r.min && n <= auto.r.max) && auto.hist && auto.legendNums >= 3,
        `numbers ${auto.nums.join(',')}`);
    check(size, 'live legend says auto range', /auto range/.test(await legend()));

    // ---- presets ----
    await pick('tuna');
    check(size, 'Tuna: 62 to 76 F, the legend says so', /62 to 76 °F/.test(await range()) && /Tuna preset/.test(await legend()),
        (await range()).replace(/\n/g, ' '));
    await pick('marlin');
    check(size, 'Marlin: 70 to 84 F', /70 to 84 °F/.test(await range()));
    await pick('gulfstream');
    const gs = await page.evaluate(() => window.SSTLook._gulfFromHist(window.SSTEngine.histogram()));
    check(size, 'Gulf Stream: top = the week\'s 99.5th percentile rounded up, 18 F span',
        new RegExp(`${gs.min} to ${gs.max} °F`).test(await range()) && gs.max - gs.min === 18, `${JSON.stringify(gs)} ${(await range()).replace(/\n/g, ' ')}`);
    await pick('custom');
    const custVis = await page.isVisible(`${P} .sstc-custom`);
    const cv = async e => +(await page.innerText(`${P} .sstc-val[data-end="${e}"]`)).replace(/[^\d-]/g, '');
    const max0 = await cv('max'), min0 = await cv('min');
    await page.click(`${P} .sstc-pm[data-end="max"][data-d="1"]`); await sleep(300);
    await page.focus(`${P} .sstc-val[data-end="min"]`);
    await page.keyboard.press('ArrowDown'); await sleep(300);
    const max1 = await cv('max'), min1 = await cv('min');
    const eng = await page.evaluate(() => window.SSTLook.range());
    check(size, 'Custom: editor opens; + and the keyboard step 1 F; the map follows',
        custVis && max1 === max0 + 1 && min1 === min0 - 1 && eng.min === min1 && eng.max === max1 &&
        new RegExp(`${min1} to ${max1} °F`).test(await range()) && /your range/.test(await legend()),
        `${min0}-${max0} -> ${min1}-${max1}; engine ${JSON.stringify(eng)}`);
    if (size === 'desk') {                                // chips are a radio group: arrows move the choice
        await page.focus(`${P} .sstc-chip[data-p="custom"]`);
        await page.keyboard.press('ArrowLeft'); await sleep(400);
        const now = await page.evaluate(() => [document.activeElement.getAttribute('data-p'),
            document.querySelector('#sst-color-panel .sstc-chip[aria-checked="true"]').getAttribute('data-p')]);
        check(size, 'keyboard: arrows move along the range chips', now[0] === 'gulfstream' && now[1] === 'gulfstream', now.join(','));
    }
    if (size !== 'desk') { await h.closeDrawer(); }
    await h.setView(40.0, -71.5, 9); await sleep(800);
    if (size !== 'desk') { await h.openDrawer(); }
    await page.click(`${P} .sstc-fit`); await sleep(600);
    const fit = await page.evaluate(() => window.SSTLook.prefs());
    check(size, 'Fit to view: a fixed range from the water on screen', fit.preset === 'view' && fit.view && fit.view.max > fit.view.min &&
        /fitted to the map view/.test(await range()) && /fit to view/.test(await legend()), JSON.stringify(fit.view));

    // ---- toggles reach the engine; depth lines on and off ----
    const opts = () => page.evaluate(() => window.SSTEngine._state ? window.SSTEngine._state.opts : null);
    const isStub = await page.evaluate(() => !!(window.SSTEngine && window.SSTEngine.__stub));
    await page.check(`${P} #sstc-stepped`); await sleep(300);
    await page.check(`${P} #sstc-breaks`); await sleep(300);
    if (isStub) {
        const o = await opts();
        check(size, 'Stepped and Breaks (isotherms + highlight) reach the engine',
            o.stepped === true && o.isotherms === true && o.breaks === true, JSON.stringify(o));
    }
    const stepBar = await page.evaluate(() => /(\d+(?:\.\d+)?)%, rgb\(\d+, \d+, \d+\) \1%/.test(getComputedStyle(document.querySelector('#sst-color-panel .sstc-ramp')).backgroundImage));
    check(size, 'Stepped: the color bar turns into 1 F bands', stepBar);
    await page.uncheck(`${P} #sstc-contours`); await sleep(500);
    const off = await page.evaluate(() => ({ disp: getComputedStyle(document.querySelector('.sst-depth-canvas')).display,
        attr: document.querySelector('#sst-map .leaflet-control-attribution').innerText }));
    await page.check(`${P} #sstc-contours`); await sleep(500);
    const on = await page.evaluate(() => ({ disp: getComputedStyle(document.querySelector('.sst-depth-canvas')).display,
        attr: document.querySelector('#sst-map .leaflet-control-attribution').innerText }));
    check(size, 'Depth lines toggle: hides and shows the lines and their credit',
        off.disp === 'none' && !/ETOPO/.test(off.attr) && on.disp !== 'none' && /ETOPO 2022/.test(on.attr), `${off.disp} / ${on.disp}`);
    look.sent = await page.evaluate(() => window.GotOneLabs._log.filter(x => /\/api\/event/.test(x.url)).map(x => x.body.e));
    await shot(page, size, 'colors-panel');
    if (size !== 'desk') { await h.closeDrawer(); }

    // ---- remembered after a reload, and nothing counted for it ----
    await page.reload();
    await h.ready().catch(() => {});
    await page.waitForFunction(() => document.documentElement.classList.contains('sst-engine-on'), null, { timeout: 30000 }).catch(() => {});
    await sleep(1500);
    const back = await page.evaluate(() => ({ prefs: window.SSTLook.prefs(), range: window.SSTLook.range(),
        stepped: document.getElementById('sstc-stepped').checked, breaks: document.getElementById('sstc-breaks').checked,
        hazeToggle: !!document.getElementById('sstc-haze'),
        opts: window.SSTEngine && window.SSTEngine._state ? window.SSTEngine._state.opts : null,
        sent: window.GotOneLabs._log.filter(x => /\/api\/event/.test(x.url)).map(x => x.body.e) }));
    check(size, 'choices remembered after a reload (range, Stepped, Breaks); no Haze toggle',
        back.prefs.preset === 'view' && back.range && back.range.min === fit.view.min && back.range.max === fit.view.max &&
        back.stepped && back.breaks && !back.hazeToggle && (!back.opts || (back.opts.stepped && back.opts.breaks)), JSON.stringify(back.prefs));
    check(size, 'restored choices send no events', !back.sent.some(e => LOOK_EVENTS.includes(e)), back.sent.join(','));

    // back to the defaults (Auto, Stepped and Breaks off) through the panel
    if (size !== 'desk') { await h.openDrawer(); }
    await pick('auto');
    await page.uncheck(`${P} #sstc-stepped`); await page.uncheck(`${P} #sstc-breaks`);
    await sleep(300);
    look.sent = look.sent.concat(await page.evaluate(() => window.GotOneLabs._log.filter(x => /\/api\/event/.test(x.url)).map(x => x.body.e)));
    if (size !== 'desk') { await h.closeDrawer(); }

    // ---- spot labels and depth labels ----
    const labels = async (lat, lng, z) => {
        await h.setView(lat, lng, z); await sleep(1200);
        return page.evaluate(() => {
            const d = window.SSTLook._state.debug, c = document.querySelector('.sst-depth-canvas');
            const g = c.getContext('2d'), im = g.getImageData(0, 0, c.width, c.height).data;
            let ink = 0; for (let i = 3; i < im.length; i += 4 * 7) { if (im[i] > 0) { ink++; } }
            return { spots: d.spotLabels || [], depth: d.depthLabels || [], ink };
        });
    };
    const z7 = await labels(40.4, -71.0, 7);
    const z8 = await labels(39.8, -72.0, 8);
    const z9 = await labels(39.75, -72.1, 9);
    const boxes = z9.spots.map(s => [s.x - 1, s.y, s.x + s.w + 1, s.y + s.h]).concat(z9.depth.map(d => d.box));
    let overlaps = 0;
    for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
            const a = boxes[i], c = boxes[j];
            if (a[0] < c[2] - 1 && a[2] > c[0] + 1 && a[1] < c[3] - 1 && a[3] > c[1] + 1) { overlaps++; }
        }
    }
    check(size, 'spot labels: none at zoom 7, short canyon names at 8, all names at 9',
        z7.spots.length === 0 && z8.spots.length > 0 && z8.spots.every(s => !/ Canyon$/.test(s.text)) &&
        z9.spots.some(s => s.text === 'Hudson Canyon'), `z8 ${z8.spots.map(s => s.text).join(',')} | z9 ${z9.spots.map(s => s.text).join(',')}`);
    check(size, 'depth lines drawn with labels at zoom 9; no label on top of another',
        z9.ink > 200 && z9.depth.length >= 2 && overlaps === 0, `${z9.depth.map(d => d.label).join(',')}; ink ${z9.ink}; overlaps ${overlaps}`);
    await shot(page, size, 'contours-z9');
    await h.setView(40.4, -71.6, 7); await sleep(800);
}

for (const s of sizes) {
    try { await run(s); } catch (e) { check(s, 'run', false, String(e).slice(0, 300)); }
}
console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
process.exit(failures ? 1 : 0);
