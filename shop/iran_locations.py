from functools import lru_cache


FALLBACK_PROVINCES = [
    "آذربایجان شرقی", "آذربایجان غربی", "اردبیل", "اصفهان", "البرز", "ایلام", "بوشهر", "تهران",
    "چهارمحال و بختیاری", "خراسان جنوبی", "خراسان رضوی", "خراسان شمالی", "خوزستان", "زنجان",
    "سمنان", "سیستان و بلوچستان", "فارس", "قزوین", "قم", "کردستان", "کرمان", "کرمانشاه",
    "کهگیلویه و بویراحمد", "گلستان", "گیلان", "لرستان", "مازندران", "مرکزی", "هرمزگان", "همدان", "یزد",
]


def _city_name(item):
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("name") or item.get("city") or "").strip()
    return ""


@lru_cache(maxsize=1)
def province_city_map():
    result = {}
    try:
        from provinces_and_cities import Iran

        data = getattr(Iran, "all", None) or getattr(Iran, "main", None) or []
        for row in data:
            if not isinstance(row, dict):
                continue
            province = str(row.get("name") or row.get("province") or "").strip()
            raw_cities = row.get("cities") or row.get("Cities") or []
            cities = sorted({name for name in (_city_name(x) for x in raw_cities) if name})
            if province:
                result[province] = cities
    except Exception:
        result = {}
    if not result:
        result = {name: [] for name in FALLBACK_PROVINCES}
    return result


def province_choices():
    return [(name, name) for name in province_city_map().keys()]


def valid_city(province, city):
    cities = province_city_map().get(province)
    if cities is None:
        return False
    return not cities or city in cities
