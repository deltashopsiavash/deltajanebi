import os
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from shop.services import source_catalog as catalog
from shop.source_registry import allowed_url

DEFAULT_BUDGET_SECONDS = max(20, int(os.getenv("DELTA_SOURCE_DISCOVERY_BUDGET", "120")))
DEFAULT_REQUEST_TIMEOUT = max(2.0, float(os.getenv("DELTA_SOURCE_DISCOVERY_REQUEST_TIMEOUT", "8")))
DEFAULT_CONNECT_TIMEOUT = max(1.0, float(os.getenv("DELTA_SOURCE_DISCOVERY_CONNECT_TIMEOUT", "5")))
DEFAULT_REQUEST_WALL_TIMEOUT = max(
    DEFAULT_REQUEST_TIMEOUT,
    float(os.getenv("DELTA_SOURCE_DISCOVERY_REQUEST_WALL_TIMEOUT", "20")),
)
DEFAULT_MAX_SITEMAPS = max(10, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_SITEMAPS", "250")))
DEFAULT_MAX_PAGES = max(20, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_PAGES", "300")))
MAX_RESPONSE_BYTES = max(1_048_576, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_RESPONSE_BYTES", str(12 * 1024 * 1024))))
MAX_HTML_PARSE_BYTES = max(524_288, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_HTML_PARSE_BYTES", str(6 * 1024 * 1024))))
MAX_ANCHORS_PER_PAGE = max(1000, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_ANCHORS", "20000")))
MAX_PAGE_PROCESS_SECONDS = max(1.0, float(os.getenv("DELTA_SOURCE_DISCOVERY_PAGE_PROCESS_SECONDS", "6")))
HEARTBEAT_SECONDS = max(0.25, float(os.getenv("DELTA_SOURCE_DISCOVERY_HEARTBEAT_SECONDS", "0.8")))

_PAGINATION_QUERY_KEYS = {
    "page",
    "paged",
    "product-page",
    "product_page",
    "productpage",
}


def _canonical_listing_url(value):
    """Drop sort/filter/tracking query strings from category/listing URLs.

    WooCommerce category pages can expose hundreds of URLs such as
    ``?orderby=price`` or attribute filters. Crawling every combination turns a
    finite catalog into a near-infinite graph and was the main reason discovery
    could appear frozen on one category. Keep only query parameters that can
    actually advance pagination.
    """
    text = str(value or "").split("#", 1)[0].strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        kept = []
        for raw_key, raw_value in parse_qsl(parts.query, keep_blank_values=True):
            key = raw_key.casefold()
            if key in _PAGINATION_QUERY_KEYS:
                kept.append((raw_key, raw_value))
        query = urlencode(kept, doseq=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    except Exception:
        return text


def _safe_get(
    url,
    site,
    deadline,
    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    heartbeat=None,
):
    """Fetch one discovery resource with both socket and wall-clock bounds.

    ``requests`` read timeouts are inactivity timeouts, not total transfer
    deadlines. A server that trickles a few bytes every few seconds can therefore
    keep ``requests.get`` alive for minutes. Streaming here gives every URL a
    real wall-clock ceiling and a response-size ceiling, so one pathological
    category can never hold the whole catalog job hostage.
    """
    if not catalog._same_source(url, site) or not allowed_url(url):
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None

    wall_timeout = max(1.0, min(DEFAULT_REQUEST_WALL_TIMEOUT, remaining))
    request_deadline = min(deadline, time.monotonic() + wall_timeout)
    connect_timeout = max(0.5, min(DEFAULT_CONNECT_TIMEOUT, wall_timeout))
    read_timeout = max(0.5, min(DEFAULT_REQUEST_TIMEOUT, wall_timeout))

    response = None
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "DeltaJanebiCatalogSync/2.3",
                "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.5",
                "Accept": accept,
                "Connection": "close",
            },
            timeout=(connect_timeout, read_timeout),
            allow_redirects=True,
            stream=True,
        )
        if response.status_code >= 400:
            response.close()
            return None
        if not catalog._same_source(response.url, site) or not allowed_url(response.url):
            response.close()
            return None

        chunks = []
        total = 0
        last_heartbeat = time.monotonic()
        truncated = False
        for chunk in response.iter_content(chunk_size=64 * 1024):
            now = time.monotonic()
            if heartbeat and now - last_heartbeat >= HEARTBEAT_SECONDS:
                heartbeat()
                last_heartbeat = now
            if now >= request_deadline:
                response.close()
                return None
            if not chunk:
                continue
            remaining_bytes = MAX_RESPONSE_BYTES - total
            if remaining_bytes <= 0:
                truncated = True
                break
            if len(chunk) > remaining_bytes:
                chunk = chunk[:remaining_bytes]
                truncated = True
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_RESPONSE_BYTES:
                truncated = True
                break

        response._content = b"".join(chunks)
        response._content_consumed = True
        response.encoding = response.encoding or "utf-8"
        response._delta_truncated = truncated
        response.close()
        return response
    except requests.RequestException:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        return None


def discover_product_urls_bounded(
    site,
    progress=None,
    budget_seconds=None,
    max_sitemaps=None,
    max_pages=None,
):
    """Discover as much of a source catalog as possible without hanging a sync job.

    Discovery has a source-wide deadline, each HTTP request has its own hard
    transfer deadline, filter/sort URL variants are collapsed, and large listing
    pages periodically emit heartbeats while their links are inspected. Products
    found before any limit is reached are retained and synced normally.
    """
    budget = float(budget_seconds or DEFAULT_BUDGET_SECONDS)
    sitemap_limit = int(max_sitemaps or DEFAULT_MAX_SITEMAPS)
    page_limit = int(max_pages or DEFAULT_MAX_PAGES)
    started = time.monotonic()
    deadline = started + budget

    products = []
    seen_products = set()
    seen_sitemaps = set()
    listing_pages = []
    seen_pages = set()
    requests_done = 0
    phase = "sitemaps"
    current_url = ""

    def expired():
        return time.monotonic() >= deadline

    def heartbeat():
        if not progress:
            return
        try:
            progress({
                "phase": phase,
                "requests": requests_done,
                "found": len(products),
                "sitemaps": len(seen_sitemaps),
                "pages": len(seen_pages),
                "elapsed": int(time.monotonic() - started),
                "budget": int(budget),
                "current_url": current_url[:500],
            })
        except Exception:
            pass

    def fetch(url, accept=None):
        nonlocal requests_done, current_url
        if expired():
            return None
        current_url = str(url or "")
        heartbeat()
        response = _safe_get(
            current_url,
            site,
            deadline,
            accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            heartbeat=heartbeat,
        )
        requests_done += 1
        heartbeat()
        return response

    def add_product(value):
        clean = str(value or "").split("#", 1)[0]
        if clean and clean not in seen_products and catalog._same_source(clean, site):
            seen_products.add(clean)
            products.append(clean)

    def add_listing(value):
        clean = _canonical_listing_url(value)
        if clean and catalog._same_source(clean, site) and clean not in listing_pages:
            listing_pages.append(clean)

    robots = fetch(urljoin(site.base_url.rstrip("/") + "/", "robots.txt"), "text/plain,*/*;q=0.8")
    robots_sitemaps = []
    if robots:
        for line in robots.text.splitlines():
            if line.lower().startswith("sitemap:"):
                value = line.split(":", 1)[1].strip()
                if value and catalog._same_source(value, site) and value not in robots_sitemaps:
                    robots_sitemaps.append(value)

    queue = []
    for value in robots_sitemaps + [
        urljoin(site.base_url.rstrip("/") + "/", "sitemap.xml"),
        urljoin(site.base_url.rstrip("/") + "/", "sitemap_index.xml"),
        urljoin(site.base_url.rstrip("/") + "/", "wp-sitemap.xml"),
    ]:
        if value not in queue:
            queue.append(value)

    while queue and len(seen_sitemaps) < sitemap_limit and len(products) < catalog.MAX_DISCOVERED_PRODUCTS and not expired():
        sitemap_url = queue.pop(0)
        if sitemap_url in seen_sitemaps or not catalog._same_source(sitemap_url, site):
            continue
        seen_sitemaps.add(sitemap_url)
        response = fetch(sitemap_url, "application/xml,text/xml,*/*;q=0.8")
        if not response:
            continue
        kind, locs = catalog._parse_sitemap(response)
        if kind == "index":
            preferred = [x for x in locs if "product" in x.lower()]
            others = [x for x in locs if x not in preferred]
            for child in preferred + others:
                if catalog._same_source(child, site) and child not in seen_sitemaps and child not in queue:
                    queue.append(child)
        elif kind == "urls":
            for loc in locs:
                if not catalog._same_source(loc, site):
                    continue
                if catalog._looks_product_url(loc, sitemap_url):
                    add_product(loc)
                    if len(products) >= catalog.MAX_DISCOVERED_PRODUCTS:
                        break
                    continue
                path = (urlparse(loc).path or "").lower()
                hint = sitemap_url.lower()
                if any(token in path for token in ("/shop", "/category", "/product-category", "/collections", "/catalog", "/store")) or any(token in hint for token in ("category", "product_cat", "collection")):
                    add_listing(loc)

    phase = "listings"
    page_queue = list(dict.fromkeys(filter(None, [
        _canonical_listing_url(site.base_url.rstrip("/") + "/"),
        _canonical_listing_url(urljoin(site.base_url.rstrip("/") + "/", "shop/")),
        _canonical_listing_url(urljoin(site.base_url.rstrip("/") + "/", "products/")),
        *listing_pages,
    ])))

    while page_queue and len(seen_pages) < page_limit and len(products) < catalog.MAX_DISCOVERED_PRODUCTS and not expired():
        page = _canonical_listing_url(page_queue.pop(0))
        if not page or page in seen_pages:
            continue
        seen_pages.add(page)
        response = fetch(page)
        if not response:
            continue

        parse_started = time.monotonic()
        raw_html = response.content[:MAX_HTML_PARSE_BYTES]
        try:
            soup = BeautifulSoup(raw_html, "lxml")
        except Exception:
            continue

        anchors = soup.find_all("a", href=True, limit=MAX_ANCHORS_PER_PAGE)
        for index, anchor in enumerate(anchors):
            now = time.monotonic()
            if index % 200 == 0:
                heartbeat()
            if expired() or now - parse_started >= MAX_PAGE_PROCESS_SECONDS:
                break

            href = urljoin(response.url, anchor.get("href"))
            if not catalog._same_source(href, site):
                continue
            if catalog._looks_product_url(href):
                add_product(href)
                if len(products) >= catalog.MAX_DISCOVERED_PRODUCTS:
                    break
                continue

            parsed = urlparse(href)
            path = (parsed.path or "").lower()
            query = (parsed.query or "").lower()
            rel = " ".join(anchor.get("rel") or []).lower()
            classes = " ".join(anchor.get("class") or []).lower()
            text = anchor.get_text(" ", strip=True).lower()
            looks_listing = any(token in path for token in (
                "/shop", "/category", "/product-category", "/collections",
                "/catalog", "/store", "/page/",
            ))
            looks_paged_query = any(token in query for token in ("page=", "paged=", "product-page="))
            looks_next = "next" in rel or "next" in classes or text in {"بعدی", "صفحه بعد", "next", ">", "»"}
            clean = _canonical_listing_url(href)
            if (
                (looks_listing or looks_paged_query or looks_next)
                and clean
                and clean not in seen_pages
                and clean not in page_queue
                and len(seen_pages) + len(page_queue) < page_limit
            ):
                page_queue.append(clean)

    elapsed = int(time.monotonic() - started)
    meta = {
        "timed_out": expired(),
        "requests": requests_done,
        "found": len(products),
        "sitemaps": len(seen_sitemaps),
        "pages": len(seen_pages),
        "elapsed": elapsed,
        "budget": int(budget),
    }
    phase = "done"
    heartbeat()
    return products, meta
