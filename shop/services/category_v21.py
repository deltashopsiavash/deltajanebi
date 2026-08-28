import json
import re
import unicodedata
from collections import defaultdict

from django.db import transaction

from shop.models import Category, Product
from shop.source_registry import source_brand_terms


_TRANSLATION = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه",
    "ؤ": "و", "إ": "ا", "أ": "ا",
})
_GENERIC = {
    "خانه", "صفحه اصلی", "فروشگاه", "محصولات", "محصول", "همه محصولات",
    "home", "shop", "products", "product", "catalog", "کاتالوگ",
}


def clean_name(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_TRANSLATION)
    text = text.replace("\u200c", " ").replace("\u200f", " ").replace("\ufeff", " ")
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-|–—>/")
    return text[:120]


def key(value):
    text = clean_name(value).casefold()
    return re.sub(r"[\s\u200c\-_–—|/\\.,،:؛;()\[\]{}]+", "", text)


def _source_terms():
    return [str(x or "").casefold() for x in source_brand_terms() if str(x or "").strip()]


def _good(value, product_title=""):
    text = clean_name(value)
    if not text or text.casefold() in _GENERIC:
        return ""
    lower = text.casefold()
    if any(term and term in lower for term in _source_terms()):
        return ""
    if product_title and key(text) == key(product_title):
        return ""
    return text


def enhanced_category_names(soup):
    """Extract category breadcrumbs across WooCommerce and generic stores.

    Unlike the old parser this does not require `/product-category/` in the URL.
    It accepts JSON-LD BreadcrumbList, Product.category, common breadcrumb
    containers and WooCommerce `posted_in` metadata while excluding the current
    product and source-brand noise.
    """
    names = []
    title_node = soup.select_one("h1.product_title, h1[itemprop='name'], main h1, h1")
    product_title = title_node.get_text(" ", strip=True) if title_node else ""

    def add(value):
        text = _good(value, product_title)
        if text and key(text) not in {key(x) for x in names}:
            names.append(text)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            raw = json.loads(tag.string or "")
        except Exception:
            continue
        stack = raw if isinstance(raw, list) else [raw]
        while stack:
            item = stack.pop(0)
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
            kind = item.get("@type", "")
            kinds = {str(x).casefold() for x in (kind if isinstance(kind, list) else [kind])}
            if "breadcrumblist" in kinds:
                entries = item.get("itemListElement") or []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    nested = entry.get("item")
                    if isinstance(nested, dict):
                        add(nested.get("name") or entry.get("name"))
                    else:
                        add(entry.get("name"))
            if "product" in kinds:
                category = item.get("category")
                values = category if isinstance(category, list) else [category]
                for value in values:
                    if isinstance(value, dict):
                        value = value.get("name")
                    for part in re.split(r"\s*(?:>|/|»|›)\s*", str(value or "")):
                        add(part)

    # Prefer a real breadcrumb chain when present.
    breadcrumb_names = []
    for selector in (
        ".breadcrumb a, .breadcrumb span",
        ".breadcrumbs a, .breadcrumbs span",
        "[class*='breadcrumb'] a, [class*='breadcrumb'] span",
        "nav[aria-label*='breadcrumb' i] a, nav[aria-label*='breadcrumb' i] span",
        "[itemtype*='BreadcrumbList'] a, [itemtype*='BreadcrumbList'] span",
    ):
        nodes = soup.select(selector)
        if not nodes:
            continue
        for node in nodes:
            value = _good(node.get_text(" ", strip=True), product_title)
            if value and key(value) not in {key(x) for x in breadcrumb_names}:
                breadcrumb_names.append(value)
        if breadcrumb_names:
            break
    if breadcrumb_names:
        names = breadcrumb_names

    # WooCommerce category links are useful on themes with no breadcrumb.
    if not names:
        for node in soup.select(".product_meta .posted_in a, .posted_in a, a[rel='tag']"):
            add(node.get_text(" ", strip=True))

    return names[:8]


def infer_top_category(name, specs=None):
    hay = " ".join([str(name or ""), *[f"{k} {v}" for k, v in (specs or {}).items()]]).casefold()
    rules = [
        (("قاب", "کاور", "case", "cover"), "قاب و کاور"),
        (("گلس", "محافظ صفحه", "screen protector"), "محافظ صفحه"),
        (("پاوربانک", "پاور بانک", "powerbank", "power bank"), "پاوربانک"),
        (("ساعت هوشمند", "smartwatch", "smart watch"), "ساعت هوشمند"),
        (("هندزفری", "هدفون", "ایرفون", "ایرباد", "earbud", "headphone", "headset"), "هدفون و هندزفری"),
        (("اسپیکر", "speaker"), "اسپیکر"),
        (("کابل", "cable", "lightning cable", "type-c cable", "usb cable"), "کابل و تبدیل"),
        (("هاب", "hub", "مبدل", "تبدیل", "converter"), "هاب و مبدل"),
        (("شارژر", "آداپتور", "کلگی", "charger", "adapter"), "شارژر و آداپتور"),
        (("هولدر", "پایه نگهدارنده", "holder", "stand"), "هولدر و پایه"),
        (("کارت حافظه", "memory card", "micro sd", "microsd", "فلش", "flash drive"), "حافظه و ذخیره‌سازی"),
        (("تبلت", "tablet"), "تبلت"),
        (("گوشی", "موبایل", "smartphone", "mobile phone"), "گوشی موبایل"),
        (("باتری", "battery"), "باتری"),
    ]
    for needles, label in rules:
        if any(token in hay for token in needles):
            return label
    return ""


def canonical_path(raw_names, product_name="", specs=None):
    cleaned = []
    seen = set()
    for raw in raw_names or []:
        item = _good(raw, product_name)
        k = key(item)
        if item and k and k not in seen:
            cleaned.append(item)
            seen.add(k)

    inferred = infer_top_category(product_name, specs)
    if not inferred:
        return cleaned[:5]

    # A stable Delta top-level category prevents each source site from creating
    # a different root for the same type of product. Keep a useful source leaf
    # underneath it when it adds detail.
    leaf = cleaned[-1] if cleaned else ""
    if leaf and key(leaf) != key(inferred) and leaf.casefold() not in {"لوازم جانبی", "اکسسوری", "accessories"}:
        return [inferred, leaf][:5]
    return [inferred]


def _direct(parent, category_key):
    for item in Category.objects.filter(parent=parent).order_by("id"):
        if key(item.name) == category_key:
            return item
    return None


def sync_category_path(names):
    """Create/reuse categories by *parent + normalized name*, never globally.

    v20 merged equal leaf names across unrelated parents, which could move a
    product into the wrong tree. v21 only reuses a category under the same
    parent. For a single leaf-only breadcrumb it may reuse a sole existing
    match anywhere instead of creating a duplicate root.
    """
    values = []
    seen = set()
    for raw in names or []:
        name = clean_name(raw)
        k = key(name)
        if name and k and k not in seen:
            values.append((name, k))
            seen.add(k)
    if not values:
        return None

    parent = None
    for index, (name, k) in enumerate(values):
        category = _direct(parent, k)
        if category is None and len(values) == 1:
            all_matches = [x for x in Category.objects.all().order_by("id") if key(x.name) == k]
            if len(all_matches) == 1:
                category = all_matches[0]
        if category is None:
            category = Category.objects.create(parent=parent, name=name, slug="", is_active=True)
        elif not category.is_active:
            category.is_active = True
            category.save(update_fields=["is_active"])
        parent = category
    return parent


def _merge_duplicate(duplicate, canonical, stats):
    if duplicate.pk == canonical.pk:
        return
    moved = Product.objects.filter(category_id=duplicate.pk).update(category_id=canonical.pk)
    stats["products_recategorized"] += moved
    for child in list(Category.objects.filter(parent_id=duplicate.pk).order_by("id")):
        collision = _direct(canonical, key(child.name))
        if collision and collision.pk != child.pk:
            _merge_duplicate(child, collision, stats)
        else:
            child.parent = canonical
            child.save(update_fields=["parent"])
    if not canonical.image_url and duplicate.image_url:
        canonical.image_url = duplicate.image_url
        canonical.save(update_fields=["image_url"])
    duplicate.delete()
    stats["categories_merged"] += 1


@transaction.atomic
def consolidate_sibling_duplicates():
    """Merge only true duplicates under the same parent, preserving branches."""
    stats = {"categories_merged": 0, "products_recategorized": 0}
    groups = defaultdict(list)
    for item in Category.objects.select_related("parent").order_by("id"):
        groups[(item.parent_id, key(item.name))].append(item)
    for (_, k), items in groups.items():
        if not k or len(items) < 2:
            continue
        canonical = max(
            items,
            key=lambda x: (
                Product.objects.filter(category_id=x.pk).count(),
                Category.objects.filter(parent_id=x.pk).count(),
                -x.pk,
            ),
        )
        for duplicate in items:
            if duplicate.pk != canonical.pk and Category.objects.filter(pk=duplicate.pk).exists():
                _merge_duplicate(duplicate, canonical, stats)
    return stats
