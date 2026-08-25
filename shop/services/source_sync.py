import ipaddress
import json
import os
import re
import socket
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from django.utils import timezone
from django.utils.text import slugify

from shop.models import Category, Product


class SourceSyncError(Exception):
    pass


def _source_brand_terms():
    raw = os.getenv("SOURCE_BRAND_TERMS", "همراه دوم,hamrahedovom")
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _contains_source_brand(value):
    text = str(value or "").lower()
    return any(term in text for term in _source_brand_terms())


def _clean_spaces(value):
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n-|–—")


def _clean_source_name(value):
    text = _clean_spaces(value)
    if not text:
        return ""
    # Source stores often append a marketing suffix after the actual product title.
    for term in _source_brand_terms():
        text = re.sub(rf"\s*[-|–—]\s*[^\n]*{re.escape(term)}[^\n]*$", "", text, flags=re.I)
        text = re.sub(re.escape(term), "", text, flags=re.I)
    text = re.sub(r"\b(?:فروشگاه|سایت)\b\s*$", "", text, flags=re.I)
    return _clean_spaces(text)


def _clean_source_description(value):
    text = str(value or "").strip()
    if not text:
        return ""
    # Remove source-store promotional sentences/clauses, keep actual product copy.
    parts = re.split(r"(?<=[.!؟\n])\s+|\s+[|–—]\s+", text)
    kept = [part.strip() for part in parts if part.strip() and not _contains_source_brand(part)]
    cleaned = " ".join(kept)
    if not cleaned and not _contains_source_brand(text):
        cleaned = text
    return _clean_spaces(cleaned)


def _allowed_url(url):
    p = urlparse(url)
    allowed = {
        x.strip().lower()
        for x in os.getenv("SOURCE_ALLOWED_HOSTS", "hamrahedovom.ir,www.hamrahedovom.ir").split(",")
        if x.strip()
    }
    if p.scheme not in ("http", "https") or not p.hostname or p.hostname.lower() not in allowed:
        return False
    try:
        for info in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80)):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
    except (socket.gaierror, ValueError):
        return False
    return True


def _digits(value):
    if value is None:
        return 0
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    s = str(value).translate(trans)
    nums = re.findall(r"\d+(?:[.,]\d+)?", s.replace(",", ""))
    if not nums:
        return 0
    try:
        return int(Decimal(nums[0]))
    except InvalidOperation:
        return 0


def _first(*vals):
    for value in vals:
        if value not in (None, "", [], {}):
            return value
    return ""


def _jsonld_objects(soup):
    objects = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            objects.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
    return objects


def _jsonld_products(soup):
    out = []
    for item in _jsonld_objects(soup):
        kind = item.get("@type", "")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(str(x).lower() == "product" for x in kinds):
            out.append(item)
    return out


def _extract_category_names(soup):
    names = []

    def add(name):
        name = _clean_source_name(name)
        if not name or _contains_source_brand(name):
            return
        if name.lower() in {"صفحه اصلی", "خانه", "محصولات", "فروشگاه"}:
            return
        if name not in names:
            names.append(name[:120])

    # Prefer structured breadcrumbs because they are stable and ordered.
    for item in _jsonld_objects(soup):
        kind = item.get("@type", "")
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(str(x).lower() == "breadcrumblist" for x in kinds):
            continue
        for entry in item.get("itemListElement") or []:
            if not isinstance(entry, dict):
                continue
            nested = entry.get("item")
            if isinstance(nested, dict):
                name = nested.get("name") or entry.get("name")
                href = nested.get("@id") or nested.get("url") or ""
            else:
                name = entry.get("name")
                href = nested if isinstance(nested, str) else ""
            if "/product-category/" in str(href).lower():
                add(name)

    if names:
        return names[:8]

    selectors = [
        ".breadcrumb a[href*='/product-category/']",
        ".breadcrumbs a[href*='/product-category/']",
        "[class*='breadcrumb'] a[href*='/product-category/']",
        "nav[aria-label*='breadcrumb' i] a[href*='/product-category/']",
    ]
    for selector in selectors:
        for anchor in soup.select(selector):
            add(anchor.get_text(" ", strip=True))
        if names:
            break
    return names[:8]


def _bad_gallery_image(src, img=None):
    if not src:
        return True
    parsed = urlparse(src)
    path = (parsed.path or "").lower()
    if path.endswith(".svg") or src.startswith("data:"):
        return True
    bad_tokens = (
        "logo", "enamad", "namad", "samandehi", "payment", "bank", "support",
        "banner", "badge", "avatar", "icon", "telegram", "instagram", "whatsapp",
        "aparat", "trust", "free-delivery", "free_shipping", "placeholder",
    )
    if any(token in path for token in bad_tokens):
        return True
    if img is not None:
        hint = " ".join([
            str(img.get("alt") or ""),
            str(img.get("title") or ""),
            " ".join(img.get("class") or []),
        ]).lower()
        if _contains_source_brand(hint) or any(token in hint for token in bad_tokens):
            return True
        width = _digits(img.get("width"))
        height = _digits(img.get("height"))
        if width and height and max(width, height) < 180:
            return True
    return False


def _gallery_candidate(img, base_url):
    src = (
        img.get("data-large_image")
        or img.get("data-zoom-image")
        or img.get("data-src")
        or img.get("data-lazy-src")
        or img.get("src")
    )
    parent = img.find_parent("a")
    if parent and parent.get("href") and re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", parent.get("href"), re.I):
        src = parent.get("href")
    if not src:
        return ""
    src = urljoin(base_url, src)
    return "" if _bad_gallery_image(src, img) else src


def _extract_gallery(soup, base_url, structured_image):
    gallery = []

    def add(value, img=None):
        if isinstance(value, dict):
            value = value.get("url") or value.get("contentUrl") or value.get("@id")
        if not value:
            return
        src = urljoin(base_url, str(value))
        if _bad_gallery_image(src, img):
            return
        if src not in gallery:
            gallery.append(src)

    if isinstance(structured_image, list):
        for item in structured_image:
            add(item)
    else:
        add(structured_image)

    selectors = [
        ".woocommerce-product-gallery__image img",
        ".woocommerce-product-gallery img",
        ".product-gallery img",
        ".product-images img",
        "[class*='product-gallery'] img",
        "[class*='ProductGallery'] img",
        "[data-product-gallery] img",
        "[data-fancybox] img",
        "figure.product img",
        "img[itemprop='image']",
    ]
    for selector in selectors:
        found = soup.select(selector)
        if not found:
            continue
        for img in found[:20]:
            candidate = _gallery_candidate(img, base_url)
            if candidate:
                add(candidate, img)
        # Once a real gallery selector matched, don't scan broad fallbacks.
        if len(gallery) > 1 or found:
            break

    return gallery[:12]


def _unique_category_slug(name):
    base = slugify(name, allow_unicode=True)[:110] or "category"
    slug = base
    i = 2
    while Category.objects.filter(slug=slug).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


def sync_category_path(names):
    parent = None
    for raw in names or []:
        name = _clean_source_name(raw)[:120]
        if not name:
            continue
        category = Category.objects.filter(parent=parent, name=name).first()
        if not category:
            category = Category.objects.create(
                parent=parent,
                name=name,
                slug=_unique_category_slug(name),
                is_active=True,
            )
        elif not category.is_active:
            category.is_active = True
            category.save(update_fields=["is_active"])
        parent = category
    return parent


def scrape_product(url):
    if not _allowed_url(url):
        raise SourceSyncError("دامنه لینک در فهرست منابع مجاز نیست.")

    headers = {
        "User-Agent": os.getenv("SOURCE_USER_AGENT", "DeltaJanebiSync/1.0"),
        "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.5",
    }
    timeout = float(os.getenv("SOURCE_REQUEST_TIMEOUT", "20"))
    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    if r.status_code >= 400:
        raise SourceSyncError(f"خطای منبع: HTTP {r.status_code}")
    if not _allowed_url(r.url):
        raise SourceSyncError("ریدایرکت به دامنه غیرمجاز انجام شد.")

    soup = BeautifulSoup(r.text, "lxml")
    ld = (_jsonld_products(soup) or [{}])[0]
    offers = ld.get("offers") or {}
    offers = offers[0] if isinstance(offers, list) and offers else offers

    def meta(*keys):
        for key in keys:
            tag = (
                soup.find("meta", property=key)
                or soup.find("meta", attrs={"name": key})
                or soup.find("meta", attrs={"itemprop": key})
            )
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    raw_name = _first(
        ld.get("name"),
        meta("og:title", "twitter:title"),
        soup.title.string.strip() if soup.title and soup.title.string else "",
    )
    name = _clean_source_name(raw_name)
    desc = _clean_source_description(_first(ld.get("description"), meta("og:description", "description")))

    structured_image = ld.get("image") or meta("og:image", "twitter:image")
    gallery = _extract_gallery(soup, r.url, structured_image)
    image = gallery[0] if gallery else ""

    price = _digits(
        _first(
            offers.get("price") if isinstance(offers, dict) else "",
            meta("product:price:amount", "og:price:amount"),
        )
    )
    currency = str(
        _first(
            offers.get("priceCurrency") if isinstance(offers, dict) else "",
            meta("product:price:currency"),
        )
    ).upper()
    if currency == "IRR" and price:
        price //= 10

    availability = str(
        _first(offers.get("availability") if isinstance(offers, dict) else "", "")
    ).lower()
    text = soup.get_text(" ", strip=True)
    stock = 0
    for node in [
        soup.select_one("[data-stock]"),
        soup.select_one("[data-quantity]"),
        soup.select_one("input[name=quantity][max]"),
    ]:
        if node:
            stock = _digits(node.get("data-stock") or node.get("data-quantity") or node.get("max"))
            if stock:
                break
    if not stock:
        for pat in [
            r"(?:موجودی|تعداد موجود)\s*[:：]?\s*([۰-۹0-9,]+)",
            r"([۰-۹0-9,]+)\s*(?:عدد|عدد موجود)",
        ]:
            match = re.search(pat, text)
            if match:
                stock = _digits(match.group(1))
                break
    if not stock and ("instock" in availability or re.search(r"\bموجود\b", text)) and not re.search(
        r"ناموجود|اتمام موجودی", text
    ):
        stock = 1

    specs = {}
    for row in soup.select("table tr")[:80]:
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
        if len(cells) >= 2 and cells[0] and cells[1]:
            if not _contains_source_brand(cells[0]) and not _contains_source_brand(cells[1]):
                specs[_clean_spaces(cells[0])[:100]] = _clean_spaces(cells[1])[:300]
    for item in soup.select(".specification, .spec-row, [class*=attribute]")[:80]:
        parts = [x.strip() for x in item.stripped_strings]
        if len(parts) >= 2:
            key = _clean_spaces(parts[0])[:100]
            value = _clean_spaces(" ".join(parts[1:]))[:300]
            if key and value and not _contains_source_brand(key) and not _contains_source_brand(value):
                specs.setdefault(key, value)

    sku = str(_first(ld.get("sku"), ld.get("mpn"), ""))
    if not sku:
        match = re.search(r"/(BKP-\d+)/", r.url, re.I)
        sku = match.group(1).upper() if match else ""

    categories = _extract_category_names(soup)

    if not name or not price:
        raise SourceSyncError("نام یا قیمت محصول از صفحه قابل استخراج نبود؛ پارسر سایت منبع باید تنظیم شود.")

    return {
        "name": name[:300],
        "description": desc[:10000],
        "price": price,
        "stock": max(0, stock),
        "image_url": image,
        "gallery": gallery,
        "specs": specs,
        "sku": sku,
        "categories": categories,
        "source_url": r.url,
    }


def sync_product(product, raise_errors=False):
    try:
        data = scrape_product(product.source_url)
        product.name = data["name"] or product.name
        product.description = data["description"] or product.description
        product.source_price = data["price"]
        product.price = product.apply_markup(data["price"])
        product.stock = data["stock"]
        product.image_url = data["image_url"] or product.image_url
        product.gallery = data["gallery"] or ([product.image_url] if product.image_url else [])
        product.specs = data["specs"] or product.specs
        category = sync_category_path(data.get("categories") or [])
        if category:
            product.category = category
        if data["sku"] and not Product.objects.exclude(pk=product.pk).filter(sku=data["sku"]).exists():
            product.sku = data["sku"]
        product.last_synced_at = timezone.now()
        product.sync_error = ""
        product.save()
        return product
    except Exception as exc:
        product.sync_error = str(exc)[:2000]
        product.last_synced_at = timezone.now()
        product.save(update_fields=["sync_error", "last_synced_at"])
        if raise_errors:
            raise
        return product


def sync_all():
    delay = float(os.getenv("SOURCE_SYNC_DELAY", "1.2"))
    for product in Product.objects.filter(source_type=Product.SYNCED, is_active=True).exclude(source_url=""):
        sync_product(product)
        if delay:
            time.sleep(delay)
