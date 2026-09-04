"""Strict source category mapping for Delta catalog v28.

The source breadcrumb is authoritative. Equal normalized category names still
reuse one global Category row, but that row is re-parented to the hierarchy
reported by the source instead of silently keeping an old/wrong parent. Stored
ProductSourceOffer.category_path values can also rebuild the category tree after
a full or single-source sync, which repairs historical imports without manual
category deletion.
"""
import json
from collections import Counter, defaultdict

from django.db import transaction

from shop.models import Category, Product
from shop.services import category_v21 as v21

clean_name = v21.clean_name
key = v21.key
infer_top_category = v21.infer_top_category


def _clean_path(values, product_name=""):
    cleaned = []
    seen = set()
    for raw in values or []:
        item = v21._good(raw, product_name)
        item_key = key(item)
        if item and item_key and item_key not in seen:
            cleaned.append(item)
            seen.add(item_key)
    return cleaned[:8]


def _jsonld_rows(soup):
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
            yield item


def enhanced_category_names(soup):
    """Extract the visible/source category chain in root -> leaf order.

    WooCommerce themes (including Woodmart-style themes) expose the useful
    hierarchy either in the rendered breadcrumb, JSON-LD BreadcrumbList, or the
    ``posted_in`` product metadata. Product titles, home/shop labels and source
    branding are removed by v21._good().
    """
    title_node = soup.select_one("h1.product_title, h1[itemprop='name'], main h1, h1")
    product_title = title_node.get_text(" ", strip=True) if title_node else ""

    # Prefer the actual rendered breadcrumb because it mirrors what the customer
    # sees on the source site. Links are enough on WooCommerce: the final plain
    # text node is normally the product title and must not become a category.
    selectors = (
        ".woocommerce-breadcrumb a",
        ".wd-breadcrumbs a",
        ".yoast-breadcrumb a",
        ".breadcrumb a",
        ".breadcrumbs a",
        "[class*='breadcrumb'] a",
        "nav[aria-label*='breadcrumb' i] a",
        "[itemtype*='BreadcrumbList'] a",
    )
    for selector in selectors:
        nodes = soup.select(selector)
        if not nodes:
            continue
        path = _clean_path([node.get_text(" ", strip=True) for node in nodes], product_title)
        if path:
            return path

    # JSON-LD is the next most reliable ordered source. Respect explicit
    # ``position`` values when a theme emits entries out of DOM order.
    for item in _jsonld_rows(soup):
        kind = item.get("@type", "")
        kinds = {str(x).casefold() for x in (kind if isinstance(kind, list) else [kind])}
        if "breadcrumblist" not in kinds:
            continue
        entries = []
        for index, entry in enumerate(item.get("itemListElement") or []):
            if not isinstance(entry, dict):
                continue
            nested = entry.get("item")
            if isinstance(nested, dict):
                name = nested.get("name") or entry.get("name")
            else:
                name = entry.get("name")
            try:
                position = int(entry.get("position") or index + 1)
            except (TypeError, ValueError):
                position = index + 1
            entries.append((position, name))
        entries.sort(key=lambda row: row[0])
        path = _clean_path([name for _, name in entries], product_title)
        if path:
            return path

    # Product.category is usually only a leaf, but it is still safer than
    # inventing a Delta category when the source explicitly provides one.
    for item in _jsonld_rows(soup):
        kind = item.get("@type", "")
        kinds = {str(x).casefold() for x in (kind if isinstance(kind, list) else [kind])}
        if "product" not in kinds:
            continue
        category = item.get("category")
        values = category if isinstance(category, list) else [category]
        path = []
        for value in values:
            if isinstance(value, dict):
                value = value.get("name")
            text = str(value or "")
            # Some stores serialize "parent > child > leaf" into one field.
            parts = [part for part in __import__("re").split(r"\s*(?:>|»|›)\s*", text) if part]
            path.extend(parts)
        path = _clean_path(path, product_title)
        if path:
            return path

    # WooCommerce fallback. Multiple posted_in links are not guaranteed to be a
    # hierarchy, so keep their order but do not fabricate parent levels.
    posted = [
        node.get_text(" ", strip=True)
        for node in soup.select(".product_meta .posted_in a, .posted_in a")
    ]
    return _clean_path(posted, product_title)


def canonical_path(raw_names, product_name="", specs=None):
    """Use the exact source breadcrumb; infer only when source has no category."""
    cleaned = _clean_path(raw_names, product_name)
    if cleaned:
        return cleaned
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


def _is_descendant(item, possible_ancestor):
    current = item
    seen = set()
    while current and current.pk not in seen:
        if current.pk == possible_ancestor.pk:
            return True
        seen.add(current.pk)
        current = current.parent
    return False


def _can_reparent(category, parent):
    if parent is None:
        return True
    if category.pk == parent.pk:
        return False
    # parent must not already live below category, otherwise this would create a
    # cycle in the tree.
    return not _is_descendant(parent, category)


def _activate_and_align(category, name, parent):
    fields = []
    if not category.is_active:
        category.is_active = True
        fields.append("is_active")
    if category.name != name and key(category.name) == key(name):
        category.name = name
        fields.append("name")
    desired_parent_id = parent.pk if parent else None
    if category.parent_id != desired_parent_id and _can_reparent(category, parent):
        category.parent = parent
        fields.append("parent")
    if fields:
        category.save(update_fields=list(dict.fromkeys(fields)))
    return category


@transaction.atomic
def sync_category_path(names):
    """Create/reuse one global row per normalized name and mirror source parents.

    The old v22 behavior returned an existing leaf immediately, leaving it under
    whatever historical parent happened to exist. v28 walks the whole source
    path and re-parents reused rows to the source hierarchy, so e.g. a category
    imported under the wrong root is repaired on the next sync instead of being
    duplicated.
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
        else:
            category = _activate_and_align(category, name, parent)
        parent = category
    return parent


def _merge_duplicate(duplicate, canonical, stats):
    if duplicate.pk == canonical.pk or not Category.objects.filter(pk=duplicate.pk).exists():
        return

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
        elif _can_reparent(child, canonical):
            child.parent = canonical
            child.save(update_fields=["parent"])

    if not canonical.image_url and duplicate.image_url:
        canonical.image_url = duplicate.image_url
        canonical.save(update_fields=["image_url"])
    duplicate.delete()
    stats["categories_merged"] += 1


@transaction.atomic
def consolidate_sibling_duplicates():
    """Merge normalized duplicates globally without inventing parallel rows."""
    stats = {"categories_merged": 0, "products_recategorized": 0}
    groups = defaultdict(list)
    for item in Category.objects.all().order_by("id"):
        item_key = key(item.name)
        if item_key:
            groups[item_key].append(item)

    for _, items in groups.items():
        live = [item for item in items if Category.objects.filter(pk=item.pk).exists()]
        if len(live) < 2:
            continue
        canonical = max(live, key=_category_score)
        for duplicate in live:
            if duplicate.pk != canonical.pk and Category.objects.filter(pk=duplicate.pk).exists():
                _merge_duplicate(duplicate, canonical, stats)
    return stats


@transaction.atomic
def rebuild_category_tree_from_offers(source_site_id=0):
    """Reconstruct the category parent graph from stored source breadcrumb paths.

    For a single-site sync that site's hierarchy is authoritative. For an all-site
    sync, the most frequently observed parent relationship wins deterministically
    when multiple sources disagree about where an equal global category belongs.
    """
    from shop.source_offer_models import ProductSourceOffer

    rows = ProductSourceOffer.objects.exclude(category_path=[]).order_by("source_site_id", "id")
    if source_site_id:
        rows = rows.filter(source_site_id=int(source_site_id))

    parent_votes = defaultdict(Counter)
    label_votes = defaultdict(Counter)
    depth_votes = defaultdict(Counter)
    scanned = 0
    for offer in rows.iterator(chunk_size=250):
        payload = dict(offer.payload or {})
        path = canonical_path(
            list(offer.category_path or []),
            str(payload.get("name") or ""),
            dict(payload.get("specs") or {}),
        )
        if not path:
            continue
        scanned += 1
        parent_key = ""
        for depth, label in enumerate(path):
            item_key = key(label)
            if not item_key:
                continue
            label_votes[item_key][clean_name(label)] += 1
            parent_votes[item_key][parent_key] += 1
            depth_votes[item_key][depth] += 1
            parent_key = item_key

    if not parent_votes:
        return {"category_paths_scanned": scanned, "categories_reparented": 0, "categories_created_from_offers": 0}

    desired_parent = {
        item_key: votes.most_common(1)[0][0]
        for item_key, votes in parent_votes.items()
    }
    desired_label = {
        item_key: votes.most_common(1)[0][0]
        for item_key, votes in label_votes.items()
    }
    desired_depth = {
        item_key: min(votes, key=lambda depth: (-votes[depth], depth))
        for item_key, votes in depth_votes.items()
    }

    created = 0
    reparented = 0
    resolved = {}
    for item_key in sorted(desired_parent, key=lambda value: (desired_depth.get(value, 999), desired_label.get(value, value))):
        parent_key = desired_parent[item_key]
        parent = resolved.get(parent_key) if parent_key else None
        if parent_key and parent is None:
            # Parent may be an existing category that did not receive its own
            # vote because a legacy path was truncated.
            parent = _best_global(parent_key)
        category = _best_global(item_key)
        if category is None:
            category = Category.objects.create(parent=parent, name=desired_label[item_key], slug="", is_active=True)
            created += 1
        else:
            before = category.parent_id
            _activate_and_align(category, desired_label[item_key], parent)
            if before != category.parent_id:
                reparented += 1
        resolved[item_key] = category

    return {
        "category_paths_scanned": scanned,
        "categories_reparented": reparented,
        "categories_created_from_offers": created,
    }


consolidate_global_duplicates = consolidate_sibling_duplicates
