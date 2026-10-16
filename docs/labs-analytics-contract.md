# GotOne Labs analytics: the shared contract (Oct 2026)

One counting service on the Labs app (Render, labs.gotoneapp.com) serves every Labs tool: Offshore SST and Currents
(this app) and The Striper Recount (static site at striperrecount.gotoneapp.com, repo
~/Code/ASGA/striper-recount-video). Same privacy rules as the existing counter (data/labstats.py): no cookies, nothing
stored in the browser, nothing about a visitor stored; a daily-salted hash of (server secret, date, IP, user agent)
only for daily-unique counts and rate limits, deleted after 90 days; Do Not Track / Global Privacy Control respected
for counting; bots ignored; only our origins accepted.

## Endpoints (all POST, body = a JSON string sent as text/plain via navigator.sendBeacon, so cross-origin posts
## from striperrecount.gotoneapp.com need no CORS preflight; all return 204 and never fail a page)

1. `POST https://labs.gotoneapp.com/api/hit` (exists; extend) body `{"p": PAGE, "r": REFERRER_HOST}`
   PAGE is one of:
   - `labs`, `sst`, `currents` (this app; `labs` is now only the redirected home, keep it for history)
   - `recount` (the Striper Recount home page, index.html: the video plus all seven interactives on one page)
   - `recount/count`, `recount/calendar`, `recount/ghosts`, `recount/pie`, `recount/removals`, `recount/states`,
     `recount/next` (the single-page versions, e.g. count.html), `recount/about` (about.html)
   The tool of a page is the part before "/" (labs, sst, currents, recount).
   REFERRER_HOST as today: a host only, "internal" for later views in a visit, "... (embedded)" when framed.

2. `POST https://labs.gotoneapp.com/api/event` (new) body `{"t": TOOL, "e": EVENT}`
   Feature use. TOOL is `sst`, `currents` or `recount`. EVENT must be in the server's whitelist
   (labstats.EVENTS[tool]); anything else is ignored. Counted as events per day plus unique visitors per day per
   event; at most 40 of one event per visitor per day. Same DNT/GPC, bot and origin rules as hits.
   Clients send each event at most once per page view unless noted, so counts read as "page views that did X".
   Whitelist (the builders may add names to their own tool; keep names [a-z0-9_]{1,32}):
   - recount: `video_play`, `video_25`, `video_50`, `video_75`, `video_end`,
     `reach_count`, `reach_calendar`, `reach_ghosts`, `reach_pie`, `reach_removals`, `reach_states`, `reach_next`
       (the page's section came into view: in the home page's long scroll, or on its single page),
     `turn_count`, `turn_calendar`, `turn_ghosts`, `turn_pie`, `turn_removals`, `turn_states`, `turn_next`
       (the reader used that page's "Your turn" gesture at least once),
     `end_count`, ..., `end_next` (that page's closing/takeaway block was reached),
     `share_four` (copied or shared the four answers), `share_page` (footer Share), `lyrics` (opened the lyrics),
     `short_version` (opened the short version), `out_asga`, `out_gotone`, `out_asmfc`, `out_labs` (clicked those links)
   - currents (as wired in assets/currents.js and app.py's Currents forwarders; a control counts only when the
     user changes it, never for the defaults a new page view hands over):
     `view_today`, `view_cycle`, `view_weeks` (switched to that view), `date` (picked another day in Today; "Now"
     isn't counted as a pick), `now` (pressed Now), `strength` (picked a tide strength in Tide cycle), `week`
     (picked another example week), `spot` (selected a NOAA station, from the list or a dot on the map), `inlet`
     (selected a harbor-inlet estimate), `how_it_works` (opened the panel; the automatic first-visit opening isn't
     counted), `scrub` (set the time on the chart: click, tap or drag), `play` (started playback, sidebar or map
     play bar), `step` (stepped an hour back or forward), `speed` (changed the playback speed), `depth` (switched
     surface / depth-averaged), `fullscreen` (went full screen, or opened the page in a new tab from an embed)
   - sst (as wired in app.py's SST feature-use clientside callback, plus the day slider's `data-labs-event`):
     `load` (pressed Load week, formerly Fetch SST; the automatic first load isn't counted), `play` (played the
     week's animation, sidebar or the map's play bar), `step` (moved to another day: step buttons, the map's play
     bar or the day slider), `readout` (tapped the map for a temperature, including inside The Dump), `poi`
     (tapped a fishing spot's circle for its details), `measure` (measured a distance: both points placed),
     `spots` (opened the spot list), `chart` (turned on the nautical chart), `bathymetry` (turned on bathymetry),
     `gfw` (turned on fishing activity), `lock_scale` (turned on "Same colors every week"), `opacity` (changed
     Color strength, the SST opacity), `how_it_works` (opened the How it works panel from the header or the
     sidebar; the automatic first-visit opening isn't counted)

3. `POST https://labs.gotoneapp.com/api/feedback` (new) body
   `{"t": TOOL, "v": "up"|"down"|"", "n": NOTE, "w": HONEYPOT, "d": SECONDS_ON_PAGE}`
   The "Helpful?" vote and optional note. Never published anywhere; read only on the private dashboard.
   Accepted only when: Origin is ours (required for this endpoint), HONEYPOT is empty, SECONDS_ON_PAGE >= 3,
   TOOL valid, a vote and/or a non-empty note, NOTE <= 500 characters (trimmed, control characters removed),
   at most 5 posts per visitor per tool per day, at most 300 posts per day overall. Recorded even with DNT/GPC on
   (the reader chose to send it), but still only with the daily hash for the rate limit. A vote then a note from the
   same page view: the client sends the vote at once and the note later as a second post `{"v": "", "n": ...}`;
   the dashboard shows notes with their vote when the same visitor voted on that tool that day.

## Dashboard: /labs/stats?key=LABS_STATS_KEY (exists; extend; JSON at /api/labs/stats)
Per tool (Offshore SST, Currents, The Striper Recount): views and daily-unique visitors for the period (7/30/90/365
days), the Striper Recount broken down by page; where visits came from; feature use (event, page views that did it,
unique visitors); Helpful votes (up/down) and the latest notes (date, tool, vote, note, newest first, escaped).
Plus the existing by-day table, summed per tool. Plain, readable on a phone, noindex, no-store.

## Client rules (both builders)
- Count only on the live hosts (`*.gotoneapp.com`, `*.onrender.com`), never locally unless a test flag is set
  (`window.GotOneLabsCountHere = true`); never with DNT/GPC (feedback excepted, see above).
- Fire-and-forget: navigator.sendBeacon(url, JSON string); fall back to fetch(keepalive, mode "no-cors"). Never throw.
- The "Helpful?" control: a small "Was this helpful?" with thumbs up / thumbs down buttons; after a tap, "Thanks!"
  and an optional one-line note field ("Anything to add? (optional, private)") with a Send button and a 500-char
  limit; a visually hidden honeypot input (name="website", tabindex -1, autocomplete off, aria-hidden) that must stay
  empty; keyboard and screen-reader friendly; no em dashes in any copy; nothing published.
- Privacy copy wherever a tool states its privacy: visits and feature use are counted anonymously by GotOne Labs, no
  cookies, nothing about you is stored, Do Not Track respected; feedback notes are private and never published.

## Server details as built (builder 1, Oct 16 2026)
- All three endpoints answer 204 whatever happens. Bodies over 500 bytes (hit, event) or 4096 bytes (feedback) are
  ignored, as are bodies that aren't a JSON object. `Origin: null` counts as foreign.
- `/api/*`, `/_dash*`, `/assets/*` and `/labs/stats` are never redirected by the LABS_HOST / LABS_HOME_URL /
  /striper-recount redirects, and none of them touches a POST.
- Bots (and an empty User-Agent) are ignored for feedback too. A note is cleaned first (line breaks become spaces,
  control and bidi characters are dropped, then trimmed); a note still over 500 characters is rejected, not cut.
- Visitor hashes are deleted after 90 days; the daily unique counts they gave (per page, per tool across its pages,
  per event) are kept first, so the 90- and 365-day views still have visitor numbers. Feedback rows stay, without
  their hash after 90 days.
- A tool's daily visitors are distinct across its pages (one reader of the Recount home and count.html is one).
- Stats storage: SQLite at `$LABS_STATS_DIR/labs.sqlite` (default `$SST_CACHE_DIR/stats`).
- This app's client: `GotOneLabs.page(path)`, `GotOneLabs.event(name, {tool, repeat})` and the Helpful control
  (an empty `<div class="labs-helpful" data-tool="sst|currents">` that assets/labs.js fills) in assets/labs.js.
