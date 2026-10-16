"""Calls to outside servers that can't hang a request thread (incident 2026-10-16, docs/incident-2026-10-16.md).

The app answers every route from one gunicorn worker with 16 request threads. A request that waits on an outside
server (NOAA ERDDAP, Global Fishing Watch, Natural Earth) holds one of them; on Oct 16, 2026 waits like that took all
16 and every route, static ones too, stopped answering. So every outside call goes through an Upstream:

- the call runs on the upstream's own few threads and the caller waits only until its deadline, so the request thread
  is free at the deadline whatever the network does (DNS, several addresses, TLS, a server that drips bytes);
- the body is read in pieces with the deadline checked after each one and the socket timeout capped by the time left,
  so the upstream's own thread is free at the deadline too (requests' timeout is per read: a slow body never trips it);
- when all its threads are busy a call waits at most `queue_s` for one, and at most `max_waiting` callers wait; the
  rest are refused at once ("busy"), so one upstream can never hold more than workers + max_waiting request threads;
- after `threshold` failures in a row of the kinds in `trip_on` (always: unreachable, i.e. no route / refused / no
  DNS / connect timeout) the circuit breaker refuses calls for `open_s` seconds ("down"), then lets one call through
  to probe. A down upstream costs a request microseconds instead of a thread for its whole budget.
"""

import json as _json
import logging
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Dict, Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

# Failure kinds (OutboundError.kind)
DOWN, BUSY, UNREACHABLE, TIMEOUT, NETWORK, TOO_BIG = (
    "down", "busy", "unreachable", "timeout", "network", "too_big")

_CHUNK = 64 * 1024


class Deadline:
    """A point in time all the work of one request must finish by."""

    def __init__(self, seconds: float, end: Optional[float] = None):
        self.end = end if end is not None else time.monotonic() + seconds

    def left(self) -> float:
        return self.end - time.monotonic()

    def expired(self) -> bool:
        return self.left() <= 0

    def minus(self, seconds: float) -> "Deadline":
        """A deadline `seconds` earlier than this one."""
        return Deadline(0, end=self.end - seconds)


class OutboundError(Exception):
    """A call failed or wasn't made. kind: down (breaker open), busy (no free thread), unreachable (no connection),
    timeout (no answer by the deadline), network (other transport error), too_big."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


class Reply:
    """A finished HTTP exchange: status, the whole body (decoded), headers."""

    __slots__ = ("status", "body", "headers")

    def __init__(self, status: int, body: bytes, headers=None):
        self.status = int(status)
        self.body = body or b""
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})

    def text(self, limit: Optional[int] = None) -> str:
        b = self.body if limit is None else self.body[:limit]
        return b.decode("utf-8", "replace")

    def json(self):
        return _json.loads(self.body.decode("utf-8"))


def _is_connect_failure(e: Exception) -> bool:
    """True when no connection could be made (no route, refused, no DNS, connect timeout)."""
    if isinstance(e, requests.exceptions.ConnectTimeout):
        return True
    if not isinstance(e, requests.exceptions.ConnectionError):
        return False
    try:
        from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
        inner = e.args[0] if e.args else None
        reason = getattr(inner, "reason", None)
        if isinstance(inner, (NewConnectionError, ConnectTimeoutError)) or \
                isinstance(reason, (NewConnectionError, ConnectTimeoutError)):
            return True
    except ImportError:  # pragma: no cover
        pass
    s = str(e)
    return ("Failed to establish a new connection" in s or "NameResolutionError" in s
            or "Name or service not known" in s or "nodename nor servname" in s)


def _kind_of(e: Exception) -> str:
    if isinstance(e, OutboundError):
        return e.kind
    if _is_connect_failure(e):
        return UNREACHABLE
    if isinstance(e, requests.exceptions.Timeout):
        return TIMEOUT
    try:
        from urllib3.exceptions import ReadTimeoutError
        if isinstance(e, ReadTimeoutError):
            return TIMEOUT
    except ImportError:  # pragma: no cover
        pass
    if isinstance(e, (socket.timeout, TimeoutError)):
        return TIMEOUT
    return NETWORK


def _socket_of(raw):
    """The socket under a urllib3 response, or None (then reads are only bounded by the read timeout)."""
    for get in (lambda r: r.connection.sock, lambda r: r._connection.sock, lambda r: r._fp.fp.raw._sock):
        try:
            s = get(raw)
            if s is not None and hasattr(s, "settimeout"):
                return s
        except Exception:  # noqa: BLE001 - urllib3 internals differ between versions
            continue
    return None


def _read_body(r, deadline: Deadline, read_s: float, max_bytes: int) -> bytes:
    """The whole (decoded) body, read in pieces; the deadline holds even when the server drips."""
    raw = getattr(r, "raw", None)
    read1 = getattr(raw, "read1", None)
    buf = bytearray()
    if callable(read1):
        sock = _socket_of(raw)
        while True:
            left = deadline.left()
            if left <= 0:
                raise OutboundError(TIMEOUT, "the deadline passed during the download")
            if sock is not None:
                try:
                    sock.settimeout(max(0.05, min(read_s, left)))
                except OSError:
                    pass
            chunk = read1(_CHUNK, decode_content=True)
            if not chunk:
                return bytes(buf)
            buf += chunk
            if len(buf) > max_bytes:
                raise OutboundError(TOO_BIG, f"more than {max_bytes} bytes")
    if hasattr(r, "iter_content"):   # test doubles, very old urllib3
        for chunk in r.iter_content(_CHUNK):
            buf += chunk
            if deadline.expired():
                raise OutboundError(TIMEOUT, "the deadline passed during the download")
            if len(buf) > max_bytes:
                raise OutboundError(TOO_BIG, f"more than {max_bytes} bytes")
        return bytes(buf)
    return bytes(getattr(r, "content", b"") or b"")


def _call(method, url, deadline, connect_s, read_s, headers, params, max_bytes) -> Reply:
    """One HTTP exchange on the calling thread (an Upstream's own thread). Raises OutboundError."""
    left = deadline.left()
    if left < 0.2:
        raise OutboundError(TIMEOUT, "no time left")
    timeout = (max(0.1, min(connect_s, left)), max(0.1, min(read_s, left)))
    fn = requests.get if method == "GET" else requests.post   # looked up per call: tests replace them
    r = None
    try:
        r = fn(url, params=params, headers=headers, timeout=timeout, stream=True)
        body = _read_body(r, deadline, read_s, max_bytes)
        return Reply(getattr(r, "status_code", 0), body, getattr(r, "headers", None))
    except OutboundError:
        raise
    except Exception as e:  # noqa: BLE001 - every transport error becomes a kind
        raise OutboundError(_kind_of(e), f"{type(e).__name__}: {e}") from None
    finally:
        close = getattr(r, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass


class Breaker:
    """Closed, open (refuse everything until open_until), then half-open (one probe call at a time)."""

    def __init__(self, name: str, open_s: float, threshold: int, trip_on):
        self.name = name
        self.open_s = float(open_s)
        self.threshold = max(1, int(threshold))
        self.trip_on = set(trip_on) | {UNREACHABLE}
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0
        self._probing = False
        self.last_error = ""
        self.trips = 0

    def _refusal(self, now):
        wait = max(0.0, self._open_until - now)
        return OutboundError(DOWN, f"{self.name} failing ({self.last_error[:160]}); not asked again for "
                                   f"{wait:.0f}s" if wait else f"{self.name} failing; another call is checking it")

    def check(self) -> None:
        """Raise OutboundError(DOWN) while the breaker is open (or a probe is out); changes nothing."""
        with self._lock:
            now = time.monotonic()
            if self._open_until and (now < self._open_until or self._probing):
                raise self._refusal(now)

    def admit(self) -> bool:
        """Go ahead (True when this call is the half-open probe), or raise OutboundError(DOWN)."""
        with self._lock:
            if not self._open_until:
                return False
            now = time.monotonic()
            if now < self._open_until or self._probing:
                raise self._refusal(now)
            self._probing = True
            return True

    def report(self, kind: Optional[str], detail: str = "", probe: bool = False) -> None:
        """The outcome of a call that was made: kind None = it got an answer (any HTTP status)."""
        with self._lock:
            if probe:
                self._probing = False
            if kind in self.trip_on:
                self._failures += 1
                if self._failures >= self.threshold or probe or self._open_until:
                    first = not self._open_until
                    self._open_until = time.monotonic() + self.open_s
                    self.last_error = detail or kind
                    if first:
                        self.trips += 1
                        logger.warning("%s is failing (%s: %s): not asking it for %.0fs", self.name, kind,
                                       detail[:200], self.open_s)
            else:  # it answered, or failed after connecting: reachable
                if self._open_until:
                    logger.info("%s answers again", self.name)
                self._failures = 0
                self._open_until = 0.0

    def is_down(self) -> bool:
        with self._lock:
            return bool(self._open_until) and (time.monotonic() < self._open_until or self._probing)

    def state(self) -> str:
        with self._lock:
            if not self._open_until:
                return f"closed ({self._failures} failures in a row)"
            left = self._open_until - time.monotonic()
            return (f"open for {left:.0f}s more" if left > 0 else "half-open" + (" (probing)" if self._probing else "")) \
                + f", last error: {self.last_error[:120]}"


class Upstream:
    """One outside server: its own threads, a cap on waiting callers, a circuit breaker."""

    def __init__(self, name: str, workers: int = 4, queue_s: float = 0.0, max_waiting: int = 0,
                 open_s: float = 60.0, threshold: int = 1, trip_on=(UNREACHABLE,)):
        self.name = name
        self.workers = max(1, int(workers))
        self.queue_s = float(queue_s)
        self.max_waiting = int(max_waiting)
        self.breaker = Breaker(name, open_s, threshold, trip_on)
        self._slots = threading.BoundedSemaphore(self.workers)
        self._pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix=f"out-{name}")
        self._lock = threading.Lock()
        self._waiting = 0
        self._inflight = 0

    def is_down(self) -> bool:
        return self.breaker.is_down()

    def _take_slot(self, deadline: Deadline) -> None:
        if self._slots.acquire(blocking=False):
            return
        wait = min(self.queue_s, max(0.0, deadline.left()))
        with self._lock:
            if wait <= 0 or self._waiting >= self.max_waiting:
                raise OutboundError(BUSY, f"{self.name}: {self.workers} calls already in flight")
            self._waiting += 1
        try:
            if not self._slots.acquire(timeout=wait):
                raise OutboundError(BUSY, f"{self.name}: no free connection within {wait:.1f}s")
        finally:
            with self._lock:
                self._waiting -= 1

    def request(self, method: str, url: str, deadline: Deadline, *, connect_s: float = 3.0,
                read_s: float = 8.0, headers: Optional[Dict] = None, params: Optional[Dict] = None,
                max_bytes: int = 32 * 1024 * 1024) -> Reply:
        """Make one call and return its Reply (any HTTP status), or raise OutboundError. Returns by the deadline."""
        self.breaker.check()          # a down upstream is refused before waiting for anything
        self._take_slot(deadline)
        try:
            probe = self.breaker.admit()
        except OutboundError:
            self._slots.release()
            raise
        with self._lock:
            self._inflight += 1

        def done(fut):
            with self._lock:
                self._inflight -= 1
            self._slots.release()
            exc = fut.exception()
            if exc is None:
                self.breaker.report(None, probe=probe)
            else:
                self.breaker.report(_kind_of(exc), str(getattr(exc, "detail", exc)), probe=probe)

        try:
            fut = self._pool.submit(_call, method, url, deadline, connect_s, read_s, headers, params, max_bytes)
        except RuntimeError as e:  # interpreter shutting down
            with self._lock:
                self._inflight -= 1
            self._slots.release()
            if probe:
                self.breaker.report(None, probe=True)
            raise OutboundError(BUSY, str(e)) from None
        fut.add_done_callback(done)
        try:
            return fut.result(timeout=max(0.0, deadline.left()) + 0.05)
        except FuturesTimeout:
            raise OutboundError(TIMEOUT, f"{self.name}: no answer by the deadline") from None
        except OutboundError:
            raise
        except Exception as e:  # noqa: BLE001
            raise OutboundError(_kind_of(e), f"{type(e).__name__}: {e}") from None

    def state(self) -> str:
        with self._lock:
            busy = f"{self._inflight}/{self.workers} in flight, {self._waiting} waiting"
        return f"{self.name}: {busy}; breaker {self.breaker.state()}"

    def reset(self) -> None:
        """Tests only: close the breaker."""
        with self.breaker._lock:
            self.breaker._failures = 0
            self.breaker._open_until = 0.0
            self.breaker._probing = False


# ---- the app's upstreams, one per (profile, host) ----
def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


PROFILES = {
    # NOAA ERDDAP: a week is 7 day requests, 3 at a time per reader; 503s are retried by data/erddap.with_retry,
    # so a full pool answers "busy" at once. The breaker opens on the first connect failure (Render, Oct 2026).
    "noaa": dict(workers=_env_int("NOAA_MAX_CALLS", 10), queue_s=0.0, max_waiting=0, open_s=60.0,
                 threshold=1, trip_on=(UNREACHABLE,)),
    # Global Fishing Watch tiles: a screenful is 12-20 tiles at once and GFW takes about a second each, so a tile
    # may wait a little for a free connection. Three connect failures or timeouts in a row open the breaker: the
    # layer then shows nothing for a minute instead of holding request threads.
    "gfw": dict(workers=_env_int("GFW_MAX_CALLS", 6), queue_s=3.0, max_waiting=4, open_s=60.0,
                threshold=3, trip_on=(UNREACHABLE, TIMEOUT)),
    "default": dict(workers=2, queue_s=0.0, max_waiting=0, open_s=60.0, threshold=1, trip_on=(UNREACHABLE,)),
}
_upstreams: Dict = {}
_registry_lock = threading.Lock()


def upstream(profile: str, host: str = "") -> Upstream:
    key = (profile, host)
    with _registry_lock:
        u = _upstreams.get(key)
        if u is None:
            label = {"noaa": "NOAA ERDDAP", "gfw": "Global Fishing Watch"}.get(profile, profile)
            u = _upstreams[key] = Upstream(f"{label} ({host})" if host else label,
                                           **PROFILES.get(profile, PROFILES["default"]))
        return u


def for_url(url: str, profile: str = "default") -> Upstream:
    return upstream(profile, (urlsplit(url).hostname or "").lower())


def states():
    with _registry_lock:
        ups = list(_upstreams.values())
    return [u.state() for u in ups]


def reset_all() -> None:
    """Tests only: close every breaker."""
    with _registry_lock:
        ups = list(_upstreams.values())
    for u in ups:
        u.reset()
