/*
 * GotOne Labs: page titles, the anonymous counter and the "Helpful?" control
 * (data/labstats.py; the rules are in docs/labs-analytics-contract.md).
 *
 * app.py's clientside callback on the URL calls GotOneLabs.page(pathname) on
 * every page view (first load and in-app navigation). It sets the tab title
 * and posts {p: page, r: referrer host} to /api/hit. The referrer is sent for
 * the first view of a visit only; later views in the same visit report
 * "internal".
 *
 * GotOneLabs.event(name) posts {t: tool, e: name} to /api/event: feature use,
 * at most once per page view per name (pass {repeat: true} to send again).
 * The tool is the current page's (sst / currents) unless {tool: ...} is given.
 * The server ignores names that aren't in labstats.EVENTS.
 *
 * The "Helpful?" control: an empty <div class="labs-helpful" data-tool="...">
 * in a Dash layout is filled here (Dash never re-renders its children). A vote
 * posts {t, v, n: "", w, d} to /api/feedback at once; the optional note comes
 * later as a second post {t, v: "", n, w, d}. w is a hidden honeypot field
 * that must stay empty, d the seconds since the page view began.
 *
 * Everything is sent only on the live hosts (or with
 * window.GotOneLabsCountHere = true for testing), fire-and-forget with
 * navigator.sendBeacon, and never throws. Hits and events are never sent with
 * Do Not Track or Global Privacy Control on; feedback is (the reader chose to
 * send it). No cookies, nothing stored in the browser.
 */
(function () {
    "use strict";
    var LIVE = /(^|\.)gotoneapp\.com$|\.onrender\.com$/;
    var NAME = /^[a-z0-9_]{1,32}$/;
    var TITLES = {
        labs: "GotOne Labs · Free tools for saltwater anglers",
        sst: "Offshore SST · GotOne Labs",
        currents: "Currents · GotOne Labs"
    };
    var TOOLS = { sst: true, currents: true };          // page ids that are tools here
    var first = true;
    var view = { page: null, tool: null, start: Date.now(), sent: {} };
    var log = [];                                       // last sends, for testing in the console

    function pageOf(path, host) {
        path = (path || "/").replace(/\/+$/, "") || "/";
        if (path === "/currents") { return "currents"; }
        if (path === "/sst") { return "sst"; }
        if (path === "/labs") { return "labs"; }
        // The old sst.gotoneapp.com still opens on the SST map.
        if (path === "/") { return /^sst\./.test(host) ? "sst" : "labs"; }
        return null;                                    // /catches etc.: not counted
    }

    function referrer() {
        var r = "", framed;
        try { r = new URL(document.referrer).hostname; } catch (e) { r = ""; }
        if (r && r === location.hostname) { r = "internal"; }
        try { framed = window.self !== window.top; } catch (e) { framed = true; }
        return framed ? (r || "unknown") + " (embedded)" : r;   // the Squarespace embed
    }

    function live() { return LIVE.test(location.hostname) || !!window.GotOneLabsCountHere; }

    function dnt() {
        return navigator.doNotTrack === "1" || window.doNotTrack === "1" ||
            !!navigator.globalPrivacyControl;
    }

    function send(path, obj) {
        try {
            var body = JSON.stringify(obj);
            log.push({ url: path, body: obj });
            if (log.length > 50) { log.shift(); }
            if (navigator.sendBeacon && navigator.sendBeacon(path, body)) { return; }
            fetch(path, { method: "POST", body: body, keepalive: true, mode: "no-cors" })
                .catch(function () { /* counting is best-effort */ });
        } catch (e) { /* never let counting break the page */ }
    }

    function seconds() { return Math.round((Date.now() - view.start) / 100) / 10; }

    // ------------------------------------------------- "Was this helpful?" --
    var THUMB = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">' +
        '<path d="M7 10v12M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8' +
        'a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" fill="none" ' +
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    function feedback(el, vote, note) {
        if (!live()) { return; }
        var hp = el.querySelector("input[name=website]");
        send("/api/feedback", { t: el.getAttribute("data-tool"), v: vote, n: note,
                                w: hp ? hp.value : "", d: seconds() });
    }

    function mountHelpful(el) {
        if (el.getAttribute("data-ready")) { return; }
        el.setAttribute("data-ready", "1");
        var id = "labs-helpful-" + (el.getAttribute("data-tool") || "x");
        el.setAttribute("role", "region");
        el.setAttribute("aria-label", "Feedback");
        el.innerHTML =
            '<div class="lh-ask" role="group" aria-labelledby="' + id + '-q">' +
            '<span class="lh-q" id="' + id + '-q">Was this helpful?</span>' +
            '<button type="button" class="lh-thumb" data-v="up" title="Yes" aria-label="Yes, helpful">' + THUMB + '</button>' +
            '<button type="button" class="lh-thumb lh-down" data-v="down" title="No" aria-label="No, not helpful">' + THUMB + '</button>' +
            '</div>' +
            '<form class="lh-more" hidden novalidate>' +
            '<p class="lh-thanks" tabindex="-1">Thanks!</p>' +
            '<label class="lh-label" for="' + id + '-n">Anything to add? (optional, private)</label>' +
            '<div class="lh-row">' +
            '<input class="lh-note" id="' + id + '-n" type="text" maxlength="500" autocomplete="off" enterkeyhint="send">' +
            '<button type="submit" class="lh-send">Send</button>' +
            '</div>' +
            '<input class="lh-hp" type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">' +
            '</form>' +
            '<p class="lh-done" tabindex="-1" hidden>Thanks, your note was sent.</p>';
        var ask = el.querySelector(".lh-ask"), form = el.querySelector(".lh-more");
        ask.addEventListener("click", function (ev) {
            var b = ev.target && ev.target.closest ? ev.target.closest("button[data-v]") : null;
            if (!b) { return; }
            feedback(el, b.getAttribute("data-v"), "");
            ask.hidden = true;
            form.hidden = false;
            el.querySelector(".lh-thanks").focus();
        });
        form.addEventListener("submit", function (ev) {
            ev.preventDefault();
            var input = el.querySelector(".lh-note");
            var note = (input.value || "").replace(/\s+/g, " ").trim().slice(0, 500);
            if (!note) { input.focus(); return; }
            feedback(el, "", note);
            form.hidden = true;
            var done = el.querySelector(".lh-done");
            done.hidden = false;
            done.focus();
        });
    }

    function mountAll() {
        var els = document.querySelectorAll(".labs-helpful:not([data-ready])");
        for (var i = 0; i < els.length; i++) { mountHelpful(els[i]); }
        return els.length;
    }

    // The router swaps the page in after page() runs (a server callback), so
    // watch for the control's container until it shows up (or 30 s pass).
    var watcher = null, watchStop = 0;
    function watchHelpful() {
        if (watcher) { watcher.disconnect(); watcher = null; clearTimeout(watchStop); }
        if (mountAll() || !window.MutationObserver) { return; }
        var root = document.getElementById("page-content") || document.body;
        watcher = new MutationObserver(function () {
            if (mountAll() && watcher) { watcher.disconnect(); watcher = null; clearTimeout(watchStop); }
        });
        watcher.observe(root, { childList: true, subtree: true });
        watchStop = setTimeout(function () {
            if (watcher) { watcher.disconnect(); watcher = null; }
        }, 30000);
    }

    // Elements marked data-labs-event="name" (data-labs-tool optional) count as
    // feature use when pressed or worked from the keyboard (the SST day slider).
    function onUse(ev) {
        try {
            if (ev.type === "keydown" && !/^(Arrow|Home$|End$|Page|Enter$| $)/.test(ev.key || "")) { return; }
            var el = ev.target && ev.target.closest ? ev.target.closest("[data-labs-event]") : null;
            if (el) {
                window.GotOneLabs.event(el.getAttribute("data-labs-event"),
                                        { tool: el.getAttribute("data-labs-tool") || null });
            }
        } catch (e) { /* never break the page */ }
    }
    document.addEventListener("pointerdown", onUse, true);
    document.addEventListener("keydown", onUse, true);

    // ----------------------------------------------------------------- API --
    window.GotOneLabs = {
        page: function (path) {
            var host = location.hostname, p = pageOf(path, host);
            if (p) { document.title = TITLES[p]; }
            var r = first ? referrer() : "internal";
            first = false;
            view = { page: p, tool: p && TOOLS[p] ? p : null, start: Date.now(), sent: {} };
            try { watchHelpful(); } catch (e) { /* the page works without it */ }
            if (!p || !live() || dnt()) { return; }
            send("/api/hit", { p: p, r: r });
        },
        // Feature use; returns true when it was sent.
        event: function (name, opts) {
            try {
                opts = opts || {};
                var tool = opts.tool || view.tool, key = tool + ":" + name;
                if (!tool || !NAME.test(name || "")) { return false; }
                if (view.sent[key] && !opts.repeat) { return false; }
                view.sent[key] = true;
                if (!live() || dnt()) { return false; }
                send("/api/event", { t: tool, e: name });
                return true;
            } catch (e) { return false; }
        },
        _log: log
    };
})();
