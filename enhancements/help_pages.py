import re
import uuid

from django.utils.text import slugify

from shop.models import SiteSetting

from .models import HelpPage


DEFAULT_HELP_PAGES = [
    ("rules", "قوانین و مقررات", 10),
    ("returns", "رویه بازگشت کالا", 20),
    ("buying-guide", "راهنمای خرید", 30),
]


def ensure_default_help_pages():
    """Create the three built-ins once and preserve the old SiteSetting terms text."""
    old_terms = ""
    try:
        old_terms = (SiteSetting.load().terms_text or "").strip()
    except Exception:
        old_terms = ""

    rows = []
    for slug, title, order in DEFAULT_HELP_PAGES:
        defaults = {
            "title": title,
            "sort_order": order,
            "is_visible": True,
            "is_builtin": True,
        }
        if slug == "rules" and old_terms:
            defaults["content"] = old_terms
        item, created = HelpPage.objects.get_or_create(slug=slug, defaults=defaults)
        changed = []
        if not item.is_builtin:
            item.is_builtin = True
            changed.append("is_builtin")
        if created:
            pass
        elif slug == "rules" and old_terms and not (item.content or "").strip():
            item.content = old_terms
            changed.append("content")
        if changed:
            item.save(update_fields=[*changed, "updated_at"])
        rows.append(item)
    return rows


def help_page_row(item):
    return {
        "id": item.id,
        "slug": item.slug,
        "title": item.title,
        "content": item.content or "",
        "is_visible": bool(item.is_visible),
        "is_builtin": bool(item.is_builtin),
        "sort_order": int(item.sort_order or 0),
        "has_content": bool((item.content or "").strip()),
    }


def make_help_slug(title):
    base = slugify(str(title or "").strip(), allow_unicode=False).strip("-")[:90]
    if not base:
        base = "page-" + uuid.uuid4().hex[:8]
    base = re.sub(r"[^a-z0-9-]+", "-", base).strip("-") or "page"
    candidate = base
    i = 2
    while HelpPage.objects.filter(slug=candidate).exists():
        candidate = f"{base[:100]}-{i}"
        i += 1
    return candidate[:120]
