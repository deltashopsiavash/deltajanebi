import re
from decimal import Decimal
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from django.utils import timezone

from shop.models import Product, SourceSite
from shop.services import source_sync
from shop.services.source_sync import sync_category_path
from shop.source_registry import allowed_url, canonical_hostname

MAX_SITEMAPS = 1000
MAX_DISCOVERED_PRODUCTS = 50000
MAX_FALLBACK_PAGES = 1000
REQUEST_TIMEOUT = 20

PRODUCT_PATH_PATTERNS = (
    r"/product/",
    r"/products/",
    r"/p/",
    r"/product-[^/]+/",
)
EXCLUDED_PATH_TOKENS = (
    "/product-category/",
    "/category/",
    "/categories/",
    "/tag/",
    "/cart",
    "/checkout",
    "/account",
    "/search",
)


class CatalogSkip(Exception):
    """Expected catalog item that should be skipped without reporting a sync failure."""


def _same_source(url, site):
    parsed = urlparse(str(url or ""))
    return bool(parsed.hostname and canonical_hostname(parsed.hostname) == canonical_hostname(site.hostname))


def _safe_get(url, site, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"):
    if not _same_source(url, site) or not allowed_url(url):
        return None
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "DeltaJanebiCatalogSync/2.1",
                "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.5",
                "Accept": accept,
            },
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException:
        return None
    if response.status_code >= 400 or not _same_source(response.url, site) or not allowed_url(response.url):
        return None
    return response


def _looks_product_url(url, sitemap_hint=""):
    parsed = urlparse(str(url or ""))
    path = (parsed.path or "").lower()
    if not path or path == "/" or any(token in path for token in EXCLUDED_PATH_TOKENS):
        return False
    if any(re.search(pattern, path, re.I) for pattern in PRODUCT_PATH_PATTERNS):
        return True
    hint = sitemap_hint.lower()
    if "product" in hint and not any(token in path for token in ("category", "tag", "brand")):
        ext = path.rsplit("/", 1)[-1]
        if ext and not re.search(r"\.(?:jpg|jpeg|png|webp|gif|svg|pdf|xml)$", ext, re.I):
            return True
    return False


def _robots_sitemaps(site):
    response = _safe_get(urljoin(site.base_url.rstrip("/") + "/", "robots.txt"), site, "text/plain,*/*;q=0.8")
    if not response:
        return []
    found = []
    for line in response.text.splitlines():
        if line.lower().startswith("sitemap:"):
            value = line.split(":", 1)[1].strip()
            if value and _same_source(value, site) and value not in found:
                found.append(value)
    return found


def _parse_sitemap(response):
    text = response.text.lstrip()
    if not text.startswith("<"):
        return None, []
    soup = BeautifulSoup(response.content, "xml")
    root = soup.find()
    if not root:
        return None, []
    root_name = root.name.lower()
    locs = [loc.get_text(strip=True) for loc in soup.find_all("loc") if loc.get_text(strip=True)]
    if root_name.endswith("sitemapindex"):
        return "index", locs
    if root_name.endswith("urlset"):
        return "urls", locs
    return None, []


def discover_product_urls(site):
    """Discover the complete catalog exposed by the registered source.

    Sitemap discovery and HTML listing crawl are deliberately merged. Older
    versions returned as soon as any sitemap product was found, which meant a
    partial/marketing sitemap could make «آپلود همه» import only a handful of
    products. We now keep those URLs and continue through category/shop
    listings to fill the gaps.
    """
    queue = []
    for value in _robots_sitemaps(site) + [
        urljoin(site.base_url.rstrip("/") + "/", "sitemap.xml"),
        urljoin(site.base_url.rstrip("/") + "/", "sitemap_index.xml"),
        urljoin(site.base_url.rstrip("/") + "/", "wp-sitemap.xml"),
    ]:
        if value not in queue:
            queue.append(value)

    products = []
    seen_products = set()
    seen_sitemaps = set()
    listing_pages = []

    def add_product(value):
        clean = str(value or "").split("#", 1)[0]
        if clean and clean not in seen_products and _same_source(clean, site):
            seen_products.add(clean)
            products.append(clean)

    def add_listing(value):
        clean = str(value or "").split("#", 1)[0]
        if clean and _same_source(clean, site) and clean not in listing_pages:
            listing_pages.append(clean)

    while queue and len(seen_sitemaps) < MAX_SITEMAPS and len(products) < MAX_DISCOVERED_PRODUCTS:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen_sitemaps or not _same_source(sitemap_url, site):
            continue
        seen_sitemaps.add(sitemap_url)
        response = _safe_get(sitemap_url, site, "application/xml,text/xml,*/*;q=0.8")
        if not response:
            continue
        kind, locs = _parse_sitemap(response)
        if kind == "index":
            preferred = [x for x in locs if "product" in x.lower()]
            others = [x for x in locs if x not in preferred]
            for child in preferred + others:
                if _same_source(child, site) and child not in seen_sitemaps and child not in queue:
                    queue.append(child)
        elif kind == "urls":
            for loc in locs:
                if not _same_source(loc, site):
                    continue
                if _looks_product_url(loc, sitemap_url):
                    add_product(loc)
                    if len(products) >= MAX_DISCOVERED_PRODUCTS:
                        break
                    continue
                path = (urlparse(loc).path or "").lower()
                hint = sitemap_url.lower()
                if any(token in path for token in ("/shop", "/category", "/product-category", "/collections", "/catalog", "/store")) or any(token in hint for token in ("category", "product_cat", "collection")):
                    add_listing(loc)

    page_queue = [
        site.base_url.rstrip("/") + "/",
        urljoin(site.base_url.rstrip("/") + "/", "shop/"),
        urljoin(site.base_url.rstrip("/") + "/", "products/"),
        *listing_pages,
    ]
    page_queue = list(dict.fromkeys(page_queue))
    seen_pages = set()
    while page_queue and len(seen_pages) < MAX_FALLBACK_PAGES and len(products) < MAX_DISCOVERED_PRODUCTS:
        page = page_queue.pop(0)
        if page in seen_pages:
            continue
        seen_pages.add(page)
        response = _safe_get(page, site)
        if not response:
            continue
        soup = BeautifulSoup(response.text, "lxml")
        for anchor in soup.find_all("a", href=True):
            href = urljoin(response.url, anchor.get("href"))
            if not _same_source(href, site):
                continue
            if _looks_product_url(href):
                add_product(href)
                if len(products) >= MAX_DISCOVERED_PRODUCTS:
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

    return products


def _markup_price(source_price, markup_type, markup_value):
    base = Decimal(source_price or 0)
    value = Decimal(markup_value or 0)
    if markup_type == SourceSite.MARKUP_PERCENT:
        result = base * (Decimal("1") + value / Decimal("100"))
    else:
        result = base + value
    return max(0, int(result.quantize(Decimal("1"))))


def _existing_for_source(site, url, data):
    product = Product.objects.filter(source_type=Product.SYNCED, source_url=url).first()
    if product:
        return product
    source_code = str(data.get("sku") or "").strip()
    if source_code:
        return Product.objects.filter(
            source_type=Product.SYNCED,
            source_product_code=source_code,
            source_url__icontains=site.hostname,
        ).first()
    return None


def _snapshot(product):
    if not product:
        return None
    return {
        "name": product.name,
        "source_price": int(product.source_price or 0),
        "price": int(product.price or 0),
        "stock": int(product.stock or 0),
        "image_url": product.image_url or "",
        "gallery": tuple(product.gallery or []),
        "specs": dict(product.specs or {}),
    }


def upsert_source_product(site, url):
    data = source_sync.scrape_product(url)
    canonical_url = data.get("source_url") or url
    product = _existing_for_source(site, canonical_url, data) or _existing_for_source(site, url, data)
    created = product is None

    incoming_price = int(data.get("price") or 0)
    if created and not incoming_price:
        raise CatalogSkip("محصول جدید فعلاً ناموجود و بدون قیمت قابل استخراج است؛ تا نمایش قیمت از ورود خودکار رد شد.")

    category = sync_category_path(data.get("categories") or []) if created else None
    sku = str(data.get("sku") or "").strip() or None
    if sku and Product.objects.exclude(pk=product.pk if product else None).filter(sku=sku).exists():
        sku = None

    if created:
        product = Product(
            category=category,
            name=data["name"],
            description=data.get("description", ""),
            source_type=Product.SYNCED,
            source_url=canonical_url,
            source_product_code=str(data.get("sku") or "")[:100],
            source_price=incoming_price,
            stock=data.get("stock", 0),
            image_url=data.get("image_url", ""),
            gallery=data.get("gallery") or [],
            specs=data.get("specs") or {},
            sku=sku,
            markup_type=site.default_markup_type,
            markup_value=site.default_markup_value,
        )
    else:
        product.name = data.get("name") or product.name
        product.description = data.get("description") or product.description
        product.source_url = canonical_url
        product.source_product_code = str(data.get("sku") or product.source_product_code or "")[:100]
        if incoming_price:
            product.source_price = incoming_price
        product.stock = data.get("stock", 0)
        product.image_url = data.get("image_url") or product.image_url
        product.gallery = data.get("gallery") or product.gallery
        product.specs = data.get("specs") or product.specs
        if sku:
            product.sku = sku
        product.markup_type = site.default_markup_type
        product.markup_value = site.default_markup_value

    effective_source_price = incoming_price or int(product.source_price or 0)
    if effective_source_price:
        product.price = _markup_price(effective_source_price, site.default_markup_type, site.default_markup_value)
    product.last_synced_at = timezone.now()
    product.sync_error = ""
    product.save()
    return product, created


def upsert_source_product_with_changes(site, url):
    data = source_sync.scrape_product(url)
    canonical_url = data.get("source_url") or url
    existing = _existing_for_source(site, canonical_url, data) or _existing_for_source(site, url, data)
    before = _snapshot(existing)

    original = source_sync.scrape_product
    source_sync.scrape_product = lambda _url: data
    try:
        product, created = upsert_source_product(site, canonical_url)
    finally:
        source_sync.scrape_product = original

    after = _snapshot(product)
    changes = {}
    if created:
        changes["new"] = True
    else:
        for key in ("name", "source_price", "price", "stock", "image_url", "gallery", "specs"):
            if before.get(key) != after.get(key):
                changes[key] = (before.get(key), after.get(key))
    return product, created, changes


def source_products(site):
    return Product.objects.filter(source_type=Product.SYNCED, source_url__icontains=site.hostname)


def apply_site_markup_to_existing(site):
    updated = 0
    for product in source_products(site).iterator(chunk_size=200):
        product.markup_type = site.default_markup_type
        product.markup_value = site.default_markup_value
        product.price = _markup_price(product.source_price, site.default_markup_type, site.default_markup_value)
        product.save(update_fields=["markup_type", "markup_value", "price"])
        updated += 1
    return updated
