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
import socket
import threading
import time
import types

import pandas as pd

import http_client
import phase6_validate as p6
import inventory_run as inv
from market_provider import RetrieveResult
from single_car import SingleCarInput


# --------------------------------------------------------------------------- #
# 0. THE regression: a server that accepts but never replies (the real CX-5
#    hang). Proves the HARD wall-clock cap bounds the whole request, including
#    the connect/header phase that requests' (connect, read) tuple can't bound.
# --------------------------------------------------------------------------- #
class _SilentServer:
    """Accepts TCP connections and then holds them open forever, sending nothing
    (mimics a server that stalls during the response-header phase)."""
    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._held: list[socket.socket] = []
        self._stop = False
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        while not self._stop:
            try:
                self._sock.settimeout(0.2)
                conn, _ = self._sock.accept()
                self._held.append(conn)  # hold open, never write a response
            except (socket.timeout, OSError):
                continue

    def close(self):
        self._stop = True
        for c in self._held:
            try:
                c.close()
            except OSError:
                pass
        try:
            self._sock.close()
        except OSError:
            pass


def test_hard_cap_bounds_a_silent_server():
    """A GET against a server that never responds must return (raise
    FetchTimeout) within ~total_deadline_s — NOT hang until read_timeout stacks
    up. This is the exact failure that stalled the run on one model."""
    server = _SilentServer()
    http_client.reset_log()
    try:
        url = f"http://127.0.0.1:{server.port}/never-answers"
        t0 = time.perf_counter()
        raised = False
        try:
            http_client.get_bounded(
                url, {"User-Agent": "test"},
                source="autobazar", brand="VW", model="Golf", page=1,
                total_deadline_s=1.5,
            )
        except http_client.FetchTimeout:
            raised = True
        elapsed = time.perf_counter() - t0
        assert raised, "a silent server must surface as FetchTimeout"
        # Returned close to the cap, not after read_timeout gaps stacked up.
        assert elapsed < 3.0, f"hard cap must bound the call, took {elapsed:.2f}s"
        # Recorded EXACTLY once despite the watchdog/worker race.
        assert http_client.request_count() == 1
    finally:
        server.close()


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

    def fake_fetch(url, brand="", model="", page=0, deadline=None):
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
    def fake_fetch(url, brand="", model="", page=0, deadline=None):
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

    def retrieve(self, source, car, pages, deadline=None):
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


# --------------------------------------------------------------------------- #
# 5. Per-model TIME BUDGET: one pathological model cannot block the whole run
# --------------------------------------------------------------------------- #
def test_wrapper_skips_network_when_budget_already_spent(monkeypatch):
    """A deadline already in the past -> the page loop makes ZERO network calls
    and returns a non-block budget-timeout note. This is what stops a model from
    stacking more sequential GETs once its budget is gone."""
    called = {"n": 0}

    def fake_fetch(url, brand="", model="", page=0, deadline=None):
        called["n"] += 1
        return "<html>"

    monkeypatch.setattr(p6.bz, "fetch", fake_fetch)
    past = time.perf_counter() - 1.0  # budget already exhausted
    df, note = p6.retrieve_bazos(_car(), max_pages=3, delay=0.0, deadline=past)
    assert called["n"] == 0, "no page GET may happen once the budget is spent"
    assert df.empty
    assert note and "TIMEOUT" in note and "budget" in note.lower()


class _UncooperativeProvider:
    """A provider that IGNORES the deadline entirely and blocks far longer than
    the budget on the first (Golf) fetch — simulating a lower layer that fails to
    honor the cooperative deadline. The AUTHORITATIVE model-level watchdog must
    still abandon it and let the run continue. This is the exact class of bug the
    user hit (a request running its full timeout past the budget)."""
    def __init__(self, block_s=30.0):
        self.block_s = block_s
        self.calls: list[tuple[str, str]] = []

    def retrieve(self, source, car, pages, deadline=None):
        self.calls.append((car.model.lower(), source))
        if car.model.lower() == "golf" and source == "autobazar":
            time.sleep(self.block_s)  # deliberately ignores `deadline`
        return RetrieveResult(
            df=pd.DataFrame([{
                "url": f"http://{source}/{car.model}", "price_eur": 8000,
                "year": 2016, "km": 150000, "title": f"{car.brand} {car.model}",
                "source": source,
            }]), err="", elapsed_s=0.0, http_requests=1,
        )


def test_watchdog_abandons_uncooperative_retrieve():
    """Even if provider.retrieve blocks 30s ignoring a 0.5s budget, the model is
    abandoned within budget+grace and the whole run stays bounded and completes."""
    prov = _UncooperativeProvider(block_s=30.0)
    t0 = time.perf_counter()
    events = list(inv.analyze_inventory(_rows(), provider=prov, model_budget_s=0.5))
    wall = time.perf_counter() - t0

    # The uncooperative model was abandoned (a group_timeout fired for Golf)...
    gts = [e for e in events if e["stage"] == "group_timeout"]
    assert any(e["model"].lower() == "golf" for e in gts), "Golf must be abandoned"
    # ...the run completed and valued every car anyway...
    summary = next(e for e in events if e["stage"] == "summary")
    assert summary["counts"]["analyzed"] == 2
    # ...and crucially it did NOT block for anywhere near the 30s the layer wanted.
    # budget 0.5s + a small grace per abandoned fetch, nowhere near 30s.
    assert wall < 10.0, f"watchdog must bound the run, took {wall:.2f}s"


class _BudgetProvider:
    """Simulates ONE pathological slow model (Golf) whose first fetch overruns
    the tiny per-model budget, while every other model is instant. Proves the
    budget is per-model (fresh each group) and that the slow model is abandoned
    without stalling the rest of the run."""
    def __init__(self, slow_model="golf", slow_sleep=0.5):
        self.slow_model = slow_model
        self.slow_sleep = slow_sleep
        self.calls: list[tuple[str, str]] = []

    def retrieve(self, source, car, pages, deadline=None):
        self.calls.append((car.model.lower(), source))
        if car.model.lower() == self.slow_model and source == "autobazar":
            time.sleep(self.slow_sleep)  # push wall-clock past the budget
        df = pd.DataFrame([{
            "url": f"http://{source}/{car.model}/{i}", "price_eur": 8000 + i * 100,
            "year": 2016, "km": 150000, "title": f"{car.brand} {car.model}",
            "source": source,
        } for i in range(6)])
        return RetrieveResult(df=df, err="", elapsed_s=0.1, http_requests=2)


def test_model_over_budget_does_not_block_the_run():
    prov = _BudgetProvider(slow_model="golf", slow_sleep=0.5)
    t0 = time.perf_counter()
    events = list(inv.analyze_inventory(_rows(), provider=prov, model_budget_s=0.3))
    wall = time.perf_counter() - t0
    stages = [e["stage"] for e in events]

    # The slow model tripped its whole-model budget exactly once.
    gts = [e for e in events if e["stage"] == "group_timeout"]
    assert len(gts) == 1, f"expected 1 group_timeout, got {len(gts)}"
    assert gts[0]["model"].lower() == "golf"

    # Once the budget was spent, the second source was SKIPPED (never called).
    assert ("golf", "bazos") not in prov.calls, "bazos must be skipped after budget spent"
    # The fast model was fully retrieved from BOTH sources.
    assert ("octavia", "autobazar") in prov.calls
    assert ("octavia", "bazos") in prov.calls

    # Crucially: every car is still valued and the run completes.
    summary = next(e for e in events if e["stage"] == "summary")
    assert summary["counts"]["analyzed"] == 2
    assert summary["model_timeouts"] == 1
    assert summary["disabled_sources"] == []      # a budget overrun is not a block

    # The whole run stayed bounded (~slow_sleep), nowhere near a hang.
    assert wall < 5.0, f"run should stay bounded, took {wall:.2f}s"


def test_each_model_gets_a_fresh_budget():
    """A slow first model must not eat into the second model's budget."""
    prov = _BudgetProvider(slow_model="golf", slow_sleep=0.5)
    events = list(inv.analyze_inventory(_rows(), provider=prov, model_budget_s=0.3))
    # Octavia (fast) completed with no timeout note on either source.
    retr = [e for e in events if e["stage"] == "retrieved" and e["model"].lower() == "octavia"]
    assert retr and not retr[0]["ab_timed_out"] and not retr[0]["bz_timed_out"]


# --------------------------------------------------------------------------- #
# 6. OVERALL run budget: protect the serverless function limit. Stop starting
#    new LIVE models before the platform kill so a summary always arrives.
# --------------------------------------------------------------------------- #
def test_overall_run_budget_truncates_cleanly():
    """With a run budget that only fits one live model, the second is skipped
    but the run still emits a clean summary flagged run_truncated — never a hang
    or a mid-stream kill. This is what keeps the SSE stream inside maxDuration."""
    prov = _BudgetProvider(slow_model="none")  # both models fast + live
    # model_budget 5s, run_budget 5s: after the first model, now+5 > deadline so
    # the second live model is not started.
    events = list(inv.analyze_inventory(
        _rows(), provider=prov, model_budget_s=5.0, run_budget_s=5.0,
    ))
    stages = [e["stage"] for e in events]

    # Exactly one run_truncated event, and it accounts for the skipped model.
    truncs = [e for e in events if e["stage"] == "run_truncated"]
    assert len(truncs) == 1, f"expected 1 run_truncated, got {len(truncs)}"
    assert truncs[0]["analyzed_groups"] == 1
    assert truncs[0]["skipped_groups"] == 1
    assert truncs[0]["total_groups"] == 2

    # A summary STILL arrives (stream closes cleanly) and is flagged truncated.
    summary = next(e for e in events if e["stage"] == "summary")
    assert summary["run_truncated"] is True
    # Only the first model's cars were analyzed; the run did not error.
    assert summary["counts"]["analyzed"] == 1
    assert "error" not in stages


def test_cached_models_run_even_when_run_budget_is_spent():
    """The run-budget early stop must apply only to LIVE models; cached models
    cost no network time and must always be allowed to finish."""
    class _AllCached:
        def peek(self, source, car, pages):
            # Advisory: tells the run this source is served from cache (no fetch),
            # which is what keeps the run-budget early-stop from truncating it.
            return {"from_cache": True, "age_s": 10.0}

        def retrieve(self, source, car, pages, deadline=None):
            df = pd.DataFrame([{
                "url": f"http://{source}/{car.model}", "price_eur": 8000,
                "year": 2016, "km": 150000, "title": f"{car.brand} {car.model}",
                "source": source,
            }])
            return RetrieveResult(df=df, err="", elapsed_s=0.0, http_requests=0,
                                  from_cache=True, age_s=10.0)

    # Even with a zero run budget, cached models are not truncated.
    events = list(inv.analyze_inventory(
        _rows(), provider=_AllCached(), model_budget_s=5.0, run_budget_s=0.0,
    ))
    assert not any(e["stage"] == "run_truncated" for e in events)
    summary = next(e for e in events if e["stage"] == "summary")
    assert summary["counts"]["analyzed"] == 2
    assert summary["run_truncated"] is False


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
