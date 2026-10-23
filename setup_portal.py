"""Setup and settings page for Tosslist.

At power-on it waits for WiFi. If the device can't get online (first boot,
new router, moved house), it creates its own WiFi network called
"Tosslist Setup". Join it from your phone and the setup page pops up.

Once online, the same page lives at http://tosslist.local for changing
settings, choosing where scans go (Todoist, Fred Meyer cart, or both),
connecting Fred Meyer, and seeing recent scans.
"""

import functools
import hashlib
import hmac
import json
import logging
import os
import secrets
import subprocess
import threading
import time

import requests
from flask import Flask, Response, redirect, render_template_string, request, session

import alerts
import barcode
import common
import kroger
import walmart

log = logging.getLogger("tosslist")

HOTSPOT_NAME = "tosslist-setup"
HOTSPOT_SSID = "Tosslist Setup"
HOTSPOT_IP = "10.42.0.1"
WIFI_IFACE = "wlan0"
BOOT_WAIT_SECONDS = 90       # time to wait for home WiFi at power-on
RECHECK_SECONDS = 180        # in setup mode, retry home WiFi this often

state = {
    "hotspot": False,
    "networks": [],          # WiFi names seen before the hotspot started
    "message": "",
    "error": "",
    "last_visit": 0.0,
    "oauth": {},             # Fred Meyer sign-in in progress
    "stores": [],            # last store search results
    "zip": "",
}
_net_lock = threading.Lock()
feedback = None
app = Flask(__name__)
# Settings cookie: never sent with requests started by other websites.
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True)


def _secret_key():
    key = None
    try:
        with open(common.SECRET_PATH) as f:
            key = f.read().strip()
    except OSError:
        pass
    if not key:
        key = secrets.token_hex(32)
        common.write_private(common.SECRET_PATH, key)
    return key


app.secret_key = _secret_key()


# ---------- PIN for settings (issue #11) ----------
#
# Everyday use (recent scans, the Walmart list) stays open. Changing settings
# needs the PIN. The PIN is stored only as a salted PBKDF2 hash. A printed
# reset card, scanned on the device itself, clears a forgotten PIN.

PIN_ITERATIONS = 100_000
UNLOCK_MINUTES = 30
MAX_TRIES = 5
LOCKOUT_SECONDS = 300
RESET_PREFIX = "TOSSLIST-RESET-"
_RESET_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O or 1/I mix-ups
state.update(pin_fails=0, pin_locked_until=0.0)


def pin_info():
    return common.read_json(common.PIN_PATH, None)


def _pin_hash(pin, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt),
                               iterations).hex()


def valid_pin(pin):
    return pin.isdigit() and 4 <= len(pin) <= 8


def set_pin(pin):
    info = pin_info() or {}
    salt = secrets.token_hex(16)
    reset = info.get("reset_code") or RESET_PREFIX + "".join(
        secrets.choice(_RESET_ALPHABET) for _ in range(8))
    new = {"salt": salt, "iterations": PIN_ITERATIONS,
           "hash": _pin_hash(pin, salt, PIN_ITERATIONS),
           "reset_code": reset, "version": secrets.token_hex(8)}
    common.write_private(common.PIN_PATH, json.dumps(new))
    return new


def check_pin(pin):
    info = pin_info()
    if not info or not valid_pin(pin):
        return False
    return hmac.compare_digest(_pin_hash(pin, info["salt"], info["iterations"]),
                               info["hash"])


def unlocked():
    info = pin_info()
    return bool(info) and session.get("pin_version") == info["version"] \
        and session.get("until", 0) > time.time()


def _unlock_session(info):
    session["pin_version"] = info["version"]
    session["until"] = time.time() + UNLOCK_MINUTES * 60


def require_unlocked(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not unlocked():
            state["error"] = ("Create a PIN first." if not pin_info()
                              else "Enter your PIN to change settings.")
            return redirect("/")
        return fn(*a, **kw)
    return wrapper


@app.route("/pin/create", methods=["POST"])
def pin_create():
    if pin_info():
        return redirect("/")
    pin, pin2 = request.form.get("pin", ""), request.form.get("pin2", "")
    if not valid_pin(pin):
        state["error"] = "Use 4 to 8 digits for the PIN."
    elif pin != pin2:
        state["error"] = "The two PINs didn't match. Try again."
    else:
        _unlock_session(set_pin(pin))
        state["message"] = ("PIN created. Next, print the PIN reset card (in the "
                            "PIN section below) and keep it somewhere safe.")
    return redirect("/")


@app.route("/pin/unlock", methods=["POST"])
def pin_unlock():
    now = time.time()
    if now < state["pin_locked_until"]:
        mins = int((state["pin_locked_until"] - now) // 60) + 1
        state["error"] = f"Too many wrong tries. Try again in {mins} minute{'s' if mins > 1 else ''}."
        return redirect("/")
    if check_pin(request.form.get("pin", "")):
        state["pin_fails"] = 0
        _unlock_session(pin_info())
    else:
        state["pin_fails"] += 1
        if state["pin_fails"] >= MAX_TRIES:
            state["pin_fails"] = 0
            state["pin_locked_until"] = now + LOCKOUT_SECONDS
            state["error"] = "Too many wrong tries. Try again in 5 minutes."
            log.warning("Settings PIN locked after %d wrong tries", MAX_TRIES)
        else:
            state["error"] = "That PIN isn't right."
    return redirect("/")


@app.route("/pin/lock", methods=["POST"])
def pin_lock():
    session.clear()
    return redirect("/")


@app.route("/pin/change", methods=["POST"])
@require_unlocked
def pin_change():
    pin, pin2 = request.form.get("new_pin", ""), request.form.get("new_pin2", "")
    if not valid_pin(pin):
        state["error"] = "Use 4 to 8 digits for the PIN."
    elif pin != pin2:
        state["error"] = "The two PINs didn't match. Try again."
    else:
        _unlock_session(set_pin(pin))
        state["message"] = "PIN changed."
    return redirect("/")


CARD_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title><style>
 body { margin:0; background:#fff; color:#111; font:16px/1.45 -apple-system, system-ui, sans-serif; }
 main { max-width:520px; margin:0 auto; padding:20px 16px; }
 .card { border:2px dashed #999; border-radius:12px; padding:18px; text-align:center; }
 .card svg { display:block; width:100%; height:110px; margin:10px 0 4px; }
 .code { font:600 1rem ui-monospace, Menlo, monospace; letter-spacing:.04em; }
 h1 { font-size:1.3rem; margin:0 0 6px; } p { margin:8px 0; }
 .actions { display:flex; gap:10px; margin-top:16px; }
 .actions a, .actions button { flex:1; padding:12px; border-radius:10px; font:inherit; font-weight:600;
   text-align:center; text-decoration:none; border:1px solid #2f7d4f; background:#2f7d4f; color:#fff; }
 .actions a.quiet { background:#fff; color:#2f7d4f; }
 @media print { .actions, .back { display:none; } main { padding:0; max-width:7in; }
   .card svg { height:1.1in; } }
</style></head><body><main>
<p class="back"><a href="/">← Back to settings</a></p>
<div class="card">
 <h1>{{ title }}</h1>
 {{ svg|safe }}
 <p class="code">{{ caption }}</p>
 {% for line in lines %}<p>{{ line }}</p>{% endfor %}
</div>
<div class="actions">
 <button onclick="window.print()">Print</button>
 <a class="quiet" href="{{ download }}" download>Download</a>
</div>
</main></body></html>"""


def card_page(title, code, caption, lines, download):
    """A printable card. The SVG is built by barcode.svg from our own codes."""
    return render_template_string(CARD_PAGE, title=title,
                                  svg=barcode.svg(code, label=caption, stretch=True),
                                  caption=caption, lines=lines, download=download)


def svg_download(svg, filename):
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.route("/pin/reset-card")
@require_unlocked
def pin_reset_card():
    code = pin_info()["reset_code"]
    return card_page("Tosslist PIN reset", code, "Tosslist PIN reset",
                     ["Forgot the settings PIN? Scan this card with Tosslist's scanner. "
                      "The PIN is cleared, and the next visit to the settings page "
                      "asks for a new one.",
                      "Keep it somewhere safe, away from the device: anyone with this "
                      "card and the scanner can reset the PIN."],
                     "/pin/reset-card.svg")


@app.route("/pin/reset-card.svg")
@require_unlocked
def pin_reset_card_svg():
    return svg_download(barcode.svg(pin_info()["reset_code"], label="Tosslist PIN reset"),
                        "tosslist-pin-reset.svg")




# ---------- talking to NetworkManager (nmcli) ----------

def run(cmd, timeout=60):
    """Run a command; return (exit_code, output). Swapped out in tests."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 1, "timed out"


def split_terse(line):
    """nmcli -t separates fields with ':' and escapes real colons as '\\:'."""
    fields, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur))
    return fields


def is_online():
    code, out = run(["nmcli", "-t", "-f", "STATE", "general"], timeout=10)
    return code == 0 and out.strip().startswith("connected") and not state["hotspot"]


def scan_networks():
    code, out = run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
                     "device", "wifi", "list", "--rescan", "yes"], timeout=30)
    best = {}
    for line in out.splitlines() if code == 0 else []:
        parts = split_terse(line)
        if len(parts) < 3 or not parts[0]:
            continue
        ssid, signal, security = parts[0], parts[1], parts[2]
        try:
            signal = int(signal)
        except ValueError:
            signal = 0
        if ssid not in best or signal > best[ssid]["signal"]:
            best[ssid] = {"ssid": ssid, "signal": signal,
                          "secure": security not in ("", "--")}
    return sorted(best.values(), key=lambda n: -n["signal"])


def saved_wifi_profiles():
    code, out = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
                    timeout=10)
    names = []
    for line in out.splitlines() if code == 0 else []:
        parts = split_terse(line)
        if len(parts) >= 2 and parts[1] == "802-11-wireless" \
                and parts[0] != HOTSPOT_NAME:
            names.append(parts[0])
    return names


def start_hotspot():
    with _net_lock:
        found = scan_networks()       # must scan before the hotspot is up
        if found:
            state["networks"] = found
        run(["nmcli", "connection", "delete", HOTSPOT_NAME], timeout=15)
        run(["nmcli", "connection", "add", "type", "wifi",
             "ifname", WIFI_IFACE, "con-name", HOTSPOT_NAME,
             "autoconnect", "no", "ssid", HOTSPOT_SSID,
             "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
             "ipv4.method", "shared",
             "ipv4.addresses", f"{HOTSPOT_IP}/24"], timeout=15)
        code, out = run(["nmcli", "connection", "up", HOTSPOT_NAME], timeout=30)
        state["hotspot"] = code == 0
        if code != 0:
            log.error("Could not start setup hotspot: %s", out)
        else:
            log.info("Setup hotspot '%s' is up", HOTSPOT_SSID)
            if feedback:
                feedback.setup_mode()


def stop_hotspot():
    run(["nmcli", "connection", "down", HOTSPOT_NAME], timeout=15)
    state["hotspot"] = False


def try_saved_networks():
    """Leave setup mode briefly to see if a known WiFi came back
    (e.g. the router was slower to restart than the Pi after a power cut)."""
    with _net_lock:
        stop_hotspot()
        run(["nmcli", "device", "wifi", "rescan"], timeout=15)
        time.sleep(5)
        for name in saved_wifi_profiles():
            code, _ = run(["nmcli", "--wait", "30", "connection", "up", name],
                          timeout=40)
            if code == 0:
                log.info("Reconnected to saved WiFi '%s'", name)
                return True
    start_hotspot()
    return False


def connect_wifi(ssid, password):
    with _net_lock:
        state["message"], state["error"] = f"Connecting to {ssid}...", ""
        stop_hotspot()
        run(["nmcli", "device", "wifi", "rescan"], timeout=15)
        time.sleep(4)
        cmd = ["nmcli", "--wait", "45", "device", "wifi", "connect", ssid,
               "ifname", WIFI_IFACE]
        if password:
            cmd += ["password", password]
        code, out = run(cmd, timeout=60)
    if code == 0:
        state["message"] = f"Connected to {ssid}."
        log.info("Joined WiFi '%s'", ssid)
        persist_wifi_profile()
        if feedback:
            feedback.success() if common.is_configured() else feedback.setup_mode()
        return True
    log.warning("Could not join '%s': %s", ssid, out.strip())
    run(["nmcli", "connection", "delete", ssid], timeout=15)  # drop bad profile
    state["message"] = ""
    state["error"] = (f"Couldn't connect to {ssid}. Check the password and "
                      "try again.")
    start_hotspot()
    return False


def persist_wifi_profile():
    """In read-only mode, NetworkManager saves the new WiFi network to RAM,
    where it would vanish at the next power-up. Copy the saved network
    onto the real disk (issue #7)."""
    if not common.read_only_active():
        return False
    code, out = run(["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device",
                     "show", WIFI_IFACE], timeout=10)
    name = split_terse(out.strip().splitlines()[0])[1] if code == 0 and out.strip() else ""
    if not name:
        return False
    code, out = run(["nmcli", "-t", "-f", "NAME,FILENAME", "connection", "show"],
                    timeout=10)
    path = ""
    for line in out.splitlines() if code == 0 else []:
        parts = split_terse(line)
        if len(parts) >= 2 and parts[0] == name:
            path = parts[1]
            break
    if not path.startswith("/etc/NetworkManager/system-connections/"):
        log.warning("Couldn't find where WiFi network '%s' is saved", name)
        return False
    dest = common.LOWER_ROOT + path
    try:
        with open(path, "rb") as f:
            data = f.read()
        with common.writable():
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
    except OSError as e:
        log.error("Couldn't save WiFi network '%s' permanently: %s", name, e)
        return False
    log.info("Saved WiFi network '%s' permanently", name)
    return True


def watch_network():
    """Power-on logic, then a slow background check while in setup mode."""
    deadline = time.time() + BOOT_WAIT_SECONDS
    while time.time() < deadline:
        if is_online():
            log.info("WiFi is connected")
            break
        time.sleep(3)
    else:
        log.info("No WiFi after %ss; starting setup mode", BOOT_WAIT_SECONDS)
        start_hotspot()
    while True:
        time.sleep(RECHECK_SECONDS)
        idle = time.time() - state["last_visit"] > 120
        if state["hotspot"] and idle and saved_wifi_profiles():
            try_saved_networks()


# ---------- checking a Todoist token (only possible when online) ----------

def check_token(token):
    """Returns None if OK, or an error message."""
    try:
        r = requests.get("https://api.todoist.com/api/v1/projects",
                         headers={"Authorization": f"Bearer {token}"},
                         params={"limit": 1}, timeout=10)
    except requests.RequestException:
        return None  # can't check offline; the scanner will report problems
    if r.status_code in (401, 403):
        return "Todoist didn't accept that API token. Copy it again and paste it in."
    return None


# ---------- web pages ----------

RULE_OPTIONS = [
    ("every", "Every store",
     "Each scan goes to every store that's on."),
    ("first_in_stock", "First in stock",
     "Goes to the first store in your order that carries it and has it in stock."),
    ("cheapest", "Cheapest in stock",
     "Goes to whichever store has it in stock for less. Walmart prices are walmart.com prices."),
]

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tosslist</title>
<style>
 :root { --bg:#f6f5f1; --card:#fff; --ink:#1f2328; --muted:#5b6370;
         --accent:#2f7d4f; --line:#dedbd2; --bad:#b3261e; }
 @media (prefers-color-scheme: dark) {
  :root { --bg:#16181b; --card:#1f2226; --ink:#eceae4; --muted:#a2a8b1;
          --accent:#5cc488; --line:#33373d; --bad:#f2827a; } }
 body { margin:0; background:var(--bg); color:var(--ink);
        font:16px/1.45 -apple-system, system-ui, sans-serif; }
 main { max-width:520px; margin:0 auto; padding:20px 16px 48px; }
 h1 { font-size:1.4rem; margin:4px 0 2px; }
 h2 { font-size:1.05rem; margin:0 0 10px; }
 p.sub { color:var(--muted); margin:0 0 18px; }
 .card { background:var(--card); border:1px solid var(--line);
         border-radius:12px; padding:16px; margin-bottom:14px; }
 label { display:block; font-weight:600; margin:12px 0 4px; }
 .hint { color:var(--muted); font-size:.88rem; margin-top:4px; }
 input, select { width:100%; box-sizing:border-box; padding:11px;
   font-size:16px; border:1px solid var(--line); border-radius:8px;
   background:var(--bg); color:var(--ink); }
 button { width:100%; margin-top:6px; padding:13px; font-size:1rem;
   font-weight:600; border:0; border-radius:10px; background:var(--accent);
   color:#fff; }
 .ok { color:var(--accent); font-weight:600; }
 .bad { color:var(--bad); font-weight:600; }
 table { width:100%; border-collapse:collapse; font-size:.9rem; }
 td { padding:6px 4px; border-top:1px solid var(--line); vertical-align:top; }
 td.t { color:var(--muted); white-space:nowrap; }
 details summary { cursor:pointer; font-weight:600; }
 .check { display:flex; gap:10px; align-items:flex-start; font-weight:600; margin:12px 0 0; }
 .check input { width:auto; margin-top:4px; }
 .check span { font-weight:400; color:var(--muted); display:block; font-size:.88rem; }
 a.btn { display:block; text-align:center; text-decoration:none; padding:13px;
   border-radius:10px; background:var(--accent); color:#fff; font-weight:600; margin-top:10px; }
 button.quiet { background:transparent; color:var(--muted); border:1px solid var(--line); }
 button:disabled { opacity:.3; }
 .row { display:flex; gap:8px; } .row > * { flex:1; }
</style></head><body><main>
<h1>Tosslist</h1>
<p class="sub">{% if hotspot %}Setup mode{% else %}Settings{% endif %}
 {% if configured %}· <span class="ok">ready to scan</span>{% endif %}</p>

{% if data_warning %}<div class="card bad">{{ data_warning }}</div>{% endif %}
{% if problems %}<div class="card"><h2 class="bad" style="margin-bottom:6px">Needs attention</h2>
 {% for p in problems %}<p style="margin:6px 0"><b>{{ p.title }}.</b> <span class="hint">{{ p.text }}</span></p>{% endfor %}
</div>{% endif %}
{% if message %}<div class="card ok">{{ message }}</div>{% endif %}
{% if error %}<div class="card bad">{{ error }}</div>{% endif %}

{% if not hotspot and walmart_items %}
<div class="card">
 <h2>Walmart list · {{ walmart_count }} item{% if walmart_count != 1 %}s{% endif %}{% if walmart_total %} · about ${{ "%.2f"|format(walmart_total) }}{% endif %}</h2>
 <table>{% for it in walmart_items %}<tr>
  <td>{{ it.qty }}×</td><td>{{ it.name }}{% if it.price %}<br><span class="hint">${{ "%.2f"|format(it.price) }} each</span>{% endif %}</td>
  <td style="width:1%"><form method="post" action="/walmart/remove"><input type="hidden" name="id" value="{{ it.id }}">
   <button class="quiet" style="margin:0;padding:6px 10px;width:auto" aria-label="Remove {{ it.name }}">✕</button></form></td></tr>
 {% endfor %}</table>
 <a class="btn" href="/walmart/open" target="_blank" rel="noopener">Open in Walmart cart</a>
 <p class="hint">Opens walmart.com with these items in your cart. Prices are
 walmart.com prices and may differ in store.{% if walmart_opened %} Last opened {{ walmart_opened }}.{% endif %}</p>
 <form method="post" action="/walmart/clear"><button class="quiet">{% if walmart_opened %}Done, clear the list{% else %}Clear the list{% endif %}</button></form>
</div>
{% endif %}

{% if not pin_set %}
<div class="card">
 <h2>Create a PIN</h2>
 <p class="hint" style="margin-top:0">A PIN keeps guests on your WiFi from changing
 Tosslist's settings. Scanning, recent scans and the Walmart list stay open to everyone.</p>
 <form method="post" action="/pin/create">
  <label for="pin">New PIN (4 to 8 digits)</label>
  <input id="pin" name="pin" type="password" inputmode="numeric" pattern="[0-9]*" autocomplete="new-password" required>
  <label for="pin2">Same PIN again</label>
  <input id="pin2" name="pin2" type="password" inputmode="numeric" pattern="[0-9]*" autocomplete="new-password" required>
  <button style="margin-top:14px">Create PIN</button>
 </form>
</div>
{% elif not unlocked %}
<div class="card">
 <h2>Settings are locked</h2>
 <form method="post" action="/pin/unlock">
  <label for="unlock_pin" style="margin-top:0">PIN</label>
  <input id="unlock_pin" name="pin" type="password" inputmode="numeric" pattern="[0-9]*" autocomplete="current-password" required autofocus>
  <button style="margin-top:12px">Unlock settings</button>
 </form>
 <p class="hint">Forgot it? Scan your printed PIN reset card with Tosslist's scanner.</p>
</div>
{% endif %}

{% if unlocked %}
<form method="post" action="/save">
 <div class="card">
  {% if hotspot %}
   <h2>1. Your home WiFi</h2>
  {% else %}
   <details><summary>Change WiFi network</summary>
   <p class="hint">Only change this if you're switching networks. The
   device will drop off this page while it reconnects.</p>
  {% endif %}
  <label for="ssid">Network</label>
  <select id="ssid" name="ssid">
   <option value="">{% if hotspot %}Choose your network{% else %}Keep current network{% endif %}</option>
   {% for n in networks %}<option value="{{ n.ssid }}">{{ n.ssid }}{% if n.secure %} 🔒{% endif %}</option>{% endfor %}
  </select>
  <label for="ssid_manual">Or type the name</label>
  <input id="ssid_manual" name="ssid_manual" autocomplete="off" autocapitalize="none">
  <label for="wifi_password">WiFi password</label>
  <input id="wifi_password" name="wifi_password" type="password" autocomplete="off">
  {% if not hotspot %}</details>{% endif %}
 </div>

 {% if not hotspot %}
 <div class="card">
  <h2>Where scans go</h2>
  <input type="hidden" name="has_dest" value="1">
  <label class="check"><input type="checkbox" name="send_to_todoist" value="1"
   {% if send_to_todoist %}checked{% endif %}><div>Todoist list
   <span>{% if todoist_ready %}Ready{% else %}Needs an API token below{% endif %}</span></div></label>
  <label class="check"><input type="checkbox" name="send_to_kroger" value="1"
   {% if send_to_kroger %}checked{% endif %}><div>Fred Meyer cart
   <span>{% if kroger_ready %}Ready · {{ kroger_store }}{% elif kroger_connected %}Pick your store below{% else %}Set up below{% endif %}</span></div></label>
  <label class="check"><input type="checkbox" name="send_to_walmart" value="1"
   {% if send_to_walmart %}checked{% endif %}><div>Walmart list
   <span>{% if walmart_ready %}Ready · open it in your Walmart cart below{% else %}Set up below{% endif %}</span></div></label>
  <p class="hint">Turn on any mix. The rules below decide which stores get each scan.</p>
 </div>

 <div class="card">
  <h2>Store order and rules</h2>
  <input type="hidden" name="has_rules" value="1">
  <p class="hint" style="margin-top:0">Your stores, most preferred first.</p>
  <table>{% for st in store_rows %}<tr>
   <td style="width:1.6em">{{ loop.index }}.</td>
   <td>{{ st.label }}<br><span class="hint">{% if st.ready %}{% if st.on %}On{% else %}Off{% endif %}{% else %}Not set up{% endif %}</span></td>
   <td style="width:1%;white-space:nowrap">
    <button class="quiet" name="move" value="{{ st.key }}:up" style="margin:0;padding:6px 10px;width:auto"
     {% if loop.first %}disabled{% endif %} aria-label="Move {{ st.label }} up">↑</button>
    <button class="quiet" name="move" value="{{ st.key }}:down" style="margin:0;padding:6px 10px;width:auto"
     {% if loop.last %}disabled{% endif %} aria-label="Move {{ st.label }} down">↓</button></td></tr>
  {% endfor %}</table>

  <label style="margin-top:16px">Which stores get each scan</label>
  {% for key, title, desc in rule_options %}
  <label class="check"><input type="radio" name="routing_rule" value="{{ key }}"
   {% if routing_rule == key %}checked{% endif %}><div>{{ title }}<span>{{ desc }}</span></div></label>
  {% endfor %}

  <label class="check" style="margin-top:16px"><input type="checkbox" name="kroger_low_counts" value="1"
   {% if kroger_low_counts %}checked{% endif %}><div>Count "low stock" at Fred Meyer as in stock
   <span>Unchecked: low stock also moves a scan to your next store.</span></div></label>

  <label for="todoist_when" style="margin-top:16px">Todoist, when it's on</label>
  <select id="todoist_when" name="todoist_when">
   <option value="always" {% if todoist_when == "always" %}selected{% endif %}>Always add to Todoist</option>
   <option value="fallback" {% if todoist_when == "fallback" %}selected{% endif %}>Only if no store took it</option>
  </select>
  <p class="hint">Recent scans shows why each scan went where it did. While
  you're on "Every store", it also shows which store each other rule would
  have picked, so you can try a rule before switching.</p>
 </div>
 {% endif %}

 <div class="card">
  <h2>{% if hotspot %}2. {% endif %}Todoist</h2>
  <label for="token">API token</label>
  <input id="token" name="token" autocomplete="off" autocapitalize="none"
   placeholder="{% if has_token %}Saved ✓ (leave blank to keep){% else %}Paste token here{% endif %}">
  <p class="hint">In Todoist: Settings → Integrations → Developer → copy API token.</p>
  <label for="project">List name</label>
  <input id="project" name="project" value="{{ project }}">
  <p class="hint">The device creates this list if it doesn't exist. Share it
  with your partner from the Todoist app.</p>
  <label><input type="checkbox" name="skip_duplicates" value="1"
   style="width:auto" {% if skip_duplicates %}checked{% endif %}>
   Don't add items already on the list</label>
 </div>

 {% if not hotspot %}
 <div class="card">
  <h2>Fred Meyer app details</h2>
  <p class="hint">From the app you registered at developer.kroger.com. See the
  setup guide for the steps.</p>
  <label for="kroger_client_id">Client ID</label>
  <input id="kroger_client_id" name="kroger_client_id" value="{{ kroger_client_id }}"
   autocomplete="off" autocapitalize="none">
  <label for="kroger_client_secret">Client secret</label>
  <input id="kroger_client_secret" name="kroger_client_secret" autocomplete="off"
   autocapitalize="none" placeholder="{% if has_kroger_secret %}Saved ✓ (leave blank to keep){% else %}Paste secret here{% endif %}">
  <label for="kroger_modality">Order type</label>
  <select id="kroger_modality" name="kroger_modality">
   <option value="PICKUP" {% if kroger_modality == "PICKUP" %}selected{% endif %}>Pickup</option>
   <option value="DELIVERY" {% if kroger_modality == "DELIVERY" %}selected{% endif %}>Delivery</option>
  </select>
  <details style="margin-top:12px"><summary>Redirect URI</summary>
   <p class="hint">Must exactly match the Redirect URI in your Kroger app.</p>
   <input name="kroger_redirect_uri" value="{{ kroger_redirect_uri }}" autocapitalize="none">
  </details>
 </div>

 <div class="card">
  <h2>Updates and alerts</h2>
  <input type="hidden" name="has_updates" value="1">
  <label class="check" style="margin-top:0"><input type="checkbox" name="auto_update" value="1"
   {% if auto_update %}checked{% endif %}><div>Install new releases each night
   <span>Checks GitHub around 3:30–4:15 AM. Only reviewed, released versions are installed.</span></div></label>
  <label class="check"><input type="checkbox" name="send_alerts" value="1"
   {% if send_alerts %}checked{% endif %}><div>Alert me in Todoist when Tosslist needs attention
   <span>Adds a red (priority 1) task due today, like "Reconnect Fred Meyer", and closes it by itself once it's fixed.</span></div></label>
 </div>

 <div class="card">
  <h2>Walmart app details</h2>
  <p class="hint">From the app you create at walmart.io. See the setup guide.</p>
  <label for="walmart_consumer_id">Consumer ID</label>
  <input id="walmart_consumer_id" name="walmart_consumer_id" value="{{ walmart_consumer_id }}"
   autocomplete="off" autocapitalize="none">
  <label for="walmart_key_version">Key version</label>
  <input id="walmart_key_version" name="walmart_key_version" value="{{ walmart_key_version }}"
   inputmode="numeric">
  <p class="hint">Shown next to your key on walmart.io. Usually 1.</p>
 </div>
 {% endif %}
 <button type="submit">Save</button>
</form>

{% if not hotspot %}
<div class="card" style="margin-top:14px">
 <h2>Fred Meyer account</h2>
 {% if kroger_connected %}
  <p class="ok">Connected ✓</p>
  <p class="hint">Tosslist can add items to your cart. It can't place orders or
  see your password.</p>
  <form method="post" action="/kroger/disconnect"><button class="quiet">Disconnect</button></form>
 {% elif kroger_has_app %}
  <p class="hint">Sign in on Fred Meyer's own page to let Tosslist add to your cart.</p>
  <a class="btn" href="/kroger/connect">Connect Fred Meyer account</a>
  <details style="margin-top:14px"><summary>Sign-in page didn't load afterwards?</summary>
   <p class="hint">If you ended on a page that wouldn't open, copy that page's
   full address from the address bar and paste it here.</p>
   <form method="post" action="/kroger/paste">
    <input name="url" autocomplete="off" autocapitalize="none" placeholder="http://...?code=...">
    <button>Finish connecting</button></form>
  </details>
 {% else %}
  <p class="hint">Enter and save the Client ID and secret above first.</p>
 {% endif %}
</div>

{% if kroger_has_app %}
<div class="card">
 <h2>Fred Meyer store</h2>
 <p>{% if kroger_store %}<span class="ok">{{ kroger_store }}</span>{% else %}No store picked yet.{% endif %}</p>
 <p class="hint">Prices and availability come from this store.</p>
 <form method="post" action="/kroger/stores" class="row">
  <input name="zip" inputmode="numeric" placeholder="ZIP code" value="{{ zip }}">
  <button style="margin-top:0">Find stores</button>
 </form>
 {% if stores %}
 <form method="post" action="/kroger/store">
  {% for st in stores %}
  <label class="check"><input type="radio" name="store_id" value="{{ st.id }}"
   {% if st.id == kroger_location_id %}checked{% endif %}><div>{{ st.name }}
   <span>{{ st.address }}</span></div></label>
  {% endfor %}
  <button>Use this store</button>
 </form>
 {% endif %}
</div>
{% endif %}

<div class="card">
 <h2>Walmart key</h2>
 {% if walmart_public_key %}
  <p class="hint">Paste this whole block into walmart.io when it asks for your
  public key. It's safe to share; the private half stays on this device.</p>
  <textarea id="pubkey" readonly rows="9" style="width:100%;box-sizing:border-box;font:12px/1.35 ui-monospace,Menlo,monospace;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)">{{ walmart_public_key }}</textarea>
  <button type="button" onclick="var t=document.getElementById('pubkey');t.select();try{navigator.clipboard.writeText(t.value);this.textContent='Copied'}catch(e){document.execCommand('copy');this.textContent='Copied'}">Copy public key</button>
  {% if walmart_consumer_id %}
  <form method="post" action="/walmart/check"><button class="quiet">Test Walmart connection</button></form>
  {% endif %}
  <details style="margin-top:12px"><summary>Make a new key</summary>
   <p class="hint">Only if you need to start over. The old key stops working, so
   you'd paste the new one into walmart.io again.</p>
   <form method="post" action="/walmart/key"><input type="hidden" name="replace" value="1">
    <button class="quiet">Replace key</button></form>
  </details>
 {% else %}
  <p class="hint">Walmart needs a security key to accept requests. Tosslist
  makes one for you.</p>
  <form method="post" action="/walmart/key"><button>Create Walmart key</button></form>
 {% endif %}
</div>

<div class="card">
 <h2>Updates</h2>
 <p>Version <b>{{ version }}</b><br>
 <span class="hint">Read-only protection: {% if read_only %}<span class="ok">on</span>{% else %}off{% endif %}</span></p>
 {% if update_status %}
 <p class="hint">Last update: {{ update_status.time }} —
  {% if update_status.result == "failed" %}<span class="bad">{{ update_status.message }}</span>{% else %}{{ update_status.message }}{% endif %}
  {% if update_status.version %}({{ update_status.version }}){% endif %}</p>
 {% endif %}
 <form method="post" action="/update/now">
  <button class="quiet">Update now</button></form>
 <p class="hint">Installs the newest release if there is one. The Pi restarts
 twice and is back in about five minutes; nothing restarts if it's already
 up to date.</p>
</div>
{% endif %}

<div class="card">
 <h2>PIN</h2>
 <p class="hint" style="margin-top:0">Settings stay unlocked on this device for {{ unlock_minutes }} minutes.</p>
 <form method="post" action="/pin/lock"><button class="quiet">Lock settings now</button></form>
 <a class="btn" href="/pin/reset-card">Print the PIN reset card</a>
 <details style="margin-top:14px"><summary>Change PIN</summary>
  <form method="post" action="/pin/change">
   <label for="new_pin">New PIN (4 to 8 digits)</label>
   <input id="new_pin" name="new_pin" type="password" inputmode="numeric" pattern="[0-9]*" autocomplete="new-password" required>
   <label for="new_pin2">Same PIN again</label>
   <input id="new_pin2" name="new_pin2" type="password" inputmode="numeric" pattern="[0-9]*" autocomplete="new-password" required>
   <button style="margin-top:12px">Change PIN</button>
  </form>
 </details>
</div>
{% endif %}

{% if recent %}
<div class="card" style="margin-top:14px">
 <h2>Recent scans</h2>
 {% if queued %}<p class="hint">{{ queued }} waiting for internet.</p>{% endif %}
 <table>{% for r in recent %}<tr><td class="t">{{ r.time }}</td>
  <td>{{ r.name or r.barcode }}<br><span class="hint">{{ r.result }}</span></td></tr>
 {% endfor %}</table>
</div>
{% endif %}
</main></body></html>"""


@app.before_request
def _note_visit():
    state["last_visit"] = time.time()


@app.route("/")
def index():
    cfg = common.load_config()
    msg, err = state["message"], state["error"]
    state["message"] = state["error"] = ""
    wb = walmart.basket()
    return render_template_string(
        PAGE, hotspot=state["hotspot"], networks=state["networks"],
        message=msg, error=err, configured=common.is_configured(cfg),
        has_token=bool(cfg.get("todoist_token")),
        project=cfg.get("project_name", ""),
        skip_duplicates=cfg.get("skip_duplicates", True),
        send_to_todoist=cfg.get("send_to_todoist", True),
        send_to_kroger=cfg.get("send_to_kroger", False),
        todoist_ready=common.todoist_ready(cfg),
        kroger_ready=common.kroger_ready(cfg),
        kroger_connected=common.kroger_connected(cfg),
        kroger_has_app=bool(cfg.get("kroger_client_id") and cfg.get("kroger_client_secret")),
        kroger_client_id=cfg.get("kroger_client_id", ""),
        has_kroger_secret=bool(cfg.get("kroger_client_secret")),
        kroger_modality=cfg.get("kroger_modality", "PICKUP"),
        kroger_redirect_uri=cfg.get("kroger_redirect_uri", ""),
        kroger_store=cfg.get("kroger_location_name", ""),
        kroger_location_id=cfg.get("kroger_location_id", ""),
        stores=state["stores"], zip=state["zip"],
        data_warning=common.DATA_WARNING,
        problems=[{"title": alerts.TITLES[k], "text": alerts.HOW_TO_FIX[k].format(**d)}
                  for k, d in alerts.active_problems(cfg).items() if k != "data_lost"],
        send_alerts=cfg.get("send_alerts", True),
        pin_set=bool(pin_info()), unlocked=unlocked(), unlock_minutes=UNLOCK_MINUTES,
        version=common.app_file("VERSION", "unknown"),
        read_only=common.read_only_active(),
        update_status=common.read_json(common.UPDATE_STATUS_PATH, None),
        auto_update=cfg.get("auto_update", True),
        store_rows=[{"key": k, "label": common.STORE_LABELS[k],
                     "on": bool(cfg.get(f"send_to_{k}")),
                     "ready": (common.kroger_ready(cfg) if k == "kroger"
                               else common.walmart_ready(cfg))}
                    for k in common.store_order(cfg)],
        rule_options=RULE_OPTIONS,
        routing_rule=cfg.get("routing_rule", "every"),
        kroger_low_counts=cfg.get("kroger_low_counts", True),
        todoist_when=cfg.get("todoist_when", "always"),
        send_to_walmart=cfg.get("send_to_walmart", False),
        walmart_ready=common.walmart_ready(cfg),
        walmart_consumer_id=cfg.get("walmart_consumer_id", ""),
        walmart_key_version=cfg.get("walmart_key_version", "1"),
        walmart_public_key=walmart.public_key(),
        walmart_items=wb["items"],
        walmart_count=sum(it["qty"] for it in wb["items"]),
        walmart_total=walmart.basket_total(wb["items"]),
        walmart_opened=wb.get("opened_at"),
        recent=common.read_json(common.RECENT_PATH, [])[:10],
        queued=common.queue_size())


@app.route("/save", methods=["POST"])
@require_unlocked
def save():
    f = request.form
    updates = {"skip_duplicates": f.get("skip_duplicates") == "1"}
    project = f.get("project", "").strip()
    if project:
        updates["project_name"] = project
    token = f.get("token", "").strip()
    if token:
        if not state["hotspot"]:
            problem = check_token(token)
            if problem:
                state["error"] = problem
                return redirect("/")
        updates["todoist_token"] = token
    if f.get("has_dest"):
        updates["send_to_todoist"] = f.get("send_to_todoist") == "1"
        updates["send_to_kroger"] = f.get("send_to_kroger") == "1"
        updates["send_to_walmart"] = f.get("send_to_walmart") == "1"
    old = common.load_config()
    cid = f.get("kroger_client_id")
    if cid is not None and cid.strip() != old.get("kroger_client_id", ""):
        updates["kroger_client_id"] = cid.strip()
        kroger.disconnect()          # a sign-in belongs to one app
    secret = f.get("kroger_client_secret", "").strip()
    if secret:
        updates["kroger_client_secret"] = secret
    if f.get("kroger_modality") in ("PICKUP", "DELIVERY"):
        updates["kroger_modality"] = f["kroger_modality"]
    uri = f.get("kroger_redirect_uri", "").strip()
    if uri:
        updates["kroger_redirect_uri"] = uri
    if f.get("walmart_consumer_id") is not None:
        updates["walmart_consumer_id"] = f["walmart_consumer_id"].strip()
    kv = f.get("walmart_key_version", "").strip()
    if kv:
        updates["walmart_key_version"] = kv
    if f.get("has_updates"):
        updates["auto_update"] = f.get("auto_update") == "1"
        updates["send_alerts"] = f.get("send_alerts") == "1"
    if f.get("has_rules"):
        if f.get("routing_rule") in {k for k, _, _ in RULE_OPTIONS}:
            updates["routing_rule"] = f["routing_rule"]
        if f.get("todoist_when") in ("always", "fallback"):
            updates["todoist_when"] = f["todoist_when"]
        updates["kroger_low_counts"] = f.get("kroger_low_counts") == "1"
    move = f.get("move", "")
    if ":" in move:
        key, direction = move.split(":", 1)
        order = common.store_order(common.load_config())
        if key in order and direction in ("up", "down"):
            i = order.index(key)
            j = i - 1 if direction == "up" else i + 1
            if 0 <= j < len(order):
                order[i], order[j] = order[j], order[i]
                updates["store_order"] = order
    cfg = common.save_config(updates)

    ssid = (f.get("ssid_manual", "").strip() or f.get("ssid", "").strip())
    if ssid:
        password = f.get("wifi_password", "")
        threading.Thread(target=connect_wifi, args=(ssid, password),
                         daemon=True).start()
        return render_template_string(CONNECTING, ssid=ssid)
    if state["hotspot"]:
        state["error"] = "Pick your home WiFi network so the device can get online."
    else:
        notes = []
        if cfg.get("send_to_todoist") and not common.todoist_ready(cfg):
            notes.append("Todoist is on but needs an API token.")
        if cfg.get("send_to_kroger") and not common.kroger_ready(cfg):
            notes.append("Fred Meyer is on but isn't finished: connect your "
                         "account and pick a store below.")
        if cfg.get("send_to_walmart") and not common.walmart_ready(cfg):
            notes.append("Walmart is on but isn't finished: add the Consumer ID "
                         "and create the Walmart key.")
        if not any(cfg.get(k) for k in ("send_to_todoist", "send_to_kroger", "send_to_walmart")):
            notes.append("Every destination is off, so scans won't go anywhere.")
        state["message"] = "Saved. " + " ".join(notes) if notes else "Saved."
        if cfg.get("routing_rule", "every") != "every" and not (
                common.kroger_ready(cfg) or common.walmart_ready(cfg)):
            state["message"] += (" Store rules only matter once a store is set up.")
        if feedback:
            feedback.ready() if common.is_configured(cfg) else feedback.setup_mode()
    return redirect("/")


# ---------- Fred Meyer sign-in and store picker ----------

@app.route("/kroger/connect")
@require_unlocked
def kroger_connect():
    cfg = common.load_config()
    if not (cfg.get("kroger_client_id") and cfg.get("kroger_client_secret")):
        state["error"] = "Save your Fred Meyer Client ID and secret first."
        return redirect("/")
    verifier, challenge = kroger.make_pkce()
    key = secrets.token_urlsafe(16)
    state["oauth"] = {key: verifier}       # only the latest attempt is valid
    return redirect(kroger.authorize_url(cfg, key, challenge))


def _finish_connect(code, key):
    verifier = state["oauth"].pop(key, None) if key else None
    if not code or not verifier:
        state["error"] = ("That sign-in link is old or incomplete. Tap "
                          "Connect Fred Meyer account and try again.")
        return redirect("/")
    try:
        kroger.exchange_code(common.load_config(), code, verifier)
    except kroger.KrogerAuthError:
        state["error"] = ("Kroger didn't accept the sign-in. Check that the "
                          "Client secret is right and the Redirect URI matches "
                          "your Kroger app exactly, then try again.")
        return redirect("/")
    except requests.RequestException:
        state["error"] = "Couldn't reach Kroger. Check the internet and try again."
        return redirect("/")
    alerts.note("kroger", True)
    cfg = common.load_config()
    state["message"] = ("Fred Meyer connected. " +
                        ("" if cfg.get("kroger_location_id") else "Now pick your store. ") +
                        ("" if cfg.get("send_to_kroger") else
                         "Turn on Fred Meyer cart under Where scans go when you're ready."))
    return redirect("/")


@app.route("/kroger/callback")
@require_unlocked
def kroger_callback():
    if request.args.get("error"):
        state["error"] = "Fred Meyer sign-in was cancelled or refused."
        return redirect("/")
    return _finish_connect(request.args.get("code"), request.args.get("state"))


@app.route("/kroger/paste", methods=["POST"])
@require_unlocked
def kroger_paste():
    code, key = kroger.code_from_pasted_url(request.form.get("url", ""))
    return _finish_connect(code, key)


@app.route("/kroger/disconnect", methods=["POST"])
@require_unlocked
def kroger_disconnect():
    kroger.disconnect()
    state["message"] = "Fred Meyer disconnected."
    return redirect("/")


@app.route("/kroger/stores", methods=["POST"])
@require_unlocked
def kroger_stores():
    z = request.form.get("zip", "").strip()
    state["zip"] = z
    if not (len(z) == 5 and z.isdigit()):
        state["error"] = "Enter a 5-digit ZIP code."
        return redirect("/")
    try:
        state["stores"] = kroger.KrogerClient(common.load_config()).find_stores(z)
    except kroger.KrogerAuthError:
        state["error"] = "Kroger rejected the Client ID or secret. Check them above."
        return redirect("/")
    except requests.RequestException:
        state["error"] = "Couldn't reach Kroger. Check the internet and try again."
        return redirect("/")
    if not state["stores"]:
        state["error"] = "No stores found near that ZIP code."
    return redirect("/")


@app.route("/kroger/store", methods=["POST"])
@require_unlocked
def kroger_store():
    sid = request.form.get("store_id", "")
    match = next((st for st in state["stores"] if st["id"] == sid), None)
    if not match:
        state["error"] = "Pick a store from the list."
        return redirect("/")
    common.save_config({"kroger_location_id": sid,
                        "kroger_location_name": f"{match['name']}, {match['address']}"})
    state["stores"] = []
    state["message"] = f"Store set: {match['name']}."
    return redirect("/")


CONNECTING = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connecting</title><style>body{font:17px/1.5 -apple-system,system-ui,sans-serif;
margin:0;padding:28px 18px;max-width:520px}</style></head><body>
<h2>Connecting to {{ ssid }}…</h2>
<p>Your phone will drop off the setup network now. That's expected.</p>
<p>If the green light on the scanner goes <b>solid</b>, it worked. Reconnect your
phone to your home WiFi and visit <b>http://tosslist.local</b> anytime
to change settings.</p>
<p>If it couldn't connect, the <b>Tosslist Setup</b> network comes back
in about a minute so you can try again.</p></body></html>"""


# ---------- Updates (issue #12) ----------

def start_update():
    """Run update.sh in the background, outside this service, because it
    may restart the Pi. Replaced in tests."""
    repo = common.app_file("REPO")
    if not repo or not os.path.exists(os.path.join(repo, "update.sh")):
        return False
    subprocess.Popen(["systemd-run", "--no-block", "--unit", "tosslist-update-now",
                      "--collect", "/bin/bash", os.path.join(repo, "update.sh")],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


@app.route("/update/now", methods=["POST"])
@require_unlocked
def update_now():
    if start_update():
        state["message"] = ("Checking for updates. If there's a new release, the Pi "
                            "restarts twice and is back in about five minutes. "
                            "Refresh this page to see the result.")
    else:
        state["error"] = ("Couldn't find Tosslist's copy from GitHub. Run the "
                          "installer again over SSH.")
    return redirect("/")


# ---------- Walmart ----------

@app.route("/walmart/key", methods=["POST"])
@require_unlocked
def walmart_key():
    if walmart.has_key() and request.form.get("replace") != "1":
        return redirect("/")
    walmart.create_key()
    state["message"] = ("Walmart key created. Copy the public key below and "
                        "paste it into your app on walmart.io.")
    return redirect("/")


@app.route("/walmart/check", methods=["POST"])
@require_unlocked
def walmart_check():
    cfg = common.load_config()
    if not common.walmart_ready(cfg):
        state["error"] = "Add the Consumer ID and create the key first."
        return redirect("/")
    try:
        walmart.WalmartClient(cfg).check()
        alerts.note("walmart", True)
        state["message"] = "Walmart accepted the connection ✓"
    except walmart.WalmartAuthError:
        alerts.note("walmart", False)
        state["error"] = ("Walmart didn't accept it. Check the Consumer ID and key "
                          "version, and that this public key is the one on walmart.io. "
                          "New keys can take a few minutes to start working.")
    except requests.RequestException:
        state["error"] = "Couldn't reach Walmart. Check the internet and try again."
    return redirect("/")


@app.route("/walmart/open")
def walmart_open():
    link = walmart.cart_link(walmart.basket()["items"])
    if not link:
        state["error"] = "The Walmart list is empty."
        return redirect("/")
    walmart.mark_opened()
    return redirect(link)


@app.route("/walmart/remove", methods=["POST"])
def walmart_remove():
    walmart.basket_remove(request.form.get("id", ""))
    return redirect("/")


@app.route("/walmart/clear", methods=["POST"])
def walmart_clear():
    walmart.basket_clear()
    state["message"] = "Walmart list cleared."
    return redirect("/")


# Phones check special web addresses to detect a sign-in page. While in
# setup mode, every address leads here, so the setup page pops up on its own.
@app.errorhandler(404)
def _captive(_e):
    if state["hotspot"]:
        return redirect(f"http://{HOTSPOT_IP}/", code=302)
    return "Not found", 404


def main():
    global feedback
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    feedback = common.Feedback()
    threading.Thread(target=watch_network, daemon=True).start()
    app.run(host="0.0.0.0", port=80, threaded=True)


if __name__ == "__main__":
    main()
