"""Tests that run on any computer, using fake versions of Todoist,
the product databases and the WiFi controls.

Run with:  python3 tests/test_tosslist.py
"""
import json, os, sys, tempfile
os.environ["TOSSLIST_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import requests
import common, scanner, setup_portal


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

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

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
        raise AssertionError(url)


def reset():
    for p in (common.CONFIG_PATH, common.QUEUE_PATH, common.CACHE_PATH, common.RECENT_PATH):
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
    assert scanner.handle_scan("0001", fb, make) == "added"
    assert s.tasks[0]["content"] == "Tillamook Whole Milk" and s.tasks[0]["project_id"] == "g"  # found via page 2
    assert scanner.handle_scan("0001", fb, make) == "duplicate"
    assert scanner.handle_scan("0002", fb, make) == "added"
    assert s.tasks[-1]["content"] == "Bounty Paper Towels"
    assert scanner.handle_scan("9999", fb, make) == "added"
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
    assert scanner.handle_scan("0001", fb, lambda t: scanner.TodoistClient(t, s)) == "auth error"
    assert fb.calls == ["error", "error"]
    assert common.read_json(common.RECENT_PATH, [])[0]["result"] == "bad Todoist token"


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
