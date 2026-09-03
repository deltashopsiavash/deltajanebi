"""Strict source image/text sanitizer for Delta catalog v24.

Source product photos are never trusted merely because their URL came from a
Product schema. Every candidate is downloaded with a real wall-clock deadline,
accepted only when it looks like studio product imagery, stripped of peripheral
watermark/logo components, stored locally as WEBP, and otherwise dropped rather
than falling back to an unclean source image. Oversized compressed images are
rejected before RGB expansion so one product cannot OOM-kill the sync worker.
"""
import hashlib
import io
import os
import re
import time
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageOps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from shop.services import source_sanitizer as old
from shop.source_registry import registered_source_for_url

CLEANUP_VERSION = "5"
MAX_GALLERY_IMAGES = max(1, min(int(os.getenv("DELTA_SOURCE_MAX_CLEAN_IMAGES", "6")), 10))
THUMB_MAX = 420
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
        "User-Agent": "DeltaJanebiImageCleaner/2.0",
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


def _box_intersection(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0
    return (x2 - x1) * (y2 - y1)


def _strict_studio_clean(image):
    """Return a local-safe product canvas or None for ambiguous/unsafe imagery."""
    width, height = image.size
    if width < 280 or height < 280:
        return None
    # A small compressed JPEG can expand to hundreds of MB in RGB. Check the
    # header dimensions while Pillow is still lazy, before exif transpose/convert
    # allocates full pixel buffers and possibly gets the detached worker OOM-killed.
    if width * height > MAX_DECODE_PIXELS or max(width, height) > MAX_DECODE_DIMENSION:
        return None

    original = ImageOps.exif_transpose(image).convert("RGB")
    ratio = max(original.width, original.height) / max(1, min(original.width, original.height))
    if ratio > 1.85:
        return None

    thumb = original.copy()
    thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
    w, h = thumb.size
    background = old._median_background(thumb)
    avg = sum(background) / 3
    spread = max(background) - min(background)
    if avg < 205 or spread > 42:
        return None

    comps = old._components(old._foreground_mask(thumb, background, threshold=30))
    if not comps:
        return None
    max_area = comps[0]["area"]
    if max_area < w * h * 0.018:
        return None

    majors = []
    for comp in comps:
        bw = comp["x2"] - comp["x1"] + 1
        bh = comp["y2"] - comp["y1"] + 1
        if comp["area"] >= max_area * 0.16 and bw < w * 0.94 and bh < h * 0.96:
            majors.append(comp)
    if not majors:
        return None

    x1 = min(c["x1"] for c in majors)
    y1 = min(c["y1"] for c in majors)
    x2 = max(c["x2"] for c in majors)
    y2 = max(c["y2"] for c in majors)
    union_w, union_h = x2 - x1 + 1, y2 - y1 + 1
    if union_w < w * 0.18 or union_h < h * 0.18:
        return None

    subject_ratio = (union_w * union_h) / max(1, w * h)
    if subject_ratio > 0.91:
        return None

    scale_x = original.width / w
    scale_y = original.height / h
    work = original.copy()
    draw = ImageDraw.Draw(work)
    major_boxes = [(c["x1"], c["y1"], c["x2"] + 1, c["y2"] + 1) for c in majors]

    for comp in comps:
        if comp in majors:
            continue
        if comp["area"] < max(10, max_area * 0.0015) or comp["area"] > max_area * 0.30:
            continue
        cx = (comp["x1"] + comp["x2"]) / 2
        cy = (comp["y1"] + comp["y2"]) / 2
        outer = cx < w * 0.24 or cx > w * 0.76 or cy < h * 0.24 or cy > h * 0.76
        if not outer:
            continue
        box = (comp["x1"], comp["y1"], comp["x2"] + 1, comp["y2"] + 1)
        overlap = max((_box_intersection(box, major) for major in major_boxes), default=0)
        if overlap > comp["area"] * 0.35:
            continue
        bx1 = max(0, int((comp["x1"] - 5) * scale_x))
        by1 = max(0, int((comp["y1"] - 5) * scale_y))
        bx2 = min(original.width, int((comp["x2"] + 6) * scale_x))
        by2 = min(original.height, int((comp["y2"] + 6) * scale_y))
        draw.rectangle((bx1, by1, bx2, by2), fill=background)

    pad_x = max(4, int(union_w * 0.075))
    pad_y = max(4, int(union_h * 0.075))
    crop = (
        max(0, int((x1 - pad_x) * scale_x)),
        max(0, int((y1 - pad_y) * scale_y)),
        min(original.width, int((x2 + pad_x + 1) * scale_x)),
        min(original.height, int((y2 + pad_y + 1) * scale_y)),
    )
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        return None
    cropped = work.crop(crop)
    if max(cropped.size) > 1800:
        cropped.thumbnail((1800, 1800), Image.Resampling.LANCZOS)

    pad = max(20, int(max(cropped.size) * 0.07))
    side = max(cropped.size) + 2 * pad
    canvas = Image.new("RGB", (side, side), background)
    canvas.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    return canvas


def _clean_image_url(url, site):
    if not url or _suspicious_url(url, site):
        return ""
    key = hashlib.sha256(
        f"{CLEANUP_VERSION}|{site.id}|{site.brand_terms}|{url}".encode("utf-8")
    ).hexdigest()[:32]
    path = f"products/clean-v23/{site.id}/{key}.webp"
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
    cleaned.save(output, "WEBP", quality=93, method=5)
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
        cleaned["image_url"] = ""
        cleaned["gallery"] = []
        cleaned["image_rejected"] = True
    else:
        cleaned["image_rejected"] = False
    return cleaned
