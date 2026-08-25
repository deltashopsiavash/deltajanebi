import ipaddress
import os
import socket
from urllib.parse import urlparse

from .models import SourceSite


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


def _validate_public_host(hostname, port):
    try:
        infos = socket.getaddrinfo(hostname, port)
    except socket.gaierror as exc:
        raise ValueError("دامنه قابل دسترسی/Resolve نیست.") from exc
    if not infos:
        raise ValueError("دامنه قابل Resolve نیست.")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            raise ValueError("IP دامنه معتبر نیست.")
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError("این دامنه به شبکه داخلی یا IP غیرمجاز اشاره می‌کند.")
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
    allowed.update(
        canonical_hostname(x)
        for x in os.getenv("SOURCE_ALLOWED_HOSTS", "hamrahedovom.ir,www.hamrahedovom.ir").split(",")
        if x.strip()
    )
    if hostname not in allowed:
        return False
    try:
        _validate_public_host(original_hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except ValueError:
        return False
    return True


def source_brand_terms():
    terms = []
    for item in SourceSite.objects.filter(is_active=True).only("name", "hostname", "brand_terms"):
        candidates = [item.name, item.hostname, item.hostname.split(".")[0]]
        candidates.extend(x.strip() for x in (item.brand_terms or "").split(",") if x.strip())
        for candidate in candidates:
            value = str(candidate or "").strip().lower()
            if value and value not in terms:
                terms.append(value)
    for value in os.getenv("SOURCE_BRAND_TERMS", "همراه دوم,hamrahedovom").split(","):
        value = value.strip().lower()
        if value and value not in terms:
            terms.append(value)
    return terms
