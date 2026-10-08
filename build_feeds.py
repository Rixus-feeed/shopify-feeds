#!/usr/bin/env python3
"""
Генератор Google Merchant XML-фідів для кількох Shopify-магазинів.

- Забирає ВСІ активні товари через Bulk Operation (без ліміту 250 і без пагінації).
- Для кожного магазину пише файл public/<slug>.xml (один файл = одне посилання).
- Якщо один магазин дав помилку, його попередній фід лишається, інші оновлюються.

Налаштування — змінна середовища STORES_JSON (у GitHub це Secret), наприклад:
[
  {"slug": "rixus",  "shop": "pxnqdi-92.myshopify.com", "client_id": "...", "client_secret": "..."},
  {"slug": "keratinequeen", "shop": "xxxx.myshopify.com", "client_id": "...", "client_secret": "..."}
]
Замість client_id/client_secret можна вказати "token": "shpat_..." (старі custom apps).
"""
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

API_VERSION = "2025-10"
OUT_DIR = os.environ.get("OUT_DIR", "public")
G = "http://base.google.com/ns/1.0"
ET.register_namespace("g", G)

BULK_QUERY = """
{
  products(query: "status:active") {
    edges { node {
      id title handle vendor productType tags description onlineStoreUrl
      options { name position }
      featuredMedia { ... on MediaImage { image { url } } }
      media { edges { node { id ... on MediaImage { image { url } } } } }
      collections { edges { node { id title } } }
      variants { edges { node {
        id title sku barcode price compareAtPrice inventoryQuantity availableForSale
        selectedOptions { name value }
        inventoryItem { measurement { weight { value unit } } }
      } } }
    } }
  }
}
"""

# ---------------------------------------------------------------- Shopify API

def get_token(store):
    if store.get("token"):
        return store["token"]
    r = requests.post(
        f"https://{store['shop']}/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": store["client_id"],
            "client_secret": store["client_secret"],
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def gql(store, token, query, variables=None):
    r = requests.post(
        f"https://{store['shop']}/admin/api/{API_VERSION}/graphql.json",
        json={"query": query, "variables": variables or {}},
        headers={"X-Shopify-Access-Token": token},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


def run_bulk(store, token):
    res = gql(store, token, """
      mutation run($q: String!) {
        bulkOperationRunQuery(query: $q) {
          bulkOperation { id status }
          userErrors { field message }
        }
      }""", {"q": BULK_QUERY})["bulkOperationRunQuery"]
    if res["userErrors"]:
        raise RuntimeError(f"Bulk start error: {res['userErrors']}")
    op_id = res["bulkOperation"]["id"]

    deadline = time.time() + 30 * 60
    while time.time() < deadline:
        time.sleep(5)
        op = gql(store, token, """
          query op($id: ID!) { node(id: $id) { ... on BulkOperation {
            status errorCode url partialDataUrl objectCount } } }""", {"id": op_id})["node"]
        if op["status"] == "COMPLETED":
            if not op["url"]:          # магазин без товарів
                return []
            lines = requests.get(op["url"], timeout=300).text.splitlines()
            return [json.loads(l) for l in lines if l.strip()]
        if op["status"] in ("FAILED", "CANCELED", "EXPIRED"):
            raise RuntimeError(f"Bulk operation {op['status']}: {op.get('errorCode')}")
    raise RuntimeError("Bulk operation timeout")


def get_shop(store, token):
    return gql(store, token, "{ shop { name currencyCode primaryDomain { url } } }")["shop"]

# ---------------------------------------------------------------- Обробка даних

def assemble(rows):
    """JSONL з bulk-операції → список товарів з вкладеними variants/images/collections."""
    products, order = {}, []
    for row in rows:
        gid = row.get("id", "")
        parent = row.get("__parentId")
        if parent is None and "/Product/" in gid:
            row.update(variants=[], images=[], collections=[])
            products[gid] = row
            order.append(gid)
        elif parent in products:
            p = products[parent]
            if "/ProductVariant/" in gid:
                p["variants"].append(row)
            elif "/Collection/" in gid:
                p["collections"].append(row.get("title", ""))
            elif row.get("image") and row["image"].get("url"):
                p["images"].append(row["image"]["url"])
    return [products[g] for g in order]


VOL_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*[xх×*]\s*\d+(?:[.,]\d+)?)?)\s*(мл|ml|кг|kg|гр|г|g|л|l)(?![a-zа-яіїєґ])",
    re.IGNORECASE,
)
UNIT_MAP = {"мл": "ml", "ml": "ml", "л": "l", "l": "l", "г": "g", "гр": "g", "g": "g", "кг": "kg", "kg": "kg"}
VOL_OPTION_WORDS = ("об'єм", "обʼєм", "об’єм", "обсяг", "объем", "обьем", "volume", "вага", "об`єм")


def volume_of(product, variant):
    for opt in variant.get("selectedOptions") or []:
        name = (opt.get("name") or "").lower()
        val = (opt.get("value") or "").strip()
        if any(w in name for w in VOL_OPTION_WORDS) and val and val != "Default Title":
            return val
    matches = VOL_RE.findall(product.get("title") or "")
    if matches:
        num, unit = matches[-1]
        return f"{num.strip()} {unit.lower()}"
    return ""


def unit_measure(volume):
    m = VOL_RE.fullmatch(volume.strip()) if volume else None
    if not m or re.search(r"[xх×*]", m.group(1), re.IGNORECASE):
        return ""
    return f"{m.group(1).replace(',', '.')} {UNIT_MAP[m.group(2).lower()]}"


CATEGORIES = [
    (("волос", "шампун", "кондиціонер", "бальзам", "hair", "кератин", "keratin"),
     "Health & Beauty > Personal Care > Hair Care"),
    (("обличч", "крем", "сироват", "тонік", "шкір", "skin", "face"),
     "Health & Beauty > Personal Care > Cosmetics > Skin Care"),
    (("макіяж", "помад", "туш", "makeup"),
     "Health & Beauty > Personal Care > Cosmetics > Makeup"),
    (("парфум", "парфю", "perfume"),
     "Health & Beauty > Personal Care > Cosmetics > Perfume & Cologne"),
]


def google_category(p):
    hay = " ".join([p.get("title") or "", p.get("productType") or "", " ".join(p.get("tags") or [])]).lower()
    for words, cat in CATEGORIES:
        if any(w in hay for w in words):
            return cat
    return "Health & Beauty > Personal Care"


BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(text):
    return BAD_XML.sub("", re.sub(r"\s+", " ", text or "")).strip()


def valid_gtin(bc):
    bc = re.sub(r"\s", "", bc or "")
    return bc if bc.isdigit() and len(bc) in (8, 12, 13, 14) else ""


def money(v, cur):
    return f"{float(v):.2f} {cur}"

# ---------------------------------------------------------------- XML

def build_xml(shop, products):
    cur = shop["currencyCode"]
    base = shop["primaryDomain"]["url"].rstrip("/")
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = shop["name"]
    ET.SubElement(ch, "link").text = base
    ET.SubElement(ch, "description").text = (
        f"{shop['name']} product feed, updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
    )

    def g(parent, tag, value):
        if value not in (None, ""):
            ET.SubElement(parent, f"{{{G}}}{tag}").text = str(value)

    count = 0
    for p in products:
        if not p.get("onlineStoreUrl"):      # не опублікований в Online Store — пропускаємо
            continue
        variants = p["variants"] or []
        multi = len(variants) > 1
        description = clean(p.get("description")) or clean(p.get("title"))
        images = list(dict.fromkeys(p["images"]))
        featured = ((p.get("featuredMedia") or {}).get("image") or {}).get("url")
        if featured:
            images = [featured] + [u for u in images if u != featured]
        gcat = google_category(p)
        ptype = p.get("productType") or (p["collections"][0] if p["collections"] else "")

        for v in variants:
            item = ET.SubElement(ch, "item")
            sku = (v.get("sku") or "").strip()
            vid = v["id"].rsplit("/", 1)[-1]
            title = p["title"]
            if multi and v.get("title") and v["title"] != "Default Title":
                title = f"{title} — {v['title']}"
            volume = volume_of(p, v)
            gtin = valid_gtin(v.get("barcode"))
            qty = max(int(v.get("inventoryQuantity") or 0), 0)
            price, compare = float(v.get("price") or 0), float(v.get("compareAtPrice") or 0)
            link = f"{p['onlineStoreUrl']}?variant={vid}" if multi else p["onlineStoreUrl"]

            g(item, "id", sku or vid)
            ET.SubElement(item, "title").text = clean(title)[:150]
            ET.SubElement(item, "description").text = description
            ET.SubElement(item, "link").text = link
            if images:
                g(item, "image_link", images[0])
                for u in images[1:11]:
                    g(item, "additional_image_link", u)
            g(item, "availability", "in_stock" if v.get("availableForSale") else "out_of_stock")
            g(item, "quantity", qty)
            if compare > price:
                g(item, "price", money(compare, cur))
                g(item, "sale_price", money(price, cur))
            else:
                g(item, "price", money(price, cur))
            g(item, "condition", "new")
            g(item, "brand", clean(p.get("vendor")))
            g(item, "gtin", gtin)
            g(item, "mpn", sku)
            if not gtin and not sku:
                g(item, "identifier_exists", "no")
            g(item, "google_product_category", gcat)
            g(item, "product_type", clean(ptype))
            if multi:
                g(item, "item_group_id", p["id"].rsplit("/", 1)[-1])
            g(item, "size", volume)
            g(item, "unit_pricing_measure", unit_measure(volume))
            w = ((((v.get("inventoryItem") or {}).get("measurement") or {}).get("weight")) or {})
            if w.get("value"):
                unit = {"GRAMS": "g", "KILOGRAMS": "kg", "POUNDS": "lb", "OUNCES": "oz"}.get(w.get("unit"), "g")
                g(item, "shipping_weight", f"{w['value']} {unit}")
            # Додаткова інформація для бота (валідні атрибути Google product_detail)
            for section, name, values in (
                ("Каталог", "Колекції", p["collections"]),
                ("Каталог", "Теги", p.get("tags") or []),
            ):
                if values:
                    d = ET.SubElement(item, f"{{{G}}}product_detail")
                    g(d, "section_name", section)
                    g(d, "attribute_name", name)
                    g(d, "attribute_value", clean(", ".join(values)))
            g(item, "custom_label_0", clean(p.get("vendor")))
            g(item, "custom_label_1", clean(ptype))
            g(item, "custom_label_2", "sale" if compare > price else "regular")
            count += 1

    ET.indent(rss)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True), count

# ---------------------------------------------------------------- main

def main():
    stores = json.loads(os.environ["STORES_JSON"])
    os.makedirs(OUT_DIR, exist_ok=True)
    failed = []
    for store in stores:
        slug = store["slug"]
        try:
            t0 = time.time()
            token = get_token(store)
            shop = get_shop(store, token)
            products = assemble(run_bulk(store, token))
            xml, n = build_xml(shop, products)
            tmp = os.path.join(OUT_DIR, f".{slug}.xml.tmp")
            with open(tmp, "wb") as f:
                f.write(xml)
            os.replace(tmp, os.path.join(OUT_DIR, f"{slug}.xml"))
            print(f"[ok] {slug}: {n} items, {time.time() - t0:.0f}s")
        except Exception as e:  # noqa: BLE001
            failed.append(slug)
            print(f"[FAIL] {slug}: {e}", file=sys.stderr)
    if failed:
        print(f"Failed: {', '.join(failed)} (previous feeds kept)", file=sys.stderr)
        if len(failed) == len(stores):
            sys.exit(1)


if __name__ == "__main__":
    main()
