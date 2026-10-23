"""Tosslist: reads the USB barcode scanner, looks up the product, and sends
it to a shared Todoist list, your Fred Meyer cart, or both (your choice on
tosslist.local).

Runs as a background service (tosslist.service) and restarts itself
if anything goes wrong.
"""

import logging
import threading
import time
import uuid

import requests

import common
import kroger

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

    def add_task(self, project_id, content, description="", request_id=None):
        return self._request(
            "POST", "/tasks",
            json={"content": content, "project_id": project_id,
                  "description": description},
            headers={"X-Request-Id": request_id or str(uuid.uuid4())})


# ---------- what happens on each scan ----------

LABELS = {"todoist": "Todoist", "kroger": "Fred Meyer"}
RESULT_TEXT = {
    "added": "added",
    "duplicate": "already on list",
    "not_found": "not sold at your store",
    "maybe": "may have been added, check cart",
    "auth": "sign-in problem, check settings",
}


def _todoist(item, cfg, todoist_factory):
    client = (todoist_factory or TodoistClient)(cfg["todoist_token"])
    if not item.get("name"):
        name = lookup_product(item["barcode"], client.session)
        item["name"] = (name or item.get("kroger_name")
                        or f"Unknown item {item['barcode']}")
    pid = client.project_id(cfg["project_name"])
    if cfg.get("skip_duplicates", True):
        if item["name"].strip().lower() in client.open_task_names(pid):
            return "duplicate"
    client.add_task(pid, item["name"], f"Barcode: {item['barcode']}",
                    request_id=item["id"])
    return "added"


def _kroger_send(item, cfg, kroger_factory):
    client = (kroger_factory or kroger.KrogerClient)(cfg)
    product = client.find_product(item["barcode"], cfg["kroger_location_id"])
    if product is None:
        return "not_found"
    item["kroger_name"], item["price"] = product["name"], product["price"]
    try:
        client.add_to_cart(product["upc"], 1, cfg.get("kroger_modality", "PICKUP"))
    except requests.ReadTimeout:
        # The request went out but no answer came back. Kroger can't tell us
        # what's in the cart, and resending could add it twice.
        return "maybe"
    return "added"


def send_item(item, cfg, todoist_factory=None, kroger_factory=None):
    """Send one scan to each destination still pending for it.
    Records results in item["results"]. Raises requests.RequestException
    (no internet) with the unfinished destinations left in item["pending"]."""
    item.setdefault("pending", ["todoist"])     # items queued by older versions
    results = item.setdefault("results", {})
    for dest in ("kroger", "todoist"):          # Fred Meyer first: it can supply the name
        if dest not in item["pending"]:
            continue
        try:
            if dest == "kroger":
                results[dest] = _kroger_send(item, cfg, kroger_factory)
            else:
                results[dest] = _todoist(item, cfg, todoist_factory)
        except (TodoistAuthError, kroger.KrogerAuthError) as e:
            log.error("%s: %s", LABELS[dest], e)
            results[dest] = "auth"
        item["pending"].remove(dest)
    if not item.get("name"):
        item["name"] = item.get("kroger_name")
    return results


def summarize(item):
    parts = []
    for dest in ("todoist", "kroger"):
        if dest in item.get("results", {}):
            text = f"{LABELS[dest]}: {RESULT_TEXT[item['results'][dest]]}"
            if dest == "kroger" and item.get("price") and item["results"][dest] in ("added", "maybe"):
                text += f" (${item['price']:.2f})"
            parts.append(text)
    for dest in item.get("pending", []):
        parts.append(f"{LABELS[dest]}: waiting for internet")
    return " · ".join(parts)


def handle_scan(barcode, feedback, todoist_factory=None, kroger_factory=None):
    cfg = common.load_config()
    entry = {"time": time.strftime("%Y-%m-%d %H:%M"), "barcode": barcode}
    dests = common.destinations(cfg)
    if not dests:
        log.warning("Scanned %s but no destination is set up yet", barcode)
        entry["result"] = "not set up: open the settings page"
        common.record_recent(entry)
        feedback.error()
        return "not configured"

    item = {"id": str(uuid.uuid4()), "barcode": barcode, "name": None,
            "pending": list(dests), "results": {}}
    try:
        results = send_item(item, cfg, todoist_factory, kroger_factory)
        if any(r in ("added", "duplicate", "maybe") for r in results.values()):
            feedback.success()
        else:
            feedback.error()
        outcome = "done"
    except requests.RequestException as e:
        log.warning("No internet (%s); saved %s for later", e, barcode)
        common.queue_add(item)
        feedback.queued()
        outcome = "queued"
    entry.update(name=item.get("name") or item.get("kroger_name"),
                 result=summarize(item))
    log.info("%s: %s (%s)", barcode, entry["name"], entry["result"])
    common.record_recent(entry)
    return outcome


def retry_queue_once(todoist_factory=None, kroger_factory=None):
    """Try to send anything saved while offline. Returns how many finished."""
    cfg = common.load_config()
    pending = common.queue_take_all()
    if not pending:
        return 0
    sent = 0
    for i, item in enumerate(pending):
        try:
            send_item(item, cfg, todoist_factory, kroger_factory)
            sent += 1
        except requests.RequestException:
            for rest in pending[i:]:
                common.queue_add(rest)
            break
    if sent:
        log.info("Sent %d saved item(s)", sent)
    return sent


def _retry_loop():
    while True:
        time.sleep(RETRY_SECONDS)
        try:
            retry_queue_once()
        except Exception:
            log.exception("Retry failed")


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
