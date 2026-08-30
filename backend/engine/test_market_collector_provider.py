"""
Focused, offline tests for market_provider.MarketCollectorProvider -- the new
DB-backed transport reading market-collector's historical SQLite snapshot
through its existing bridge. NOT the production default; these tests only
exercise the class directly.

Every test builds its own throwaway temp SQLite DB via market-collector's own
db.py + analysis DDL (never market_history.sqlite3) and points the provider
at it explicitly via MARKET_COLLECTOR_DB_PATH / the constructor's db_path arg.
No network access anywhere in this file.

Run: python test_market_collector_provider.py
  or: python -m pytest test_market_collector_provider.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

import pandas as pd

import http_client
import market_provider as mp
from phase6_validate import InvCar, adaptive_estimate
from inventory_run import analyze_inventory

MARKET_COLLECTOR_PATH = mp.MARKET_COLLECTOR_PATH
if MARKET_COLLECTOR_PATH not in sys.path:
    sys.path.insert(0, MARKET_COLLECTOR_PATH)
import db as mc_db  # noqa: E402  (market-collector's own db module)
from analysis.evaluate import DDL as ANALYSIS_DDL  # noqa: E402


class ProviderTestBase(unittest.TestCase):
    """Builds a fresh, throwaway market-collector-shaped SQLite DB per test."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.conn = mc_db.get_connection(self.db_path)
        mc_db.init_db(self.conn)
        self.conn.executescript(ANALYSIS_DDL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.remove(self.db_path)

    def _run(self, observed_at, run_scope="full", finished=True):
        run_id = mc_db.start_run(self.conn, observed_at, observed_at, target_count=1, run_scope=run_scope)
        if finished:
            mc_db.finish_run(self.conn, run_id, observed_at, 0, 0, "")
        self.conn.commit()
        return run_id

    def _insert_bazos_vehicle(self, **overrides):
        row = {
            "source": "bazos", "listing_id": "b1", "observed_at": "2026-01-01T00:00:00Z",
            "title": "Skoda Octavia 2.0 TDI", "year": 2020, "km": 80_000, "price": 10_000,
            "fuel": "Diesel", "transmission": "Manual", "engine": "2.0 TDI", "power": 110,
            "body_type": "Kombi", "listing_date": None, "url": "http://x/bz1",
            "search_query": "Skoda Octavia", "search_page": 1, "raw_description": None,
            "filter_reason": None, "year_source": None, "km_source": None, "fuel_source": None,
            "transmission_source": None, "engine_source": None, "power_source": None,
            "body_type_source": None, "raw_json": "{}",
        }
        row.update(overrides)
        mc_db.insert_bazos_rows(self.conn, [row])
        self.conn.commit()
        bazos_id = self.conn.execute("SELECT MAX(id) FROM bazos_listings").fetchone()[0]
        self.conn.execute(
            "INSERT INTO bazos_vehicle_classification (bazos_id, classification, reason) "
            "VALUES (?, 'vehicle', 'test fixture')",
            (bazos_id,),
        )
        self.conn.commit()
        return bazos_id

    def _insert_autobazar(self, **overrides):
        row = {
            "source": "autobazar", "listing_id": "a1", "observed_at": "2026-01-01T00:00:00Z",
            "title": "Skoda Octavia 2.0 TDI", "year": 2020, "km": 80_000, "price": 10_500,
            "fuel": "Diesel", "transmission": "Manual", "power": 110, "body_type": "Combi",
            "listing_date": None, "url": "http://x/ab1", "parse_mode": "next_data",
            "search_brand": "Skoda", "search_model": "Octavia", "search_page": 1, "raw_json": "{}",
        }
        row.update(overrides)
        mc_db.insert_autobazar_rows(self.conn, [row])
        self.conn.commit()

    def _car(self) -> InvCar:
        return InvCar(
            row_index=0, brand="Skoda", model="Octavia", variant_engine="2.0 TDI",
            fuel="Diesel", year=2020, km=80_000, price=10_200, power_kw=110,
            transmission="Manual", body_type="Kombi", variant_raw="2.0 TDI",
            power_source="structured",
        )


# --------------------------------------------------------------------------- #
# Latest FULL run selected, newer PARTIAL run ignored
# --------------------------------------------------------------------------- #
class TestRunSelection(ProviderTestBase):
    def test_selects_latest_full_run_ignoring_newer_partial(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._run("2026-08-16T00:00:00Z", run_scope="partial")
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        self.assertIsNotNone(provider.run)
        self.assertEqual(provider.run.observed_at, "2026-08-10T00:00:00Z")

    def test_partial_only_database_yields_documented_no_snapshot_result(self):
        self._run("2026-08-16T00:00:00Z", run_scope="partial")
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        self.assertIsNone(provider.run)

        res = provider.retrieve("bazos", self._car(), pages=1, deadline=None)
        self.assertTrue(res.df.empty)
        self.assertEqual(res.err, mp.NO_SNAPSHOT_ERR)
        self.assertEqual(res.http_requests, 0)


# --------------------------------------------------------------------------- #
# Run resolved once at construction, reused for every retrieve() call --
# including across a NEW full run appearing mid-"analysis".
# --------------------------------------------------------------------------- #
class TestRunPinnedAcrossRetrieveCalls(ProviderTestBase):
    def test_all_retrieve_calls_use_the_run_resolved_at_construction(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(observed_at="2026-08-10T00:00:00Z", price=10_000)
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        pinned_run_id = provider.run.id
        pinned_observed_at = provider.run.observed_at

        # A second, NEWER full run finishes -- simulating a collection that
        # completes mid-analysis, after this provider was already constructed.
        self._run("2026-08-16T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(
            listing_id="new-run-listing", observed_at="2026-08-16T00:00:00Z", price=99_000,
        )

        for _ in range(3):
            res = provider.retrieve("bazos", self._car(), pages=1, deadline=None)
            # provider.run itself never changes...
            self.assertEqual(provider.run.id, pinned_run_id)
            self.assertEqual(provider.run.observed_at, pinned_observed_at)
            # ...and the DATA returned is still only the pinned run's: the
            # newer run's 99_000 listing must never appear.
            self.assertEqual(len(res.df), 1)
            self.assertEqual(res.df.iloc[0]["price"], 10_000)


# --------------------------------------------------------------------------- #
# Bazos and Autobazar both come from the SAME selected snapshot
# --------------------------------------------------------------------------- #
class TestBothSourcesShareOneSnapshot(ProviderTestBase):
    def test_bazos_and_autobazar_both_use_the_same_run(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(observed_at="2026-08-10T00:00:00Z")
        self._insert_autobazar(observed_at="2026-08-10T00:00:00Z")
        # Older data for the same target must not leak in either.
        self._run("2026-08-05T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(
            listing_id="stale-bazos", observed_at="2026-08-05T00:00:00Z", price=1,
        )
        self._insert_autobazar(
            listing_id="stale-ab", observed_at="2026-08-05T00:00:00Z", price=1,
        )

        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        bz_res = provider.retrieve("bazos", self._car(), pages=1, deadline=None)
        ab_res = provider.retrieve("autobazar", self._car(), pages=1, deadline=None)

        self.assertEqual(len(bz_res.df), 1)
        self.assertEqual(len(ab_res.df), 1)
        self.assertEqual(bz_res.df.iloc[0]["listing_id"], "b1")
        self.assertEqual(ab_res.df.iloc[0]["listing_id"], "a1")


# --------------------------------------------------------------------------- #
# Bridge output reaches the existing (unmodified) valuation code correctly
# --------------------------------------------------------------------------- #
class TestFeedsValuation(ProviderTestBase):
    def test_retrieved_pool_is_accepted_by_adaptive_estimate(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        for i, price in enumerate([9_800, 10_200, 10_500, 10_900]):
            self._insert_bazos_vehicle(listing_id=f"b{i}", observed_at="2026-08-10T00:00:00Z", price=price)

        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        res = provider.retrieve("bazos", self._car(), pages=1, deadline=None)

        est, matched = adaptive_estimate(self._car(), res.df)
        self.assertEqual(est["comparable_count"], 4)
        self.assertFalse(est["insufficient_sample"])
        self.assertIsNotNone(est["estimated_market_price"])
        self.assertEqual(len(matched), 4)


# --------------------------------------------------------------------------- #
# RetrieveResult fields, pages/deadline handling, no HTTP
# --------------------------------------------------------------------------- #
class TestRetrieveResultShape(ProviderTestBase):
    def test_fields_populated_correctly_and_pages_deadline_ignored(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(observed_at="2026-08-10T00:00:00Z")
        provider = mp.MarketCollectorProvider(db_path=self.db_path)

        res_shallow = provider.retrieve("bazos", self._car(), pages=1, deadline=None)
        res_deep = provider.retrieve("bazos", self._car(), pages=99, deadline=123.0)

        for res in (res_shallow, res_deep):
            self.assertEqual(res.err, "")
            self.assertEqual(res.http_requests, 0)
            self.assertTrue(res.from_cache)
            self.assertIsInstance(res.age_s, float)
            self.assertGreaterEqual(res.age_s, 0.0)
            self.assertGreaterEqual(res.elapsed_s, 0.0)

        # pages/deadline have no effect on a DB read: identical results either way.
        pd.testing.assert_frame_equal(res_shallow.df, res_deep.df)

    def test_peek_reports_fixed_snapshot_status(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        status = provider.peek("bazos", self._car(), pages=1)
        self.assertEqual(status["from_cache"], True)
        self.assertGreaterEqual(status["age_s"], 0.0)

    def test_peek_still_reports_cache_status_with_no_fallback_snapshot(self):
        """2026-08-24: peek() no longer returns None just because no
        collection_runs fallback snapshot exists -- the primary incremental
        path can still serve real data either way, and peek() never queries
        per-target to predict that, so it stays unconditionally cache-shaped."""
        provider = mp.MarketCollectorProvider(db_path=self.db_path)  # empty DB, no runs at all
        self.assertIsNone(provider.run)
        status = provider.peek("bazos", self._car(), pages=1)
        self.assertEqual(status, {"from_cache": True, "age_s": None})

    def test_no_network_requests_occur(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(observed_at="2026-08-10T00:00:00Z")
        provider = mp.MarketCollectorProvider(db_path=self.db_path)

        before = http_client.request_count()
        provider.retrieve("bazos", self._car(), pages=1, deadline=None)
        provider.retrieve("autobazar", self._car(), pages=3, deadline=None)
        after = http_client.request_count()
        self.assertEqual(before, after)


# --------------------------------------------------------------------------- #
# "VW" -> "Volkswagen" brand mapping (see MarketCollectorProvider docstring
# for the investigation: exactly one evidence-based mapping, not a general
# alias system).
# --------------------------------------------------------------------------- #
class TestVwBrandMapping(ProviderTestBase):
    def _vw_car(self, brand="VW") -> InvCar:
        return InvCar(
            row_index=0, brand=brand, model="Golf", variant_engine=None, fuel="Petrol",
            year=2019, km=70_000, price=15_000, power_kw=110, transmission="Manual",
            body_type="Hatchback", variant_raw=None, power_source="missing",
        )

    def test_vw_branded_car_matches_volkswagen_collected_data(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(
            observed_at="2026-08-10T00:00:00Z", title="Volkswagen Golf 1.5 TSI",
            search_query="Volkswagen Golf", price=14_500,
        )
        provider = mp.MarketCollectorProvider(db_path=self.db_path)

        res = provider.retrieve("bazos", self._vw_car(brand="VW"), pages=1, deadline=None)
        self.assertEqual(len(res.df), 1)
        self.assertEqual(res.df.iloc[0]["price"], 14_500)

    def test_volkswagen_branded_car_still_works_unmapped(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(
            observed_at="2026-08-10T00:00:00Z", search_query="Volkswagen Golf", price=14_500,
        )
        provider = mp.MarketCollectorProvider(db_path=self.db_path)

        res = provider.retrieve("bazos", self._vw_car(brand="Volkswagen"), pages=1, deadline=None)
        self.assertEqual(len(res.df), 1)

    def test_unmapped_brand_mismatch_yields_empty_pool_not_a_crash(self):
        """Fiat has no market-collector data under ANY spelling -- must stay a
        documented empty result, not something this override tries to fix."""
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        car = InvCar(row_index=0, brand="Fiat", model="Panda", variant_engine=None,
                     fuel="Petrol", year=2018, km=60_000, price=8_000, power_kw=51,
                     transmission="Manual", body_type="Hatchback", variant_raw=None,
                     power_source="missing")
        res = provider.retrieve("bazos", car, pages=1, deadline=None)
        self.assertEqual(res.err, "")
        self.assertTrue(res.df.empty)


# --------------------------------------------------------------------------- #
# Full analyze_inventory() integration: the provider as actually used by the
# inventory pipeline, not called in isolation.
# --------------------------------------------------------------------------- #
def _inv_row(row_index, brand, model, **overrides):
    row = {
        "row_index": row_index, "row_number": row_index + 1,
        "brand": brand, "model": model, "variant": None, "year": 2020,
        "fuel": "Diesel", "km": 80_000, "price": 10_000, "body_type": "Kombi",
    }
    row.update(overrides)
    return row


class TestFullInventoryAnalysisIntegration(ProviderTestBase):
    def test_pinned_snapshot_used_across_multiple_models_including_vw_mapping(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        for i, price in enumerate([9_800, 10_200, 10_500, 10_900]):
            self._insert_bazos_vehicle(
                listing_id=f"oct{i}", observed_at="2026-08-10T00:00:00Z",
                search_query="Skoda Octavia", price=price,
            )
        for i, price in enumerate([14_000, 14_500, 15_000, 15_500]):
            self._insert_bazos_vehicle(
                listing_id=f"golf{i}", observed_at="2026-08-10T00:00:00Z",
                title="Volkswagen Golf 1.5 TSI", search_query="Volkswagen Golf",
                price=price, fuel="Petrol",  # matches the car's fuel="Petrol" below
            )

        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        rows = [
            _inv_row(0, "Skoda", "Octavia", price=10_000),
            _inv_row(1, "VW", "Golf", price=14_000, fuel="Petrol"),
        ]

        events = list(analyze_inventory(rows, provider=provider))
        car_events = [e for e in events if e["stage"] == "car_done"]
        self.assertEqual(len(car_events), 2)

        by_model = {c["car"]["model"]: c["car"] for c in car_events}
        self.assertGreater(by_model["Octavia"]["bazos"]["comparable_count"], 0)
        self.assertGreater(by_model["Golf"]["bazos"]["comparable_count"], 0)
        self.assertIsNone(by_model["Golf"]["bazos"]["error"])

    def test_newer_full_run_appearing_mid_analysis_does_not_change_results(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(
            observed_at="2026-08-10T00:00:00Z", search_query="Skoda Octavia", price=10_000,
        )
        for i in range(1, 4):  # reach MIN_USABLE so the tier isn't "insufficient"
            self._insert_bazos_vehicle(
                listing_id=f"oct{i}", observed_at="2026-08-10T00:00:00Z",
                search_query="Skoda Octavia", price=10_000 + i * 100,
            )
        provider = mp.MarketCollectorProvider(db_path=self.db_path)  # pins the run NOW

        # A newer FULL run finishes only after the provider (and thus the
        # pinned snapshot) already exists -- simulating a collection landing
        # mid-analysis.
        self._run("2026-08-16T00:00:00Z", run_scope="full")
        for i in range(4):
            self._insert_bazos_vehicle(
                listing_id=f"newrun{i}", observed_at="2026-08-16T00:00:00Z",
                search_query="Skoda Octavia", price=99_000,
            )

        rows = [_inv_row(0, "Skoda", "Octavia", price=10_000)]
        events = list(analyze_inventory(rows, provider=provider))
        car = [e for e in events if e["stage"] == "car_done"][0]["car"]

        self.assertLess(car["bazos"]["median_eur"], 15_000)  # nowhere near the newer run's 99_000

    def test_partial_only_database_produces_documented_empty_result_not_a_crash(self):
        self._run("2026-08-16T00:00:00Z", run_scope="partial")
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        self.assertIsNone(provider.run)

        rows = [_inv_row(0, "Skoda", "Octavia")]
        events = list(analyze_inventory(rows, provider=provider))
        car = [e for e in events if e["stage"] == "car_done"][0]["car"]
        self.assertEqual(car["bazos"]["comparable_count"], 0)
        self.assertEqual(car["bazos"]["error"], mp.NO_SNAPSHOT_ERR)
        self.assertEqual(car["confidence_flag"], "INSUFFICIENT")

    def test_no_http_requests_across_a_full_analysis(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(observed_at="2026-08-10T00:00:00Z", search_query="Skoda Octavia")
        self._insert_autobazar(observed_at="2026-08-10T00:00:00Z")
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        rows = [
            _inv_row(0, "Skoda", "Octavia"),
            _inv_row(1, "VW", "Golf"),
        ]

        before = http_client.request_count()
        list(analyze_inventory(rows, provider=provider))
        after = http_client.request_count()
        self.assertEqual(before, after)


# --------------------------------------------------------------------------- #
# Incremental pipeline path (2026-08-24) -- discover.py/collect_daily.py's
# rolling ledger, read with NO collection_runs/resolve_collection_run
# involved. ProviderTestBase's existing fixtures (_insert_bazos_vehicle /
# _insert_autobazar) deliberately do NOT set up discovered_listings or
# parse_mode='sitemap_detail' -- they represent the OLD collect.py shape,
# which is exactly why every test above still passes unchanged: the new
# incremental query correctly finds nothing for them and falls through to
# the existing fallback path. These tests build the INCREMENTAL shape
# explicitly instead.
# --------------------------------------------------------------------------- #
class IncrementalProviderTestBase(ProviderTestBase):
    def _insert_discovered(self, source, listing_id, disappeared_at=None):
        self.conn.execute(
            "INSERT INTO discovered_listings "
            "(source, listing_id, url, sitemap_lastmod, first_discovered_at, "
            " last_discovered_at, fields_captured_at, disappeared_at) "
            "VALUES (?, ?, ?, NULL, '2026-08-24T00:00:00+00:00', "
            " '2026-08-24T00:00:00+00:00', '2026-08-24T00:00:00+00:00', ?)",
            (source, listing_id, f"http://x/{listing_id}", disappeared_at),
        )
        self.conn.commit()

    def _insert_incremental_bazos(self, listing_id="b1", disappeared_at=None, **overrides):
        self._insert_bazos_vehicle(listing_id=listing_id, search_query="(sitemap_discovery)", **overrides)
        self._insert_discovered("bazos", listing_id, disappeared_at=disappeared_at)

    def _insert_incremental_autobazar(self, **overrides):
        overrides.setdefault("parse_mode", "sitemap_detail")
        # Real sitemap_detail rows carry search_brand from Autobazar's OWN
        # advertised-brand field, which spells this test's default car
        # ("Skoda") WITH a diacritic ("Škoda") -- see market_provider.py's
        # _INCREMENTAL_BRAND_OVERRIDES docstring. Snapshot/next_data rows
        # (_insert_autobazar's own default) correctly stay unaccented, since
        # those come from market-collector's own unaccented search targets.
        overrides.setdefault("search_brand", "Škoda")
        self._insert_autobazar(**overrides)


class TestIncrementalPathPreferredWhenPresent(IncrementalProviderTestBase):
    def test_incremental_bazos_used_even_with_no_fallback_snapshot_at_all(self):
        """No collection_runs row exists anywhere in this DB (provider.run
        is None) -- the incremental hit alone must still be enough."""
        self._insert_incremental_bazos(price=10_000)
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        self.assertIsNone(provider.run)

        res = provider.retrieve("bazos", self._car(), pages=1, deadline=None)
        self.assertFalse(res.df.empty)
        self.assertEqual(res.err, "")
        self.assertEqual(res.df.iloc[0]["price"], 10_000)
        self.assertIsNone(res.age_s)  # no single age for a many-timestamp incremental pool

    def test_incremental_autobazar_used_even_with_no_fallback_snapshot(self):
        self._insert_incremental_autobazar(price=11_000)
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        res = provider.retrieve("autobazar", self._car(), pages=1, deadline=None)
        self.assertFalse(res.df.empty)
        self.assertEqual(res.df.iloc[0]["price"], 11_000)

    def test_incremental_preferred_over_fallback_when_both_have_data(self):
        """Both an old-style snapshot AND fresh incremental data exist for
        the same target -- the incremental (actively-maintained) result
        must win, not the older snapshot."""
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(observed_at="2026-08-10T00:00:00Z", price=99_000)  # old snapshot
        self._insert_incremental_bazos(price=10_000)  # fresh incremental

        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        res = provider.retrieve("bazos", self._car(), pages=1, deadline=None)
        self.assertEqual(res.df.iloc[0]["price"], 10_000)  # incremental wins

    def test_falls_back_to_snapshot_when_incremental_empty_for_this_target(self):
        """Incremental data exists in the DB, but not for THIS brand/model --
        the fallback snapshot must still be tried and used."""
        self._insert_incremental_bazos(title="BMW X5 3.0d")
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_bazos_vehicle(observed_at="2026-08-10T00:00:00Z", price=15_000)  # Skoda Octavia

        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        res = provider.retrieve("bazos", self._car(), pages=1, deadline=None)  # car() is Skoda Octavia
        self.assertFalse(res.df.empty)
        self.assertEqual(res.df.iloc[0]["price"], 15_000)  # the fallback row, not the BMW


class TestIncrementalDisappearedAtExclusion(IncrementalProviderTestBase):
    def test_disappeared_bazos_listing_excluded_from_incremental_pool(self):
        self._insert_incremental_bazos(disappeared_at="2026-08-23T00:00:00+00:00")
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        res = provider.retrieve("bazos", self._car(), pages=1, deadline=None)
        self.assertTrue(res.df.empty)  # vanished -- must not be offered as a comparable

    def test_still_present_bazos_listing_included(self):
        self._insert_incremental_bazos(disappeared_at=None)
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        res = provider.retrieve("bazos", self._car(), pages=1, deadline=None)
        self.assertFalse(res.df.empty)


class TestIncrementalAutobazarParseModeFiltering(IncrementalProviderTestBase):
    def test_old_search_based_autobazar_rows_excluded_from_incremental_query(self):
        """A row with the OLD collect_autobazar parse_mode (next_data) must
        never be picked up by the incremental path, even with no
        collection_runs snapshot to fall back to -- it should surface via
        the fallback path instead, exercised in the next test."""
        self._insert_autobazar(parse_mode="next_data", price=20_000)
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        res = provider.retrieve("autobazar", self._car(), pages=1, deadline=None)
        self.assertTrue(res.df.empty)
        self.assertEqual(res.err, mp.NO_SNAPSHOT_ERR)  # no fallback snapshot exists either

    def test_old_row_still_reachable_via_fallback_when_a_snapshot_exists(self):
        self._run("2026-08-10T00:00:00Z", run_scope="full")
        self._insert_autobazar(observed_at="2026-08-10T00:00:00Z", parse_mode="next_data", price=20_000)
        provider = mp.MarketCollectorProvider(db_path=self.db_path)
        res = provider.retrieve("autobazar", self._car(), pages=1, deadline=None)
        self.assertFalse(res.df.empty)
        self.assertEqual(res.df.iloc[0]["price"], 20_000)


if __name__ == "__main__":
    unittest.main()
