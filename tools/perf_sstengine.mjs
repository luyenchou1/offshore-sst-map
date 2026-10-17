// Frame times of the client-side SST engine (assets/sstengine.js) on a throttled phone profile (sprint 2 budget:
// recolor < 50 ms per frame with the CPU slowed 4x; isotherms and breaks computed once per day, off the main thread).
//
//   node tools/perf_sstengine.mjs --base http://127.0.0.1:8081            # phone 390x664 @2x, CPU 4x slower
//   node tools/perf_sstengine.mjs --base ... --throttle 1 --size 1440x900 --dpr 1   # laptop
//
// Needs a server whose newest saved week is in the new format (data/sstgrid) and Playwright (as tools/smoke_sst.mjs).
// --only coast skips everything before the coast section (the coastline, interpolation, land per view).
// Prints one line per measurement (median and worst of several runs) and exits 1 if a budget is missed.

import os from 'node:os';

const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, arr) => {
    if (v.startsWith('--')) { a.push([v.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : '1']); }
    return a;
}, []));
const BASE = (args.base || 'http://127.0.0.1:8081').replace(/\/$/, '');
const THROTTLE = +(args.throttle || 4);
const [W, H] = (args.size || '390x664').split('x').map(Number);
const DPR = +(args.dpr || 2);
const BUDGET = { recolor: 50, frame: 50 };

async function loadPlaywright() {
    for (const t of [process.env.PLAYWRIGHT, 'playwright', `${os.homedir()}/Code/bf6-recon/node_modules/playwright/index.mjs`].filter(Boolean)) {
        try { return await import(t); } catch (e) { /* next */ }
    }
    console.error('Playwright not found: npm i playwright, or set PLAYWRIGHT=/path/to/playwright/index.mjs');
    process.exit(2);
}
const { chromium } = await loadPlaywright();
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: DPR,
                                       hasTouch: W < 800, isMobile: W < 800 });
await ctx.addInitScript(() => { try { localStorage.setItem('gotone-sst-intro-v1', '1'); } catch (e) { /* */ } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
const cdp = await ctx.newCDPSession(page);
await page.goto(`${BASE}/sst`);
await page.waitForFunction(() => window.SSTEngine && window.SSTEngine.state().ready, null, { timeout: 90000 });
await page.evaluate(() => window.SST._state.map.setView([39.9, -71.6], 8, { animate: false }));
await page.waitForTimeout(800);
await cdp.send('Emulation.setCPUThrottlingRate', { rate: THROTTLE });

const med = a => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
let failed = 0;
function line(name, vals, budget) {
    const m = med(vals), worst = Math.max(...vals);
    const ok = budget === undefined || worst <= budget;
    if (!ok) { failed++; }
    console.log(`${ok ? 'ok  ' : 'OVER'} ${name.padEnd(44)} median ${m.toFixed(1).padStart(6)} ms   worst ${worst.toFixed(1).padStart(6)} ms` +
                (budget ? `   (budget ${budget} ms)` : ''));
}

if (args.only !== 'coast') {
    // decode a week again (the same bytes), throttled
    const decode = await page.evaluate(async () => {
        const E = window.SSTEngine, S = E._state;
        const buf = await (await fetch(S.url)).arrayBuffer();
        const out = [];
        for (let k = 0; k < 3; k++) {
            const t0 = performance.now(), p = S.payload;
            const ready = new Promise(r => { const off = E.on('ready', () => { off(); r(); }); });
            E.setWeek(Object.assign({}, p, { grid: Object.assign({}, p.grid, { url: p.grid.url + '&k=' + k }) }));
            await ready;
            out.push({ total: performance.now() - t0, decode: E._stats.decodeMs, hist: E._stats.histMs, bytes: buf.byteLength });
        }
        return out;
    });
    line(`week arrives: decode (${(decode[0].bytes / 1e6).toFixed(1)} MB of codes)`, decode.map(d => d.decode));
    line('week arrives: histogram + Auto range', decode.map(d => d.hist));

    // a palette / range / stepped change: LUT + recolor of the day + one redraw, all synchronous
    const recolor = await page.evaluate(() => {
        const E = window.SSTEngine, out = [];
        const steps = [() => E.setPalette('classic'), () => E.setPalette('spectrum'), () => E.setRange({ mode: 'fixed', min: 62, max: 76 }),
                       () => E.setOptions({ stepped: true }), () => E.setOptions({ stepped: false }), () => E.setRange({ mode: 'auto' }),
                       () => E.setPalette('cvd'), () => E.setPalette('spectrum')];
        steps.forEach(f => { const t0 = performance.now(); f(); out.push(performance.now() - t0); });
        return out;
    });
    line('palette / range / stepped change (recolor + draw)', recolor, BUDGET.recolor);
    // Chrome's throttle slices time, so a single short task can slip through: also 20 changes back to back, averaged
    const recolor20 = await page.evaluate(() => {
        const E = window.SSTEngine, out = [];
        for (let rep = 0; rep < 3; rep++) {
            const t0 = performance.now();
            for (let k = 0; k < 20; k++) { E.setRange({ mode: 'fixed', min: 60 + (k % 5), max: 78 }); }
            out.push((performance.now() - t0) / 20);
        }
        E.setRange({ mode: 'auto' });
        return out;
    });
    line('  ... 20 range changes back to back, per change', recolor20, BUDGET.recolor);

    // a day change: the new day's colors, then a 300 ms crossfade (frames measured by the engine itself)
    const days = await page.evaluate(async () => {
        const E = window.SSTEngine, S = E._state, first = [], frames = [];
        for (let k = 0; k < 6; k++) {
            S.stats.fadeFrameMs.length = 0;
            const t0 = performance.now();
            E.setDay(k % 2 ? 6 : 2 + (k % 3));
            first.push(performance.now() - t0);
            await new Promise(r => setTimeout(r, 450));
            frames.push(...S.stats.fadeFrameMs);
        }
        return { first, frames };
    });
    line('day change: setDay (recolor of the new day)', days.first, BUDGET.recolor);
    line(`crossfade frames (${days.frames.length})`, days.frames, BUDGET.frame);

    // What the reader sees: the gaps between animation frames during crossfades (raster and compositing included), stepping
    // forward through the week as Play does (one day every STEP_MS; the page's own interval is 1500 ms), against the gaps
    // of an idle page under the same throttle.
    const STEP_MS = +(args.step || 900);
    async function frameGaps(label) {
        const g = await page.evaluate(async (stepMs) => {
            const E = window.SSTEngine, S = E._state, idle = [], fade = [];
            let last = null, run = true, inFade = false;
            const tick = t => {
                if (last !== null) { (inFade ? fade : idle).push(t - last); }
                last = t;
                inFade = S.prevDay !== null;
                if (run) { requestAnimationFrame(tick); }
            };
            requestAnimationFrame(tick);
            await new Promise(r => setTimeout(r, 1000));
            for (let k = 0; k < 8; k++) { E.setDay((S.day + 1) % S.week.days.length); await new Promise(r => setTimeout(r, stepMs)); }
            run = false;
            return { idle, fade };
        }, STEP_MS);
        line(label + ` (${g.fade.length} frames)`, g.fade, BUDGET.frame + 17);
        line('  ... idle page, same throttle', g.idle.length ? g.idle : [0]);
    }
    await frameGaps('frame gaps while crossfading');

    // isotherms + breaks: computed once per day in the worker; then frames with the lines drawn
    const iso = await page.evaluate(async () => {
        const E = window.SSTEngine, S = E._state;
        E.setOptions({ isotherms: true, breaks: true });
        const t0 = performance.now();
        await new Promise(r => { const iv = setInterval(() => { const a = S.analysis[S.day]; if (a && a.iso && a.breakA) { clearInterval(iv); r(); } }, 20); });
        const firstReady = performance.now() - t0;
        await new Promise(r => setTimeout(r, 6000));       // let the other days finish in the background
        const draws = [];
        for (let k = 0; k < 6; k++) {
            const t1 = performance.now();
            S.layer._redraw();
            draws.push(performance.now() - t1);
        }
        // cold: a jump to a day whose isotherm layer isn't drawn yet, right away (the first frame draws it)
        S.stats.fadeFrameMs.length = 0;
        E.setDay(S.day <= 3 ? 6 : 0);
        await new Promise(r => setTimeout(r, 450));
        const cold = S.stats.fadeFrameMs.slice();
        // warm: a step to the next day after a moment of idle (as Play does: the neighbours are drawn while idle)
        await new Promise(r => setTimeout(r, 1200));
        S.stats.fadeFrameMs.length = 0;
        E.setDay((S.day + 1) % S.week.days.length);
        await new Promise(r => setTimeout(r, 450));
        const fade = S.stats.fadeFrameMs.slice();
        const pan = [];
        for (let k = 0; k < 4; k++) {                       // a pan: new view, labels placed again
            S.map.panBy([60, 30], { animate: false });
            const t2 = performance.now();
            S.layer._redraw();
            pan.push(performance.now() - t2);
        }
        return { firstReady, perDay: Object.values(S.stats.analysisMs), draws, fade, cold, pan };
    });
    line('isotherms + breaks for the day on the map (worker)', [iso.firstReady]);
    line('  ... per day in the worker', iso.perDay);
    line('redraw with isotherms + labels', iso.draws, BUDGET.frame);
    line('crossfade frames with isotherms (next day)', iso.fade.length ? iso.fade : [0], BUDGET.frame);
    line('  ... jump to a far day at once (no budget)', iso.cold.length ? iso.cold : [0]);
    line('redraw after a pan (labels placed again)', iso.pan, BUDGET.frame);
    await frameGaps('frame gaps while crossfading, isotherms + breaks on');
}

// The coast (docs/coast-progress.md): the coastline's level 0 load and tile preparation, then views zoomed in near
// the coast. A pan or zoom ends in one redraw (Leaflet moves and scales the canvas in between): the interpolated
// water for the visible map, the land canvases, the isotherm layer. Measured per view at zoom 9 to 12 over Hampton
// Bays and Casco Bay (the box's most detailed coast), then the same view panned, and crossfade frames at zoom 11.
const coast = await page.evaluate(async () => {
    const E = window.SSTEngine, S = E._state, L = window.SSTLand, m = S.map, out = { views: [] };
    E.setOptions({ isotherms: false, breaks: false });
    const settle = () => new Promise(r => setTimeout(r, 60));
    const tilesIn = async () => { for (let k = 0; k < 200 && Object.keys(L._state.pending).length; k++) { await settle(); } };
    const go = async (lat, lon, z) => {
        for (let k = 0; k < 40; k++) {
            if (!m._animatingZoom) { m.setView([lat, lon], z, { animate: false }); }
            await settle();
            if (!m._animatingZoom && m.getZoom() === z) { break; }
        }
        await tilesIn();
    };
    // a redraw for a view not seen yet (the caches dropped); "raster" adds the canvas's own drawing, which Chrome
    // otherwise does later (forced here by reading one pixel back)
    const one = () => {
        S.interp = null; S.ival = null; S.landCache = null; S.comp = null;
        S.stats.interpMs.length = 0; S.stats.landMs.length = 0;
        const t0 = performance.now();
        S.layer._redraw();
        const total = performance.now() - t0;
        S.layer._ctx.getImageData(0, 0, 1, 1);
        const raster = performance.now() - t0;
        return { total, raster, interp: S.stats.interpMs[0] || 0, land: S.stats.landMs[0] || 0 };
    };
    // the coastline alone for a new view: its paths, then drawn over a copy of the SST and rastered
    const landOnly = () => {
        const c = S.layer._container, cv = document.createElement('canvas');
        cv.width = c.width; cv.height = c.height;
        const g = cv.getContext('2d'), b0 = S.layer._bounds, mm = c.width / (b0.max.x - b0.min.x);
        g.drawImage(c, 0, 0);
        g.getImageData(0, 0, 1, 1);
        g.setTransform(mm, 0, 0, mm, -b0.min.x * mm, -b0.min.y * mm);
        S.landCache = null;
        const t0 = performance.now();
        E._debug.drawLand(g, S.layer);
        g.getImageData(0, 0, 1, 1);
        return performance.now() - t0;
    };
    // what the reader waits for when a pan ends: from the moveend (Leaflet redraws every layer then) to the next
    // frame, raster and compositing included
    const panGap = (dx, dy) => new Promise(res => requestAnimationFrame(() => {
        const t0 = performance.now();
        m.panBy([dx, dy], { animate: false });
        requestAnimationFrame(() => res(performance.now() - t0));
    }));
    for (const [name, lat, lon] of [['Hampton Bays', 40.84, -72.5], ['Casco Bay', 43.66, -70.18]]) {
        for (const z of [9, 10, 11, 12]) {
            await go(lat, lon, z);
            one();                                       // warm the tiles' paths
            const runs = [];
            for (let k = 0; k < 4; k++) { runs.push(one()); }
            const lands = [];
            for (let k = 0; k < 4; k++) { lands.push(landOnly()); }
            const pans = [];
            for (let k = 0; k < 4; k++) {
                pans.push(await panGap(k % 2 ? -80 : 80, 40));
                await new Promise(r => setTimeout(r, 250));
            }
            out.views.push({ name, z, runs, pans, lands, interp: E.state().interp, level: E.state().coast.level,
                             rlevel: S.landCache ? S.landCache.rimLevel : null,
                             px: S.stats.interpPx, verts: (S.landCache && S.landCache.tiles) || 0 });
        }
    }
    // Stepped: the bands come from a smoothed window of the field (more work per view)
    E.setOptions({ stepped: true });
    out.stepped = [];
    for (const z of [9, 11]) {
        await go(40.84, -72.5, z);
        const ms = [];
        for (let k = 0; k < 4; k++) { S.interp = null; S.ival = null; S.comp = null; S.stats.interpMs.length = 0; S.layer._redraw(); ms.push(S.stats.interpMs[0] || 0); }
        out.stepped.push({ z, ms });
    }
    E.setOptions({ stepped: false });
    await go(40.84, -72.5, 11);
    await new Promise(r => setTimeout(r, 1500));
    const fades = [], gaps = [];
    for (let k = 0; k < 4; k++) {
        S.stats.fadeFrameMs.length = 0;
        let last = null, run = true;
        const tick = t => { if (last !== null && S.prevDay !== null) { gaps.push(t - last); } last = t; if (run) { requestAnimationFrame(tick); } };
        requestAnimationFrame(tick);
        E.setDay((S.day + 1) % S.week.days.length);
        await new Promise(r => setTimeout(r, 1300));
        run = false;
        fades.push(...S.stats.fadeFrameMs);
    }
    out.fades = fades;
    out.fadeGaps = gaps;
    // the same pans before the coast work, for comparison: interpolation and the coastline off
    const L0 = window.SSTLand, saveReady = L0.ready;
    L0.ready = () => false; S.fillOn = false; S.landCache = null;
    S.interpOff = true;
    const basePans = [];
    for (let k = 0; k < 6; k++) { basePans.push(await panGap(k % 2 ? -80 : 80, 40)); await new Promise(r => setTimeout(r, 250)); }
    S.interpOff = false;
    L0.ready = saveReady; S.plan = null;
    out.basePans = basePans;
    out.l0Ms = L._stats.l0Ms;
    out.prep = L._stats.prepMs.slice();
    out.plan = S.stats.planMs;
    out.fill = S.stats.fillMs.slice();
    out.grid = S.stats.landGridMs;
    out.ns = S.nsPerPx;
    return out;
});
line('coast: fill-under-the-coast plan (once per week)', [coast.plan || 0]);
line('coast: fill per day', coast.fill.length ? coast.fill : [0]);
line('coast: land cover per cell for labels (once per week)', [coast.grid || 0]);
line('coast: coastline level 0 fetch + prepare (page load)', [coast.l0Ms || 0]);
line('coast: prepare one tile (paths)', coast.prep.length ? coast.prep : [0]);
coast.views.forEach(v => {
    const tag = `${v.name} z${v.z} (${v.interp ? 'interp ' + v.px.join('x') : 'cells'}, land L${v.level}, rim ${v.rlevel === null ? 'none' : 'L' + v.rlevel})`;
    line(`coast: new view ${tag}`, v.runs.map(r => r.total), BUDGET.frame);
    line('  ... of which interpolation', v.runs.map(r => r.interp));
    line('  ... of which the land paths', v.runs.map(r => r.land));
    line('  ... the coastline alone, with its raster', v.lands, 16);
    line('  ... with the canvas raster', v.runs.map(r => r.raster));
    line('  ... a pan ending: moveend to the next frame', v.pans);
});
coast.stepped.forEach(v => line(`coast: interpolation with Stepped (smoothed bands), zoom ${v.z}`, v.ms));
line(`coast: crossfade frames at zoom 11, interpolated (${coast.fades.length})`, coast.fades.length ? coast.fades : [0], BUDGET.frame);
line('  ... frame gaps while crossfading', coast.fadeGaps.length ? coast.fadeGaps : [0], BUDGET.frame + 17);
line('coast: a pan ending at zoom 11 without the coast work (cells, no coastline)', coast.basePans);

await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
console.log(`\n${W}x${H} @${DPR}x, CPU throttled ${THROTTLE}x; page errors: ${errors.length ? errors.join(' | ') : 'none'}`);
await browser.close();
process.exit(failed || errors.length ? 1 : 0);
