import os
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from shop.services import source_catalog as catalog
from shop.source_registry import allowed_url

DEFAULT_BUDGET_SECONDS = max(20, int(os.getenv("DELTA_SOURCE_DISCOVERY_BUDGET", "120")))
DEFAULT_REQUEST_TIMEOUT = max(2.0, float(os.getenv("DELTA_SOURCE_DISCOVERY_REQUEST_TIMEOUT", "8")))
DEFAULT_MAX_SITEMAPS = max(10, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_SITEMAPS", "250")))
DEFAULT_MAX_PAGES = max(20, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_PAGES", "300")))


def _safe_get(url, site, deadline, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"):
    if not catalog._same_source(url, site) or not allowed_url(url):
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    timeout = max(0.5, min(DEFAULT_REQUEST_TIMEOUT, remaining))
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "DeltaJanebiCatalogSync/2.2",
                "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.5",
                "Accept": accept,
            },
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException:
        return None
    if response.status_code >= 400:
        return None
    if not catalog._same_source(response.url, site) or not allowed_url(response.url):
        return None
    return response


def discover_product_urls_bounded(
    site,
    progress=None,
    budget_seconds=None,
    max_sitemaps=None,
    max_pages=None,
):
    """Discover as much of a source catalog as possible without hanging a sync job.

    The original crawler can legally visit up to 1000 sitemaps and 1000 listing
    pages. With a 20 second request timeout that makes the planning phase look
    frozen at 0/0 for hours on a slow source. This version preserves sitemap +
    listing discovery, but gives each source a hard wall-clock budget and emits
    a heartbeat after every network attempt. Any products found before the
    deadline are retained and synced normally.
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
                "pages": len(seen_pages) if "seen_pages" in locals() else 0,
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
        response = _safe_get(
            current_url,
            site,
            deadline,
            accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
        clean = str(value or "").split("#", 1)[0]
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
    page_queue = list(dict.fromkeys([
        site.base_url.rstrip("/") + "/",
        urljoin(site.base_url.rstrip("/") + "/", "shop/"),
        urljoin(site.base_url.rstrip("/") + "/", "products/"),
        *listing_pages,
    ]))
    seen_pages = set()

    while page_queue and len(seen_pages) < page_limit and len(products) < catalog.MAX_DISCOVERED_PRODUCTS and not expired():
        page = page_queue.pop(0)
        if page in seen_pages:
            continue
        seen_pages.add(page)
        response = fetch(page)
        if not response:
            continue
        soup = BeautifulSoup(response.text, "lxml")
        for anchor in soup.find_all("a", href=True):
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
            clean = href.split("#", 1)[0]
            if (looks_listing or looks_paged_query or looks_next) and clean not in seen_pages and clean not in page_queue:
                page_queue.append(clean)
                if len(page_queue) + len(seen_pages) >= page_limit:
                    break

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
