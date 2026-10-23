"""Walmart connection.

Walmart's public Affiliate API (walmart.io) can look up a product by
barcode, with its walmart.com price, but it has no way for an app to put
items in *your* cart. So Tosslist keeps a Walmart list on the device, and
tosslist.local shows an "Open in Walmart cart" button. Tap it on your phone
and walmart.com opens with those items in your cart. You check out there.

Walmart requires every request to be signed with a private key. Tosslist
creates the key pair itself; you paste the public half into walmart.io.
The private half never leaves the device. No Walmart password is stored.
"""

import base64
import os
import time
import uuid
from urllib.parse import quote

import requests

import common
import kroger  # for UPC-E expansion

API = "https://developer.api.walmart.com/api-proxy/service/affil/product/v2"
CART_URL = "https://affil.walmart.com/cart/addToCart?items="
KEY_PATH = common.WALMART_KEY_PATH
BASKET_PATH = common.WALMART_BASKET_PATH


class WalmartAuthError(Exception):
    """Walmart rejected the Consumer ID, key version or key."""


# ---------- the signing key ----------

def create_key():
    """Make a new 2048-bit RSA key pair. Saves the private key (readable by
    the device only) and returns the public key to paste into walmart.io."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption())
    with common.writable():
        os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
        tmp = KEY_PATH + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, KEY_PATH)
    return public_key()


def has_key():
    return os.path.exists(KEY_PATH)


def _private_key():
    from cryptography.hazmat.primitives import serialization
    with open(KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def public_key():
    """The public key, as text to paste into walmart.io."""
    if not has_key():
        return ""
    from cryptography.hazmat.primitives import serialization
    return _private_key().public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def signed_headers(consumer_id, key_version, now_ms=None):
    """Walmart's request signature: consumer ID, timestamp (ms) and key
    version, each followed by a newline, signed with RSA-SHA256, base64."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
    message = f"{consumer_id}\n{ts}\n{key_version}\n".encode()
    sig = _private_key().sign(message, padding.PKCS1v15(), hashes.SHA256())
    return {
        "WM_CONSUMER.ID": consumer_id,
        "WM_CONSUMER.INTIMESTAMP": ts,
        "WM_SEC.KEY_VERSION": str(key_version),
        "WM_SEC.AUTH_SIGNATURE": base64.b64encode(sig).decode(),
        "Accept": "application/json",
    }


# ---------- looking things up ----------

def upc_candidates(barcode):
    """Walmart looks items up by the full 12-digit UPC."""
    code = (barcode or "").strip()
    if not code.isdigit():
        return []
    if len(code) == 8:
        code = kroger._expand_upce(code)
    out = [code]
    if len(code) == 13 and code.startswith("0"):
        out.insert(0, code[1:])
    return out


class WalmartClient:
    def __init__(self, cfg, session=None):
        self.cfg = cfg
        self.session = session or requests.Session()

    def _get(self, path, params):
        r = self.session.get(API + path, params=params, timeout=10,
                             headers=signed_headers(self.cfg["walmart_consumer_id"],
                                                    self.cfg.get("walmart_key_version") or "1"))
        if r.status_code in (401, 403):
            raise WalmartAuthError("Walmart rejected the Consumer ID or key")
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        return r.json()

    def find_product(self, barcode, item_id=None):
        """Returns {"item_id", "name", "price", "stock"} or None if Walmart
        doesn't know it. "stock" is walmart.com availability text, e.g.
        "Available" or "Not available" (not your local store).
        item_id: a Walmart item to use instead of the barcode (a taught item)."""
        if item_id and str(item_id).isdigit():
            found = _items(self._get(f"/items/{item_id}", {}))
            return found[0] if found else None
        for upc in upc_candidates(barcode):
            found = _items(self._get("/items", {"upc": upc}))
            if found:
                return found[0]
        return None

    def search(self, query, limit=8):
        """walmart.com products matching some words (for teaching an item)."""
        if len(query.strip()) < 3:
            return []
        return _items(self._get("/search", {"query": query.strip(),
                                            "numItems": limit}))[:limit]

    def check(self):
        """Raises WalmartAuthError if the setup is wrong."""
        self._get("/items", {"upc": "000000000000"})


# ---------- the Walmart list kept on the device ----------

def _items(data):
    """Walmart answers with {"items": [...]}, a list, or a single item."""
    if not data:
        return []
    if isinstance(data, dict):
        items = data.get("items")
        if items is None:
            items = [data] if data.get("itemId") else []
    else:
        items = data
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("itemId"):
            price = it.get("salePrice") or it.get("msrp")
            out.append({"item_id": str(it["itemId"]),
                        "name": (it.get("name") or "").strip(),
                        "price": float(price) if price else None,
                        "stock": (it.get("stock") or None)})
    return out


def in_stock(product):
    """True / False, or None when Walmart gave no availability."""
    stock = (product.get("stock") or "").strip().lower()
    if not stock:
        return None
    return not (stock.startswith("not") or "out of stock" in stock)


def basket():
    return common.read_json(BASKET_PATH, {"items": [], "opened_at": None})


def basket_add(product, barcode):
    b = basket()
    for it in b["items"]:
        if it["item_id"] == product["item_id"]:
            it["qty"] += 1
            break
    else:
        b["items"].append({"id": uuid.uuid4().hex[:8], "item_id": product["item_id"],
                           "name": product["name"], "price": product["price"],
                           "barcode": barcode, "qty": 1})
    common.write_json(BASKET_PATH, b)
    return b


def basket_remove(entry_id):
    b = basket()
    b["items"] = [it for it in b["items"] if it["id"] != entry_id]
    common.write_json(BASKET_PATH, b)


def basket_decrement(item_id):
    """Take one of this item off the list. Returns False if it wasn't there."""
    b = basket()
    for it in b["items"]:
        if it["item_id"] == item_id:
            it["qty"] -= 1
            if it["qty"] <= 0:
                b["items"].remove(it)
            common.write_json(BASKET_PATH, b)
            return True
    return False


def basket_clear():
    common.write_json(BASKET_PATH, {"items": [], "opened_at": None})


def mark_opened():
    b = basket()
    b["opened_at"] = time.strftime("%Y-%m-%d %H:%M")
    common.write_json(BASKET_PATH, b)


def cart_link(items):
    """walmart.com link that adds every item (ID|quantity, comma-separated)."""
    parts = [f"{it['item_id']}|{it['qty']}" for it in items if it["item_id"].isdigit()]
    return CART_URL + quote(",".join(parts), safe="|,") if parts else ""


def basket_total(items):
    return sum((it["price"] or 0) * it["qty"] for it in items)
