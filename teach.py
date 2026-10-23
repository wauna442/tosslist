"""Teach Tosslist an item (issue #9).

When a barcode comes up as "Unknown item" or "not sold at your store", you
can teach it from tosslist.local: take a photo of the package with your
phone, let the Pi read the text on it, search Open Food Facts, Fred Meyer
and Walmart with that text, and pick the right product. Tosslist remembers
your choice for that barcode from then on: its name, and which Fred Meyer
and Walmart product it is.

The text is read on the Pi itself with Tesseract (open-source OCR). The
photo is never sent anywhere and isn't kept.
"""

import os
import re
import subprocess
import tempfile
import time

import requests

import common

TAUGHT_PATH = os.path.join(common.DATA_DIR, "taught.json")
MAX_PHOTO_BYTES = 8 * 1024 * 1024
OCR_SECONDS = 90
USER_AGENT = "Tosslist/1.0 (home Raspberry Pi project)"


# ---------- what's been taught ----------

def taught():
    return common.read_json(TAUGHT_PATH, {})


def get(barcode):
    return taught().get(barcode)


def remember(barcode, name, kroger_upc=None, walmart_item_id=None):
    barcode = clean_barcode(barcode)
    name = clean_name(name)
    if not barcode or not name:
        raise ValueError("A barcode and a name are needed")
    entry = {"name": name, "taught_at": time.strftime("%Y-%m-%d %H:%M")}
    if kroger_upc and kroger_upc.isdigit() and len(kroger_upc) == 13:
        entry["kroger_upc"] = kroger_upc
    if walmart_item_id and walmart_item_id.isdigit():
        entry["walmart_item_id"] = walmart_item_id
    data = taught()
    data[barcode] = entry
    common.write_json(TAUGHT_PATH, data)
    return entry


def forget(barcode):
    data = taught()
    if data.pop(barcode, None) is not None:
        common.write_json(TAUGHT_PATH, data)
        return True
    return False


def clean_barcode(text):
    text = (text or "").strip()
    return text if re.fullmatch(r"[0-9A-Za-z\-]{4,40}", text) else ""


def clean_name(text):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text[:120]


# ---------- reading text from a photo ----------

def run_ocr(path):
    """Text from an image file, via Tesseract. Replaced in tests."""
    p = subprocess.run(["tesseract", path, "stdout", "--psm", "11"],
                       capture_output=True, text=True, timeout=OCR_SECONDS)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or "Tesseract failed")
    return p.stdout


def read_photo(data):
    """Useful lines of text from a photo's bytes, best guesses first."""
    if not data:
        return []
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("That photo is too large")
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
            or data[8:12] == b"WEBP"):
        raise ValueError("That doesn't look like a photo (JPEG, PNG or WebP)")
    fd, path = tempfile.mkstemp(suffix=".img", dir=_scratch_dir())
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        text = run_ocr(path)
    finally:
        os.remove(path)
    return useful_lines(text)


def _scratch_dir():
    # RAM (/run) on the device, so photos never touch the SD card.
    d = os.path.join(common.LOCK_DIR, "photos")
    os.makedirs(d, exist_ok=True)
    return d


def useful_lines(text, limit=12):
    """Keep lines that look like words on a package. Wordy lines first;
    lines heavy with numbers (weights, codes) last."""
    seen, out = set(), []
    for raw in (text or "").splitlines():
        line = re.sub(r"[^\w&'%.,\- ]+", " ", raw)
        line = re.sub(r"\s+", " ", line).strip(" .,-")
        letters = sum(c.isalpha() for c in line)
        if letters < 3 or letters < len(line.replace(" ", "")) * 0.5:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    out.sort(key=lambda s: -(sum(c.isalpha() for c in s) - 2 * sum(c.isdigit() for c in s)))
    return out[:limit]


# ---------- searching ----------

def search_open_food_facts(text, session=None, limit=6):
    session = session or requests.Session()
    r = session.get("https://world.openfoodfacts.org/cgi/search.pl",
                    params={"search_terms": text, "search_simple": 1, "json": 1,
                            "page_size": limit,
                            "fields": "code,product_name,brands,quantity"},
                    headers={"User-Agent": USER_AGENT}, timeout=10)
    r.raise_for_status()
    out = []
    for p in (r.json().get("products") or [])[:limit]:
        name = (p.get("product_name") or "").strip()
        if not name:
            continue
        brand = (p.get("brands") or "").split(",")[0].strip()
        if brand and brand.lower() not in name.lower():
            name = f"{brand} {name}"
        qty = (p.get("quantity") or "").strip()
        out.append({"name": f"{name} ({qty})" if qty else name})
    return out


def search_everywhere(text, cfg, kroger_client=None, walmart_client=None, session=None):
    """Results from each source, plus a note for any that couldn't be reached."""
    import kroger
    import walmart
    text = clean_name(text)
    results = {"off": [], "kroger": [], "walmart": [], "problems": []}
    if len(text) < 3:
        results["problems"].append("Type at least 3 letters to search.")
        return results
    try:
        results["off"] = search_open_food_facts(text, session)
    except requests.RequestException:
        results["problems"].append("Couldn't reach Open Food Facts.")
    if common.kroger_ready(cfg):
        try:
            client = kroger_client or kroger.KrogerClient(cfg)
            results["kroger"] = client.search(text, cfg["kroger_location_id"])
        except (requests.RequestException, kroger.KrogerAuthError):
            results["problems"].append("Couldn't search Fred Meyer.")
    if common.walmart_ready(cfg):
        try:
            client = walmart_client or walmart.WalmartClient(cfg)
            results["walmart"] = client.search(text)
        except (requests.RequestException, walmart.WalmartAuthError):
            results["problems"].append("Couldn't search Walmart.")
    return results
