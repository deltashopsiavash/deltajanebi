import re
import unicodedata
from collections import defaultdict

from django.db import migrations


_TRANSLATION = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه",
    "ؤ": "و", "إ": "ا", "أ": "ا",
})


def _key(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_TRANSLATION)
    text = text.replace("\u200c", " ").replace("\u200f", " ").replace("\ufeff", " ")
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-|–—>/").casefold()
    return re.sub(r"[\s\u200c\-_–—|/\\.,،:؛;()\[\]{}]+", "", text)


def merge_duplicate_categories(apps, schema_editor):
    Category = apps.get_model("shop", "Category")
    Product = apps.get_model("shop", "Product")

    def depth(item):
        value = 0
        seen = set()
        parent_id = item.parent_id
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = Category.objects.filter(pk=parent_id).only("id", "parent_id").first()
            if not parent:
                break
            value += 1
            parent_id = parent.parent_id
        return value

    def is_ancestor(candidate_id, node_id):
        seen = set()
        current = Category.objects.filter(pk=node_id).only("id", "parent_id").first()
        parent_id = current.parent_id if current else None
        while parent_id and parent_id not in seen:
            if parent_id == candidate_id:
                return True
            seen.add(parent_id)
            current = Category.objects.filter(pk=parent_id).only("id", "parent_id").first()
            parent_id = current.parent_id if current else None
        return False

    keys = defaultdict(list)
    for row in Category.objects.all().only("id", "name"):
        key = _key(row.name)
        if key:
            keys[key].append(row.id)

    for key, ids in keys.items():
        if len(ids) < 2:
            continue
        rows = list(Category.objects.filter(pk__in=ids))
        if len(rows) < 2:
            continue

        def score(item):
            return (
                depth(item),
                Product.objects.filter(category_id=item.id).count(),
                Category.objects.filter(parent_id=item.id).count(),
                -item.id,
            )

        canonical = max(rows, key=score)
        for duplicate in rows:
            if duplicate.id == canonical.id or not Category.objects.filter(pk=duplicate.id).exists():
                continue

            if is_ancestor(duplicate.id, canonical.id):
                canonical.parent_id = duplicate.parent_id
                canonical.save(update_fields=["parent"])

            Product.objects.filter(category_id=duplicate.id).update(category_id=canonical.id)
            Category.objects.filter(parent_id=duplicate.id).exclude(pk=canonical.id).update(parent_id=canonical.id)

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


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("shop", "0015_order_wallet_payments")]
    operations = [migrations.RunPython(merge_duplicate_categories, noop)]
