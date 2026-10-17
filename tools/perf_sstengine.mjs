// Frame times of the client-side SST engine (assets/sstengine.js) on a throttled phone profile (sprint 2 budget:
// recolor < 50 ms per frame with the CPU slowed 4x; isotherms and breaks computed once per day, off the main thread).
//
//   node tools/perf_sstengine.mjs --base http://127.0.0.1:8081            # phone 390x664 @2x, CPU 4x slower
//   node tools/perf_sstengine.mjs --base ... --throttle 1 --size 1440x900 --dpr 1   # laptop
//
// Needs a server whose newest saved week is in the new format (data/sstgrid) and Playwright (as tools/smoke_sst.mjs).
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

await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
console.log(`\n${W}x${H} @${DPR}x, CPU throttled ${THROTTLE}x; page errors: ${errors.length ? errors.join(' | ') : 'none'}`);
await browser.close();
process.exit(failed || errors.length ? 1 : 0);
