"""Clone only the explicit «دسته‌بندی محصولات» block from a source homepage.

The scraper is intentionally strict: it must first find the product-categories
heading and then reads cards only from the smallest surrounding block that
contains that heading. Other homepage widgets/collections/promos are ignored.
The hamburger/menu Category tree is never modified by this feature.
"""
import re
import time
import unicodedata
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from django.db import transaction
from django.utils import timezone

from shop.home_category_models import HomeCategoryShowcase, HomeCategoryTile
from shop.models import Category, SourceSite
from shop.services.source_discovery_v19 import _safe_get

MAX_TILES = 48
FETCH_BUDGET_SECONDS = 30

_TRANSLATION = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه",
    "ؤ": "و", "إ": "ا", "أ": "ا",
})

_TILE_SELECTORS = (
    ".elementor-widget-wd_product_categories .category-grid-item",
    ".elementor-widget-woocommerce-product-categories .product-category",
    ".wd-categories .category-grid-item",
    ".wd-categories .wd-cat",
    ".category-grid-item",
    "li.product-category",
    ".product-category",
)
_TITLE_SELECTORS = (
    ".woocommerce-loop-category__title",
    ".wd-entities-title",
    ".category-title",
    ".category-name",
    ".hover-mask h2",
    ".hover-mask h3",
    "h2",
    "h3",
    "h4",
)
_IMAGE_ATTRS = ("data-large_image", "data-src", "data-lazy-src", "data-original", "src")
_CATEGORY_URL_TOKENS = (
    "/product-category/", "/product_cat/", "/category/", "/categories/",
    "/collection/", "/collections/", "product_cat=", "product-category=",
)
_BAD_NAMES = {
    "خانه", "صفحه اصلی", "فروشگاه", "محصولات", "همه محصولات", "مشاهده همه",
    "home", "shop", "products", "view all", "all products",
}
_PRODUCT_CATEGORY_HEADING_KEYS = (
    "دستهبندیمحصولات",
    "دستهبندیهایمحصولات",
    "productcategories",
    "productcategory",
)


def _norm(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_TRANSLATION)
    text = text.replace("\u200c", " ").replace("\u200f", " ").replace("\ufeff", " ")
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-|–—:/")
    return text


def _key(value):
    return re.sub(r"[\s\u200c\-_–—|/\\:.,،؛;()\[\]{}]+", "", _norm(value)).casefold()


def _clean_name(value):
    text = _norm(value)
    text = re.sub(r"\s*\(\s*[۰-۹٠-٩0-9,.]+\s*\)\s*$", "", text).strip()
    if not text or len(text) > 160 or text.casefold() in _BAD_NAMES:
        return ""
    return text


def _same_host_or_subdomain(url, site):
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
    except Exception:
        return False
    base = str(site.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if base.startswith("www."):
        base = base[4:]
    return bool(host and base and (host == base or host.endswith("." + base)))


def _looks_category_url(url):
    lowered = str(url or "").casefold()
    if any(token in lowered for token in _CATEGORY_URL_TOKENS):
        return True
    try:
        query = parse_qs(urlparse(url).query)
    except Exception:
        return False
    return bool({"product_cat", "product-category", "category", "collection"} & set(query))


def _image_from_node(node, base_url):
    img = node.select_one("img")
    if not img:
        return ""
    for attr in _IMAGE_ATTRS:
        raw = str(img.get(attr) or "").strip()
        if raw and not raw.startswith("data:"):
            return urljoin(base_url, raw)
    srcset = str(img.get("srcset") or img.get("data-srcset") or "").strip()
    if srcset:
        candidates = []
        for part in srcset.split(","):
            bits = part.strip().split()
            if bits:
                width = 0
                if len(bits) > 1:
                    match = re.search(r"(\d+)", bits[-1])
                    width = int(match.group(1)) if match else 0
                candidates.append((width, bits[0]))
        if candidates:
            return urljoin(base_url, sorted(candidates)[-1][1])
    return ""


def _name_from_node(node):
    for selector in _TITLE_SELECTORS:
        item = node.select_one(selector)
        if item:
            name = _clean_name(item.get_text(" ", strip=True))
            if name:
                return name
    img = node.select_one("img")
    if img:
        name = _clean_name(img.get("alt") or img.get("title"))
        if name:
            return name
    anchor = node if getattr(node, "name", "") == "a" else node.select_one("a")
    if anchor:
        return _clean_name(anchor.get("aria-label") or anchor.get("title") or anchor.get_text(" ", strip=True))
    return ""


def _tile_from_node(node, base_url, site, strict=False):
    anchor = node if getattr(node, "name", "") == "a" else node.select_one("a[href]")
    if not anchor:
        return None
    href = urljoin(base_url, str(anchor.get("href") or "").strip())
    if not href.startswith(("http://", "https://")) or not _same_host_or_subdomain(href, site):
        return None

    classes = " ".join((node.get("class") or []) + (anchor.get("class") or [])).casefold()
    categoryish = "categor" in classes or "wd-cat" in classes or "product-cat" in classes
    if not strict and not (_looks_category_url(href) or categoryish):
        return None

    name = _name_from_node(node)
    if not name:
        return None
    return {
        "name": name,
        "image_url": _image_from_node(node, base_url),
        "source_category_url": href,
    }


def _is_product_categories_heading(value):
    marker = _key(value)
    return bool(marker and any(marker.startswith(prefix) for prefix in _PRODUCT_CATEGORY_HEADING_KEYS))


def _candidate_count(node):
    """Cheap estimate used only to choose the smallest heading-owned block."""
    seen = set()
    for selector in _TILE_SELECTORS:
        for item in node.select(selector):
            seen.add(id(item))
    if len(seen) >= 2:
        return len(seen)
    category_links = 0
    for anchor in node.select("a[href]"):
        if _looks_category_url(anchor.get("href")):
            category_links += 1
    return max(len(seen), category_links)


def _product_categories_scope(soup):
    """Return only the DOM block owned by the «دسته‌بندی محصولات» heading.

    We deliberately do not fall back to scanning the entire homepage. If the
    named section cannot be isolated, cloning fails and the previous layout is
    kept rather than importing unrelated homepage cards.
    """
    main = soup.select_one("main, .main-page-wrapper, .site-content, [role='main']") or soup
    headings = []
    for heading in main.select("h1,h2,h3,h4,h5,h6"):
        if _is_product_categories_heading(heading.get_text(" ", strip=True)):
            headings.append(heading)
    if not headings:
        return None

    for heading in headings:
        current = heading.parent
        best = None
        for _ in range(7):
            if current is None or current is main.parent:
                break
            count = _candidate_count(current)
            if count >= 2:
                best = current
                break
            if current is main:
                break
            current = current.parent
        if best is not None:
            return best
    return None


def extract_homepage_categories(html, base_url, site):
    """Extract cards only from the explicit product-category section."""
    soup = BeautifulSoup(str(html or ""), "lxml")
    for node in soup.select("header,nav,footer,aside,.mobile-nav,.mobile-menu,.wd-header-nav,.menu,.offcanvas-sidebar"):
        node.decompose()

    scope = _product_categories_scope(soup)
    if scope is None:
        return {"title": "", "subtitle": "", "tiles": []}

    rows = []
    seen_names = set()

    def add(tile):
        if not tile:
            return
        name_marker = _key(tile["name"])
        if not name_marker or name_marker in seen_names:
            return
        seen_names.add(name_marker)
        rows.append(tile)

    for selector in _TILE_SELECTORS:
        found = scope.select(selector)
        if not found:
            continue
        for node in found:
            add(_tile_from_node(node, base_url, site, strict=True))
            if len(rows) >= MAX_TILES:
                break
        if len(rows) >= 2:
            break

    if len(rows) < 2:
        for anchor in scope.select("a[href]"):
            href = urljoin(base_url, str(anchor.get("href") or "").strip())
            if not _looks_category_url(href):
                continue
            parent = anchor
            for _ in range(3):
                if not parent.parent or parent.parent is scope.parent:
                    break
                candidate = parent.parent
                classes = " ".join(candidate.get("class") or []).casefold()
                if "categor" in classes or candidate.select_one("img"):
                    parent = candidate
                    break
                parent = candidate
            add(_tile_from_node(parent, base_url, site, strict=False))
            if len(rows) >= MAX_TILES:
                break

    if not rows:
        return {"title": "", "subtitle": "", "tiles": []}

    # Do not import arbitrary source headings/subtitles/promotional copy. Only
    # the category cards themselves are cloned, under Delta's fixed section title.
    return {"title": "دسته‌بندی محصولات", "subtitle": "", "tiles": rows[:MAX_TILES]}


def _category_lookup():
    mapping = {}
    for category in Category.objects.filter(is_active=True).select_related("parent").order_by("id"):
        marker = _key(category.name)
        if marker and marker not in mapping:
            mapping[marker] = category
    return mapping


def clone_homepage_categories(source_site_id):
    """Fetch one registered source homepage and atomically replace only home tiles."""
    site = SourceSite.objects.filter(pk=source_site_id, is_active=True).first()
    if not site:
        raise ValueError("سایت منبع پیدا نشد یا غیرفعال است.")

    deadline = time.monotonic() + FETCH_BUDGET_SECONDS
    response = _safe_get(site.base_url, site, deadline)
    if response is None:
        raise ValueError("صفحه اصلی سایت منبع قابل دریافت نبود.")

    result = extract_homepage_categories(response.text, response.url or site.base_url, site)
    rows = result["tiles"]
    if not rows:
        raise ValueError("بخش «دسته‌بندی محصولات» در صفحه اصلی این سایت پیدا نشد؛ چیدمان قبلی دست‌نخورده ماند.")

    categories = _category_lookup()
    matched = 0
    with transaction.atomic():
        showcase = HomeCategoryShowcase.load()
        showcase.source_site = site
        showcase.title = "دسته‌بندی محصولات"
        showcase.subtitle = ""
        showcase.source_url = (response.url or site.base_url)[:4096]
        showcase.enabled = True
        showcase.last_synced_at = timezone.now()
        showcase.save()

        showcase.tiles.all().delete()
        objects = []
        for order, row in enumerate(rows):
            category = categories.get(_key(row["name"]))
            if category:
                matched += 1
            objects.append(HomeCategoryTile(
                showcase=showcase,
                source_site=site,
                category=category,
                name=row["name"][:160],
                image_url=str(row.get("image_url") or "")[:4096],
                source_category_url=str(row.get("source_category_url") or "")[:4096],
                order=order,
                is_active=True,
            ))
        HomeCategoryTile.objects.bulk_create(objects, batch_size=100)

    return {
        "source_site_id": site.id,
        "source_site_name": site.name,
        "title": showcase.title,
        "subtitle": "",
        "count": len(rows),
        "matched_categories": matched,
        "unmatched_categories": len(rows) - matched,
        "items": [row["name"] for row in rows],
        "menu_untouched": True,
        "section_only": True,
    }


def reset_homepage_categories():
    """Return homepage to Delta's default root categories; menu stays untouched."""
    with transaction.atomic():
        showcase = HomeCategoryShowcase.load()
        showcase.enabled = False
        showcase.source_site = None
        showcase.source_url = ""
        showcase.last_synced_at = timezone.now()
        showcase.save(update_fields=["enabled", "source_site", "source_url", "last_synced_at", "updated_at"])
    return {"enabled": False, "menu_untouched": True}


def homepage_category_status():
    showcase = HomeCategoryShowcase.objects.select_related("source_site").filter(pk=1).first()
    if not showcase:
        return {"enabled": False, "count": 0, "source_site_id": None, "source_site_name": ""}
    count = showcase.tiles.filter(is_active=True).count() if showcase.enabled else 0
    return {
        "enabled": bool(showcase.enabled and count),
        "count": count,
        "source_site_id": showcase.source_site_id,
        "source_site_name": showcase.source_site.name if showcase.source_site_id else "",
        "title": showcase.title,
        "subtitle": "",
        "last_synced_at": showcase.last_synced_at.isoformat() if showcase.last_synced_at else None,
        "menu_untouched": True,
        "section_only": True,
    }
