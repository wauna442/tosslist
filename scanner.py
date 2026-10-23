"""Tosslist: reads the USB barcode scanner, looks up the product, and sends
it to any mix of: a shared Todoist list, your Fred Meyer cart, and a Walmart
list you open in your Walmart cart (your choice on tosslist.local).

Runs as a background service (tosslist.service) and restarts itself
if anything goes wrong.
"""

import logging
import threading
import time
import uuid

import requests

import common
import alerts
import kroger
import teach
import walmart

log = logging.getLogger("tosslist")

USER_AGENT = "Tosslist/1.0 (home Raspberry Pi project)"
TODOIST_API = "https://api.todoist.com/api/v1"
RETRY_SECONDS = 60


# ---------- turning scanner "keystrokes" into a barcode ----------

_PLAIN = {f"KEY_{d}": d for d in "0123456789"}
_PLAIN.update({f"KEY_{c}": c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"})
_PLAIN.update({"KEY_MINUS": "-", "KEY_DOT": ".", "KEY_SLASH": "/",
               "KEY_SPACE": " ", "KEY_EQUAL": "="})
for _d in "0123456789":
    _PLAIN[f"KEY_KP{_d}"] = _d
_SHIFTED = {f"KEY_{c}": c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
_SHIFTED.update({"KEY_MINUS": "_", "KEY_EQUAL": "+"})
_SHIFT_KEYS = {"KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"}
_ENTER_KEYS = {"KEY_ENTER", "KEY_KPENTER"}


class BarcodeDecoder:
    """The scanner pretends to be a keyboard. Feed it key events; when it
    'presses Enter', you get the finished barcode back."""

    def __init__(self):
        self.buffer = []
        self.shift = False

    def feed(self, key, value):
        # value: 1 = key down, 0 = key up, 2 = held
        if key in _SHIFT_KEYS:
            self.shift = value != 0
            return None
        if value != 1:
            return None
        if key in _ENTER_KEYS:
            code = "".join(self.buffer).strip()
            self.buffer = []
            return code or None
        table = _SHIFTED if self.shift else _PLAIN
        ch = table.get(key) or _PLAIN.get(key)
        if ch:
            self.buffer.append(ch)
        return None


# ---------- barcode -> product name ----------

def _off_lookup(barcode, session):
    """Open Food Facts: free, no account needed. Best for food."""
    r = session.get(
        f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json",
        params={"fields": "product_name,product_name_en,brands,quantity"},
        headers={"User-Agent": USER_AGENT}, timeout=8)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    if data.get("status") != 1:
        return None
    p = data.get("product") or {}
    name = (p.get("product_name_en") or p.get("product_name") or "").strip()
    if not name:
        return None
    brand = (p.get("brands") or "").split(",")[0].strip()
    if brand and brand.lower() not in name.lower():
        name = f"{brand} {name}"
    return name


def _upcitemdb_lookup(barcode, session):
    """UPCitemdb free tier (100 lookups/day, no account). Catches non-food
    items like paper towels or soap."""
    r = session.get("https://api.upcitemdb.com/prod/trial/lookup",
                    params={"upc": barcode},
                    headers={"User-Agent": USER_AGENT}, timeout=8)
    if r.status_code in (400, 404, 429):
        return None
    r.raise_for_status()
    items = r.json().get("items") or []
    if not items:
        return None
    return (items[0].get("title") or "").strip() or None


def lookup_product(barcode, session=None):
    """Returns a product name, or None if nobody knows this barcode.
    Raises requests.RequestException if there's no internet."""
    taught = teach.get(barcode)              # you taught it a name (issue #9)
    if taught:
        return taught["name"]
    session = session or requests.Session()
    cache = common.read_json(common.CACHE_PATH, {})
    if barcode in cache:
        return cache[barcode]
    network_error = None
    name = None
    for source in (_off_lookup, _upcitemdb_lookup):
        try:
            name = source(barcode, session)
        except requests.RequestException as e:
            network_error = e
            continue
        if name:
            break
    if name is None and network_error is not None:
        raise network_error
    if name:
        cache[barcode] = name
        common.write_json(common.CACHE_PATH, cache)
    return name


# ---------- Todoist ----------

class TodoistAuthError(Exception):
    pass


class TodoistClient:
    def __init__(self, token, session=None):
        self.session = session or requests.Session()
        self.headers = {"Authorization": f"Bearer {token}"}
        self._project_cache = {}

    def _request(self, method, path, **kw):
        headers = dict(self.headers)
        headers.update(kw.pop("headers", {}))
        r = self.session.request(method, TODOIST_API + path,
                                 headers=headers, timeout=10, **kw)
        if r.status_code in (401, 403):
            raise TodoistAuthError("Todoist rejected the API token")
        r.raise_for_status()
        return r.json() if r.content else None

    def _get_all(self, path, params=None):
        """Todoist v1 returns pages: {"results": [...], "next_cursor": ...}"""
        params = dict(params or {})
        out = []
        while True:
            data = self._request("GET", path, params=params)
            if isinstance(data, list):  # tolerate un-paged responses
                return out + data
            out.extend(data.get("results", []))
            cursor = data.get("next_cursor")
            if not cursor:
                return out
            params["cursor"] = cursor

    def project_id(self, name):
        """Find the list by name, creating it if it doesn't exist yet."""
        if name in self._project_cache:
            return self._project_cache[name]
        for p in self._get_all("/projects"):
            if p.get("name", "").strip().lower() == name.strip().lower():
                self._project_cache[name] = p["id"]
                return p["id"]
        p = self._request("POST", "/projects", json={"name": name})
        self._project_cache[name] = p["id"]
        return p["id"]

    def open_task_names(self, project_id):
        tasks = self._get_all("/tasks", {"project_id": project_id})
        return {t.get("content", "").strip().lower() for t in tasks}

    def add_task(self, project_id, content, description="", request_id=None,
                 priority=None, due_string=None):
        body = {"content": content, "project_id": project_id,
                "description": description}
        if priority:
            body["priority"] = priority          # 4 = p1, Todoist's highest
        if due_string:
            body["due_string"] = due_string
        return self._request(
            "POST", "/tasks", json=body,
            headers={"X-Request-Id": request_id or str(uuid.uuid4())})

    def close_task(self, task_id):
        return self._request("POST", f"/tasks/{task_id}/close")

    def delete_task(self, task_id):
        return self._request("DELETE", f"/tasks/{task_id}")


# ---------- what happens on each scan ----------
#
# Routing rules (chosen on tosslist.local, see issue #4):
#   every           - every store that's on gets the item (the default)
#   first_in_stock  - stores are tried in your order; the first that carries
#                     it and has it in stock gets it
#   cheapest        - of the stores that have it in stock, the cheapest gets it
# Todoist is separate: always, or only when no store took the item.
#
# Principle IV: if a store can't be reached, the scan waits and retries. It
# never falls through to the next store because of a network problem.

STORES = common.STORES
LABELS = {"todoist": "Todoist", "kroger": "Fred Meyer", "walmart": "Walmart"}
RULES = {"every": "Every store", "first_in_stock": "First in stock",
         "cheapest": "Cheapest in stock"}
TOOK_IT = ("added", "maybe", "listed")
RESULT_TEXT = {
    "added": "added",
    "duplicate": "already on list",
    "not_found": "not sold at your store",
    "not_found_walmart": "not found at Walmart",
    "listed": "on Walmart list",
    "maybe": "may have been added, check cart",
    "auth": "sign-in problem, check settings",
    "out_of_stock": "out of stock",
    "passed": "had it, another store was picked",
    "not_needed": "skipped, a store had it",
}


store_order = common.store_order


def _todoist(item, cfg, todoist_factory):
    client = (todoist_factory or TodoistClient)(cfg["todoist_token"])
    if not item.get("name"):
        name = lookup_product(item["barcode"], client.session)
        item["name"] = (name or item.get("store_name")
                        or f"Unknown item {item['barcode']}")
    pid = client.project_id(cfg["project_name"])
    if cfg.get("skip_duplicates", True):
        if item["name"].strip().lower() in client.open_task_names(pid):
            return "duplicate"
    task = client.add_task(pid, item["name"], f"Barcode: {item['barcode']}",
                           request_id=item["id"])
    item["todoist_task_id"] = (task or {}).get("id")       # for undo (issue #8)
    return "added"


class _Stores:
    """Looks things up at each store (safe to repeat) and adds to it (not)."""

    def __init__(self, cfg, kroger_factory=None, walmart_factory=None):
        self.cfg = cfg
        self.factories = {"kroger": kroger_factory or kroger.KrogerClient,
                          "walmart": walmart_factory or walmart.WalmartClient}
        self.clients = {}

    def client(self, store):
        if store not in self.clients:
            self.clients[store] = self.factories[store](self.cfg)
        return self.clients[store]

    def lookup(self, store, item):
        """Returns the product (or None) and records a check on the item:
        {"found", "in_stock" (True/False/None), "price"}."""
        taught = teach.get(item["barcode"]) or {}
        if store == "kroger":
            product = self.client(store).find_product(
                item["barcode"], self.cfg["kroger_location_id"],
                ids=[taught["kroger_upc"]] if taught.get("kroger_upc") else None)
            avail = kroger.in_stock(product, self.cfg.get("kroger_low_counts", True)) \
                if product else None
        else:
            product = self.client(store).find_product(
                item["barcode"], item_id=taught.get("walmart_item_id"))
            avail = walmart.in_stock(product) if product else None
        item.setdefault("checks", {})[store] = {
            "found": product is not None, "in_stock": avail,
            "price": product.get("price") if product else None}
        if product:
            item.setdefault("store_name", product["name"])
        return product

    def add(self, store, item, product):
        if store == "kroger":
            try:
                self.client(store).add_to_cart(
                    product["upc"], 1, self.cfg.get("kroger_modality", "PICKUP"))
            except requests.ReadTimeout:
                # The request went out but no answer came back. Kroger can't
                # tell us what's in the cart, and resending could add it twice.
                return "maybe"
            return "added"
        walmart.basket_add(product, item["barcode"])
        item["walmart_item_id"] = product["item_id"]        # for undo (issue #8)
        return "listed"


def _not_found(store):
    return "not_found" if store == "kroger" else "not_found_walmart"


def _available(check):
    """Usable for routing: carried, and not reported out of stock.
    Unknown stock counts as available, since the store didn't say."""
    return check["found"] and check["in_stock"] is not False


def pick_first(checks, order):
    for s in order:
        if s in checks and _available(checks[s]):
            return s
    return None


def pick_cheapest(checks, order):
    avail = [s for s in order if s in checks and _available(checks[s])]
    priced = [s for s in avail if checks[s]["price"]]
    if priced:
        return min(priced, key=lambda s: (checks[s]["price"], order.index(s)))
    return avail[0] if avail else None


def _route(item, cfg, stores, results):
    rule, order = item["rule"], item["route_stores"]
    pick = pick_first if rule == "first_in_stock" else pick_cheapest
    for s in order:                      # a retry starts the decision over
        results.pop(s, None)
    item.pop("chosen", None)
    item["checks"] = {}
    products, looked, excluded = {}, set(), set()

    def usable():
        return {s: c for s, c in item["checks"].items()
                if s in order and s not in excluded}

    while True:
        for s in order:
            if s in looked or s in excluded:
                continue
            if rule == "first_in_stock" and pick(usable(), order):
                break                    # an earlier store has it; stop asking
            looked.add(s)
            try:
                products[s] = stores.lookup(s, item)
            except (kroger.KrogerAuthError, walmart.WalmartAuthError) as e:
                log.error("%s: %s", LABELS[s], e)
                results[s] = "auth"
                excluded.add(s)
                continue
            check = item["checks"][s]
            if not check["found"]:
                results[s] = _not_found(s)
            elif check["in_stock"] is False:
                results[s] = "out_of_stock"
        chosen = pick(usable(), order)
        if chosen is None:
            return
        try:
            results[chosen] = stores.add(chosen, item, products[chosen])
        except (kroger.KrogerAuthError, walmart.WalmartAuthError) as e:
            # Its sign-in failed: nothing was added there, so try the next store.
            log.error("%s: %s", LABELS[chosen], e)
            results[chosen] = "auth"
            excluded.add(chosen)
            continue
        item["chosen"] = chosen
        for s, c in usable().items():
            if s != chosen and _available(c):
                results[s] = "passed"
        return


def send_item(item, cfg, todoist_factory=None, kroger_factory=None,
              walmart_factory=None):
    """Work through the scan's pending steps. Records results in
    item["results"]. Raises requests.RequestException (no internet) with
    the unfinished steps left in item["pending"]."""
    item.setdefault("pending", ["todoist"])     # items queued by older versions
    item.setdefault("rule", "every")
    taught = teach.get(item["barcode"])
    if taught and not item.get("name"):
        item["name"] = taught["name"]
    results = item.setdefault("results", {})
    stores = _Stores(cfg, kroger_factory, walmart_factory)
    steps = [st for st in item["pending"] if st in STORES]
    steps += [st for st in item["pending"] if st == "route"]
    steps += [st for st in item["pending"] if st == "todoist"]
    for step in steps:
        try:
            if step == "route":
                _route(item, cfg, stores, results)
            elif step in STORES:               # "every store" rule
                product = stores.lookup(step, item)
                results[step] = (stores.add(step, item, product) if product
                                 else _not_found(step))
            elif (item.get("todoist_when") == "fallback"
                  and any(results.get(s) in TOOK_IT for s in STORES)):
                results["todoist"] = "not_needed"
            else:
                results["todoist"] = _todoist(item, cfg, todoist_factory)
        except (TodoistAuthError, kroger.KrogerAuthError,
                walmart.WalmartAuthError) as e:
            log.error("%s: %s", LABELS.get(step, step), e)
            results[step] = "auth"
        item["pending"].remove(step)
    if not item.get("name"):
        item["name"] = item.get("store_name")
    for s in STORES:                    # for Todoist alerts (issue #10)
        if s in results:
            alerts.note(s, results[s] != "auth")
    return results


def _store_text(item, s, code):
    text = f"{LABELS[s]}: {RESULT_TEXT[code]}"
    check = item.get("checks", {}).get(s) or {}
    price = check.get("price") or (item.get("price") if s == "kroger" else
                                   item.get("walmart_price"))
    if price and code in TOOK_IT + ("passed",):
        text += f" (${price:.2f})"
    if check.get("in_stock") is False and code in TOOK_IT:
        text += ", shown as out of stock"
    return text


def summarize(item):
    results = item.get("results", {})
    parts = []
    if "todoist" in results:
        parts.append(f"Todoist: {RESULT_TEXT[results['todoist']]}")
    order = item.get("route_stores") or list(STORES)
    for s in order:
        if s in results:
            parts.append(_store_text(item, s, results[s]))
    for step in item.get("pending", []):
        label = RULES.get(item.get("rule"), "Stores") if step == "route" else LABELS[step]
        parts.append(f"{label}: waiting for internet")
    text = " · ".join(parts)
    rule = item.get("rule", "every")
    if rule != "every" and "route" not in item.get("pending", []):
        if item.get("chosen"):
            text = f"{RULES[rule]} → {LABELS[item['chosen']]}. " + text
        else:
            text = f"{RULES[rule]}: no store had it. " + text
    elif rule == "every":
        checks = {s: c for s, c in item.get("checks", {}).items()}
        if len(checks) >= 2:
            first = pick_first(checks, order)
            cheap = pick_cheapest(checks, order)
            text += (" · Would pick: First in stock → "
                     f"{LABELS[first] if first else 'none'}, Cheapest → "
                     f"{LABELS[cheap] if cheap else 'none'}")
    return text


CARD_PREFIX = "TOSSLIST-"


def handle_card(code, feedback, todoist_factory=None):
    """Tosslist's own printed cards. Returns True if `code` was one; such
    codes are never sent to a store or list."""
    if not code.upper().startswith(CARD_PREFIX):
        return False
    if code.upper() == UNDO_CODE:
        undo_last(feedback, todoist_factory)
        return True
    entry = {"time": time.strftime("%Y-%m-%d %H:%M"), "barcode": code, "kind": "card"}
    info = common.read_json(common.PIN_PATH, None)
    if info and code.upper() == info.get("reset_code", "").upper():
        common.remove_file(common.PIN_PATH)
        log.info("Settings PIN cleared with the reset card")
        entry.update(name="PIN reset card",
                     result="Settings PIN cleared. Open the settings page to create a new one.")
        feedback.success()
    else:
        entry.update(name="Tosslist card", result="Not recognized (an old or unknown card)")
        feedback.error()
    common.record_recent(entry)
    return True


def handle_scan(barcode, feedback, todoist_factory=None, kroger_factory=None,
                walmart_factory=None):
    if handle_card(barcode, feedback, todoist_factory):
        return "card"
    cfg = common.load_config()
    entry = {"time": time.strftime("%Y-%m-%d %H:%M"), "barcode": barcode, "kind": "scan"}
    dests = common.destinations(cfg)
    if not dests:
        log.warning("Scanned %s but no destination is set up yet", barcode)
        entry["result"] = "not set up: open the settings page"
        common.record_recent(entry)
        feedback.error()
        return "not configured"

    rule = cfg.get("routing_rule", "every")
    if rule not in RULES:
        rule = "every"
    stores = [s for s in store_order(cfg) if s in dests]
    pending = list(stores) if rule == "every" else (["route"] if stores else [])
    if "todoist" in dests:
        pending.append("todoist")
    item = {"id": str(uuid.uuid4()), "barcode": barcode, "name": None,
            "pending": pending, "results": {}, "rule": rule,
            "route_stores": stores,
            "todoist_when": cfg.get("todoist_when", "always")}
    try:
        results = send_item(item, cfg, todoist_factory, kroger_factory, walmart_factory)
        if any(r in TOOK_IT + ("duplicate",) for r in results.values()):
            feedback.success()
        else:
            feedback.error()
        outcome = "done"
    except requests.RequestException as e:
        log.warning("No internet (%s); saved %s for later", e, barcode)
        common.queue_add(item)
        feedback.queued()
        outcome = "queued"
    entry.update(_recent_fields(item))
    log.info("%s: %s (%s)", barcode, entry["name"], entry["result"])
    common.record_recent(entry)
    return outcome


def retry_queue_once(todoist_factory=None, kroger_factory=None, walmart_factory=None):
    """Try to send anything saved while offline. Returns how many finished."""
    cfg = common.load_config()
    pending = common.queue_take_all()
    if not pending:
        return 0
    sent = 0
    for i, item in enumerate(pending):
        try:
            send_item(item, cfg, todoist_factory, kroger_factory, walmart_factory)
            sent += 1
        except requests.RequestException:
            for rest in pending[i:]:
                common.queue_add(rest)
            break
        finally:
            common.update_recent(item.get("id"), _recent_fields(item))
    if sent:
        log.info("Sent %d saved item(s)", sent)
    return sent


# ---------- undo (issue #8) ----------
#
# Scanning the printed undo card (or tapping Undo in Recent scans) reverses
# the most recent scan that hasn't been undone; scanning it again goes one
# further back. Fred Meyer's API can't remove cart items, so for those the
# person is told to remove it in the app.

UNDO_CODE = "TOSSLIST-UNDO"
UNDO_WINDOW = 24 * 60 * 60


def _undo_info(item):
    results = item.get("results", {})
    info = {}
    if results.get("todoist") == "added" and item.get("todoist_task_id"):
        info["todoist_task_id"] = item["todoist_task_id"]
    if results.get("walmart") == "listed" and item.get("walmart_item_id"):
        info["walmart_item_id"] = item["walmart_item_id"]
    if results.get("kroger") in ("added", "maybe"):
        info["kroger"] = True
    if item.get("pending"):
        info["queued"] = True
    return info


def _recent_fields(item):
    return {"id": item.get("id"), "name": item.get("name") or item.get("store_name"),
            "result": summarize(item), "undo": _undo_info(item)}


def undo_entry(entry, todoist_factory=None):
    """Reverse one recent scan. Returns (finished, explanation, what's left),
    so a retry never repeats a part that already worked."""
    info = dict(entry.get("undo") or {})
    parts = []
    if info.pop("queued", False):
        queued = common.queue_remove(entry.get("id"))
        if queued is None:
            fresh = next((e for e in common.read_json(common.RECENT_PATH, [])
                          if e.get("id") == entry.get("id")), {})
            if (fresh.get("undo") or {}).get("queued"):
                return (False, "it's being sent right now. Scan Undo again in a minute",
                        entry.get("undo"))
            info = dict(fresh.get("undo") or {})
        else:
            info = _undo_info(dict(queued, pending=[]))
            parts.append("taken out of the waiting scans")
    left = {}
    if info.get("todoist_task_id"):
        cfg = common.load_config()
        try:
            client = (todoist_factory or TodoistClient)(cfg["todoist_token"])
            client.delete_task(info["todoist_task_id"])
            parts.append("removed from Todoist")
        except (requests.RequestException, TodoistAuthError):
            left["todoist_task_id"] = info["todoist_task_id"]
            parts.append("couldn't reach Todoist to remove it, so scan Undo again later")
    if info.get("walmart_item_id"):
        parts.append("removed from the Walmart list"
                     if walmart.basket_decrement(info["walmart_item_id"])
                     else "already gone from the Walmart list")
    if info.get("kroger"):
        parts.append("remove it from your Fred Meyer cart in the app "
                     "(Fred Meyer doesn't let apps remove items)")
    text = ", ".join(parts) or "it hadn't added anything, so there was nothing to remove"
    return not left, text, left


def undo_last(feedback, todoist_factory=None, entry_id=None):
    """Undo the newest scan not yet undone (or a specific one)."""
    now = time.time()
    target = None
    for e in common.read_json(common.RECENT_PATH, []):
        if e.get("kind") != "scan" or e.get("undone") or not e.get("id"):
            continue
        if entry_id and e.get("id") != entry_id:
            continue
        if now - e.get("ts", 0) <= UNDO_WINDOW:
            target = e
        break
    record = {"time": time.strftime("%Y-%m-%d %H:%M"), "barcode": UNDO_CODE,
              "kind": "card", "name": "Undo"}
    if target is None:
        record["result"] = "Nothing to undo from the last 24 hours"
        common.record_recent(record)
        feedback.error()
        return False
    finished, text, left = undo_entry(target, todoist_factory)
    name = target.get("name") or target.get("barcode")
    record["result"] = f"{name}: {text}."
    if finished:
        common.update_recent(target["id"], {"undone": True,
                                            "result": target.get("result", "") + " · Undone"})
        feedback.success()
    else:
        common.update_recent(target["id"], {"undo": left})   # retry only what's left
        feedback.error()
    common.record_recent(record)
    log.info("Undo %s: %s", name, text)
    return finished


ALERT_EVERY = 5          # check for problems every 5th retry pass (~5 min)


def _retry_loop():
    passes = 0
    while True:
        time.sleep(RETRY_SECONDS)
        try:
            retry_queue_once()
        except Exception:
            log.exception("Retry failed")
        passes += 1
        if passes % ALERT_EVERY == 1:
            try:
                alerts.sync()
            except Exception:
                log.exception("Alert check failed")


# ---------- finding and reading the scanner ----------

def find_scanner():
    """Return the first keyboard-like USB input device (the scanner)."""
    import evdev
    from evdev import ecodes
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        keys = dev.capabilities().get(ecodes.EV_KEY, [])
        if ecodes.KEY_ENTER in keys and ecodes.KEY_1 in keys:
            return dev
        dev.close()
    return None


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from evdev import ecodes

    cfg = common.load_config()
    feedback = common.Feedback(cfg.get("buzzer_pin"))
    threading.Thread(target=_retry_loop, daemon=True).start()

    while True:
        dev = find_scanner()
        if dev is None:
            log.info("Waiting for the barcode scanner to be plugged in...")
            time.sleep(5)
            continue
        log.info("Using scanner: %s", dev.name)
        if common.is_configured():
            feedback.ready()
        else:
            feedback.setup_mode()
        decoder = BarcodeDecoder()
        try:
            dev.grab()  # keep scans from typing into the login screen
            for event in dev.read_loop():
                if event.type != ecodes.EV_KEY:
                    continue
                key = ecodes.KEY.get(event.code)
                if isinstance(key, list):
                    key = key[0]
                code = decoder.feed(key, event.value)
                if code:
                    handle_scan(code, feedback)
        except OSError:
            log.warning("Scanner unplugged; waiting for it to come back")
            time.sleep(2)


if __name__ == "__main__":
    main()
