import re
import unicodedata
from collections import defaultdict

from django.db import transaction

from shop.models import Category, Product


_TRANSLATION = str.maketrans({
    "ي": "ی",
    "ى": "ی",
    "ك": "ک",
    "ة": "ه",
    "ۀ": "ه",
    "ؤ": "و",
    "إ": "ا",
    "أ": "ا",
})


def clean_category_name(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_TRANSLATION)
    text = text.replace("\u200c", " ").replace("\u200f", " ").replace("\ufeff", " ")
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-|–—>/")
    return text[:120]


def category_key(value):
    """Return a source-independent key for Persian category labels.

    Whitespace/ZWNJ and harmless punctuation are ignored so labels such as
    «کابل ها»، «کابل‌ها» and «کابلها» resolve to the same canonical category.
    """
    text = clean_category_name(value).casefold()
    text = re.sub(r"[\s\u200c\-_–—|/\\.,،:؛;()\[\]{}]+", "", text)
    return text


def _depth(category):
    depth = 0
    seen = set()
    current = category.parent
    while current and current.pk not in seen:
        seen.add(current.pk)
        depth += 1
        current = current.parent
    return depth


def _is_ancestor(candidate, node):
    if not candidate or not node:
        return False
    seen = set()
    current = node.parent
    while current and current.pk not in seen:
        if current.pk == candidate.pk:
            return True
        seen.add(current.pk)
        current = current.parent
    return False


def _score(category):
    # Prefer the category that already carries the richer hierarchy, then the
    # one currently used by more products/children, and finally the oldest id.
    return (
        _depth(category),
        Product.objects.filter(category_id=category.pk).count(),
        Category.objects.filter(parent_id=category.pk).count(),
        -int(category.pk or 0),
    )


def _matching_categories(key):
    if not key:
        return []
    return [item for item in Category.objects.select_related("parent").all() if category_key(item.name) == key]


def _best_match(key, parent=None):
    matches = _matching_categories(key)
    if not matches:
        return None
    if parent:
        direct = [item for item in matches if item.parent_id == parent.pk]
        if direct:
            return max(direct, key=_score)
    return max(matches, key=_score)


def _safe_reparent(category, parent):
    if not category or not parent or category.pk == parent.pk:
        return False
    if category.parent_id == parent.pk:
        return False
    if _is_ancestor(category, parent):
        return False
    category.parent = parent
    category.save(update_fields=["parent"])
    return True


def sync_category_path(names):
    """Resolve a source breadcrumb into one canonical Delta category tree.

    A leaf-only breadcrumb never creates a second root category if the same
    category already exists under a richer hierarchy. Conversely, when a root
    category already exists and a later source provides a richer parent path,
    that existing category is safely moved under the richer parent so products
    attached to it are preserved automatically.
    """
    parent = None
    seen_keys = set()
    for raw in names or []:
        name = clean_category_name(raw)
        key = category_key(name)
        if not name or not key or key in seen_keys:
            continue
        seen_keys.add(key)

        category = _best_match(key, parent=parent)
        if category is None:
            category = Category.objects.create(parent=parent, name=name, slug="", is_active=True)
        else:
            # Enrich only a root category with a known parent. Never move a
            # category between two established non-root hierarchies based on a
            # weaker source breadcrumb; that would make sources fight each other.
            if parent and category.parent_id is None:
                _safe_reparent(category, parent)
            if not category.is_active:
                category.is_active = True
                category.save(update_fields=["is_active"])
        parent = category
    return parent


def _merge_one(duplicate, canonical, stats):
    if duplicate.pk == canonical.pk:
        return canonical

    # Extremely defensive: if the chosen canonical category is inside the
    # duplicate subtree, detach it first so deleting duplicate cannot cascade it.
    if _is_ancestor(duplicate, canonical):
        canonical.parent_id = duplicate.parent_id
        canonical.save(update_fields=["parent"])

    moved = Product.objects.filter(category_id=duplicate.pk).update(category_id=canonical.pk)
    stats["products_recategorized"] += moved

    for child in list(Category.objects.filter(parent_id=duplicate.pk).order_by("id")):
        if child.pk == canonical.pk:
            continue
        collision = next(
            (
                item
                for item in Category.objects.filter(parent_id=canonical.pk).exclude(pk=child.pk)
                if category_key(item.name) == category_key(child.name)
            ),
            None,
        )
        if collision:
            _merge_one(child, collision, stats)
        elif not _is_ancestor(child, canonical):
            child.parent_id = canonical.pk
            child.save(update_fields=["parent"])

    changed = []
    if not canonical.image_url and duplicate.image_url:
        canonical.image_url = duplicate.image_url
        changed.append("image_url")
    if duplicate.is_active and not canonical.is_active:
        canonical.is_active = True
        changed.append("is_active")
    if duplicate.order < canonical.order:
        canonical.order = duplicate.order
        changed.append("order")
    if changed:
        canonical.save(update_fields=changed)

    duplicate.delete()
    stats["categories_merged"] += 1
    return canonical


@transaction.atomic
def consolidate_duplicate_categories():
    """Merge duplicate category labels already present in the live database."""
    stats = {"categories_merged": 0, "products_recategorized": 0}
    keys = defaultdict(int)
    for item in Category.objects.all().only("id", "name"):
        key = category_key(item.name)
        if key:
            keys[key] += 1

    for key, count in list(keys.items()):
        if count < 2:
            continue
        matches = _matching_categories(key)
        while len(matches) > 1:
            canonical = max(matches, key=_score)
            for duplicate in list(matches):
                if duplicate.pk != canonical.pk and Category.objects.filter(pk=duplicate.pk).exists():
                    _merge_one(duplicate, canonical, stats)
            matches = _matching_categories(key)

    return stats
