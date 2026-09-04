import ipaddress
import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlparse

from django.utils import timezone

from .models import Product, SourceSite

_ACTIVE_SOURCE_HOST = ContextVar("delta_active_source_host", default="")
_DNS_CACHE = {}
_DNS_CACHE_LOCK = threading.Lock()
_DNS_TIMEOUT = max(1.0, min(float(os.getenv("DELTA_SOURCE_DNS_TIMEOUT", "4")), 15.0))
_DNS_CACHE_SECONDS = max(10.0, min(float(os.getenv("DELTA_SOURCE_DNS_CACHE_SECONDS", "60")), 600.0))


def canonical_hostname(value):
    hostname = str(value or "").lower().strip().strip(".")
    return hostname[4:] if hostname.startswith("www.") else hostname


def normalize_site_url(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("آدرس سایت خالی است.")
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("آدرس سایت معتبر نیست.")
    original_hostname = parsed.hostname.lower().strip(".")
    hostname = canonical_hostname(original_hostname)
    _validate_public_host(original_hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    base_url = f"{parsed.scheme}://{hostname}"
    if parsed.port:
        base_url += f":{parsed.port}"
    return base_url, hostname


def _resolve_with_deadline(hostname, port):
    """Run libc DNS in a daemon thread so a wedged resolver cannot block Django."""
    box = {}
    done = threading.Event()

    def worker():
        try:
            box["infos"] = socket.getaddrinfo(hostname, port)
        except Exception as exc:
            box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=worker, name="delta-source-dns", daemon=True)
    thread.start()
    if not done.wait(_DNS_TIMEOUT):
        raise ValueError(f"زمان Resolve دامنه پس از {_DNS_TIMEOUT:g} ثانیه تمام شد.")
    if box.get("error"):
        raise ValueError("دامنه قابل دسترسی/Resolve نیست.") from box["error"]
    return box.get("infos") or []


def _validate_public_host(hostname, port):
    key = (str(hostname or "").lower().strip("."), int(port))
    now = time.monotonic()
    with _DNS_CACHE_LOCK:
        cached = _DNS_CACHE.get(key)
        if cached and cached[0] > now:
            if cached[1]:
                return True
            raise ValueError(cached[2])

    try:
        infos = _resolve_with_deadline(key[0], key[1])
        if not infos:
            raise ValueError("دامنه قابل Resolve نیست.")
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError as exc:
                raise ValueError("IP دامنه معتبر نیست.") from exc
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise ValueError("این دامنه به شبکه داخلی یا IP غیرمجاز اشاره می‌کند.")
    except ValueError as exc:
        # Negative results are cached briefly to avoid a DNS storm while still
        # allowing a recently-fixed source to recover quickly.
        with _DNS_CACHE_LOCK:
            _DNS_CACHE[key] = (now + min(15.0, _DNS_CACHE_SECONDS), False, str(exc))
        raise

    with _DNS_CACHE_LOCK:
        _DNS_CACHE[key] = (now + _DNS_CACHE_SECONDS, True, "")
    return True


def registered_source_for_url(url, active_only=True):
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    hostname = canonical_hostname(parsed.hostname)
    qs = SourceSite.objects.all()
    if active_only:
        qs = qs.filter(is_active=True)
    return qs.filter(hostname=hostname).first()


def allowed_url(url):
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    original_hostname = parsed.hostname.lower().strip(".")
    hostname = canonical_hostname(original_hostname)
    allowed = {canonical_hostname(x) for x in SourceSite.objects.filter(is_active=True).values_list("hostname", flat=True)}
    if hostname not in allowed:
        return False
    try:
        _validate_public_host(original_hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except ValueError:
        return False
    return True


@contextmanager
def source_context(url):
    parsed = urlparse(str(url or ""))
    hostname = canonical_hostname(parsed.hostname) if parsed.hostname else ""
    token = _ACTIVE_SOURCE_HOST.set(hostname)
    try:
        yield
    finally:
        _ACTIVE_SOURCE_HOST.reset(token)


def source_brand_terms():
    hostname = _ACTIVE_SOURCE_HOST.get()
    if not hostname:
        return []
    item = SourceSite.objects.filter(hostname=hostname).only("name", "hostname", "brand_terms").first()
    if not item:
        return []
    candidates = [item.name, item.hostname, item.hostname.split(".")[0]]
    candidates.extend(x.strip() for x in (item.brand_terms or "").split(",") if x.strip())
    terms = []
    for candidate in candidates:
        value = str(candidate or "").strip().lower()
        if value and value not in terms:
            terms.append(value)
    return terms


def generic_category_names(soup):
    names = []
    bad_names = {"خانه", "صفحه اصلی", "فروشگاه", "محصولات", "home", "shop", "products"}

    def add(value):
        text = " ".join(str(value or "").split()).strip(" -|–—")
        if not text or text.lower() in bad_names or len(text) > 120:
            return
        lower = text.lower()
        if any(term in lower for term in source_brand_terms()):
            return
        if text not in names:
            names.append(text)

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
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
            kind = item.get("@type", "")
            kinds = kind if isinstance(kind, list) else [kind]
            if not any(str(x).lower() == "breadcrumblist" for x in kinds):
                continue
            entries = item.get("itemListElement") or []
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                nested = entry.get("item")
                if isinstance(nested, dict):
                    name = nested.get("name") or entry.get("name")
                    href = nested.get("@id") or nested.get("url") or ""
                else:
                    name = entry.get("name")
                    href = nested if isinstance(nested, str) else ""
                href_lower = str(href).lower()
                is_last = index == len(entries) - 1
                if is_last and not any(token in href_lower for token in ("category", "/cat/", "/collection/")):
                    continue
                add(name)

    if names:
        return names[:8]

    selectors = [
        ".breadcrumb a",
        ".breadcrumbs a",
        "[class*='breadcrumb'] a",
        "nav[aria-label*='breadcrumb' i] a",
        "[itemtype*='BreadcrumbList'] a",
    ]
    for selector in selectors:
        anchors = soup.select(selector)
        if not anchors:
            continue
        for anchor in anchors:
            href = str(anchor.get("href") or "").lower()
            text = anchor.get_text(" ", strip=True)
            if any(token in href for token in ("/product/", "/products/", "/p/")) and not any(token in href for token in ("category", "/cat/", "/collection/")):
                continue
            add(text)
        if names:
            break
    return names[:8]


def stable_sync_product(product, raise_errors=False):
    """Sync mutable source data without overwriting the category chosen by the admin."""
    from shop.services.source_sync import scrape_product

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
