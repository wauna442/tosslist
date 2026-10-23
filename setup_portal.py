"""Setup and settings page for Tosslist.

At power-on it waits for WiFi. If the device can't get online (first boot,
new router, moved house), it creates its own WiFi network called
"Tosslist Setup". Join it from your phone and the setup page pops up.

Once online, the same page lives at http://tosslist.local for changing
settings, choosing where scans go (Todoist, Fred Meyer cart, or both),
connecting Fred Meyer, and seeing recent scans.
"""

import logging
import secrets
import subprocess
import threading
import time

import requests
from flask import Flask, redirect, render_template_string, request

import common
import kroger

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
 .row { display:flex; gap:8px; } .row > * { flex:1; }
</style></head><body><main>
<h1>Tosslist</h1>
<p class="sub">{% if hotspot %}Setup mode{% else %}Settings{% endif %}
 {% if configured %}· <span class="ok">ready to scan</span>{% endif %}</p>

{% if message %}<div class="card ok">{{ message }}</div>{% endif %}
{% if error %}<div class="card bad">{{ error }}</div>{% endif %}

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
  <p class="hint">Turn on one or both. Each scan goes to everything that's on.</p>
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
        recent=common.read_json(common.RECENT_PATH, [])[:10],
        queued=common.queue_size())


@app.route("/save", methods=["POST"])
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
        if not cfg.get("send_to_todoist") and not cfg.get("send_to_kroger"):
            notes.append("Both destinations are off, so scans won't go anywhere.")
        state["message"] = "Saved. " + " ".join(notes) if notes else "Saved."
        if feedback:
            feedback.ready() if common.is_configured(cfg) else feedback.setup_mode()
    return redirect("/")


# ---------- Fred Meyer sign-in and store picker ----------

@app.route("/kroger/connect")
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
    cfg = common.load_config()
    state["message"] = ("Fred Meyer connected. " +
                        ("" if cfg.get("kroger_location_id") else "Now pick your store. ") +
                        ("" if cfg.get("send_to_kroger") else
                         "Turn on Fred Meyer cart under Where scans go when you're ready."))
    return redirect("/")


@app.route("/kroger/callback")
def kroger_callback():
    if request.args.get("error"):
        state["error"] = "Fred Meyer sign-in was cancelled or refused."
        return redirect("/")
    return _finish_connect(request.args.get("code"), request.args.get("state"))


@app.route("/kroger/paste", methods=["POST"])
def kroger_paste():
    code, key = kroger.code_from_pasted_url(request.form.get("url", ""))
    return _finish_connect(code, key)


@app.route("/kroger/disconnect", methods=["POST"])
def kroger_disconnect():
    kroger.disconnect()
    state["message"] = "Fred Meyer disconnected."
    return redirect("/")


@app.route("/kroger/stores", methods=["POST"])
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
