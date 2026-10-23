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

import contextlib
import fcntl
import json
import logging
import os
import subprocess
import threading
import time

# ---------- read-only mode (issue #7) ----------
#
# With Raspberry Pi OS's overlay file system on, everything written to the
# SD card goes to RAM and disappears at the next power-up, so a power cut
# can't damage the system. Tosslist's own data still has to survive, so it
# is kept on the real disk (mounted read-only at /media/root-ro) and that
# disk is unlocked only for the moment of each write.

SYSTEM_DATA_DIR = "/var/lib/tosslist"
LOWER_ROOT = os.environ.get("TOSSLIST_LOWER_ROOT", "/media/root-ro")
PROC_CMDLINE = os.environ.get("TOSSLIST_PROC_CMDLINE", "/proc/cmdline")
LOCK_DIR = os.environ.get("TOSSLIST_LOCK_DIR", "/run/tosslist")


def read_only_active():
    """True when the overlay (read-only) file system is on right now."""
    try:
        with open(PROC_CMDLINE) as f:
            return "overlayroot=tmpfs" in f.read().split()
    except OSError:
        return False


DATA_WARNING = ""


def _choose_data_dir():
    global DATA_WARNING
    if os.environ.get("TOSSLIST_DATA_DIR"):
        return os.environ["TOSSLIST_DATA_DIR"]
    lower = LOWER_ROOT + SYSTEM_DATA_DIR
    if read_only_active():
        if os.path.isdir(lower):
            return lower
        DATA_WARNING = ("Read-only mode is on, but Tosslist can't find its saved "
                        "data, so changes will be lost at the next restart. Run "
                        "the installer again over SSH.")
        logging.getLogger("tosslist").error(DATA_WARNING)
    return SYSTEM_DATA_DIR


DATA_DIR = _choose_data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
QUEUE_PATH = os.path.join(DATA_DIR, "queue.json")
CACHE_PATH = os.path.join(DATA_DIR, "cache.json")
RECENT_PATH = os.path.join(DATA_DIR, "recent.json")
KROGER_TOKEN_PATH = os.path.join(DATA_DIR, "kroger_token.json")
UPDATE_STATUS_PATH = os.path.join(DATA_DIR, "update-status.json")
PIN_PATH = os.path.join(DATA_DIR, "pin.json")
SECRET_PATH = os.path.join(DATA_DIR, "secret.key")


def write_private(path, data):
    """Write a small private file (owner-only), atomically."""
    with writable():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def remove_file(path):
    with writable():
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def app_file(name, default=""):
    """VERSION and REPO, written next to the program by install.sh."""
    try:
        with open(os.path.join(APP_DIR, name)) as f:
            return f.read().strip() or default
    except OSError:
        return default
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
    # Updates (issue #12)
    "auto_update": True,              # install new releases each night
    # Alerts (issue #10)
    "send_alerts": True,              # add a p1 Todoist task when Tosslist needs a person
}

log = logging.getLogger("tosslist")
_lock = threading.Lock()


# ---------- small, power-loss-safe JSON files ----------

def _remount(mode):
    """Remount the real disk "rw" or "ro". Replaced in tests."""
    subprocess.run(["mount", "-o", f"remount,{mode}", LOWER_ROOT],
                   check=True, capture_output=True, timeout=30)


_depth = threading.local()


@contextlib.contextmanager
def writable():
    """Unlock the real disk for the duration of the block, when read-only mode
    is on; otherwise do nothing. Safe to nest, and safe across the two
    Tosslist services, which take turns through a lock file in /run (RAM)."""
    if not DATA_DIR.startswith(LOWER_ROOT + "/"):
        yield
        return
    if getattr(_depth, "n", 0):
        _depth.n += 1
        try:
            yield
        finally:
            _depth.n -= 1
        return
    os.makedirs(LOCK_DIR, exist_ok=True)
    with open(os.path.join(LOCK_DIR, "rw.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _remount("rw")
        _depth.n = 1
        try:
            yield
        finally:
            _depth.n = 0
            os.sync()
            for attempt in range(10):
                try:
                    _remount("ro")
                    break
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    time.sleep(0.2)
            else:
                log.warning("Couldn't re-lock the SD card; it will be retried "
                            "after the next save")


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
    with writable():
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

@contextlib.contextmanager
def _shared(name):
    """A lock both Tosslist services respect (the settings page can now undo
    scans, so it touches the queue and recent scans too)."""
    with _lock:
        os.makedirs(LOCK_DIR, exist_ok=True)
        with open(os.path.join(LOCK_DIR, name + ".lock"), "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield


def queue_add(item):
    item.setdefault("queued_at", time.time())
    with _shared("queue"):
        q = read_json(QUEUE_PATH, [])
        q.append(item)
        write_json(QUEUE_PATH, q)


def queue_take_all():
    with _shared("queue"):
        q = read_json(QUEUE_PATH, [])
        if q:
            write_json(QUEUE_PATH, [])
        return q


def queue_size():
    return len(read_json(QUEUE_PATH, []))


def queue_remove(item_id):
    """Take one scan out of the queue. Returns it, or None if it isn't there."""
    with _shared("queue"):
        q = read_json(QUEUE_PATH, [])
        keep = [i for i in q if i.get("id") != item_id]
        if len(keep) == len(q):
            return None
        write_json(QUEUE_PATH, keep)
        return next(i for i in q if i.get("id") == item_id)


def update_recent(entry_id, changes):
    with _shared("recent"):
        recent = read_json(RECENT_PATH, [])
        for e in recent:
            if e.get("id") == entry_id:
                e.update(changes)
                write_json(RECENT_PATH, recent)
                return e
        return None


def record_recent(entry, keep=20):
    entry.setdefault("ts", time.time())
    with _shared("recent"):
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
