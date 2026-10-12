/*
 * GotOne Labs: page titles + the anonymous page counter (data/labstats.py).
 *
 * app.py's clientside callback on the URL calls GotOneLabs.page(pathname) on
 * every page view (first load and in-app navigation). It sets the tab title
 * and posts {p: page, r: referrer host} to /api/hit, only on the live hosts,
 * never with Do Not Track or Global Privacy Control on. No cookies, nothing
 * stored in the browser. The referrer is sent for the first view of a visit
 * only; later views in the same visit report "internal".
 */
(function () {
    "use strict";
    var LIVE = /(^|\.)gotoneapp\.com$|\.onrender\.com$/;
    var TITLES = {
        labs: "GotOne Labs · Free tools for saltwater anglers",
        sst: "Offshore SST · GotOne Labs",
        currents: "Currents · GotOne Labs"
    };
    var first = true;

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

    window.GotOneLabs = {
        page: function (path) {
            var host = location.hostname, p = pageOf(path, host);
            if (p) { document.title = TITLES[p]; }
            var r = first ? referrer() : "internal";
            first = false;
            if (!p || !(LIVE.test(host) || window.GotOneLabsCountHere)) { return; }
            if (navigator.doNotTrack === "1" || window.doNotTrack === "1" ||
                navigator.globalPrivacyControl) { return; }
            var body = JSON.stringify({ p: p, r: r });
            try {
                if (navigator.sendBeacon) {
                    navigator.sendBeacon("/api/hit", body);
                } else {
                    fetch("/api/hit", { method: "POST", body: body, keepalive: true })
                        .catch(function () { /* counting is best-effort */ });
                }
            } catch (e) { /* never let counting break the page */ }
        }
    };
})();
