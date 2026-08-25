import json,os,re,socket,time
from decimal import Decimal,InvalidOperation
from urllib.parse import urlparse,urljoin
import requests
from bs4 import BeautifulSoup
from django.utils import timezone
from shop.models import Product
class SourceSyncError(Exception): pass
def _allowed_url(url):
    p=urlparse(url); allowed={x.strip().lower() for x in os.getenv("SOURCE_ALLOWED_HOSTS","hamrahedovom.ir,www.hamrahedovom.ir").split(",") if x.strip()}
    if p.scheme not in ("http","https") or not p.hostname or p.hostname.lower() not in allowed: return False
    try:
        for info in socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=="https" else 80)):
            ip=info[4][0]
            if ip.startswith(("127.","10.","192.168.","169.254.")) or (ip.startswith("172.") and 16<=int(ip.split('.')[1])<=31): return False
    except socket.gaierror: pass
    return True
def _digits(value):
    if value is None:return 0
    trans=str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩","01234567890123456789"); s=str(value).translate(trans); nums=re.findall(r"\d+(?:[.,]\d+)?",s.replace(",",""))
    if not nums:return 0
    try:return int(Decimal(nums[0]))
    except InvalidOperation:return 0
def _first(*vals):
    for v in vals:
        if v not in (None,"",[],{}): return v
    return ""
def _jsonld_products(soup):
    out=[]
    for tag in soup.find_all("script",type="application/ld+json"):
        try:data=json.loads(tag.string or "")
        except Exception:continue
        stack=data if isinstance(data,list) else [data]
        for item in list(stack):
            if isinstance(item,dict) and "@graph" in item: stack.extend(item.get("@graph") or [])
            if isinstance(item,dict) and str(item.get("@type","")).lower()=="product": out.append(item)
    return out
def scrape_product(url):
    if not _allowed_url(url): raise SourceSyncError("دامنه لینک در فهرست منابع مجاز نیست.")
    headers={"User-Agent":os.getenv("SOURCE_USER_AGENT","DeltaJanebiSync/1.0"),"Accept-Language":"fa-IR,fa;q=0.9,en;q=0.5"}; timeout=float(os.getenv("SOURCE_REQUEST_TIMEOUT","20")); r=requests.get(url,headers=headers,timeout=timeout,allow_redirects=True)
    if r.status_code>=400: raise SourceSyncError(f"خطای منبع: HTTP {r.status_code}")
    if not _allowed_url(r.url): raise SourceSyncError("ریدایرکت به دامنه غیرمجاز انجام شد.")
    soup=BeautifulSoup(r.text,"lxml"); ld=(_jsonld_products(soup) or [{}])[0]; offers=ld.get("offers") or {}; offers=(offers[0] if isinstance(offers,list) and offers else offers)
    def meta(*keys):
        for key in keys:
            tag=soup.find("meta",property=key) or soup.find("meta",attrs={"name":key}) or soup.find("meta",attrs={"itemprop":key})
            if tag and tag.get("content"): return tag["content"].strip()
        return ""
    name=_first(ld.get("name"),meta("og:title","twitter:title"),soup.title.string.strip() if soup.title and soup.title.string else ""); desc=_first(ld.get("description"),meta("og:description","description")); image=ld.get("image") or meta("og:image","twitter:image"); gallery=[]
    if isinstance(image,list): gallery=[urljoin(r.url,x) for x in image if x]; image=gallery[0] if gallery else ""
    elif isinstance(image,dict): image=image.get("url","")
    if image: image=urljoin(r.url,image); gallery=[image]
    for img in soup.select(".product img, .product-gallery img, [class*=gallery] img")[:15]:
        src=img.get("data-src") or img.get("data-lazy-src") or img.get("src")
        if src:
            src=urljoin(r.url,src)
            if src not in gallery: gallery.append(src)
    price=_digits(_first(offers.get("price") if isinstance(offers,dict) else "",meta("product:price:amount","og:price:amount"))); currency=str(_first(offers.get("priceCurrency") if isinstance(offers,dict) else "",meta("product:price:currency"))).upper()
    if currency=="IRR" and price: price//=10
    availability=str(_first(offers.get("availability") if isinstance(offers,dict) else "","")).lower(); text=soup.get_text(" ",strip=True); stock=0
    for node in [soup.select_one("[data-stock]"),soup.select_one("[data-quantity]"),soup.select_one("input[name=quantity][max]")]:
        if node:
            stock=_digits(node.get("data-stock") or node.get("data-quantity") or node.get("max"))
            if stock: break
    if not stock:
        for pat in [r"(?:موجودی|تعداد موجود)\s*[:：]?\s*([۰-۹0-9,]+)",r"([۰-۹0-9,]+)\s*(?:عدد|عدد موجود)"]:
            m=re.search(pat,text)
            if m: stock=_digits(m.group(1)); break
    if not stock and ("instock" in availability or re.search(r"\bموجود\b",text)) and not re.search(r"ناموجود|اتمام موجودی",text): stock=1
    specs={}
    for row in soup.select("table tr")[:80]:
        cells=[c.get_text(" ",strip=True) for c in row.find_all(["th","td"])]
        if len(cells)>=2 and cells[0] and cells[1]: specs[cells[0][:100]]=cells[1][:300]
    for item in soup.select(".specification, .spec-row, [class*=attribute]")[:80]:
        parts=[x.strip() for x in item.stripped_strings]
        if len(parts)>=2: specs.setdefault(parts[0][:100]," ".join(parts[1:])[:300])
    sku=str(_first(ld.get("sku"),ld.get("mpn"),""))
    if not sku:
        m=re.search(r"/(BKP-\d+)/",r.url,re.I); sku=m.group(1).upper() if m else ""
    if not name or not price: raise SourceSyncError("نام یا قیمت محصول از صفحه قابل استخراج نبود؛ پارسر سایت منبع باید تنظیم شود.")
    return {"name":name[:300],"description":str(desc)[:10000],"price":price,"stock":max(0,stock),"image_url":image,"gallery":gallery[:20],"specs":specs,"sku":sku,"source_url":r.url}
def sync_product(product,raise_errors=False):
    try:
        data=scrape_product(product.source_url); product.name=data["name"] or product.name; product.description=data["description"] or product.description; product.source_price=data["price"]; product.price=product.apply_markup(data["price"]); product.stock=data["stock"]; product.image_url=data["image_url"] or product.image_url; product.gallery=data["gallery"] or product.gallery; product.specs=data["specs"] or product.specs
        if data["sku"] and not Product.objects.exclude(pk=product.pk).filter(sku=data["sku"]).exists(): product.sku=data["sku"]
        product.last_synced_at=timezone.now(); product.sync_error=""; product.save(); return product
    except Exception as e:
        product.sync_error=str(e)[:2000]; product.last_synced_at=timezone.now(); product.save(update_fields=["sync_error","last_synced_at"])
        if raise_errors: raise
        return product
def sync_all():
    delay=float(os.getenv("SOURCE_SYNC_DELAY","1.2"))
    for p in Product.objects.filter(source_type=Product.SYNCED,is_active=True).exclude(source_url=""):
        sync_product(p)
        if delay: time.sleep(delay)
