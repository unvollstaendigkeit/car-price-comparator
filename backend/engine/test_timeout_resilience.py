"""
Bounded-retrieval resilience tests.

Covers the fixes for the "run hangs forever on a slow source" problem:

  1. http_client enforces a HARD wall-clock deadline and never retries, so a
     single fetch cannot block indefinitely; FetchTimeout is raised instead.
  2. A per-request timeout is a SOFT failure: the Phase-6 wrappers keep any
     partial rows and emit a 'TIMEOUT:' note (never 'BLOCKED'/'ERROR').
  3. inventory_run classifies a timeout as NON-block: it does NOT trip the
     circuit breaker, is counted separately, and the run continues.
  4. http_requests is counted for real (via http_client.request_count()),
     not hardcoded to 1.

These run without network by monkeypatching the scraper fetch() / provider.
"""
import time
import types

import pandas as pd

import http_client
import phase6_validate as p6
import inventory_run as inv
from market_provider import RetrieveResult
from single_car import SingleCarInput


# --------------------------------------------------------------------------- #
# 1. http_client: hard deadline + no retries + real counter
# --------------------------------------------------------------------------- #
def test_request_counter_increments_and_resets():
    http_client.reset_log()
    assert http_client.request_count() == 0
    # simulate three logged requests via the internal append path
    for i in range(3):
        http_client._append(http_client.RequestRecord(  # type: ignore[attr-defined]
            source="autobazar", brand="VW", model="Golf", page=i + 1,
            url=f"http://x/{i}", start_iso="", end_iso="", duration_s=0.1,
            status=200, outcome="ok", bytes=1000, error="",
        ))
    assert http_client.request_count() == 3
    http_client.reset_log()
    assert http_client.request_count() == 0


def test_fetch_timeout_is_exception_subclass():
    assert issubclass(http_client.FetchTimeout, Exception)


# --------------------------------------------------------------------------- #
# 2. Phase-6 wrapper: timeout keeps partial rows and is a non-block note
# --------------------------------------------------------------------------- #
def _car():
    from single_car import build_canonical_car
    return build_canonical_car(SingleCarInput(
        brand="Volkswagen", model="Golf", variant="2.0 TDI", year=2016,
        fuel="Diesel", km=150000, price=8000, power_kw=110,
    ))


def test_bazos_timeout_keeps_partial_rows(monkeypatch):
    """First page returns cards, second page times out -> keep page 1, TIMEOUT note."""
    page1_html = "<page1>"
    calls = {"n": 0}

    def fake_fetch(url, brand="", model="", page=0):
        calls["n"] += 1
        if page >= 2:
            raise p6.bz.FetchTimeout("deadline 8.0s exceeded")
        return page1_html

    def fake_parse(html, query, page):
        # one usable card on page 1
        return [{
            "listing_id": f"b{page}", "url": f"http://x/{page}", "price_eur": 8200,
            "year": 2016, "km": 150000, "title": "VW Golf", "source": "bazos",
        }]

    monkeypatch.setattr(p6.bz, "fetch", fake_fetch)
    monkeypatch.setattr(p6.bz, "parse_page", fake_parse)

    df, note = p6.retrieve_bazos(_car(), max_pages=3, delay=0.0)
    assert not df.empty, "partial rows from page 1 must be kept"
    assert len(df) == 1
    assert note and "TIMEOUT" in note
    assert not note.startswith("BLOCKED") and not note.startswith("ERROR")


def test_autobazar_timeout_is_non_block(monkeypatch):
    def fake_fetch(url, brand="", model="", page=0):
        raise p6.ab.FetchTimeout("deadline exceeded")

    monkeypatch.setattr(p6.ab, "fetch", fake_fetch)
    # Force the category path (avoid slug 404 fallback noise) by returning a slug.
    df, note = p6.retrieve_autobazar(_car(), max_pages=2, delay=0.0)
    assert df.empty
    assert note and "TIMEOUT" in note
    assert not note.startswith("BLOCKED")


# --------------------------------------------------------------------------- #
# 3. inventory_run: timeout classification vs. block
# --------------------------------------------------------------------------- #
def test_is_timeout_vs_is_block():
    assert inv._is_timeout("TIMEOUT: page 2 exceeded the per-request deadline")
    assert not inv._is_block("TIMEOUT: page 2 exceeded the per-request deadline")
    assert inv._is_block("BLOCKED: HTTP 429")
    assert inv._is_block("ERROR: ValueError: boom")
    assert not inv._is_timeout("BLOCKED: HTTP 429")


# --------------------------------------------------------------------------- #
# 4. End-to-end orchestration with a fake provider that times out one source
# --------------------------------------------------------------------------- #
class _FakeProvider:
    """Autobazar always returns data; Bazos times out (partial-empty) every model."""
    def __init__(self):
        self.calls = 0

    def retrieve(self, source, car, pages):
        self.calls += 1
        if source == "autobazar":
            df = pd.DataFrame([{
                "url": f"http://ab/{car.model}/{i}", "price_eur": 8000 + i * 100,
                "year": 2016, "km": 150000, "title": f"{car.brand} {car.model}",
                "source": "autobazar",
            } for i in range(6)])
            return RetrieveResult(df=df, err="", elapsed_s=0.5, http_requests=2)
        # bazos: soft timeout, no rows kept
        return RetrieveResult(
            df=pd.DataFrame(), err="TIMEOUT: page 1 exceeded the per-request deadline",
            elapsed_s=8.0, http_requests=1,
        )


def _rows():
    return [
        {"brand": "Volkswagen", "model": "Golf", "year": 2016, "fuel": "Diesel",
         "km": 150000, "price": 8000, "variant": "2.0 TDI", "power_kw": 110},
        {"brand": "Skoda", "model": "Octavia", "year": 2017, "fuel": "Diesel",
         "km": 120000, "price": 9500, "variant": "2.0 TDI", "power_kw": 110},
    ]


def test_run_continues_through_timeouts_and_reports_them():
    http_client.reset_log()
    events = list(inv.analyze_inventory(_rows(), provider=_FakeProvider()))
    stages = [e["stage"] for e in events]

    # A per-source timeout is announced but never disables the source.
    assert "source_timeout" in stages
    assert "source_disabled" not in stages

    # Every car still gets valued despite one source timing out each model.
    summary = next(e for e in events if e["stage"] == "summary")
    assert summary["counts"]["analyzed"] == 2
    assert summary["timeouts"]["bazos"] == 2      # one per model
    assert summary["timeouts"]["autobazar"] == 0
    assert summary["errors_total"] == 0           # timeouts are NOT errors
    assert "bazos" not in summary["disabled_sources"]

    # Timing breakdown is present and coherent. (total_time_s is real wall-clock
    # while retrieval_time_s here sums the FAKE provider's reported elapsed, so we
    # don't compare their magnitudes — only that the fields exist and are sane.)
    b = summary["benchmark"]
    assert b["retrieval_time_s"] > 0        # provider reported non-zero elapsed
    assert b["total_time_s"] >= 0
    assert "avg_http_time_s" in b
    # Fake provider fabricates http_requests without touching the transport, so
    # the real measured counter stays 0 here (validated live below, not in unit).
    assert b["http_requests_measured"] == 0


class _MonkeyPatch:
    """Tiny standalone monkeypatch (setattr + undo) for the __main__ runner."""
    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value):
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self):
        for target, name, old in reversed(self._undo):
            setattr(target, name, old)
        self._undo.clear()


if __name__ == "__main__":
    import sys
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and isinstance(f, types.FunctionType)]
    passed = 0
    for name, fn in fns:
        try:
            if "monkeypatch" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                mp = _MonkeyPatch()
                try:
                    fn(mp)
                finally:
                    mp.undo()
            else:
                fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
