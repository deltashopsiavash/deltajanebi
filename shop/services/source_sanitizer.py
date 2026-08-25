import hashlib
import io
import re
import socket
import ipaddress
from pathlib import Path
from statistics import median
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from shop.models import SourceSite
from shop.source_registry import canonical_hostname, registered_source_for_url

CLEANUP_VERSION = "3"
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_GALLERY_IMAGES = 10
THUMB_MAX = 340


def _terms_for_site(site):
    if not site:
        return []
    values = [site.name, site.hostname, site.hostname.split(".")[0]]
    values.extend(x.strip() for x in (site.brand_terms or "").split(",") if x.strip())
    result = []
    for value in values:
        value = str(value or "").strip()
        if value and value.lower() not in [x.lower() for x in result]:
            result.append(value)
    return result


def normalize_brand_terms(value):
    raw = re.split(r"[,،\n|]+", str(value or ""))
    result = []
    for item in raw:
        item = re.sub(r"\s+", " ", item).strip(" \t\r\n-|–—")
        if item and item.lower() not in [x.lower() for x in result]:
            result.append(item[:120])
    return ",".join(result)[:500]


def _strip_terms(value, terms):
    text = str(value or "")
    if not text:
        return ""
    for term in sorted(terms, key=len, reverse=True):
        if not term:
            continue
        text = re.sub(re.escape(term), " ", text, flags=re.I)
    text = re.sub(r"\(\s*\)|\[\s*\]|\{\s*\}", " ", text)
    text = re.sub(r"\s*[-|–—:/]+\s*(?=$)", " ", text)
    text = re.sub(r"(?<=\s)[-|–—:/]+(?=\s)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-|–—:/")
    return text


def sanitize_scraped_text(data, source_url):
    site = registered_source_for_url(source_url, active_only=False)
    if not site:
        return dict(data or {})
    terms = _terms_for_site(site)
    cleaned = dict(data or {})
    cleaned["name"] = _strip_terms(cleaned.get("name", ""), terms)[:300]
    cleaned["description"] = _strip_terms(cleaned.get("description", ""), terms)[:10000]

    specs = {}
    for key, value in (cleaned.get("specs") or {}).items():
        new_key = _strip_terms(key, terms)[:100]
        new_value = _strip_terms(value, terms)[:300]
        if new_key and new_value:
            specs[new_key] = new_value
    cleaned["specs"] = specs

    categories = []
    for value in cleaned.get("categories") or []:
        item = _strip_terms(value, terms)[:120]
        if item and item not in categories:
            categories.append(item)
    cleaned["categories"] = categories
    return cleaned


def _validate_public_image_url(url):
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def _download_image(url):
    if not _validate_public_image_url(url):
        return None
    headers = {"User-Agent": "DeltaJanebiImageCleaner/1.0", "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    try:
        with requests.get(url, headers=headers, timeout=18, stream=True, allow_redirects=True) as response:
            if response.status_code >= 400 or not _validate_public_image_url(response.url):
                return None
            ctype = (response.headers.get("Content-Type") or "").lower()
            if ctype and not ctype.startswith("image/"):
                return None
            chunks, total = [], 0
            for chunk in response.iter_content(65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException:
        return None


def _median_background(image):
    w, h = image.size
    pixels = image.load()
    edge = max(2, min(w, h) // 90)
    samples = []
    step = max(1, min(w, h) // 180)
    for x in range(0, w, step):
        for y in range(edge):
            samples.append(pixels[x, y])
            samples.append(pixels[x, h - 1 - y])
    for y in range(0, h, step):
        for x in range(edge):
            samples.append(pixels[x, y])
            samples.append(pixels[w - 1 - x, y])
    if not samples:
        return (255, 255, 255)
    return tuple(int(median([p[channel] for p in samples])) for channel in range(3))


def _foreground_mask(image, background, threshold=35):
    w, h = image.size
    bg_r, bg_g, bg_b = background
    raw = bytearray(w * h)
    for index, (r, g, b) in enumerate(image.getdata()):
        distance_sq = (r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2
        if distance_sq > threshold * threshold:
            raw[index] = 255
    mask = Image.frombytes("L", (w, h), bytes(raw))
    return mask.filter(ImageFilter.MaxFilter(3))


def _components(mask):
    w, h = mask.size
    data = bytearray(mask.tobytes())
    margin = max(2, int(min(w, h) * 0.03))
    for y in range(h):
        for x in range(w):
            if y < margin or y >= h - margin or x < margin or x >= w - margin:
                data[y * w + x] = 0

    seen = bytearray(w * h)
    comps = []
    for y in range(h):
        for x in range(w):
            start = y * w + x
            if not data[start] or seen[start]:
                continue
            stack = [(x, y)]
            seen[start] = 1
            area = 0
            x1 = x2 = x
            y1 = y2 = y
            while stack:
                cx, cy = stack.pop()
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
                        pos = ny * w + nx
                        if data[pos] and not seen[pos]:
                            seen[pos] = 1
                            stack.append((nx, ny))
            comps.append({"area": area, "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    return sorted(comps, key=lambda c: c["area"], reverse=True)


def _clean_studio_image(image):
    original = ImageOps.exif_transpose(image).convert("RGB")
    if original.width < 300 or original.height < 300:
        return None

    thumb = original.copy()
    thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
    w, h = thumb.size
    background = _median_background(thumb)
    if sum(background) / 3 < 225 or max(background) - min(background) > 28:
        return None

    comps = _components(_foreground_mask(thumb, background))
    if not comps:
        return None
    max_area = comps[0]["area"]
    if max_area < w * h * 0.025:
        return None

    majors = []
    for comp in comps:
        box_w = comp["x2"] - comp["x1"] + 1
        box_h = comp["y2"] - comp["y1"] + 1
        if comp["area"] >= max_area * 0.20 and box_w < w * 0.82 and box_h < h * 0.92:
            majors.append(comp)
    if not majors:
        return None

    x1 = min(c["x1"] for c in majors)
    y1 = min(c["y1"] for c in majors)
    x2 = max(c["x2"] for c in majors)
    y2 = max(c["y2"] for c in majors)
    union_w, union_h = x2 - x1 + 1, y2 - y1 + 1
    if union_w < w * 0.22 or union_h < h * 0.22:
        return None

    pad_x = max(3, int(union_w * 0.04))
    pad_y = max(3, int(union_h * 0.04))
    crop_x1 = max(0, x1 - pad_x)
    crop_y1 = max(0, y1 - pad_y)
    crop_x2 = min(w - 1, x2 + pad_x)
    crop_y2 = min(h - 1, y2 + pad_y)

    scale_x = original.width / w
    scale_y = original.height / h
    work = original.copy()
    draw = ImageDraw.Draw(work)
    blanked = 0
    for comp in comps:
        if comp in majors or comp["area"] < max_area * 0.012:
            continue
        center_x = (comp["x1"] + comp["x2"]) / 2
        center_y = (comp["y1"] + comp["y2"]) / 2
        outside_subject = center_y < y1 or center_y > y2 or center_x < x1 or center_x > x2
        outer_band = center_y < h * 0.28 or center_y > h * 0.78 or center_x < w * 0.18 or center_x > w * 0.82
        if not (outside_subject and outer_band):
            continue
        bx1 = max(0, int((comp["x1"] - 3) * scale_x))
        by1 = max(0, int((comp["y1"] - 3) * scale_y))
        bx2 = min(original.width, int((comp["x2"] + 4) * scale_x))
        by2 = min(original.height, int((comp["y2"] + 4) * scale_y))
        draw.rectangle((bx1, by1, bx2, by2), fill=background)
        blanked += 1

    crop_box = (
        int(crop_x1 * scale_x),
        int(crop_y1 * scale_y),
        min(original.width, int((crop_x2 + 1) * scale_x)),
        min(original.height, int((crop_y2 + 1) * scale_y)),
    )
    removed_ratio = 1 - ((crop_box[2] - crop_box[0]) * (crop_box[3] - crop_box[1])) / (original.width * original.height)
    if blanked == 0 and removed_ratio < 0.08:
        return None

    cropped = work.crop(crop_box)
    if max(cropped.size) > 1800:
        cropped.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
    pad = max(18, int(max(cropped.size) * 0.06))
    side = max(cropped.size) + 2 * pad
    canvas = Image.new("RGB", (side, side), background)
    canvas.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    return canvas


def _clean_image_url(url, site):
    key = hashlib.sha256(f"{CLEANUP_VERSION}|{site.id}|{site.brand_terms}|{url}".encode("utf-8")).hexdigest()[:28]
    path = f"products/clean/{site.id}/{key}.webp"
    if default_storage.exists(path):
        return default_storage.url(path), True

    raw = _download_image(url)
    if not raw:
        return url, False
    try:
        with Image.open(io.BytesIO(raw)) as image:
            cleaned = _clean_studio_image(image)
    except Exception:
        return url, False
    if cleaned is None:
        return url, False

    output = io.BytesIO()
    cleaned.save(output, "WEBP", quality=92, method=4)
    saved = default_storage.save(path, ContentFile(output.getvalue()))
    return default_storage.url(saved), True


def clean_product_gallery(data, source_url):
    site = registered_source_for_url(source_url, active_only=False)
    if not site:
        return dict(data or {})

    cleaned = dict(data or {})
    urls = []
    for value in [cleaned.get("image_url"), *(cleaned.get("gallery") or [])]:
        value = str(value or "").strip()
        if value and value not in urls:
            urls.append(value)
    if not urls:
        return cleaned

    result = []
    for url in urls[:MAX_GALLERY_IMAGES]:
        local_or_original, _ = _clean_image_url(url, site)
        if local_or_original and local_or_original not in result:
            result.append(local_or_original)
    if result:
        cleaned["gallery"] = result
        cleaned["image_url"] = result[0]
    return cleaned


def sanitize_scraped_product(data, source_url):
    cleaned = sanitize_scraped_text(data, source_url)
    return clean_product_gallery(cleaned, source_url)
