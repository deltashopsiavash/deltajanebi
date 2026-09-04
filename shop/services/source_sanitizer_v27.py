"""Final strict image-cleaning patch for Delta catalog v27.

The v22/v27 cleaner already rejects promotional URLs, borders and detached
components. This patch removes the one remaining failure mode: its legacy
foreground mask dilated pixels before connected-component analysis, which could
bridge a nearby logo/ad to the product itself. We segment on an undilated mask,
then only dilate the already-selected product silhouette for antialiasing.
"""
from PIL import Image, ImageFilter, ImageOps

from shop.services import source_sanitizer as old
from shop.services import source_sanitizer_v22 as base


CLEANUP_VERSION = "8"


def _strict_foreground_mask(image, background, threshold=28):
    """Build foreground without pre-dilation so close ads stay disconnected."""
    w, h = image.size
    bg_r, bg_g, bg_b = background
    raw = bytearray(w * h)
    limit = threshold * threshold
    for index, (r, g, b) in enumerate(image.getdata()):
        distance_sq = (r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2
        if distance_sq > limit:
            raw[index] = 255
    return Image.frombytes("L", (w, h), bytes(raw))


def _strict_studio_clean(image):
    """Extract only the selected product components onto a fresh white canvas."""
    width, height = image.size
    if width < 280 or height < 280:
        return None
    if width * height > base.MAX_DECODE_PIXELS or max(width, height) > base.MAX_DECODE_DIMENSION:
        return None

    original = ImageOps.exif_transpose(image).convert("RGB")
    ratio = max(original.width, original.height) / max(1, min(original.width, original.height))
    if ratio > 2.15:
        return None

    thumb = original.copy()
    thumb.thumbnail((base.THUMB_MAX, base.THUMB_MAX), Image.Resampling.LANCZOS)
    w, h = thumb.size
    background = old._median_background(thumb)
    avg = sum(background) / 3
    spread = max(background) - min(background)
    if avg < 195 or spread > 55:
        return None

    # Critical difference from the previous cleaner: do NOT MaxFilter/dilate
    # before connected-component analysis. A logo 2-5 px from the product must
    # remain a separate component so it can be discarded.
    foreground = _strict_foreground_mask(thumb, background, threshold=28)
    comps = old._components(foreground)
    if not comps:
        return None
    selected = base._choose_subject_components(comps, w, h)
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

    subject_mask = base._subject_component_mask(foreground, selected)
    if not subject_mask.getbbox():
        return None
    subject_mask = base._fill_holes(subject_mask)
    # Edge recovery is safe only AFTER detached components have been removed.
    subject_mask = subject_mask.filter(ImageFilter.MaxFilter(3))

    scale_x = original.width / w
    scale_y = original.height / h
    full_mask = subject_mask.resize(original.size, Image.Resampling.BILINEAR)
    full_mask = full_mask.filter(ImageFilter.GaussianBlur(radius=max(0.55, min(original.size) / 1900)))
    clean = Image.new("RGB", original.size, (255, 255, 255))
    clean.paste(original, (0, 0), full_mask)

    # Keep padding deliberately tight. This removes source-site frames and
    # leaves less room for any advertising that escaped color segmentation.
    pad_x = max(4, int(union_w * 0.055))
    pad_y = max(4, int(union_h * 0.055))
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

    pad = max(20, int(max(cropped.size) * 0.065))
    side = max(cropped.size) + 2 * pad
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(cropped, ((side - cropped.width) // 2, (side - cropped.height) // 2))
    return canvas


# Reuse the battle-tested bounded downloader, URL rejection and payload policy,
# but switch their module globals to this final segmentation implementation.
base.CLEANUP_VERSION = CLEANUP_VERSION
base._strict_studio_clean = _strict_studio_clean
sanitize_scraped_product = base.sanitize_scraped_product
