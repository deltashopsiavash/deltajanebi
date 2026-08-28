"""Delta category mapping v22.

Trust the source breadcrumb when it is available, reuse an existing normalized
category globally (even when it currently lives below another parent), and
merge historical duplicate category rows without creating parallel branches.
"""
from collections import defaultdict

from django.db import transaction

from shop.models import Category, Product
from shop.services import category_v21 as v21

clean_name = v21.clean_name
key = v21.key
enhanced_category_names = v21.enhanced_category_names
infer_top_category = v21.infer_top_category


def canonical_path(raw_names, product_name="", specs=None):
    """Prefer the exact source breadcrumb; infer a category only as fallback.

    v21 replaced a valid source tree with a Delta-inferred root plus one leaf.
    That was able to turn precise source branches such as storage capacities
    into a different local hierarchy. v22 preserves every useful source level
    (up to eight) and only uses the classifier when no usable breadcrumb exists.
    """
    cleaned = []
    seen = set()
    for raw in raw_names or []:
        item = v21._good(raw, product_name)
        item_key = key(item)
        if item and item_key and item_key not in seen:
            cleaned.append(item)
            seen.add(item_key)
    if cleaned:
        return cleaned[:8]
    inferred = infer_top_category(product_name, specs)
    return [inferred] if inferred else []


def _depth(item):
    try:
        return max(0, len(item.ancestor_chain()) - 1)
    except Exception:
        return 999


def _category_score(item):
    return (
        int(bool(item.is_active)),
        Product.objects.filter(category_id=item.pk).count(),
        Category.objects.filter(parent_id=item.pk).count(),
        -_depth(item),
        -int(item.pk),
    )


def _matches(category_key, exclude_ids=()):
    excluded = {int(x) for x in exclude_ids if x}
    return [
        item
        for item in Category.objects.all().order_by("id")
        if item.pk not in excluded and key(item.name) == category_key
    ]


def _best_global(category_key, exclude_ids=()):
    matches = _matches(category_key, exclude_ids)
    return max(matches, key=_category_score) if matches else None


def _direct(parent, category_key):
    for item in Category.objects.filter(parent=parent).order_by("id"):
        if key(item.name) == category_key:
            return item
    return None


def sync_category_path(names):
    """Resolve a source path without ever creating a second equal category.

    A direct child is preferred when it already exists. Otherwise any category
    with the same normalized name anywhere in the tree is reused and the product
    is attached to that existing category instead of creating a duplicate branch.
    """
    values = []
    seen = set()
    for raw in names or []:
        name = clean_name(raw)
        item_key = key(name)
        if name and item_key and item_key not in seen:
            values.append((name, item_key))
            seen.add(item_key)
    if not values:
        return None

    parent = None
    for name, item_key in values:
        category = _direct(parent, item_key)
        if category is None:
            category = _best_global(item_key)
        if category is None:
            category = Category.objects.create(parent=parent, name=name, slug="", is_active=True)
        elif not category.is_active:
            category.is_active = True
            category.save(update_fields=["is_active"])
        parent = category
    return parent


def _is_descendant(item, possible_ancestor):
    current = item
    seen = set()
    while current and current.pk not in seen:
        if current.pk == possible_ancestor.pk:
            return True
        seen.add(current.pk)
        current = current.parent
    return False


def _merge_duplicate(duplicate, canonical, stats):
    if duplicate.pk == canonical.pk or not Category.objects.filter(pk=duplicate.pk).exists():
        return

    # Canonical is normally the shallower row. Keep a defensive cycle guard for
    # malformed old trees before moving children.
    if _is_descendant(canonical, duplicate):
        canonical.parent = duplicate.parent
        canonical.save(update_fields=["parent"])

    moved = Product.objects.filter(category_id=duplicate.pk).update(category_id=canonical.pk)
    stats["products_recategorized"] += moved

    for child in list(Category.objects.filter(parent_id=duplicate.pk).order_by("id")):
        if child.pk == canonical.pk:
            continue
        child_key = key(child.name)
        collision = _best_global(child_key, exclude_ids=(duplicate.pk, child.pk)) if child_key else None
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
    """Compatibility name: v22 now merges normalized duplicates globally."""
    stats = {"categories_merged": 0, "products_recategorized": 0}
    groups = defaultdict(list)
    for item in Category.objects.all().order_by("id"):
        item_key = key(item.name)
        if item_key:
            groups[item_key].append(item)

    for item_key, items in groups.items():
        live = [item for item in items if Category.objects.filter(pk=item.pk).exists()]
        if len(live) < 2:
            continue
        min_depth = min(_depth(item) for item in live)
        shallow = [item for item in live if _depth(item) == min_depth]
        canonical = max(shallow, key=_category_score)
        for duplicate in live:
            if duplicate.pk != canonical.pk and Category.objects.filter(pk=duplicate.pk).exists():
                _merge_duplicate(duplicate, canonical, stats)
    return stats


consolidate_global_duplicates = consolidate_sibling_duplicates
