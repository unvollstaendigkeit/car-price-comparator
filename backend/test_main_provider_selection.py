"""
Integration tests for /api/inventory/analyze/stream's provider selection
(main.py:_build_inventory_provider). Tests the plain selection function and
its constructed provider objects directly -- no fastapi TestClient, no HTTP,
no ASGI server. /api/compare and /api/compare/stream are not touched by this
selection mechanism at all and are not exercised here.

Run: python test_main_provider_selection.py
  or: python -m pytest test_main_provider_selection.py -q
"""
from __future__ import annotations

import os
import sys
import unittest

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

    def test_default_unset_env_selects_persistent_cache_provider(self):
        self.assertNotIn(_ENV_KEY, os.environ)
        provider = main._build_inventory_provider(self._payload())
        self.assertIsInstance(provider, PersistentCacheProvider)

    def test_explicit_market_collector_selects_new_provider(self):
        os.environ[_ENV_KEY] = "market_collector"
        provider = main._build_inventory_provider(self._payload())
        self.assertIsInstance(provider, MarketCollectorProvider)

    def test_unrecognized_value_keeps_the_default_not_a_crash(self):
        os.environ[_ENV_KEY] = "something_unexpected"
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


if __name__ == "__main__":
    unittest.main()
