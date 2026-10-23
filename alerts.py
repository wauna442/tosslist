"""Alerts in Todoist when Tosslist needs a person (issue #10).

The device has no screen, so problems that need someone to act are added
to the shared Todoist list as a priority 1 (red) task due today, which puts
it at the top of Today and of any list sorted by priority. There is one task
per problem, and Tosslist closes it by itself once the problem is fixed.

If Todoist itself is the problem (its token stopped working), the only
signal is the light and the settings page.
"""

import logging
import time

import requests

import common

log = logging.getLogger("tosslist")

PROBLEMS_PATH = common.DATA_DIR + "/problems.json"   # store -> {"error": t, "ok": t}
ALERTS_PATH = common.DATA_DIR + "/alerts.json"       # problem -> {"task_id", "since"}
STUCK_AFTER = 2 * 60 * 60                            # queued scans older than this

TITLES = {
    "kroger_auth": "Reconnect Fred Meyer",
    "walmart_auth": "Fix the Walmart connection",
    "queue_stuck": "Scans are stuck waiting to send",
    "update_failed": "An update didn't finish",
    "data_lost": "Settings aren't being saved",
}
HOW_TO_FIX = {
    "kroger_auth": ("Fred Meyer's sign-in stopped working, so scans can't reach your Fred "
                    "Meyer cart. Open http://tosslist.local, unlock settings, and tap "
                    "Connect Fred Meyer account."),
    "walmart_auth": ("Walmart rejected Tosslist's key, so scans can't reach the Walmart "
                     "list. Open http://tosslist.local, unlock settings, and tap Test "
                     "Walmart connection under Walmart key."),
    "queue_stuck": ("{n} scan(s) have been waiting more than 2 hours to send. Check that "
                    "the Pi is on WiFi, then look at Recent scans on http://tosslist.local."),
    "update_failed": ("The last update stopped: {message} Open http://tosslist.local and "
                      "tap Update now to try again."),
    "data_lost": ("Read-only mode is on, but Tosslist can't find its saved settings, so "
                  "changes are lost at each restart. Run the installer again over SSH."),
}


def note(store, ok):
    """Record whether a store just worked (ok=True) or failed to sign in.
    Writes only when the state flips, to keep SD card writes rare."""
    problems = common.read_json(PROBLEMS_PATH, {})
    entry = problems.get(store, {})
    failing = entry.get("error", 0) > entry.get("ok", 0)
    if ok == (not failing) and entry:
        return
    entry["ok" if ok else "error"] = time.time()
    problems[store] = entry
    common.write_json(PROBLEMS_PATH, problems)


def active_problems(cfg, now=None):
    """problem key -> details, for everything that currently needs a person."""
    now = now or time.time()
    out = {}
    problems = common.read_json(PROBLEMS_PATH, {})
    for store, key in (("kroger", "kroger_auth"), ("walmart", "walmart_auth")):
        e = problems.get(store, {})
        if cfg.get(f"send_to_{store}") and e.get("error", 0) > e.get("ok", 0):
            out[key] = {}
    queued = common.read_json(common.QUEUE_PATH, [])
    stuck = [q for q in queued if now - q.get("queued_at", now) > STUCK_AFTER]
    if stuck:
        out["queue_stuck"] = {"n": len(stuck)}
    status = common.read_json(common.UPDATE_STATUS_PATH, None) or {}
    if status.get("result") == "failed":
        out["update_failed"] = {"message": status.get("message", "")}
    if common.DATA_WARNING:
        out["data_lost"] = {}
    return out


def sync(cfg=None, client_factory=None):
    """Open a Todoist alert for each new problem; close alerts for fixed ones.
    Returns (opened, closed). Network trouble is retried on the next sync."""
    cfg = cfg or common.load_config()
    if not cfg.get("send_alerts", True) or not common.todoist_ready(cfg):
        return [], []
    import scanner                          # here, to avoid a circular import
    client = (client_factory or scanner.TodoistClient)(cfg["todoist_token"])
    alerts = common.read_json(ALERTS_PATH, {})
    active = active_problems(cfg)
    opened, closed = [], []
    try:
        for key, details in active.items():
            if key in alerts:
                continue
            pid = client.project_id(cfg["project_name"])
            task = client.add_task(
                pid, f"⚠️ Tosslist: {TITLES[key]}",
                HOW_TO_FIX[key].format(**details) +
                "\n\nTosslist closes this task by itself once it's fixed.",
                request_id=f"tosslist-alert-{key}-{int(time.time())}",
                priority=4, due_string="today")
            alerts[key] = {"task_id": (task or {}).get("id"), "since": time.time()}
            opened.append(key)
            log.warning("Alert sent to Todoist: %s", TITLES[key])
        for key in [k for k in alerts if k not in active]:
            if alerts[key].get("task_id"):
                client.close_task(alerts[key]["task_id"])
            del alerts[key]
            closed.append(key)
            log.info("Alert closed: %s", TITLES[key])
    except (requests.RequestException, scanner.TodoistAuthError) as e:
        log.warning("Couldn't update Todoist alerts yet: %s", e)
    if opened or closed:
        common.write_json(ALERTS_PATH, alerts)
    return opened, closed
