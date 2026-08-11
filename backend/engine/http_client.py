"""
Bounded, instrumented HTTP GET — the single network choke point for both
marketplace scrapers.

Why this exists
---------------
`requests.get(url, timeout=30)` does NOT bound total wall-clock time. The
`timeout` is `(connect, read)`, and `read` is only the maximum gap BETWEEN
received bytes. A server that trickles one byte every <read seconds, streams a
slow/large chunked body, or chains redirects keeps the socket "alive" forever,
so the call never returns and never raises. On the inventory run's single
worker thread that parks the entire generator: no SSE events, UI stuck on
"Searching" indefinitely.

`get_bounded()` fixes that with a hard, predictable per-request ceiling:

  * connect timeout + read-gap timeout (the `requests` tuple), PLUS
  * an absolute WALL-CLOCK deadline enforced while streaming the body, PLUS
  * a maximum response size, PLUS
  * a small redirect cap.

There are NO retries (a failed/timed-out page returns/raises once and the
caller moves on) and NO concurrency, proxies, or bypasses — a request is still
one ordinary, polite GET. It also records a structured log entry per request so
the retrieval path is fully observable: (source, brand, model, page), start,
end, duration, HTTP status, outcome, bytes.

Text decoding reuses `requests`' own `.text` path on the buffered bytes, so the
HTML handed to the parsers is byte-for-byte identical to the previous code —
nothing about parsing or matching changes.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

# --------------------------------------------------------------------------- #
# Hard per-request bounds. These define the run's predictable worst case.
# --------------------------------------------------------------------------- #
CONNECT_TIMEOUT_S = 6.0     # max time to establish the TCP/TLS connection
READ_GAP_TIMEOUT_S = 12.0   # max gap between bytes (the requests `read` timeout)
TOTAL_DEADLINE_S = 20.0     # absolute wall-clock ceiling for the whole request
MAX_BYTES = 8_000_000       # refuse absurdly large bodies (defensive)
MAX_REDIRECTS = 3           # cap redirect chains so they cannot multiply time
_CHUNK = 16_384


class FetchTimeout(Exception):
    """
    A request exceeded its hard wall-clock deadline or size cap. This is a SOFT,
    NON-BLOCK failure: unlike BlockedError it must NOT trip the anti-bot circuit
    breaker (a slow response is not an access restriction). Callers treat it as
    "this page/source is unavailable right now; keep what we have and move on".
    """


# --------------------------------------------------------------------------- #
# Per-request instrumentation (run-scoped, thread-safe).
# --------------------------------------------------------------------------- #
@dataclass
class RequestRecord:
    source: str
    brand: str
    model: str
    page: int
    url: str
    start_iso: str
    end_iso: str
    duration_s: float
    status: Optional[int]
    outcome: str            # ok | timeout | oversize | network_error | http_error
    bytes: int
    error: str = ""
    # Filled in later by the orchestrator (it alone knows these):
    listings: Optional[int] = None
    top_up: Optional[bool] = None
    retry: int = 0          # always 0 — we never retry; kept for the report schema

    def as_dict(self) -> dict:
        return {
            "source": self.source, "brand": self.brand, "model": self.model,
            "page": self.page, "url": self.url,
            "start": self.start_iso, "end": self.end_iso,
            "duration_s": round(self.duration_s, 3),
            "status": self.status, "outcome": self.outcome,
            "bytes": self.bytes, "listings": self.listings,
            "top_up": self.top_up, "retry": self.retry,
            "error": self.error,
        }


@dataclass
class _RunLog:
    records: list[RequestRecord] = field(default_factory=list)
    count: int = 0


_lock = threading.Lock()
_log = _RunLog()


def reset_log() -> None:
    """Start a fresh instrumentation window (call at the start of a run)."""
    global _log
    with _lock:
        _log = _RunLog()


def request_count() -> int:
    """Total real HTTP requests issued since the last reset (monotonic)."""
    with _lock:
        return _log.count


def records() -> list[RequestRecord]:
    with _lock:
        return list(_log.records)


def _append(rec: RequestRecord) -> None:
    with _lock:
        _log.records.append(rec)
        _log.count += 1
    # Structured, greppable server-side line so the path is observable live.
    print(
        f"[v0][http] {rec.source} {rec.brand}/{rec.model} p{rec.page} "
        f"{rec.outcome} status={rec.status} {rec.duration_s:.2f}s "
        f"bytes={rec.bytes} url={rec.url}",
        flush=True,
    )


def last_record() -> Optional[RequestRecord]:
    with _lock:
        return _log.records[-1] if _log.records else None


# --------------------------------------------------------------------------- #
# The bounded GET
# --------------------------------------------------------------------------- #
def get_bounded(
    url: str,
    headers: dict,
    *,
    source: str = "",
    brand: str = "",
    model: str = "",
    page: int = 0,
    connect_timeout: float = CONNECT_TIMEOUT_S,
    read_timeout: float = READ_GAP_TIMEOUT_S,
    total_deadline_s: float = TOTAL_DEADLINE_S,
    max_bytes: int = MAX_BYTES,
) -> tuple[int, str, dict]:
    """
    Perform ONE bounded GET. Returns (status_code, text, response_headers).

    Guarantees a finite worst case: the connection phase is bounded by
    (redirects+1) x (connect+read) and the body phase by `total_deadline_s`.
    Raises FetchTimeout on deadline/size overrun; re-raises requests'
    RequestException for connect/DNS/reset errors (callers decide how to map
    those). Every outcome is recorded exactly once.
    """
    t0 = time.perf_counter()
    start_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    status: Optional[int] = None
    total = 0
    outcome = "ok"
    error = ""

    def _finish(text_len_note: str = "") -> None:  # noqa: ARG001 - kept for clarity
        _append(RequestRecord(
            source=source, brand=brand, model=model, page=page, url=url,
            start_iso=start_iso,
            end_iso=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            duration_s=time.perf_counter() - t0,
            status=status, outcome=outcome, bytes=total, error=error,
        ))

    session = requests.Session()
    session.max_redirects = MAX_REDIRECTS
    try:
        resp = session.get(
            url, headers=headers, stream=True, allow_redirects=True,
            timeout=(connect_timeout, read_timeout),
        )
        status = resp.status_code
        deadline = t0 + total_deadline_s
        chunks: list[bytes] = []
        for chunk in resp.iter_content(_CHUNK):
            if time.perf_counter() > deadline:
                outcome = "timeout"
                error = f"exceeded {total_deadline_s:.0f}s wall-clock deadline while reading body"
                resp.close()
                _finish()
                raise FetchTimeout(f"{url!r}: {error}")
            if chunk:
                total += len(chunk)
                chunks.append(chunk)
                if total > max_bytes:
                    outcome = "oversize"
                    error = f"response exceeded {max_bytes} bytes"
                    resp.close()
                    _finish()
                    raise FetchTimeout(f"{url!r}: {error}")

        # Reuse requests' exact decoding on the buffered bytes so the HTML text
        # is identical to `resp.text` from the old code path.
        resp._content = b"".join(chunks)
        resp._content_consumed = True
        text = resp.text or ""
        if status != 200:
            outcome = "http_error"
        _finish()
        return status, text, dict(resp.headers)

    except FetchTimeout:
        raise  # already recorded
    except requests.Timeout as exc:
        outcome = "timeout"
        error = f"connect/read timeout: {exc}"
        _finish()
        raise FetchTimeout(f"{url!r}: {error}") from exc
    except requests.RequestException as exc:
        outcome = "network_error"
        error = f"{type(exc).__name__}: {exc}"
        _finish()
        raise
    finally:
        session.close()
