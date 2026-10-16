"""Simulate a broken network for chosen hosts, inside one Python process (tests only).

Python imports `sitecustomize` at startup from sys.path, so putting this directory on PYTHONPATH loads it
into every Python process started with that environment (gunicorn included). It does nothing unless
NETSIM is set:

    NETSIM="coastwatch.pfeg.noaa.gov=unreachable,*.noaa.gov=unreachable,*.globalfishingwatch.org=blackhole"

Host patterns are exact names or "*.suffix". Modes:

  unreachable  connect() fails at once with [Errno 101] Network is unreachable (Render to NOAA, Oct 2026)
  refused      connect() fails at once with [Errno 61/111] Connection refused
  dnsfail      getaddrinfo() fails (the name doesn't resolve)
  blackhole    connect() waits out the socket's timeout, then times out (a firewall that drops packets);
               with no timeout on the socket it waits NETSIM_MAX_S (default 3600 s)
  tarpit       connect() goes to a local server that accepts and never sends a byte (TLS handshake hangs)
  v4drop       two addresses, like coastwatch.pfeg.noaa.gov's A + AAAA seen from a host without IPv6: the first
               (IPv4) is dropped (waits out the connect timeout), the second fails at once with errno 101. So
               each attempt takes the whole connect timeout but reports "Network is unreachable" (urllib3 raises
               the last address's error): what Render's log showed on Oct 16, 2026
  drip         connect() goes to a local server that answers "200 OK" with a big Content-Length and then
               sends one byte every NETSIM_DRIP_S (default 2 s). Needs NETSIM_TLS_CERT + NETSIM_TLS_KEY (a
               certificate for the host; point REQUESTS_CA_BUNDLE at a bundle that trusts it) for https.

Every simulated failure is written to stderr as "[netsim] ...", so a server log shows them.
"""

import errno
import os
import socket
import sys
import threading
import time

_SPEC = os.environ.get("NETSIM", "").strip()


def _parse(spec):
    rules = []
    for part in spec.split(","):
        part = part.strip()
        if "=" in part:
            host, mode = part.split("=", 1)
            rules.append((host.strip().lower(), mode.strip().lower()))
    return rules


_RULES = _parse(_SPEC)
_MAX_S = float(os.environ.get("NETSIM_MAX_S", "3600"))
_DRIP_S = float(os.environ.get("NETSIM_DRIP_S", "2"))
_QUIET = os.environ.get("NETSIM_QUIET") == "1"


def _mode_for(host):
    h = (host or "").lower().rstrip(".")
    for pat, mode in _RULES:
        if pat.startswith("*."):
            if h == pat[2:] or h.endswith(pat[1:]):
                return mode
        elif h == pat:
            return mode
    return None


def _say(msg):
    if not _QUIET:
        try:
            sys.stderr.write(f"[netsim] {msg}\n")
            sys.stderr.flush()
        except Exception:
            pass


# Fake addresses (198.18.0.0/15 is reserved for benchmarking) stand for the simulated hosts, so connect()
# can tell them apart without guessing from real IPs.
_fake_ip = {}      # host -> fake ip
_fake_host = {}    # fake ip -> (host, mode)
_lock = threading.Lock()


def _fake_for(host, mode):
    with _lock:
        ip = _fake_ip.get(host)
        if ip is None:
            n = len(_fake_ip) + 1
            ip = f"198.18.{n // 250}.{n % 250 + 1}"
            _fake_ip[host] = ip
            _fake_host[ip] = (host, mode)
        return ip


# ---- local servers for tarpit / drip ----
_servers = {}


def _serve_forever(lsock, handler):
    while True:
        try:
            c, _ = lsock.accept()
        except OSError:
            return
        threading.Thread(target=handler, args=(c,), daemon=True).start()


def _tarpit_handler(c):
    try:
        while c.recv(65536):        # read whatever comes, never answer
            pass
    except OSError:
        pass
    finally:
        c.close()


def _drip_handler(c):
    cert, key = os.environ.get("NETSIM_TLS_CERT"), os.environ.get("NETSIM_TLS_KEY")
    try:
        if cert and key:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            c = ctx.wrap_socket(c, server_side=True)
        c.settimeout(30)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = c.recv(65536)
            if not chunk:
                return
            buf += chunk
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
                  b"Content-Length: 10000000\r\nConnection: close\r\n\r\n")
        while True:
            time.sleep(_DRIP_S)
            c.sendall(b"x")
    except Exception:
        pass
    finally:
        try:
            c.close()
        except Exception:
            pass


def _local_server(kind):
    with _lock:
        port = _servers.get(kind)
        if port:
            return port
        ls = _real_socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind(("127.0.0.1", 0))
        ls.listen(128)
        port = ls.getsockname()[1]
        handler = _tarpit_handler if kind == "tarpit" else _drip_handler
        threading.Thread(target=_serve_forever, args=(ls, handler), daemon=True,
                         name=f"netsim-{kind}").start()
        _servers[kind] = port
        return port


# ---- the patches ----
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_socket = socket.socket


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    mode = _mode_for(host if isinstance(host, str) else (host or b"").decode("ascii", "ignore"))
    if mode is None:
        return _real_getaddrinfo(host, port, family, type, proto, flags)
    if mode == "dnsfail":
        _say(f"getaddrinfo({host}) -> EAI_NONAME")
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known")
    if mode == "v4drop":
        a = _fake_for(host.lower() + "#v4", "blackhole")
        b = _fake_for(host.lower() + "#v6", "unreachable")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, int(port or 0))),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (b, int(port or 0)))]
    ip = _fake_for(host.lower(), mode)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, int(port or 0)))]


def _simulate(sock, address):
    """None to connect for real (not a simulated host), or the (host, port) to connect to instead."""
    if not (isinstance(address, tuple) and address and address[0] in _fake_host):
        return None
    host, mode = _fake_host[address[0]]
    if mode == "unreachable":
        _say(f"connect({host}) -> ENETUNREACH")
        raise OSError(errno.ENETUNREACH, "Network is unreachable")
    if mode == "refused":
        _say(f"connect({host}) -> ECONNREFUSED")
        raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
    if mode == "blackhole":
        t = sock.gettimeout()
        wait = _MAX_S if t is None else t
        _say(f"connect({host}) -> blackhole, waiting {wait:.1f}s")
        time.sleep(wait)
        raise TimeoutError("timed out")
    if mode in ("tarpit", "drip"):
        _say(f"connect({host}) -> {mode}")
        return ("127.0.0.1", _local_server(mode))
    return None


def _connect(self, address):
    alt = _simulate(self, address)
    return _real_connect(self, alt or address)


def _connect_ex(self, address):
    try:
        alt = _simulate(self, address)
    except OSError as e:
        return e.errno or errno.ETIMEDOUT
    return _real_connect_ex(self, alt or address)


if _RULES:
    socket.getaddrinfo = _getaddrinfo
    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    _say(f"active in pid {os.getpid()}: {_SPEC}")
