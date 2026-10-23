"""Fred Meyer (Kroger) connection.

Uses Kroger's official public API (developer.kroger.com):
  - find nearby stores
  - match a scanned barcode to a product at your store, with its price
  - add the product to your Fred Meyer cart

Tosslist never sees your Fred Meyer password. You sign in on Kroger's own
page, and Kroger gives Tosslist a key that can only read products and add
to your cart. Disconnect on tosslist.local, or remove the app from your
Kroger account, to revoke it.

The API can't place orders or read the cart, so you check out in the
Fred Meyer app as usual.
"""

import base64
import fcntl
import hashlib
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

import common

API = "https://api.kroger.com/v1"
USER_SCOPES = "cart.basic:write product.compact"
APP_SCOPES = "product.compact"
TOKEN_PATH = common.KROGER_TOKEN_PATH
EXPIRY_MARGIN = 120  # renew a couple of minutes early


class KrogerAuthError(Exception):
    """The Fred Meyer connection needs to be set up again."""


# ---------- barcode -> Kroger product ID ----------

def _expand_upce(code):
    """Small packages (soda cans, gum) use 8-digit UPC-E codes.
    Expand to the full 12-digit UPC-A."""
    n, d, check = code[0], code[1:7], code[7]
    last = d[5]
    if last in "012":
        body = d[0:2] + last + "0000" + d[2:5]
    elif last == "3":
        body = d[0:3] + "00000" + d[3:5]
    elif last == "4":
        body = d[0:4] + "00000" + d[4]
    else:
        body = d[0:5] + "0000" + last
    return n + body + check


def upc_candidates(barcode):
    """Kroger IDs are 13 digits: the barcode without its last (check) digit,
    padded with zeros. Return likely matches, best guess first."""
    code = (barcode or "").strip()
    if not code.isdigit():
        return []
    if len(code) == 8:
        code = _expand_upce(code)
    out = []
    for c in (code[:-1].zfill(13), code.zfill(13)):
        if len(c) == 13 and c not in out:
            out.append(c)
    return out


# ---------- sign-in (done once from tosslist.local) ----------

def make_pkce():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(cfg, state, challenge):
    params = {
        "scope": USER_SCOPES,
        "client_id": cfg["kroger_client_id"],
        "redirect_uri": cfg["kroger_redirect_uri"],
        "response_type": "code",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{API}/connect/oauth2/authorize?{urlencode(params)}"


def code_from_pasted_url(text):
    """Pull ?code=...&state=... out of an address the user copied."""
    q = parse_qs(urlparse(text.strip()).query)
    return (q.get("code") or [None])[0], (q.get("state") or [None])[0]


def _basic_auth(cfg):
    raw = f"{cfg['kroger_client_id']}:{cfg['kroger_client_secret']}"
    return {"Authorization": "Basic " + base64.b64encode(raw.encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded"}


def _token_request(cfg, data, session):
    r = session.post(f"{API}/connect/oauth2/token", data=data,
                     headers=_basic_auth(cfg), timeout=15)
    if r.status_code in (400, 401, 403):
        raise KrogerAuthError(f"Kroger refused the sign-in ({r.status_code})")
    r.raise_for_status()
    tok = r.json()
    tok["expires_at"] = time.time() + float(tok.get("expires_in", 1800))
    return tok


def _save_token(tok):
    with common.writable():
        common.write_json(TOKEN_PATH, tok)
        try:
            os.chmod(TOKEN_PATH, 0o600)
        except OSError:
            pass


def exchange_code(cfg, code, verifier, session=None):
    tok = _token_request(cfg, {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": cfg["kroger_redirect_uri"],
        "code_verifier": verifier}, session or requests.Session())
    _save_token(tok)
    return tok


def disconnect():
    with common.writable():
        try:
            os.remove(TOKEN_PATH)
        except FileNotFoundError:
            pass


# ---------- everyday use ----------

class KrogerClient:
    def __init__(self, cfg, session=None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self._app_token = None
        self._app_expires = 0

    # -- keys --
    def app_token(self):
        """Key for looking up stores and products (no account needed)."""
        if self._app_token and time.time() < self._app_expires - EXPIRY_MARGIN:
            return self._app_token
        tok = _token_request(self.cfg, {"grant_type": "client_credentials",
                                        "scope": APP_SCOPES}, self.session)
        self._app_token, self._app_expires = tok["access_token"], tok["expires_at"]
        return self._app_token

    def user_token(self, force_refresh=False):
        """Key for your cart. Renews itself; Kroger hands out a new renewal
        key each time, so this is done under a file lock."""
        # The lock file lives in RAM (/run): a file held open for writing on
        # the real disk would stop it from being locked again (issue #7).
        os.makedirs(common.LOCK_DIR, exist_ok=True)
        with open(os.path.join(common.LOCK_DIR, "kroger-token.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            tok = common.read_json(TOKEN_PATH, None)
            if not tok:
                raise KrogerAuthError("Fred Meyer isn't connected")
            if force_refresh or time.time() >= tok.get("expires_at", 0) - EXPIRY_MARGIN:
                if not tok.get("refresh_token"):
                    raise KrogerAuthError("Fred Meyer sign-in expired")
                new = _token_request(self.cfg, {
                    "grant_type": "refresh_token",
                    "refresh_token": tok["refresh_token"]}, self.session)
                new.setdefault("refresh_token", tok["refresh_token"])
                _save_token(new)
                tok = new
            return tok["access_token"]

    def _get(self, path, params):
        r = self.session.get(API + path, params=params, timeout=10,
                             headers={"Authorization": f"Bearer {self.app_token()}",
                                      "Accept": "application/json"})
        if r.status_code == 401:
            self._app_token = None
            r = self.session.get(API + path, params=params, timeout=10,
                                 headers={"Authorization": f"Bearer {self.app_token()}",
                                          "Accept": "application/json"})
        if r.status_code in (401, 403):
            raise KrogerAuthError("Kroger rejected the app's Client ID or secret")
        r.raise_for_status()
        return r.json()

    # -- stores --
    def find_stores(self, zip_code, limit=10):
        data = self._get("/locations", {"filter.zipCode.near": zip_code,
                                        "filter.limit": limit})
        stores = []
        for s in data.get("data", []):
            a = s.get("address") or {}
            stores.append({
                "id": s.get("locationId"),
                "name": s.get("name") or s.get("chain") or "Store",
                "chain": s.get("chain", ""),
                "address": ", ".join(x for x in (a.get("addressLine1"), a.get("city")) if x),
            })
        return [s for s in stores if s["id"]]

    # -- products --
    def find_product(self, barcode, location_id):
        """Returns {"upc", "name", "price", "stock"} or None if not sold there.
        "stock" is Kroger's stock level at that store: "HIGH", "LOW",
        "TEMPORARILY_OUT_OF_STOCK", or None if Kroger didn't say."""
        cands = upc_candidates(barcode)
        if not cands:
            return None
        data = self._get("/products", {"filter.productId": ",".join(cands),
                                       "filter.locationId": location_id})
        found = {p.get("productId") or p.get("upc"): p for p in data.get("data", [])}
        for c in cands:
            p = found.get(c)
            if p:
                return {"upc": c, "name": _product_name(p), "price": _price(p),
                        "stock": _stock(p)}
        return None

    # -- cart --
    def add_to_cart(self, upc, quantity=1, modality="PICKUP"):
        body = {"items": [{"upc": upc, "quantity": quantity, "modality": modality}]}
        for attempt in (0, 1):
            r = self.session.put(f"{API}/cart/add", json=body, timeout=15,
                                 headers={"Authorization":
                                          f"Bearer {self.user_token(force_refresh=attempt == 1)}"})
            if r.status_code != 401:
                break
        if r.status_code in (401, 403):
            raise KrogerAuthError("Fred Meyer sign-in needs to be redone")
        r.raise_for_status()


def _product_name(p):
    name = (p.get("description") or "").strip()
    size = ((p.get("items") or [{}])[0].get("size") or "").strip()
    return f"{name} ({size})" if name and size else name


def _stock(p):
    inv = (p.get("items") or [{}])[0].get("inventory") or {}
    level = inv.get("stockLevel")
    return level.upper() if isinstance(level, str) and level else None


def in_stock(product, low_counts=True):
    """True / False, or None when Kroger gave no stock level."""
    level = product.get("stock")
    if level is None:
        return None
    if level == "TEMPORARILY_OUT_OF_STOCK":
        return False
    if level == "LOW":
        return bool(low_counts)
    return True


def _price(p):
    price = (p.get("items") or [{}])[0].get("price") or {}
    promo, regular = price.get("promo") or 0, price.get("regular") or 0
    if promo and (not regular or promo < regular):
        return promo
    return regular or None
