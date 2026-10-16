"""Load test for the Oct 16, 2026 outage (docs/incident-2026-10-16.md).

Runs the app exactly like Render (gunicorn gthread: 1 worker, 16 threads, timeout 180) while tools/netsim makes NOAA
(and Global Fishing Watch) fail the way they can fail from Render, fires 50 concurrent mixed requests twice (the
SST page, Currents, the currents manifest, GFW tiles, the SST fetch callback for uncached old weeks, analytics
beacons) and requires every response within 30 s. Then it checks that `kill -USR1 <worker>` writes every thread's
stack to the log, that /api/debug/threads answers, and that no request is left running.

    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES .venv/bin/python tools/load_sst_outage.py
    .venv/bin/python tools/load_sst_outage.py --scenarios render --port 8071 --log-dir /tmp/sst-load
    .venv/bin/python tools/load_sst_outage.py --extra-path /path/to/other/gunicorn   # another gunicorn version

Scenarios:
  unreachable  NOAA fails at once with errno 101; no GFW token
  render       Render on Oct 16: NOAA's IPv4 dropped then errno 101 (the full connect timeout each try); GFW
               (fake token) dropping packets too
  slow         NOAA and GFW answer, then drip a byte every 2 s (needs openssl for a test certificate)
Exit code 1 if any response is late or missing, or a check fails.
"""

import argparse
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT_S = 30.0
TOKEN = "load-test-token"

SCENARIOS = {
    "unreachable": {"NETSIM": "*.noaa.gov=unreachable", "gfw": False},
    "render": {"NETSIM": "*.noaa.gov=v4drop,*.globalfishingwatch.org=blackhole", "gfw": True},
    "slow": {"NETSIM": "*.noaa.gov=drip,*.globalfishingwatch.org=drip", "gfw": True, "tls": True},
}


def log(msg):
    print(msg, flush=True)


def http(base, path, data=None, headers=None, timeout=45.0, method=None, want_headers=False):
    """(status or error name, seconds, body[, headers])."""
    req = urllib.request.Request(base + path, data=data, headers=headers or {}, method=method)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = (r.status, time.monotonic() - t0, r.read())
            return out + (dict(r.headers),) if want_headers else out
    except urllib.error.HTTPError as e:
        out = (e.code, time.monotonic() - t0, e.read())
        return out + (dict(e.headers),) if want_headers else out
    except Exception as e:  # noqa: BLE001 - a timeout or reset is the failure we're testing for
        return type(e).__name__, time.monotonic() - t0, b""


def make_cert(d: Path):
    """A self-signed certificate for the NOAA and GFW hosts and a CA bundle that trusts it, or None."""
    if not shutil.which("openssl"):
        return None
    (d / "san.cnf").write_text(
        "[req]\ndistinguished_name = dn\nx509_extensions = v3\nprompt = no\n[dn]\nCN = netsim\n[v3]\n"
        "subjectAltName = DNS:coastwatch.pfeg.noaa.gov, DNS:gateway.api.globalfishingwatch.org\n"
        "basicConstraints = critical, CA:TRUE\nkeyUsage = digitalSignature, keyCertSign\n"
        "extendedKeyUsage = serverAuth\n")
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(d / "key.pem"),
                        "-out", str(d / "cert.pem"), "-days", "2", "-config", str(d / "san.cnf")],
                       capture_output=True)
    if r.returncode != 0:
        return None
    import certifi
    (d / "bundle.pem").write_bytes(Path(certifi.where()).read_bytes() + (d / "cert.pem").read_bytes())
    return d / "cert.pem", d / "key.pem", d / "bundle.pem"


def fetch_callback(base):
    """The SST fetch callback's spec from /_dash-dependencies."""
    status, _, raw = http(base, "/_dash-dependencies", timeout=20)
    deps = json.loads(raw)
    return next(c for c in deps if "sst-store.data" in c["output"] and
              any(isinstance(i.get("id"), str) and i["id"] == "fetch-btn" for i in c["inputs"]))


def fetch_body(cb, week):
    """The fetch callback's request body for Load week pressed with `week` in the date box."""
    outputs = []
    for part in cb["output"].strip(".").split("..."):
        cid, prop = part.split(".", 1)
        outputs.append({"id": cid, "property": prop})
    inputs = []
    for i in cb["inputs"]:
        if not isinstance(i.get("id"), str) or i["id"].startswith("{"):
            inputs.append([])                       # the saved-week buttons (pattern-matching ALL): none
        else:
            value = {"fetch-btn": random.randint(1, 10 ** 6), "lock-scale": []}.get(i["id"])
            inputs.append({"id": i["id"], "property": i["property"], "value": value})
    state = [{"id": s["id"], "property": s["property"], "value": week} for s in cb["state"]]
    return json.dumps({"output": cb["output"], "outputs": outputs, "inputs": inputs, "state": state,
                       "changedPropIds": ["fetch-btn.n_clicks"]}).encode()


def wave(base, n, seed, gfw_range, cb):
    rnd = random.Random(seed)
    kinds = ["fetch", "gfw", "manifest", "sst", "fetch", "gfw", "manifest", "sst", "currents", "event"]

    def one(i):
        kind = kinds[i % len(kinds)]
        if kind == "fetch":
            wk = date(2003, 6, 1) + timedelta(days=rnd.randint(0, 6000))
            st, el, body = http(base, "/_dash-update-component", data=fetch_body(cb, wk.isoformat()),
                                headers={"Content-Type": "application/json"})
            ok_text = b"fetch-status" in body
        elif kind == "gfw":
            st, el, body, hdr = http(base, f"/api/gfw/7/{36 + i % 5}/{45 + i % 6}.png?dr={gfw_range}",
                                     want_headers=True)
            if st == 503:   # "busy, ask again in a moment" (assets/sst.js retries it): fine if it says so
                ok_text = hdr.get("Retry-After") == "1" and hdr.get("Cache-Control") == "no-store"
                st = "503-busy"
            else:           # a PNG, or 204 when no GFW token is set (the layer then does nothing)
                ok_text = st == 204 or hdr.get("Content-Type", "").startswith("image/png")
        elif kind == "manifest":
            st, el, body = http(base, "/api/currents/manifest.json")
            ok_text = body.startswith(b"{")
        elif kind == "event":
            st, el, body = http(base, "/api/event", data=b'{"t":"sst","e":"gfw"}',
                                headers={"Content-Type": "text/plain"})
            ok_text = True
        else:
            st, el, body = http(base, f"/{kind}")
            ok_text = b"_dash-config" in body
        return kind, st, el, ok_text

    barrier = threading.Barrier(n)

    def start(i):
        barrier.wait()
        return one(i)

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(start, range(n)))


def worker_pid(master_pid):
    try:
        import psutil
        kids = psutil.Process(master_pid).children()
        return kids[0].pid if kids else None
    except Exception:  # noqa: BLE001
        out = subprocess.run(["pgrep", "-P", str(master_pid)], capture_output=True, text=True).stdout.split()
        return int(out[0]) if out else None


def run_scenario(name, args, workdir: Path) -> bool:
    sc = SCENARIOS[name]
    d = workdir / name
    (d / "cache" / "sst").mkdir(parents=True)
    (d / "stats").mkdir()
    saved = sorted((ROOT / "cache" / "sst").glob("*_adaptive.json.gz"))[-3:] if (ROOT / "cache" / "sst").is_dir() else []
    for p in saved:   # a few saved weeks, so the page opens on one and "nearest saved weeks" has something
        shutil.copy(p, d / "cache" / "sst" / p.name)
    env = dict(os.environ)
    for var in ("GFW_API_TOKEN", "LABS_HOST", "LABS_HOME_URL", "RENDER", "_SST_PREWARM_STARTED",
                "_SST_WATCHDOG_STARTED"):
        env.pop(var, None)
    env.update({"OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES", "SST_CACHE_DIR": str(d / "cache"),
                "LABS_STATS_DIR": str(d / "stats"), "CACHE_UPLOAD_TOKEN": TOKEN, "NETSIM": sc["NETSIM"],
                "NETSIM_QUIET": "1", "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": os.pathsep.join([p for p in (args.extra_path, str(ROOT / "tools" / "netsim")) if p])})
    if sc.get("gfw"):
        env["GFW_API_TOKEN"] = "fake-load-test-token"
    if sc.get("tls"):
        cert = make_cert(d)
        if not cert:
            log(f"[{name}] skipped: openssl not available for the test certificate")
            return True
        env.update({"NETSIM_TLS_CERT": str(cert[0]), "NETSIM_TLS_KEY": str(cert[1]), "REQUESTS_CA_BUNDLE": str(cert[2])})
    cmd = [sys.executable, "-m", "gunicorn", "app:server", "--bind", f"127.0.0.1:{args.port}",
           "--workers", "1", "--threads", "16", "--timeout", "180"]
    help_text = subprocess.run([sys.executable, "-m", "gunicorn", "--help"], capture_output=True, text=True,
                               env=env, cwd=ROOT).stdout
    if "--control-socket" in help_text:   # gunicorn >= 25 would leave gunicorn.ctl in the repo
        cmd += ["--control-socket", str(d / "gunicorn.ctl")]
    if http(f"http://127.0.0.1:{args.port}", "/", timeout=2)[0] != "URLError":
        log(f"[{name}] FAIL port {args.port} is already in use")
        return False
    logf = open(d / "server.log", "w")
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{args.port}"
    ok = True

    def fail(msg):
        nonlocal ok
        ok = False
        log(f"[{name}] FAIL {msg}")

    try:
        t_end = time.monotonic() + 90
        while time.monotonic() < t_end:
            st, _, _ = http(base, "/api/currents/manifest.json", timeout=5)
            if st == 200:
                break
            if proc.poll() is not None:
                fail("gunicorn exited at startup")
                return False
            time.sleep(0.5)
        else:
            fail("server never came up")
            return False
        version = next((ln.split("Starting gunicorn")[1].strip() for ln in (d / "server.log").read_text().splitlines()
                        if "Starting gunicorn" in ln), "?")
        log(f"[{name}] gunicorn {version} on {base}, NETSIM={sc['NETSIM']}")
        http(base, "/sst")
        cb = fetch_callback(base)
        gfw_range = "2026-09-01,2026-09-07"
        for w in (1, 2):
            t0 = time.monotonic()
            res = wave(base, args.n, seed=w * 7 + len(name), gfw_range=gfw_range, cb=cb)
            wall = time.monotonic() - t0
            late = [(k, st, round(el, 1)) for k, st, el, _ in res
                    if not (isinstance(st, int) or st == "503-busy") or el > LIMIT_S]
            bad = [(k, st) for k, st, el, okt in res if not okt or (isinstance(st, int) and st >= 500)]
            busy = sum(1 for _, st, _, _ in res if st == "503-busy")
            by = {}
            for k, st, el, _ in res:
                by.setdefault(k, []).append(el)
            log(f"[{name}] wave {w}: {len(res)} requests at once, all answered in {wall:.1f}s; slowest "
                + ", ".join(f"{k} {max(v):.1f}s" for k, v in sorted(by.items()))
                + (f"; {busy} GFW tiles answered 'busy, retry' (503)" if busy else ""))
            if late:
                fail(f"wave {w}: {len(late)} late or unanswered (limit {LIMIT_S:.0f}s): {late[:8]}")
            if bad:
                fail(f"wave {w}: bad answers: {bad[:8]}")
        # nothing left running
        t_end = time.monotonic() + 15
        running = None
        while time.monotonic() < t_end:
            st, _, body = http(base, "/api/debug/threads", headers={"X-Upload-Token": TOKEN}, timeout=10)
            text = body.decode("utf-8", "replace")
            running = next((ln for ln in text.splitlines() if ln.startswith("requests running")), "")
            if st == 200 and running.strip() == "requests running: 1":
                break
            time.sleep(1)
        if running is None or running.strip() != "requests running: 1":
            fail(f"/api/debug/threads: requests still running after 15 s: {running!r}")
        else:
            log(f"[{name}] /api/debug/threads ok; " + "; ".join(ln.strip() for ln in text.splitlines()
                                                                 if ln.startswith("upstream"))[:300])
        # kill -USR1 <worker>: every thread's stack in the log
        pid = worker_pid(proc.pid)
        before = (d / "server.log").read_text().count("Current thread 0x")
        if pid:
            os.kill(pid, signal.SIGUSR1)
            time.sleep(1.5)
            after = (d / "server.log").read_text().count("Current thread 0x")
            if after <= before:
                fail("SIGUSR1 to the worker wrote no stack dump")
            else:
                log(f"[{name}] kill -USR1 {pid}: stack dump in the log; server still answers: "
                    f"{http(base, '/api/currents/manifest.json', timeout=10)[0]}")
        else:
            fail("couldn't find the worker pid")
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(40)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)
        logf.close()
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", default="unreachable,render,slow")
    ap.add_argument("--port", type=int, default=8071)
    ap.add_argument("--n", type=int, default=50, help="concurrent requests per wave")
    ap.add_argument("--extra-path", default="", help="put this first on PYTHONPATH (e.g. another gunicorn)")
    ap.add_argument("--log-dir", default="", help="keep the server logs here (default: a temp dir, removed)")
    args = ap.parse_args()
    workdir = Path(args.log_dir) if args.log_dir else Path(tempfile.mkdtemp(prefix="sst-load-"))
    workdir.mkdir(parents=True, exist_ok=True)
    results = {}
    for name in [s for s in args.scenarios.split(",") if s]:
        if name not in SCENARIOS:
            log(f"unknown scenario {name}")
            return 2
        results[name] = run_scenario(name, args, workdir)
    if not args.log_dir:
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        log(f"server logs in {workdir}")
    log("\n" + ", ".join(f"{k}: {'ok' if v else 'FAIL'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
