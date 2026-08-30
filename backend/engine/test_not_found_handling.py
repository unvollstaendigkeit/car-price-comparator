"""
2026-08-24 fix: HTTP 404/410 on a detail-page fetch was previously funneled
into the generic `status != 200` -> BlockedError fallback in both
autobazar_scraper.fetch() and bazos_scraper.fetch(), indistinguishable from
an actual block/rate-limit. Detail-page adapters in the sibling
market-collector project caught BlockedError as a transient failure and left
the item in the capture backlog to retry forever, so a genuinely removed
listing could never drain from the queue.

Covers:
  1. fetch() now raises the more specific NotFoundError for 404/410.
  2. NotFoundError IS a BlockedError subclass, so every existing
     `except BlockedError` call site (search-page fetches) still catches it
     and behaves exactly as before -- no call-site regression.
  3. A genuine block (401/403/429/5xx) still raises plain BlockedError, not
     NotFoundError.

Runs without network by monkeypatching http_client.get_bounded.
"""
import unittest
from unittest.mock import patch

import autobazar_scraper as ab
import bazos_scraper as bz


class TestAutobazarFetchNotFound(unittest.TestCase):
    @patch("autobazar_scraper.http_client.get_bounded")
    def test_404_raises_not_found_error(self, mock_get):
        mock_get.return_value = (404, "<html>not found</html>", {})
        with self.assertRaises(ab.NotFoundError):
            ab.fetch("https://www.autobazar.eu/detail-aaa/gone/AbC123/")

    @patch("autobazar_scraper.http_client.get_bounded")
    def test_410_raises_not_found_error(self, mock_get):
        mock_get.return_value = (410, "<html>gone</html>", {})
        with self.assertRaises(ab.NotFoundError):
            ab.fetch("https://www.autobazar.eu/detail-aaa/gone/AbC123/")

    @patch("autobazar_scraper.http_client.get_bounded")
    def test_not_found_error_is_a_blocked_error(self, mock_get):
        """Existing `except BlockedError` call sites (search-page fetches)
        must keep catching this with no code change."""
        mock_get.return_value = (404, "", {})
        try:
            ab.fetch("https://www.autobazar.eu/detail-aaa/gone/AbC123/")
        except ab.BlockedError as exc:
            self.assertIsInstance(exc, ab.NotFoundError)
        else:
            self.fail("expected BlockedError (NotFoundError) to be raised")

    @patch("autobazar_scraper.http_client.get_bounded")
    def test_genuine_block_still_plain_blocked_error_not_not_found(self, mock_get):
        mock_get.return_value = (429, "", {})
        with self.assertRaises(ab.BlockedError):
            ab.fetch("https://www.autobazar.eu/vysledky/")
        mock_get.return_value = (503, "", {})
        with self.assertRaises(ab.BlockedError):
            ab.fetch("https://www.autobazar.eu/vysledky/")
        # Neither should be the more specific NotFoundError.
        mock_get.return_value = (429, "", {})
        try:
            ab.fetch("https://www.autobazar.eu/vysledky/")
        except ab.NotFoundError:
            self.fail("429 must not raise NotFoundError")
        except ab.BlockedError:
            pass


class TestBazosFetchNotFound(unittest.TestCase):
    @patch("bazos_scraper.http_client.get_bounded")
    def test_404_raises_not_found_error(self, mock_get):
        mock_get.return_value = (404, "<html>not found</html>", {})
        with self.assertRaises(bz.NotFoundError):
            bz.fetch("https://auto.bazos.sk/inzerat/999/gone.php")

    @patch("bazos_scraper.http_client.get_bounded")
    def test_410_raises_not_found_error(self, mock_get):
        mock_get.return_value = (410, "<html>gone</html>", {})
        with self.assertRaises(bz.NotFoundError):
            bz.fetch("https://auto.bazos.sk/inzerat/999/gone.php")

    @patch("bazos_scraper.http_client.get_bounded")
    def test_not_found_error_is_a_blocked_error(self, mock_get):
        mock_get.return_value = (404, "", {})
        try:
            bz.fetch("https://auto.bazos.sk/inzerat/999/gone.php")
        except bz.BlockedError as exc:
            self.assertIsInstance(exc, bz.NotFoundError)
        else:
            self.fail("expected BlockedError (NotFoundError) to be raised")

    @patch("bazos_scraper.http_client.get_bounded")
    def test_genuine_block_still_plain_blocked_error_not_not_found(self, mock_get):
        mock_get.return_value = (403, "", {})
        try:
            bz.fetch("https://auto.bazos.sk/")
        except bz.NotFoundError:
            self.fail("403 must not raise NotFoundError")
        except bz.BlockedError:
            pass


if __name__ == "__main__":
    unittest.main()
