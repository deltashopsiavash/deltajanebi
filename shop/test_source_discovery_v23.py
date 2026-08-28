from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from shop.services import source_discovery_v19 as discovery


class _FakeResponse:
    def __init__(self, chunks, url="https://example-source.ir/product-category/cables/"):
        self.status_code = 200
        self.url = url
        self.encoding = "utf-8"
        self._chunks = list(chunks)
        self.closed = False

    def iter_content(self, chunk_size=65536):
        yield from self._chunks

    def close(self):
        self.closed = True


class SourceDiscoveryV23Tests(SimpleTestCase):
    def test_listing_url_drops_sort_filter_and_tracking_queries(self):
        value = (
            "https://example-source.ir/product-category/cable/?orderby=price"
            "&filter_brand=apple&paged=3&utm_source=test#items"
        )
        self.assertEqual(
            discovery._canonical_listing_url(value),
            "https://example-source.ir/product-category/cable/?paged=3",
        )

    def test_listing_url_keeps_product_page_pagination(self):
        value = "https://example-source.ir/shop/?product-page=4&min_price=100&max_price=900"
        self.assertEqual(
            discovery._canonical_listing_url(value),
            "https://example-source.ir/shop/?product-page=4",
        )

    @patch.object(discovery, "allowed_url", return_value=True)
    @patch.object(discovery.requests, "get")
    def test_safe_get_caps_response_body(self, get, _allowed):
        response = _FakeResponse([b"abc", b"def"])
        get.return_value = response
        site = SimpleNamespace(hostname="example-source.ir")

        with patch.object(discovery, "MAX_RESPONSE_BYTES", 5):
            result = discovery._safe_get(
                response.url,
                site,
                deadline=1000,
            )

        self.assertIs(result, response)
        self.assertEqual(result.content, b"abcde")
        self.assertTrue(result._delta_truncated)
        self.assertTrue(response.closed)
        self.assertTrue(get.call_args.kwargs["stream"])
        self.assertEqual(get.call_args.kwargs["headers"]["Connection"], "close")

    @patch.object(discovery, "allowed_url", return_value=True)
    @patch.object(discovery.requests, "get")
    def test_safe_get_has_real_wall_clock_deadline(self, get, _allowed):
        response = _FakeResponse([b"first", b"second"])
        get.return_value = response
        site = SimpleNamespace(hostname="example-source.ir")

        # Calls are: source remaining, request deadline, last heartbeat, first
        # streamed chunk. The fourth value jumps past the per-request wall limit.
        with patch.object(discovery.time, "monotonic", side_effect=[0, 0, 0, 25]):
            result = discovery._safe_get(
                response.url,
                site,
                deadline=1000,
            )

        self.assertIsNone(result)
        self.assertTrue(response.closed)
