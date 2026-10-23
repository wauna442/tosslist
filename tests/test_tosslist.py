"""Tests that run on any computer, using fake versions of Todoist,
Fred Meyer (Kroger), Walmart, the product databases and the WiFi controls.

Run with:  python3 tests/test_tosslist.py
"""
import os, sys, tempfile, time
os.environ["TOSSLIST_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import requests
import common, scanner, setup_portal, kroger, walmart


class FakeFeedback:
    def __init__(self): self.calls = []
    def __getattr__(self, n): return lambda *a: self.calls.append(n)


class Resp:
    def __init__(self, code, data=None):
        self.status_code, self._d = code, data
        self.content = b"x" if data is not None else b""
    def json(self): return self._d
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError(str(self.status_code))


class FakeSession:
    """Simulates Open Food Facts, UPCitemdb and Todoist v1."""
    def __init__(self, offline=False, bad_token=False):
        self.offline, self.bad_token = offline, bad_token
        self.projects, self.tasks, self.calls = [], [], []
        self.cart, self.kroger_tokens_issued = [], 0
        self.cart_timeout = False
        self.expired_user_tokens = set()

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("params"), kw.get("json")))
        if self.offline: raise requests.ConnectionError("offline")
        if "openfoodfacts" in url:
            assert "User-Agent" in kw["headers"]
            if "0001" in url: return Resp(200, {"status": 1, "product": {"product_name": "Whole Milk", "brands": "Tillamook,Other"}})
            return Resp(404, {"status": 0})
        if "upcitemdb" in url:
            if kw["params"]["upc"] == "0002": return Resp(200, {"items": [{"title": "Bounty Paper Towels"}]})
            return Resp(404, {})
        if "todoist" in url:
            if self.bad_token: return Resp(401, {})
            path = url.split("/api/v1")[1]
            if method == "GET" and path == "/projects":
                if kw["params"].get("cursor"): return Resp(200, {"results": self.projects[1:], "next_cursor": None})
                return Resp(200, {"results": self.projects[:1], "next_cursor": "c1" if len(self.projects) > 1 else None})
            if method == "POST" and path == "/projects":
                p = {"id": f"p{len(self.projects)}", "name": kw["json"]["name"]}
                self.projects.append(p); return Resp(200, p)
            if method == "GET" and path == "/tasks":
                return Resp(200, {"results": [t for t in self.tasks if t["project_id"] == kw["params"]["project_id"]], "next_cursor": None})
            if method == "POST" and path == "/tasks":
                assert "X-Request-Id" in kw["headers"]
                self.tasks.append(kw["json"]); return Resp(200, kw["json"])
        if "api.kroger.com" in url:
            return self.kroger(method, url.split("/v1")[1], kw)
        if "developer.api.walmart.com" in url:
            return self.walmart(method, url.split("/affil/product/v2")[1], kw)
        raise AssertionError(url)

    def walmart(self, method, path, kw):
        h = kw["headers"]
        # verify the signature exactly as Walmart would, using the public key
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        import base64
        pub = serialization.load_pem_public_key(walmart.public_key().encode())
        msg = f"{h['WM_CONSUMER.ID']}\n{h['WM_CONSUMER.INTIMESTAMP']}\n{h['WM_SEC.KEY_VERSION']}\n".encode()
        try:
            pub.verify(base64.b64decode(h["WM_SEC.AUTH_SIGNATURE"]), msg, padding.PKCS1v15(), hashes.SHA256())
        except Exception:
            return Resp(401, {"error": "bad signature"})
        if h["WM_CONSUMER.ID"] != "wm-consumer" or self.bad_token:
            return Resp(401, {"error": "unknown consumer"})
        assert abs(int(h["WM_CONSUMER.INTIMESTAMP"]) - time.time() * 1000) < 60000
        assert method == "GET" and path == "/items"
        upc = kw["params"]["upc"]
        catalog = {"011110417005": {"itemId": 10450114, "name": "Great Value 2% Milk, 1 gal", "salePrice": 3.12, "upc": upc},
                   "042100005264": {"itemId": 55512, "name": "Small Soda 12 oz", "salePrice": 0.98}}
        if upc in catalog:
            return Resp(200, {"items": [catalog[upc]]})
        return Resp(400, {"errors": [{"code": 4002, "message": "Invalid UPC"}]})

    def kroger(self, method, path, kw):
        h = kw.get("headers", {})
        if path == "/connect/oauth2/token":
            assert h["Authorization"].startswith("Basic ")
            if self.bad_token: return Resp(401, {"error": "invalid_client"})
            d = kw["data"]
            self.kroger_tokens_issued += 1
            if d["grant_type"] == "client_credentials":
                assert d["scope"] == "product.compact"
                return Resp(200, {"access_token": "app", "expires_in": 1800})
            if d["grant_type"] == "authorization_code":
                assert d["code"] == "good-code" and len(d["code_verifier"]) >= 43
                assert d["redirect_uri"] == "http://tosslist.local/kroger/callback"
                return Resp(200, {"access_token": "user1", "refresh_token": "r1", "expires_in": 1800})
            if d["grant_type"] == "refresh_token":
                if d["refresh_token"] == "dead": return Resp(400, {"error": "invalid_grant"})
                return Resp(200, {"access_token": "user2", "refresh_token": "r2", "expires_in": 1800})
        if path == "/locations":
            assert h["Authorization"] == "Bearer app"
            assert kw["params"]["filter.zipCode.near"] == "97266"
            return Resp(200, {"data": [{"locationId": "70100123", "name": "Fred Meyer - Hawthorne",
                                         "chain": "FRED", "address": {"addressLine1": "3805 SE Hawthorne Blvd", "city": "Portland"}}]})
        if path == "/products":
            assert h["Authorization"] == "Bearer app"
            assert kw["params"]["filter.locationId"] == "70100123"
            ids = kw["params"]["filter.productId"].split(",")
            catalog = {
                "0001111041700": {"productId": "0001111041700", "description": "Kroger 2% Milk",
                                  "items": [{"size": "1 gal", "price": {"regular": 3.49, "promo": 2.99}}]},
                "0004210000526": {"productId": "0004210000526", "description": "Small Soda",
                                  "items": [{"size": "12 oz", "price": {"regular": 1.25, "promo": 0}}]},
            }
            return Resp(200, {"data": [catalog[i] for i in ids if i in catalog]})
        if path == "/cart/add":
            tok = h["Authorization"].split()[1]
            if tok in self.expired_user_tokens: return Resp(401, {})
            if self.cart_timeout: raise requests.ReadTimeout("no reply")
            self.cart.append((tok, kw["json"]["items"][0]))
            return Resp(204)
        raise AssertionError(path)


def reset():
    for p in (common.CONFIG_PATH, common.QUEUE_PATH, common.CACHE_PATH, common.RECENT_PATH,
              common.KROGER_TOKEN_PATH, common.WALMART_BASKET_PATH):
        if os.path.exists(p): os.remove(p)


def test_decoder():
    d = scanner.BarcodeDecoder()
    keys = [("KEY_0", 1), ("KEY_0", 0), ("KEY_7", 1), ("KEY_LEFTSHIFT", 1), ("KEY_A", 1),
            ("KEY_LEFTSHIFT", 0), ("KEY_B", 1), ("KEY_KP3", 1), ("KEY_ENTER", 1)]
    out = [d.feed(k, v) for k, v in keys]
    assert out[-1] == "07Ab3", out
    assert d.feed("KEY_ENTER", 1) is None   # empty scan ignored


def test_scan_flow():
    reset()
    common.save_config({"todoist_token": "t"})
    s = FakeSession(); s.projects = [{"id": "x", "name": "Other"}, {"id": "g", "name": "grocery list"}]
    make = lambda tok: scanner.TodoistClient(tok, s)
    fb = FakeFeedback()
    assert scanner.handle_scan("0001", fb, make) == "done"
    assert s.tasks[0]["content"] == "Tillamook Whole Milk" and s.tasks[0]["project_id"] == "g"  # found via page 2
    assert scanner.handle_scan("0001", fb, make) == "done"
    assert len(s.tasks) == 1
    assert common.read_json(common.RECENT_PATH, [])[0]["result"] == "Todoist: already on list"
    assert scanner.handle_scan("0002", fb, make) == "done"
    assert s.tasks[-1]["content"] == "Bounty Paper Towels"
    assert scanner.handle_scan("9999", fb, make) == "done"
    assert s.tasks[-1]["content"] == "Unknown item 9999"
    assert fb.calls.count("success") == 4
    assert common.read_json(common.CACHE_PATH, {})["0001"] == "Tillamook Whole Milk"


def test_creates_project():
    reset(); common.save_config({"todoist_token": "t", "project_name": "Trash Log"})
    s = FakeSession(); scanner.handle_scan("0001", FakeFeedback(), lambda t: scanner.TodoistClient(t, s))
    assert s.projects[0]["name"] == "Trash Log" and s.tasks[0]["project_id"] == "p0"


def test_offline_then_retry():
    reset(); common.save_config({"todoist_token": "t"})
    off = FakeSession(offline=True); fb = FakeFeedback()
    assert scanner.handle_scan("0001", fb, lambda t: scanner.TodoistClient(t, off)) == "queued"
    assert scanner.handle_scan("0002", fb, lambda t: scanner.TodoistClient(t, off)) == "queued"
    assert common.queue_size() == 2 and fb.calls == ["queued", "queued"]
    assert scanner.retry_queue_once(lambda t: scanner.TodoistClient(t, off)) == 0
    assert common.queue_size() == 2  # nothing lost
    on = FakeSession()
    assert scanner.retry_queue_once(lambda t: scanner.TodoistClient(t, on)) == 2
    assert common.queue_size() == 0
    assert [t["content"] for t in on.tasks] == ["Tillamook Whole Milk", "Bounty Paper Towels"]


def test_bad_token_and_unconfigured():
    reset(); fb = FakeFeedback()
    assert scanner.handle_scan("0001", fb) == "not configured"
    common.save_config({"todoist_token": "bad"})
    s = FakeSession(bad_token=True)
    assert scanner.handle_scan("0001", fb, lambda t: scanner.TodoistClient(t, s)) == "done"
    assert fb.calls == ["error", "error"]
    assert common.read_json(common.RECENT_PATH, [])[0]["result"] == "Todoist: sign-in problem, check settings"
    assert common.queue_size() == 0     # a bad token isn't retried forever


def test_split_terse_and_scan():
    assert setup_portal.split_terse(r"My\:Net:70:WPA2") == ["My:Net", "70", "WPA2"]
    setup_portal.run = lambda cmd, timeout=60: (0, "Home:40:WPA2\nHome:80:WPA2\nCafe:55:--\n:30:WPA2\n")
    nets = setup_portal.scan_networks()
    assert nets == [{"ssid": "Home", "signal": 80, "secure": True}, {"ssid": "Cafe", "signal": 55, "secure": False}]


def test_portal_pages():
    reset()
    cmds = []
    def fake_run(cmd, timeout=60):
        cmds.append(cmd)
        if "list" in cmd: return 0, "Home:80:WPA2\n"
        return 0, ""
    setup_portal.run = fake_run
    setup_portal.feedback = FakeFeedback()
    setup_portal.start_hotspot()
    assert setup_portal.state["hotspot"]
    c = setup_portal.app.test_client()
    html = c.get("/").get_data(as_text=True)
    assert "Setup mode" in html and "Home" in html and "1. Your home WiFi" in html
    # phone captive-portal probes get redirected to the setup page
    r = c.get("/generate_204"); assert r.status_code == 302 and "10.42.0.1" in r.location
    r = c.get("/hotspot-detect.html"); assert r.status_code == 302
    # XSS-safe: a malicious network name is escaped
    setup_portal.state["networks"] = [{"ssid": "<script>x</script>", "signal": 1, "secure": False}]
    assert "<script>x" not in c.get("/").get_data(as_text=True)
    # submit setup
    import threading
    started = []
    orig = threading.Thread
    setup_portal.threading.Thread = lambda target, args, daemon: type("T", (), {"start": lambda self: started.append(args)})()
    r = c.post("/save", data={"ssid": "Home", "wifi_password": "pw", "token": "tok123", "project": "Groceries", "skip_duplicates": "1"})
    setup_portal.threading.Thread = orig
    assert "Connecting to Home" in r.get_data(as_text=True)
    assert started == [("Home", "pw")]
    cfg = common.load_config()
    assert cfg["todoist_token"] == "tok123" and cfg["project_name"] == "Groceries"
    # connect succeeds
    assert setup_portal.connect_wifi("Home", "pw")
    assert not setup_portal.state["hotspot"]
    assert any(c[:4] == ["nmcli", "--wait", "45", "device"] and c[-1] == "pw" for c in cmds)


def test_portal_failed_wifi_restarts_hotspot():
    def fake_run(cmd, timeout=60):
        if "connect" in cmd: return 4, "Secrets were required"
        return 0, ""
    setup_portal.run = fake_run
    setup_portal.time.sleep = lambda s: None
    assert not setup_portal.connect_wifi("Home", "wrong")
    assert setup_portal.state["hotspot"] and "Couldn't connect" in setup_portal.state["error"]


def test_portal_settings_mode_blank_token_keeps_old():
    reset(); common.save_config({"todoist_token": "keepme"})
    setup_portal.state["hotspot"] = False
    c = setup_portal.app.test_client()
    html = c.get("/").get_data(as_text=True)
    assert "Saved ✓" in html and "Change WiFi network" in html
    r = c.post("/save", data={"token": "", "project": "Grocery List"})
    assert r.status_code == 302 and common.load_config()["todoist_token"] == "keepme"
    assert c.get("/nonexistent").status_code == 404
    # bad token rejected when online
    setup_portal.check_token = lambda t: "Todoist didn't accept that API token."
    c.post("/save", data={"token": "wrong"})
    assert common.load_config()["todoist_token"] == "keepme"
    assert "didn" in c.get("/").get_data(as_text=True)


# ---------------- Fred Meyer ----------------

def kroger_setup(session, location=True, todoist=False):
    reset()
    common.save_config({"send_to_todoist": todoist, "send_to_kroger": True,
                        "todoist_token": "t" if todoist else "",
                        "kroger_client_id": "cid", "kroger_client_secret": "sec",
                        "kroger_location_id": "70100123" if location else "",
                        "kroger_location_name": "Fred Meyer - Hawthorne"})
    common.write_json(common.KROGER_TOKEN_PATH, {"access_token": "user1", "refresh_token": "r1",
                                                 "expires_at": time.time() + 1800})
    return lambda cfg: kroger.KrogerClient(cfg, session)


def test_upc_candidates():
    assert kroger.upc_candidates("011110417005")[0] == "0001111041700"   # UPC-A
    assert kroger.upc_candidates("0011110417005")[0] == "0001111041700"  # EAN-13 form
    assert kroger.upc_candidates("04252614")[0] == "0004210000526"       # UPC-E (soda can)
    assert kroger.upc_candidates("abc") == [] and kroger.upc_candidates("") == []


def test_destinations():
    reset()
    common.save_config({"todoist_token": "t"})
    assert common.destinations() == ["todoist"]
    common.save_config({"send_to_kroger": True, "kroger_client_id": "c", "kroger_client_secret": "s"})
    assert common.destinations() == ["todoist"]          # not connected yet
    common.write_json(common.KROGER_TOKEN_PATH, {"access_token": "x"})
    assert common.destinations() == ["todoist"]          # no store yet
    common.save_config({"kroger_location_id": "70100123"})
    assert common.destinations() == ["todoist", "kroger"]
    common.save_config({"send_to_todoist": False})
    assert common.destinations() == ["kroger"]
    common.save_config({"send_to_kroger": False})
    assert common.destinations() == [] and not common.is_configured()


def test_kroger_only_scan():
    s = FakeSession(); kf = kroger_setup(s); fb = FakeFeedback()
    assert scanner.handle_scan("011110417005", fb, None, kf) == "done"
    assert s.cart == [("user1", {"upc": "0001111041700", "quantity": 1, "modality": "PICKUP"})]
    assert not s.tasks and fb.calls == ["success"]
    r = common.read_json(common.RECENT_PATH, [])[0]
    assert r["name"] == "Kroger 2% Milk (1 gal)"
    assert r["result"] == "Fred Meyer: added ($2.99)"           # sale price used
    # not sold there -> error blink, nothing added
    assert scanner.handle_scan("9999", fb, None, kf) == "done"
    assert fb.calls[-1] == "error" and len(s.cart) == 1
    assert common.read_json(common.RECENT_PATH, [])[0]["result"] == "Fred Meyer: not sold at your store"


def test_both_destinations():
    s = FakeSession(); kf = kroger_setup(s, todoist=True); fb = FakeFeedback()
    tf = lambda t: scanner.TodoistClient(t, s)
    common.save_config({"kroger_modality": "DELIVERY"})
    scanner.handle_scan("04252614", fb, tf, kf)     # unknown to food DBs, known to Fred Meyer
    assert s.cart[0][1] == {"upc": "0004210000526", "quantity": 1, "modality": "DELIVERY"}
    assert s.tasks[0]["content"] == "Small Soda (12 oz)"      # name borrowed from Fred Meyer
    assert common.read_json(common.RECENT_PATH, [])[0]["result"] == \
        "Todoist: added · Fred Meyer: added ($1.25)"
    # Todoist still gets items Fred Meyer doesn't carry
    scanner.handle_scan("0002", fb, tf, kf)
    assert s.tasks[-1]["content"] == "Bounty Paper Towels" and len(s.cart) == 1
    assert fb.calls == ["success", "success"]


def test_kroger_token_renewal():
    s = FakeSession(); kf = kroger_setup(s)
    common.write_json(common.KROGER_TOKEN_PATH, {"access_token": "old", "refresh_token": "r1",
                                                 "expires_at": time.time() - 10})
    scanner.handle_scan("011110417005", FakeFeedback(), None, kf)
    assert s.cart[0][0] == "user2"
    tok = common.read_json(common.KROGER_TOKEN_PATH, {})
    assert tok["refresh_token"] == "r2" and tok["expires_at"] > time.time()
    # Kroger says a still-fresh key is expired: renew once and retry
    common.write_json(common.KROGER_TOKEN_PATH, {"access_token": "user1", "refresh_token": "r2",
                                                 "expires_at": time.time() + 1800})
    s.expired_user_tokens.add("user1")
    scanner.handle_scan("011110417005", FakeFeedback(), None, kf)
    assert s.cart[-1][0] == "user2" and len(s.cart) == 2
    # renewal key revoked -> clear message, not retried forever
    common.write_json(common.KROGER_TOKEN_PATH, {"access_token": "x", "refresh_token": "dead",
                                                 "expires_at": 0})
    fb = FakeFeedback()
    scanner.handle_scan("011110417005", fb, None, kf)
    assert fb.calls == ["error"] and common.queue_size() == 0
    assert "sign-in problem" in common.read_json(common.RECENT_PATH, [])[0]["result"]


def test_kroger_offline_and_partial_retry():
    s = FakeSession(); kf = kroger_setup(s, todoist=True)
    tf = lambda t: scanner.TodoistClient(t, s)
    s.offline = True
    assert scanner.handle_scan("011110417005", FakeFeedback(), tf, kf) == "queued"
    q = common.read_json(common.QUEUE_PATH, [])
    assert q[0]["pending"] == ["todoist", "kroger"]
    assert "waiting for internet" in common.read_json(common.RECENT_PATH, [])[0]["result"]
    s.offline = False
    assert scanner.retry_queue_once(tf, kf) == 1
    assert len(s.cart) == 1 and len(s.tasks) == 1 and common.queue_size() == 0
    # an old-style queued item (before Fred Meyer existed) still goes to Todoist only
    common.queue_add({"id": "old", "barcode": "0002", "name": None})
    assert scanner.retry_queue_once(tf, kf) == 1
    assert s.tasks[-1]["content"] == "Bounty Paper Towels" and len(s.cart) == 1


def test_kroger_lost_reply_not_resent():
    s = FakeSession(); kf = kroger_setup(s); fb = FakeFeedback()
    s.cart_timeout = True
    assert scanner.handle_scan("011110417005", fb, None, kf) == "done"
    assert common.queue_size() == 0 and fb.calls == ["success"]
    assert "may have been added" in common.read_json(common.RECENT_PATH, [])[0]["result"]


def test_portal_kroger_flow():
    s = FakeSession()
    orig_session = kroger.requests.Session
    kroger.requests.Session = lambda: s
    try:
        reset()
        common.save_config({"todoist_token": "t"})
        setup_portal.state.update(hotspot=False, stores=[], zip="", oauth={})
        c = setup_portal.app.test_client()
        html = c.get("/").get_data(as_text=True)
        assert "Where scans go" in html and "Enter and save the Client ID" in html
        # connect before credentials -> friendly error
        c.get("/kroger/connect"); assert "Client ID and secret first" in c.get("/").get_data(as_text=True)
        # save credentials and turn on Fred Meyer
        c.post("/save", data={"has_dest": "1", "send_to_todoist": "1", "send_to_kroger": "1",
                              "kroger_client_id": "cid", "kroger_client_secret": "sec",
                              "kroger_modality": "PICKUP", "project": "Grocery List"})
        html = c.get("/").get_data(as_text=True)
        assert "Fred Meyer is on but isn" in html and "Connect Fred Meyer account" in html
        assert common.load_config()["send_to_kroger"] is True
        # sign-in redirect goes to Kroger with PKCE
        r = c.get("/kroger/connect")
        assert r.status_code == 302 and r.location.startswith("https://api.kroger.com/v1/connect/oauth2/authorize?")
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(r.location).query)
        assert q["scope"] == ["cart.basic:write product.compact"] and q["code_challenge_method"] == ["S256"]
        assert q["redirect_uri"] == ["http://tosslist.local/kroger/callback"]
        key = q["state"][0]
        # a forged/old state is refused
        c.get("/kroger/callback?code=good-code&state=wrong")
        assert "old or incomplete" in c.get("/").get_data(as_text=True)
        assert not common.kroger_connected(common.load_config())
        # the paste fallback works with the real state
        c.post("/kroger/paste", data={"url": f"http://localhost:8000/callback?code=good-code&state={key}"})
        html = c.get("/").get_data(as_text=True)
        assert "Fred Meyer connected" in html and "Connected ✓" in html
        assert oct(os.stat(common.KROGER_TOKEN_PATH).st_mode)[-3:] == "600"
        # store search and pick
        c.post("/kroger/stores", data={"zip": "97266"})
        html = c.get("/").get_data(as_text=True)
        assert "Fred Meyer - Hawthorne" in html and "3805 SE Hawthorne Blvd" in html
        c.post("/kroger/store", data={"store_id": "70100123"})
        cfg = common.load_config()
        assert cfg["kroger_location_id"] == "70100123" and common.destinations(cfg) == ["todoist", "kroger"]
        assert "Ready · Fred Meyer - Hawthorne" in c.get("/").get_data(as_text=True)
        # bad ZIP, and a store id that wasn't offered
        c.post("/kroger/stores", data={"zip": "12"}); assert "5-digit" in c.get("/").get_data(as_text=True)
        c.post("/kroger/store", data={"store_id": "evil"}); assert "Pick a store" in c.get("/").get_data(as_text=True)
        # blank secret keeps the saved one; changing client ID disconnects
        c.post("/save", data={"has_dest": "1", "send_to_kroger": "1", "kroger_client_id": "cid", "kroger_client_secret": ""})
        assert common.load_config()["kroger_client_secret"] == "sec" and common.kroger_connected(common.load_config())
        assert common.load_config()["send_to_todoist"] is False
        c.post("/save", data={"has_dest": "1", "send_to_kroger": "1", "kroger_client_id": "newid"})
        assert not common.kroger_connected(common.load_config())
        # disconnect
        common.write_json(common.KROGER_TOKEN_PATH, {"access_token": "x"})
        c.post("/kroger/disconnect"); assert not os.path.exists(common.KROGER_TOKEN_PATH)
        assert "Fred Meyer disconnected" in c.get("/").get_data(as_text=True)
        # hotspot mode hides Fred Meyer (needs internet)
        setup_portal.state["hotspot"] = True
        assert "Fred Meyer" not in c.get("/").get_data(as_text=True)
        setup_portal.state["hotspot"] = False
    finally:
        kroger.requests.Session = orig_session


# ---------------- Walmart ----------------

_KEY_MADE = []

def walmart_setup(session, todoist=False):
    reset()
    if not _KEY_MADE:                       # making RSA keys is slow; once is enough
        walmart.create_key(); _KEY_MADE.append(1)
    common.save_config({"send_to_todoist": todoist, "todoist_token": "t" if todoist else "",
                        "send_to_walmart": True, "walmart_consumer_id": "wm-consumer",
                        "walmart_key_version": "1"})
    return lambda cfg: walmart.WalmartClient(cfg, session)


def test_walmart_key_and_signature():
    walmart_setup(FakeSession())
    assert oct(os.stat(common.WALMART_KEY_PATH).st_mode)[-3:] == "600"
    pub = walmart.public_key()
    assert pub.startswith("-----BEGIN PUBLIC KEY-----") and "PRIVATE" not in pub
    h = walmart.signed_headers("wm-consumer", "1")
    assert set(h) >= {"WM_CONSUMER.ID", "WM_CONSUMER.INTIMESTAMP", "WM_SEC.KEY_VERSION", "WM_SEC.AUTH_SIGNATURE"}
    assert walmart.upc_candidates("0011110417005") == ["011110417005", "0011110417005"]
    assert walmart.upc_candidates("04252614") == ["042100005264"]


def test_walmart_scans_build_basket():
    s = FakeSession(); wf = walmart_setup(s); fb = FakeFeedback()
    assert scanner.handle_scan("011110417005", fb, None, None, wf) == "done"
    assert scanner.handle_scan("011110417005", fb, None, None, wf) == "done"
    assert scanner.handle_scan("04252614", fb, None, None, wf) == "done"
    b = walmart.basket()["items"]
    assert [(i["item_id"], i["qty"]) for i in b] == [("10450114", 2), ("55512", 1)]
    assert common.read_json(common.RECENT_PATH, [])[0]["result"] == "Walmart: on Walmart list ($0.98)"
    assert abs(walmart.basket_total(b) - 7.22) < 0.001
    link = walmart.cart_link(b)
    assert link == "https://affil.walmart.com/cart/addToCart?items=10450114|2,55512|1"
    # not carried
    scanner.handle_scan("9999", fb, None, None, wf)
    assert fb.calls == ["success", "success", "success", "error"]
    assert common.read_json(common.RECENT_PATH, [])[0]["result"] == "Walmart: not found at Walmart"


def test_walmart_bad_setup_and_offline():
    s = FakeSession(); wf = walmart_setup(s); fb = FakeFeedback()
    common.save_config({"walmart_consumer_id": "typo"})
    scanner.handle_scan("011110417005", fb, None, None, wf)
    assert fb.calls == ["error"] and common.queue_size() == 0
    assert "sign-in problem" in common.read_json(common.RECENT_PATH, [])[0]["result"]
    common.save_config({"walmart_consumer_id": "wm-consumer"})
    s.offline = True
    assert scanner.handle_scan("011110417005", fb, None, None, wf) == "queued"
    s.offline = False
    assert scanner.retry_queue_once(None, None, wf) == 1
    assert walmart.basket()["items"][0]["qty"] == 1


def test_all_three_destinations():
    s = FakeSession(); wf = walmart_setup(s, todoist=True)
    kf_cfg = {"send_to_kroger": True, "kroger_client_id": "cid", "kroger_client_secret": "sec",
              "kroger_location_id": "70100123"}
    common.save_config(kf_cfg)
    common.write_json(common.KROGER_TOKEN_PATH, {"access_token": "user1", "refresh_token": "r1",
                                                 "expires_at": time.time() + 1800})
    kf = lambda cfg: kroger.KrogerClient(cfg, s)
    tf = lambda t: scanner.TodoistClient(t, s)
    assert common.destinations() == ["todoist", "kroger", "walmart"]
    scanner.handle_scan("04252614", FakeFeedback(), tf, kf, wf)
    r = common.read_json(common.RECENT_PATH, [])[0]
    assert r["result"] == "Todoist: added · Fred Meyer: added ($1.25) · Walmart: on Walmart list ($0.98)"
    assert s.tasks[0]["content"] == "Small Soda (12 oz)" and len(s.cart) == 1
    assert walmart.basket()["items"][0]["item_id"] == "55512"


def test_portal_walmart():
    s = FakeSession()
    orig = walmart.requests.Session
    walmart.requests.Session = lambda: s
    try:
        reset()
        common.save_config({"todoist_token": "t"})
        setup_portal.state.update(hotspot=False, stores=[], zip="", oauth={})
        c = setup_portal.app.test_client()
        if os.path.exists(common.WALMART_KEY_PATH): os.remove(common.WALMART_KEY_PATH)
        _KEY_MADE.clear()
        html = c.get("/").get_data(as_text=True)
        assert "Create Walmart key" in html and "Walmart list ·" not in html
        c.post("/walmart/key")
        _KEY_MADE.append(1)
        html = c.get("/").get_data(as_text=True)
        assert "Walmart key created" in html and "BEGIN PUBLIC KEY" in html and "PRIVATE" not in html
        first = walmart.public_key()
        c.post("/walmart/key"); assert walmart.public_key() == first          # no accidental replace
        c.post("/save", data={"has_dest": "1", "send_to_todoist": "1", "send_to_walmart": "1",
                              "walmart_consumer_id": "wm-consumer", "walmart_key_version": "1"})
        cfg = common.load_config()
        assert cfg["send_to_walmart"] and common.destinations(cfg) == ["todoist", "walmart"]
        c.post("/walmart/check"); assert "accepted the connection" in c.get("/").get_data(as_text=True)
        c.post("/save", data={"has_dest": "1", "send_to_walmart": "1", "walmart_consumer_id": "typo"})
        c.get("/")
        c.post("/walmart/check"); assert "accept it. Check the Consumer ID" in c.get("/").get_data(as_text=True)
        # list card
        walmart.basket_add({"item_id": "10450114", "name": "Great Value 2% Milk", "price": 3.12}, "011110417005")
        walmart.basket_add({"item_id": "55512", "name": "Small Soda", "price": 0.98}, "04252614")
        html = c.get("/").get_data(as_text=True)
        assert "Walmart list · 2 items · about $4.10" in html and "Open in Walmart cart" in html
        r = c.get("/walmart/open")
        from urllib.parse import unquote
        assert r.status_code == 302 and unquote(r.location) == "https://affil.walmart.com/cart/addToCart?items=10450114|1,55512|1"
        assert "Done, clear the list" in c.get("/").get_data(as_text=True)
        rid = walmart.basket()["items"][1]["id"]
        c.post("/walmart/remove", data={"id": rid}); assert len(walmart.basket()["items"]) == 1
        c.post("/walmart/clear"); assert walmart.basket()["items"] == []
        r = c.get("/walmart/open"); assert r.location == "/"
        assert "list is empty" in c.get("/").get_data(as_text=True)
        # replace key
        c.post("/walmart/key", data={"replace": "1"}); assert walmart.public_key() != first
    finally:
        walmart.requests.Session = orig


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
