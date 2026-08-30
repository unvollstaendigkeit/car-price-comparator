"""
Integration tests for main.py's provider selection: _build_inventory_provider
(/api/inventory/analyze/stream) AND _build_compare_provider (/api/compare,
/api/compare/stream, 2026-08-30) -- both are thin wrappers around the same
shared _build_market_provider, exercised directly here rather than through
each endpoint. No fastapi TestClient, no HTTP, no ASGI server.

Run: python test_main_provider_selection.py
  or: python -m pytest test_main_provider_selection.py -q
"""
from __future__ import annotations

import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine"))

import main  # noqa: E402
from market_provider import PersistentCacheProvider, MarketCollectorProvider  # noqa: E402

_ENV_KEY = "INVENTORY_MARKET_PROVIDER"


class TestInventoryProviderSelection(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(_ENV_KEY, None)

    def tearDown(self):
        os.environ.pop(_ENV_KEY, None)
        if self._saved is not None:
            os.environ[_ENV_KEY] = self._saved

    def _payload(self):
        return main.InventoryAnalyzePayload(rows=[])

    def test_default_unset_env_now_selects_market_collector_provider(self):
        # 2026-08-24: MarketCollectorProvider became the default -- this
        # assertion is the deliberate behavior change, not a regression.
        self.assertNotIn(_ENV_KEY, os.environ)
        provider = main._build_inventory_provider(self._payload())
        self.assertIsInstance(provider, MarketCollectorProvider)

    def test_explicit_market_collector_selects_new_provider(self):
        os.environ[_ENV_KEY] = "market_collector"
        provider = main._build_inventory_provider(self._payload())
        self.assertIsInstance(provider, MarketCollectorProvider)

    def test_unrecognized_value_keeps_the_default_not_a_crash(self):
        # "the default" is now MarketCollectorProvider -- an unrecognized
        # env value must not be misread as the "legacy" escape hatch.
        os.environ[_ENV_KEY] = "something_unexpected"
        provider = main._build_inventory_provider(self._payload())
        self.assertIsInstance(provider, MarketCollectorProvider)

    def test_legacy_escape_hatch_forces_persistent_cache_provider(self):
        os.environ[_ENV_KEY] = "legacy"
        provider = main._build_inventory_provider(self._payload())
        self.assertIsInstance(provider, PersistentCacheProvider)

    def test_refresh_true_forces_persistent_cache_provider_regardless_of_env(self):
        # MarketCollectorProvider has no live-fetch capability at all -- an
        # explicit refresh request must always get the live provider, even
        # with no env override set.
        self.assertNotIn(_ENV_KEY, os.environ)
        payload = main.InventoryAnalyzePayload(rows=[], refresh=True)
        provider = main._build_inventory_provider(payload)
        self.assertIsInstance(provider, PersistentCacheProvider)

    def test_market_collector_construction_failure_falls_back_gracefully(self):
        # Simulates market-collector simply not being present on this
        # machine/deployment -- the endpoint must degrade to the original
        # provider, not crash.
        self.assertNotIn(_ENV_KEY, os.environ)
        with unittest.mock.patch(
            "main.MarketCollectorProvider", side_effect=RuntimeError("boom"),
        ):
            provider = main._build_inventory_provider(self._payload())
        self.assertIsInstance(provider, PersistentCacheProvider)

    def test_market_collector_provider_supports_the_unconditional_flush_call(self):
        # main.py's endpoint always calls provider.flush() in a `finally`
        # block regardless of which provider was selected (PersistentCacheProvider
        # already has one; this confirms the new provider's no-op flush() means
        # that call site doesn't need a type check to support both).
        os.environ[_ENV_KEY] = "market_collector"
        provider = main._build_inventory_provider(self._payload())
        provider.flush()  # must not raise


class TestCompareProviderSelection(unittest.TestCase):
    """Mirrors TestInventoryProviderSelection above, for
    _build_compare_provider (2026-08-30) -- separate env var
    (COMPARE_MARKET_PROVIDER), independent of INVENTORY_MARKET_PROVIDER,
    so each endpoint's legacy escape hatch can be toggled without
    affecting the other."""

    _COMPARE_ENV_KEY = "COMPARE_MARKET_PROVIDER"

    def setUp(self):
        self._saved_inventory = os.environ.pop(_ENV_KEY, None)
        self._saved_compare = os.environ.pop(self._COMPARE_ENV_KEY, None)

    def tearDown(self):
        os.environ.pop(_ENV_KEY, None)
        os.environ.pop(self._COMPARE_ENV_KEY, None)
        if self._saved_inventory is not None:
            os.environ[_ENV_KEY] = self._saved_inventory
        if self._saved_compare is not None:
            os.environ[self._COMPARE_ENV_KEY] = self._saved_compare

    def _payload(self, **overrides):
        return main.CarPayload(brand="Skoda", model="Octavia", **overrides)

    def test_default_unset_env_selects_market_collector_provider(self):
        self.assertNotIn(self._COMPARE_ENV_KEY, os.environ)
        provider = main._build_compare_provider(self._payload())
        self.assertIsInstance(provider, MarketCollectorProvider)

    def test_unrecognized_value_keeps_the_default_not_a_crash(self):
        os.environ[self._COMPARE_ENV_KEY] = "something_unexpected"
        provider = main._build_compare_provider(self._payload())
        self.assertIsInstance(provider, MarketCollectorProvider)

    def test_legacy_escape_hatch_forces_persistent_cache_provider(self):
        os.environ[self._COMPARE_ENV_KEY] = "legacy"
        provider = main._build_compare_provider(self._payload())
        self.assertIsInstance(provider, PersistentCacheProvider)

    def test_refresh_true_forces_persistent_cache_provider_regardless_of_env(self):
        self.assertNotIn(self._COMPARE_ENV_KEY, os.environ)
        payload = self._payload(refresh=True)
        provider = main._build_compare_provider(payload)
        self.assertIsInstance(provider, PersistentCacheProvider)

    def test_market_collector_construction_failure_falls_back_gracefully(self):
        self.assertNotIn(self._COMPARE_ENV_KEY, os.environ)
        with unittest.mock.patch(
            "main.MarketCollectorProvider", side_effect=RuntimeError("boom"),
        ):
            provider = main._build_compare_provider(self._payload())
        self.assertIsInstance(provider, PersistentCacheProvider)

    def test_inventory_env_var_does_not_affect_compare_selection(self):
        """The two endpoints' legacy escape hatches must be independent --
        forcing legacy for inventory must not force it for compare."""
        os.environ[_ENV_KEY] = "legacy"
        self.assertNotIn(self._COMPARE_ENV_KEY, os.environ)
        provider = main._build_compare_provider(self._payload())
        self.assertIsInstance(provider, MarketCollectorProvider)

    def test_compare_env_var_does_not_affect_inventory_selection(self):
        os.environ[self._COMPARE_ENV_KEY] = "legacy"
        self.assertNotIn(_ENV_KEY, os.environ)
        provider = main._build_inventory_provider(main.InventoryAnalyzePayload(rows=[]))
        self.assertIsInstance(provider, MarketCollectorProvider)


class TestCompareSingleCarProviderSeam(unittest.TestCase):
    """compare_single_car's new `provider=` parameter (2026-08-30) -- the
    engine-level seam _build_compare_provider's result is actually passed
    through. `retrieved=` still takes priority when both are given
    (existing behavior, unchanged) and no-args-at-all still falls through
    to live retrieval (also unchanged) -- both asserted here so a future
    edit can't quietly break either existing path while adding a third."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine"))
        import single_car  # noqa: E402
        self.single_car = single_car

    def test_provider_retrieve_is_called_for_both_sources(self):
        import pandas as pd

        class _FakeResult:
            def __init__(self, df, err=""):
                self.df = df
                self.err = err

        class _FakeProvider:
            def __init__(self):
                self.calls = []

            def retrieve(self, source, car, pages, deadline=None):
                self.calls.append(source)
                return _FakeResult(pd.DataFrame())

        provider = _FakeProvider()
        inp = self.single_car.SingleCarInput(brand="Skoda", model="Octavia")
        self.single_car.compare_single_car(inp, provider=provider)
        self.assertEqual(sorted(provider.calls), ["autobazar", "bazos"])

    def test_retrieved_takes_priority_over_provider_when_both_given(self):
        import pandas as pd

        class _ExplodingProvider:
            def retrieve(self, source, car, pages, deadline=None):
                raise AssertionError("provider must not be used when retrieved= is given")

        inp = self.single_car.SingleCarInput(brand="Skoda", model="Octavia")
        # Must not raise -- proves the exploding provider was never touched.
        self.single_car.compare_single_car(
            inp, retrieved=(pd.DataFrame(), "", pd.DataFrame(), ""),
            provider=_ExplodingProvider(),
        )


if __name__ == "__main__":
    unittest.main()
