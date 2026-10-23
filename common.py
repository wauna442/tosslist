"""Shared helpers: settings file, offline queue, and LED/buzzer feedback.

Everything the device remembers lives in /var/lib/tosslist:
  config.json  - WiFi-independent settings (Todoist token, list name, ...)
  queue.json   - scans waiting to be sent while the internet is down
  cache.json   - barcode -> product name, so repeat items skip the lookup
  recent.json  - last few scans, shown on the settings page
  kroger_token.json - the Fred Meyer sign-in key (never your password)
  walmart_key.pem   - the key that signs Walmart requests (made on the device)
  walmart_basket.json - items waiting to be opened in your Walmart cart
"""

import json
import logging
import os
import threading
import time

DATA_DIR = os.environ.get("TOSSLIST_DATA_DIR", "/var/lib/tosslist")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
QUEUE_PATH = os.path.join(DATA_DIR, "queue.json")
CACHE_PATH = os.path.join(DATA_DIR, "cache.json")
RECENT_PATH = os.path.join(DATA_DIR, "recent.json")
KROGER_TOKEN_PATH = os.path.join(DATA_DIR, "kroger_token.json")
WALMART_KEY_PATH = os.path.join(DATA_DIR, "walmart_key.pem")
WALMART_BASKET_PATH = os.path.join(DATA_DIR, "walmart_basket.json")

DEFAULT_CONFIG = {
    # where each scan goes: either or both
    "send_to_todoist": True,
    "send_to_kroger": False,
    "send_to_walmart": False,
    "todoist_token": "",
    "project_name": "Grocery List",
    "skip_duplicates": True,   # don't add an item that's already on the list
    "buzzer_pin": None,        # GPIO number if you add a buzzer later, e.g. 18
    # Fred Meyer (Kroger API app you register at developer.kroger.com)
    "kroger_client_id": "",
    "kroger_client_secret": "",
    "kroger_redirect_uri": "http://tosslist.local/kroger/callback",
    "kroger_location_id": "",
    "kroger_location_name": "",
    "kroger_modality": "PICKUP",   # or DELIVERY
    # Walmart (app you create at walmart.io)
    "walmart_consumer_id": "",
    "walmart_key_version": "1",
    # Store routing (issue #4)
    "routing_rule": "every",          # every | first_in_stock | cheapest
    "store_order": ["kroger", "walmart"],
    "todoist_when": "always",         # always | fallback (only if no store took it)
    "kroger_low_counts": True,        # Fred Meyer "low stock" counts as in stock
}

log = logging.getLogger("tosslist")
_lock = threading.Lock()


# ---------- small, power-loss-safe JSON files ----------

def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def write_json(path, data):
    """Write to a temp file, flush to disk, then rename over the old file.
    If power is cut mid-write, you keep either the old file or the new one,
    never a half-written one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(os.path.dirname(path), os.O_RDONLY)
        os.fsync(dir_fd)
        os.close(dir_fd)
    except OSError:
        pass


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(read_json(CONFIG_PATH, {}))
    return cfg


def save_config(updates):
    with _lock:
        cfg = load_config()
        cfg.update(updates)
        write_json(CONFIG_PATH, cfg)
        return cfg


def todoist_ready(cfg):
    return bool(cfg.get("todoist_token")) and bool(cfg.get("project_name"))


def kroger_connected(cfg):
    return (bool(cfg.get("kroger_client_id")) and bool(cfg.get("kroger_client_secret"))
            and os.path.exists(KROGER_TOKEN_PATH))


def kroger_ready(cfg):
    return kroger_connected(cfg) and bool(cfg.get("kroger_location_id"))


STORES = ("kroger", "walmart")
STORE_LABELS = {"kroger": "Fred Meyer", "walmart": "Walmart"}


def store_order(cfg):
    """The ranked store list, with any store missing from it appended."""
    order = [s for s in (cfg.get("store_order") or []) if s in STORES]
    return order + [s for s in STORES if s not in order]


def walmart_ready(cfg):
    return bool(cfg.get("walmart_consumer_id")) and os.path.exists(WALMART_KEY_PATH)


def destinations(cfg=None):
    """Where a scan made right now should go: any of "todoist", "kroger",
    "walmart"."""
    cfg = cfg or load_config()
    out = []
    if cfg.get("send_to_todoist") and todoist_ready(cfg):
        out.append("todoist")
    if cfg.get("send_to_kroger") and kroger_ready(cfg):
        out.append("kroger")
    if cfg.get("send_to_walmart") and walmart_ready(cfg):
        out.append("walmart")
    return out


def is_configured(cfg=None):
    return bool(destinations(cfg))


# ---------- offline queue ----------

def queue_add(item):
    with _lock:
        q = read_json(QUEUE_PATH, [])
        q.append(item)
        write_json(QUEUE_PATH, q)


def queue_take_all():
    with _lock:
        q = read_json(QUEUE_PATH, [])
        if q:
            write_json(QUEUE_PATH, [])
        return q


def queue_size():
    return len(read_json(QUEUE_PATH, []))


def record_recent(entry, keep=20):
    with _lock:
        recent = read_json(RECENT_PATH, [])
        recent.insert(0, entry)
        write_json(RECENT_PATH, recent[:keep])


# ---------- feedback: the Pi's green LED (and optional buzzer) ----------

class Feedback:
    """Patterns:
      solid on        = ready to scan
      2 quick blinks  = added to the list
      1 long blink    = no internet; saved, will send later
      5 fast blinks   = problem (check the settings page)
      slow pulse      = setup mode, connect to the setup WiFi
    """

    def __init__(self, buzzer_pin=None):
        self.led = None
        for name in ("ACT", "led0"):
            path = f"/sys/class/leds/{name}"
            if os.path.isdir(path):
                self.led = path
                break
        self.buzzer = None
        if buzzer_pin is not None:
            try:
                from gpiozero import Buzzer
                self.buzzer = Buzzer(int(buzzer_pin))
            except Exception as e:  # buzzer is optional
                log.warning("Buzzer not available: %s", e)

    def _write(self, name, value):
        if not self.led:
            return
        try:
            with open(os.path.join(self.led, name), "w") as f:
                f.write(str(value))
        except OSError:
            pass

    def _set(self, on):
        self._write("brightness", 1 if on else 0)
        if self.buzzer:
            self.buzzer.on() if on else self.buzzer.off()

    def _pattern(self, on_s, off_s, times):
        self._write("trigger", "none")
        for _ in range(times):
            self._set(True)
            time.sleep(on_s)
            self._set(False)
            time.sleep(off_s)
        self.ready()

    def ready(self):
        self._write("trigger", "none")
        self._write("brightness", 1)
        if self.buzzer:
            self.buzzer.off()

    def setup_mode(self):
        self._write("trigger", "heartbeat")

    def success(self):
        self._pattern(0.12, 0.12, 2)

    def queued(self):
        self._pattern(1.0, 0.2, 1)

    def error(self):
        self._pattern(0.06, 0.06, 5)
