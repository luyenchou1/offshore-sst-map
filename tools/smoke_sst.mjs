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
// analytics events the page sends (GotOneLabs._log). Exit code 1 on any failure.

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
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push('pageerror: ' + String(e)));
    page.on('console', m => { if (m.type() === 'error' && !NOISE.test(m.text() + (m.location().url || ''))) { errors.push(m.text()); } });

    const ready = () => page.waitForFunction(() => {
        const i = document.querySelector('img.leaflet-image-layer');
        const o = document.getElementById('map-loading-overlay');
        return i && i.src.startsWith('data:') && o && getComputedStyle(o).display === 'none';
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
    check(size, 'week loads (overlay shown, loading hidden)', await page.evaluate(() =>
        !!document.querySelector('img.leaflet-image-layer[src^="data:"]')));
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
            legend: q('#legend-container')?.innerText || '', playbar: vis('#sst-playbar'),
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
    check(size, "legend says this week's range", /°F/.test(ui.legend) && /this week's range|fixed scale/.test(ui.legend));
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
    await tap(39.54, -72.05);
    check(size, 'tap a spot: its card', await waitTip('Hudson Canyon'), await tipText());
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

    // ---- measure ----
    await openDrawer();
    await page.click('#measure-btn'); await sleep(700);
    check(size, 'Measure: button turns into Cancel and the map shows a hint',
        (await page.innerText('#measure-btn')) === 'Cancel' && await page.evaluate(() => getComputedStyle(document.getElementById('sst-map-hint')).display !== 'none'));
    if (size !== 'desk') {
        check(size, 'Measure closes the drawer', await page.evaluate(() => document.querySelector('#sidebar-col').getBoundingClientRect().left < 0));
    }
    await tap(41.0, -71.8083); await sleep(1200);          // Cartwright
    await tap(39.54, -72.05); await sleep(1500);           // Hudson Canyon
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
    const fake = await page.evaluate(() => [document.getElementById('sst-status-line').innerText, document.getElementById('legend-container').innerText]);
    check(size, 'strip with meta: source, freshness, closest week, newest',
        /OISST/.test(fake[0]) && /updated 3 (hours|h) ago/.test(fake[0]) && /closest (saved )?week to Jan 1/.test(fake[0]) &&
        /newest (available )?Jan 2/.test(fake[0]) && /no data for Jan 3 yet/.test(fake[0]),
        fake[0].replace(/\n/g, ' / '));
    // (phones: the legend carries the warning, the strip keeps the short source name)
    check(size, 'OISST week: legend (and the strip, if wide) say broad pattern only',
        /broad pattern only/.test(fake[1]) && (size === 'phone' || /broad pattern only/.test(fake[0])));
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

    // ---- "Same colors every week" reloads with the fixed scale (laptop only) ----
    if (size === 'desk') {
        await page.getByLabel('Same colors every week', { exact: false }).check();
        const locked = await page.waitForFunction(() => /fixed scale/.test(document.getElementById('legend-container').innerText)
            && getComputedStyle(document.getElementById('map-loading-overlay')).display === 'none', null, { timeout: 60000 })
            .then(() => true).catch(() => false);
        check(size, 'Same colors every week: legend says fixed scale', locked,
            (await page.innerText('#legend-container')).replace(/\n/g, ' '));
        await page.getByLabel('Same colors every week', { exact: false }).uncheck();
        await page.waitForFunction(() => /this week's range/.test(document.getElementById('legend-container').innerText), null, { timeout: 60000 }).catch(() => {});
    }

    // ---- How it works from the header counts; a reload doesn't reopen it ----
    await page.click('#sst-about-open');
    check(size, 'How it works opens from the header', await page.waitForSelector('.modal.show', { timeout: 5000 }).then(() => true).catch(() => false));
    await page.click('#sst-about-close'); await sleep(600);
    const sent = await page.evaluate(() => window.GotOneLabs._log.filter(x => /\/api\/event/.test(x.url)).map(x => x.body.e));
    for (const e of ['readout', 'poi', 'step', 'measure', 'how_it_works']) {
        check(size, `analytics event "${e}" sent`, sent.includes(e), sent.join(','));
    }
    await page.reload();
    await ready().catch(() => {});
    await sleep(1500);
    check(size, 'How it works stays closed on the next visit', !(await page.evaluate(() => !!document.querySelector('.modal.show'))));

    check(size, 'no page or console errors', errors.length === 0, errors.slice(0, 5).join(' | '));
    await browser.close();
}

for (const s of sizes) {
    try { await run(s); } catch (e) { check(s, 'run', false, String(e).slice(0, 300)); }
}
console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
process.exit(failures ? 1 : 0);
