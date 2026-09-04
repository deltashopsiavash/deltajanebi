"""Strict source image/text sanitizer for Delta catalog v27.

Every source image is treated as untrusted promotional artwork. The cleaner now
keeps only the dominant product subject components, removes every detached logo,
text block, badge and border even when it sits inside the eventual crop, places
the subject on a fresh white square canvas, and rejects ambiguous imagery rather
than retaining advertising. A cleanup-version bump forces old accepted images to
be regenerated on the next sync.
"""
import hashlib
import io
import os
import re
import time
from urllib.parse import urlparse

import requests
from PIL import Image, ImageFilter, ImageOps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from shop.services import source_sanitizer as old
from shop.source_registry import registered_source_for_url

CLEANUP_VERSION = "7"
MAX_GALLERY_IMAGES = max(1, min(int(os.getenv("DELTA_SOURCE_MAX_CLEAN_IMAGES", "6")), 10))
THUMB_MAX = 440
IMAGE_CONNECT_TIMEOUT = max(1.0, min(float(os.getenv("DELTA_SOURCE_IMAGE_CONNECT_TIMEOUT", "3")), 10.0))
IMAGE_READ_TIMEOUT = max(1.0, min(float(os.getenv("DELTA_SOURCE_IMAGE_READ_TIMEOUT", "3")), 10.0))
IMAGE_WALL_TIMEOUT = max(2.0, min(float(os.getenv("DELTA_SOURCE_IMAGE_WALL_TIMEOUT", "4.5")), 20.0))
MAX_DECODE_PIXELS = max(2_000_000, min(int(os.getenv("DELTA_SOURCE_IMAGE_MAX_PIXELS", "12000000")), 30_000_000))
MAX_DECODE_DIMENSION = max(1800, min(int(os.getenv("DELTA_SOURCE_IMAGE_MAX_DIMENSION", "5500")), 9000))

_PROMO_TOKENS = (
    "logo", "watermark", "brandmark", "banner", "slider", "slide", "advert", "ads-", "-ads",
    "campaign", "promo", "promotion", "offer", "discount", "coupon", "sale-banner", "story",
    "enamad", "namad", "samandehi", "badge", "trust", "telegram", "instagram", "whatsapp",
    "aparat", "social", "free-shipping", "free_shipping", "delivery-banner", "header", "footer",
)


def _site_terms(site):
    return [x.casefold() for x in old._terms_for_site(site) if str(x or "").strip()]


def _suspicious_url(url, site):
    parsed = urlparse(str(url or ""))
    hay = f"{parsed.path} {parsed.query}".casefold()
    if any(token in hay for token in _PROMO_TOKENS):
        return True
    compact = re.sub(r"[^a-z0-9آ-ی]+", "", hay)
    for term in _site_terms(site):
        normalized = re.sub(r"[^a-z0-9آ-ی]+", "", term)
        if len(normalized) >= 4 and normalized in compact:
            return True
    return False


def _download_image_bounded(url):
    if not old._validate_public_image_url(url):
        return None

    response = None
    deadline = time.monotonic() + IMAGE_WALL_TIMEOUT
    headers = {
        "User-Agent": "DeltaJanebiImageCleaner/2.7",
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Connection": "close",
    }
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=(IMAGE_CONNECT_TIMEOUT, IMAGE_READ_TIMEOUT),
            stream=True,
            allow_redirects=True,
        )
        if response.status_code >= 400 or not old._validate_public_image_url(response.url):
            return None
        ctype = (response.headers.get("Content-Type") or "").lower()
        if ctype and not ctype.startswith("image/"):
            return None
        try:
            declared = int(response.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > old.MAX_IMAGE_BYTES:
            return None

        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if time.monotonic() >= deadline:
                return None
            if not chunk:
                continue
            total += len(chunk)
            if total > old.MAX_IMAGE_BYTES:
                return None
            chunks.append(chunk)
        if time.monotonic() >= deadline:
            return None
        return b"".join(chunks)
    except requests.RequestException:
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _component_center(comp):
    return ((comp["x1"] + comp["x2"]) / 2.0, (comp["y1"] + comp["y2"]) / 2.0)


def _component_score(comp, w, h):
    cx, cy = _component_center(comp)
    nx = abs(cx / max(1, w) - 0.5)
    ny = abs(cy / max(1, h) - 0.5)
    distance = min(0.9, (nx * nx + ny * ny) ** 0.5)
    bw = comp["x2"] - comp["x1"] + 1
    bh = comp["y2"] - comp["y1"] + 1
    box_ratio = (bw * bh) / max(1, w * h)
    center_bonus = 1.45 - distance
    border_penalty = 0.70 if (cx < w * 0.16 or cx > w * 0.84 or cy < h * 0.16 or cy > h * 0.84) else 1.0
    huge_penalty = 0.60 if box_ratio > 0.76 else 1.0
    return float(comp["area"]) * center_bonus * border_penalty * huge_penalty


def _box_gap(a, b):
    dx = max(0, max(a["x1"], b["x1"]) - min(a["x2"], b["x2"]) - 1)
    dy = max(0, max(a["y1"], b["y1"]) - min(a["y2"], b["y2"]) - 1)
    return (dx * dx + dy * dy) ** 0.5


def _choose_subject_components(comps, w, h):
    """Choose the central product cluster, never the union of all big artwork."""
    candidates = []
    for comp in comps:
        bw = comp["x2"] - comp["x1"] + 1
        bh = comp["y2"] - comp["y1"] + 1
        if comp["area"] < w * h * 0.006:
            continue
        # Full-width/full-height components are commonly borders or banners.
        if bw >= w * 0.94 or bh >= h * 0.94:
            continue
        candidates.append(comp)
    if not candidates:
        return []

    primary = max(candidates, key=lambda comp: _component_score(comp, w, h))
    if primary["area"] < w * h * 0.015:
        return []

    selected = [primary]
    primary_area = max(1, primary["area"])
    near_limit = max(8.0, max(w, h) * 0.11)
    pcx, pcy = _component_center(primary)
    for comp in candidates:
        if comp is primary:
            continue
        ratio = comp["area"] / primary_area
        if ratio < 0.075:
            continue
        bw = comp["x2"] - comp["x1"] + 1
        bh = comp["y2"] - comp["y1"] + 1
        aspect = max(bw, bh) / max(1, min(bw, bh))
        # Long detached strips are usually advertising text/ribbons. A long
        # cable can still be the primary component and is therefore preserved.
        if aspect > 6.0 and ratio < 0.45:
            continue
        cx, cy = _component_center(comp)
        center_distance = (((cx - pcx) / max(1, w)) ** 2 + ((cy - pcy) / max(1, h)) ** 2) ** 0.5
        if _box_gap(primary, comp) <= near_limit or (ratio >= 0.32 and center_distance <= 0.34):
            selected.append(comp)
    return selected


def _subject_component_mask(mask, selected):
    """Return pixels belonging only to selected connected components.

    This is stricter than cropping their union box: detached logos/text inside
    that rectangle remain separate components and are therefore blanked too.
    """
    w, h = mask.size
    data = bytearray(mask.tobytes())
    margin = max(2, int(min(w, h) * 0.03))
    for y in range(h):
        for x in range(w):
            if y < margin or y >= h - margin or x < margin or x >= w - margin:
                data[y * w + x] = 0

    wanted = {
        (int(c["area"]), int(c["x1"]), int(c["y1"]), int(c["x2"]), int(c["y2"]))
        for c in selected
    }
    seen = bytearray(w * h)
    output = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            start = y * w + x
            if not data[start] or seen[start]:
                continue
            stack = [(x, y)]
            seen[start] = 1
            pixels = []
            area = 0
            x1 = x2 = x
            y1 = y2 = y
            while stack:
                cx, cy = stack.pop()
                pos = cy * w + cx
                pixels.append(pos)
                area += 1
                x1, x2 = min(x1, cx), max(x2, cx)
                y1, y2 = min(y1, cy), max(y2, cy)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = cx + dx, cy + dy
                        if nx < 0 or ny < 0 or nx >= w or ny >= h:
                            continue
                        npos = ny * w + nx
                        if data[npos] and not seen[npos]:
                            seen[npos] = 1
                            stack.append((nx, ny))
            if (area, x1, y1, x2, y2) in wanted:
                for pos in pixels:
                    output[pos] = 255
    return Image.frombytes("L", (w, h), bytes(output))


def _fill_holes(mask):
    """Fill background-colored holes inside a kept product silhouette."""
    w, h = mask.size
    raw = bytearray(mask.tobytes())
    outside = bytearray(w * h)
    stack = []
    for x in range(w):
        stack.extend([(x, 0), (x, h - 1)])
    for y in range(h):
        stack.extend([(0, y), (w - 1, y)])
    while stack:
        x, y = stack.pop()
        pos = y * w + x
        if outside[pos] or raw[pos]:
            continue
        outside[pos] = 1
        if x > 0:
            stack.append((x - 1, y))
        if x + 1 < w:
            stack.append((x + 1, y))
        if y > 0:
            stack.append((x, y - 1))
        if y + 1 < h:
            stack.append((x, y + 1))
    for pos in range(w * h):
        if not raw[pos] and not outside[pos]:
            raw[pos] = 255
    return Image.frombytes("L", (w, h), bytes(raw))


def _strict_studio_clean(image):
    """Extract the product itself onto a new white canvas or reject the image."""
    width, height = image.size
    if width < 280 or height < 280:
        return None
    if width * height > MAX_DECODE_PIXELS or max(width, height) > MAX_DECODE_DIMENSION:
        return None

    original = ImageOps.exif_transpose(image).convert("RGB")
    ratio = max(original.width, original.height) / max(1, min(original.width, original.height))
    if ratio > 2.15:
        return None

    thumb = original.copy()
    thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
    w, h = thumb.size
    background = old._median_background(thumb)
    avg = sum(background) / 3
    spread = max(background) - min(background)
    # Only images with a reasonably clean/light outer background are suitable
    # for deterministic ad removal. Ambiguous artwork is safer to drop.
    if avg < 195 or spread > 55:
        return None

    foreground = old._foreground_mask(thumb, background, threshold=28)
    comps = old._components(foreground)
    if not comps:
        return None
    selected = _choose_subject_components(comps, w, h)
    if not selected:
        return None

    x1 = min(c["x1"] for c in selected)
    y1 = min(c["y1"] for c in selected)
    x2 = max(c["x2"] for c in selected)
    y2 = max(c["y2"] for c in selected)
    union_w, union_h = x2 - x1 + 1, y2 - y1 + 1
    if union_w < w * 0.13 or union_h < h * 0.13:
        return None
    if (union_w * union_h) / max(1, w * h) > 0.86:
        return None

    subject_mask = _subject_component_mask(foreground, selected)
    if not subject_mask.getbbox():
        return None
    subject_mask = _fill_holes(subject_mask)
    # Recover antialiasing/shadows close to the subject while still excluding
    # all detached advertising components and the outer frame.
    subject_mask = subject_mask.filter(ImageFilter.MaxFilter(5))

    scale_x = original.width / w
    scale_y = original.height / h
    full_mask = subject_mask.resize(original.size, Image.Resampling.BILINEAR)
    full_mask = full_mask.filter(ImageFilter.GaussianBlur(radius=max(0.7, min(original.size) / 1600)))
    clean = Image.new("RGB", original.size, (255, 255, 255))
    clean.paste(original, (0, 0), full_mask)

    pad_x = max(5, int(union_w * 0.075))
    pad_y = max(5, int(union_h * 0.075))
    crop = (
        max(0, int((x1 - pad_x) * scale_x)),
        max(0, int((y1 - pad_y) * scale_y)),
        min(original.width, int((x2 + pad_x + 1) * scale_x)),
        min(original.height, int((y2 + pad_y + 1) * scale_y)),
    )
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        return None
    cropped = clean.crop(crop)
    if max(cropped.size) > 1800:
        cropped.thumbnail((1800, 1800), Image.Resampling.LANCZOS)

    pad = max(22, int(max(cropped.size) * 0.075))
    side = max(cropped.size) + 2 * pad
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    return canvas


def _clean_image_url(url, site):
    if not url or _suspicious_url(url, site):
        return ""
    key = hashlib.sha256(
        f"{CLEANUP_VERSION}|{site.id}|{site.brand_terms}|{url}".encode("utf-8")
    ).hexdigest()[:32]
    path = f"products/clean-v27/{site.id}/{key}.webp"
    if default_storage.exists(path):
        return default_storage.url(path)

    raw = _download_image_bounded(url)
    if not raw:
        return ""
    try:
        with Image.open(io.BytesIO(raw)) as image:
            cleaned = _strict_studio_clean(image)
    except Exception:
        return ""
    if cleaned is None:
        return ""

    output = io.BytesIO()
    cleaned.save(output, "WEBP", quality=94, method=5)
    saved = default_storage.save(path, ContentFile(output.getvalue()))
    return default_storage.url(saved)


def sanitize_scraped_product(data, source_url):
    cleaned = old.sanitize_scraped_text(data, source_url)
    site = registered_source_for_url(source_url, active_only=False)
    if not site:
        return cleaned

    urls = []
    for value in [cleaned.get("image_url"), *(cleaned.get("gallery") or [])]:
        value = str(value or "").strip()
        if value and value not in urls:
            urls.append(value)

    accepted = []
    for url in urls[:MAX_GALLERY_IMAGES]:
        local = _clean_image_url(url, site)
        if local and local not in accepted:
            accepted.append(local)

    if accepted:
        cleaned["image_url"] = accepted[0]
        cleaned["gallery"] = accepted
        cleaned["image_rejected"] = False
    elif urls:
        # Never keep a previous/source ad because cleaning was uncertain.
        cleaned["image_url"] = ""
        cleaned["gallery"] = []
        cleaned["image_rejected"] = True
    else:
        cleaned["image_rejected"] = False
    return cleaned
