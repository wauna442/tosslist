"""Kinds of items (issue #24).

Each scan gets a "kind": the top-level product category UPCitemdb reports
(for example "Home & Garden" from "Home & Garden > Lighting > Light Bulbs").
Items Open Food Facts knows are food. Items nobody knows have no kind.

In Todoist, each kind is a section of your shared list. Each kind can also
skip the stores, so a light bulb never lands in a grocery cart. A kind
taught on the Teach page always wins over UPCitemdb.

UPCitemdb's categories come from retailers and aren't always right (AA
batteries can show up as "Health & Beauty"), which is why the switches and
the Teach override exist.
"""

import os

import common

KINDS_PATH = os.path.join(common.DATA_DIR, "kinds.json")
FOOD = "Food, Beverages & Tobacco"

# UPCitemdb's top-level categories (Google's product taxonomy). Kinds where
# a grocery store is a natural place to buy start with stores on.
STORE_KINDS = {FOOD, "Health & Beauty", "Home & Garden", "Baby & Toddler",
               "Animals & Pet Supplies"}
KNOWN = sorted(STORE_KINDS | {
    "Apparel & Accessories", "Arts & Entertainment", "Business & Industrial",
    "Cameras & Optics", "Electronics", "Furniture", "Hardware",
    "Luggage & Bags", "Media", "Office Supplies", "Religious & Ceremonial",
    "Software", "Sporting Goods", "Toys & Games", "Vehicles & Parts"})


def from_category(category):
    """'Home & Garden > Lighting > Light Bulbs' -> 'Home & Garden'."""
    top = (category or "").split(">")[0].strip()
    return top[:60] or None


def _all():
    return common.read_json(KINDS_PATH, {})


def settings(kind):
    """{"section": name in Todoist, "stores": try Fred Meyer / Walmart}."""
    saved = _all().get(kind, {})
    return {"section": saved.get("section") or kind,
            "stores": saved.get("stores", kind in STORE_KINDS or kind not in KNOWN)}


def seen(kind):
    """Remember a kind the first time it shows up, for the settings page."""
    if not kind:
        return
    data = _all()
    if kind not in data:
        data[kind] = {}
        common.write_json(KINDS_PATH, data)


def listing():
    """Every kind seen so far, with its settings, food first."""
    names = sorted(_all(), key=lambda k: (k != FOOD, k))
    return [dict(settings(k), kind=k) for k in names]


def update(changes):
    """changes: kind -> {"section": str, "stores": bool}"""
    data = _all()
    for kind, c in changes.items():
        if kind not in data:
            continue
        section = " ".join((c.get("section") or "").split())[:60]
        data[kind] = {"section": section if section and section != kind else "",
                      "stores": bool(c.get("stores"))}
    common.write_json(KINDS_PATH, data)
