# Tosslist — Setup Guide

Scan an item as it goes in the trash. A few seconds later it shows up on a
shared Todoist list that you and your partner both see, in your Fred Meyer
cart, on a Walmart list you open in your Walmart cart, or any mix of those.
You choose on the settings page.

Plan on about 40 minutes the first time. You need: the Raspberry Pi, its power
supply, the microSD card, the OTG adapter, the barcode scanner, a computer
with an SD card slot (or a USB SD card reader), and your home WiFi password.
Tosslist downloads straight from GitHub, so there's nothing to copy onto the
card yourself.

---

## Step 1 — Get your Todoist API token (5 min)

1. Sign in at **todoist.com** (create a free account if needed).
2. Go to **Settings → Integrations → Developer**.
3. Find **API token** and click **Copy**. Paste it somewhere handy, like a note
   to yourself. Treat it like a password.

---

## Step 2 — Put Raspberry Pi OS on the SD card (15 min)

1. On your computer, download and install **Raspberry Pi Imager** from
   **raspberrypi.com/software**.
2. Put the microSD card in your computer and open Imager.
3. Choose:
   - **Device:** your Pi model (e.g. Raspberry Pi Zero 2 W)
   - **Operating system:** Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**
   - **Storage:** your SD card
4. When Imager offers to customize settings, say yes and fill in:
   - **Hostname:** `tosslist` (type it exactly like this)
   - **Username and password:** pick your own. Write them down.
   - **WiFi:** your home WiFi name and password
   - **Remote access / SSH:** turn it **on**, with password login
5. Write the card. It takes a few minutes.

---

## Step 3 — Start the Pi (5 min)

1. Put the SD card in the Pi.
2. Plug the barcode scanner into the Pi's **data** USB port using the OTG
   adapter. (On a Pi Zero, it's the port closer to the middle, labeled USB.
   The one on the end, labeled PWR, is power only.)
3. Plug in the power. Wait **3 minutes** for the first startup to finish.

---

## Step 4 — Install Tosslist from GitHub (10 min)

You'll type commands into the Pi from your computer. This is called **SSH**.

**On a Mac:** open the **Terminal** app (search for it with Spotlight).
**On Windows:** open **PowerShell** (search for it in the Start menu).

Type this, replacing `yourname` with the username you picked in Step 2, then
press Enter:

```
ssh yourname@tosslist.local
```

- If it asks *"Are you sure you want to continue connecting?"*, type `yes`.
- Type your Pi password. **Nothing appears on screen while you type**. That's
  normal. Press Enter.

You're now controlling the Pi. Run these three commands, one at a time,
pressing Enter after each:

```
sudo apt-get install -y git
```

```
git clone https://github.com/wauna442/tosslist.git ~/tosslist
```

```
sudo bash ~/tosslist/install.sh
```

What they do:
1. Installs **git**, the tool that talks to GitHub.
2. **Clones** (downloads) Tosslist from GitHub into a `tosslist` folder in your
   home folder on the Pi. This copy is also how you'll get updates later.
3. Runs the installer. It downloads what Tosslist needs and finishes with
   **"All done."** You can close the window afterwards.

> **If the repo is private,** the clone command asks for a GitHub username and password.
> GitHub doesn't accept your normal password there. Either make the repo public
> (it contains no passwords or keys) or create a personal access token on
> GitHub and paste that as the password.

---

## Step 5 — Connect it to Todoist (3 min)

1. On your phone or computer (on your home WiFi), open
   **http://tosslist.local**
2. Paste your **API token**.
3. Keep the list name **Grocery List** or change it. Tap **Save**.
4. Scan something. The green light on the Pi blinks twice, and the item
   appears in Todoist under that list.

**Share the list with your partner:** in the Todoist app, open the list →
tap **Share** → invite their email. They need a free Todoist account.

---

## Optional — Send scans to your Fred Meyer cart (15 min)

Tosslist uses Kroger's official API (Fred Meyer is part of Kroger). It
matches the exact product you scanned at your store, adds it to your cart, and
shows the price. You still review the cart and check out in the Fred Meyer
app, because the API can't place orders.

**Your password stays private.** You sign in on Fred Meyer's own page, and
Tosslist only gets a key that can look up products and add to your cart.

### A. Register a Kroger developer app

1. Go to **developer.kroger.com** and create an account (separate from your
   Fred Meyer shopping account).
2. Register a new application. Use any name, like *Tosslist*.
3. Choose the **Production** environment if it asks.
4. For **Redirect URI**, enter exactly:
   `http://tosslist.local/kroger/callback`
5. Turn on the **Products**, **Cart** and **Locations** APIs if it asks
   which ones you need.
6. Save it. Copy the **Client ID** and **Client secret**.

If the portal won't accept that Redirect URI, use
`http://localhost:8000/callback` instead. See the note in step B.

### B. Connect it on tosslist.local

1. Open **http://tosslist.local** and fill in **Fred Meyer app details**:
   Client ID, Client secret, and Pickup or Delivery. If you used the
   `localhost` Redirect URI, open **Redirect URI** and change it to match.
   Tap **Save**.
2. Tap **Connect Fred Meyer account**, sign in with your normal Fred Meyer
   login, and approve.
   - With the `tosslist.local` address, you land back on the settings page.
   - With the `localhost` address, you'll land on a page that won't load.
     That's expected. Copy that page's full address from the address bar,
     go back to tosslist.local, open **"Sign-in page didn't load
     afterwards?"**, paste it in, and tap **Finish connecting**.
3. Under **Fred Meyer store**, enter your ZIP, tap **Find stores**, pick
   yours, and tap **Use this store**.
4. Under **Where scans go**, check **Fred Meyer cart**. Leave **Todoist list**
   checked for both, or uncheck it for the cart only. Tap **Save**.

### Good to know

- **Each scan adds one.** Scan two empty milk jugs and the cart gets two.
  Kroger doesn't let apps see the cart, so Tosslist can't skip duplicates
  there the way it does in Todoist.
- **"Not sold at your store"** in Recent scans means Fred Meyer doesn't carry
  that exact product. It still goes to Todoist if that's on.
- **"May have been added, check cart"** means the internet dropped right as
  the item was sent. Tosslist doesn't resend it, so it can't be added twice.
  Glance at your cart.
- **Prices** in Recent scans are your store's price, including the sale
  price when there is one.

---

## Optional — Walmart list (15 min)

Walmart's public API can look up products and prices but can't put items
in your cart for you. So Tosslist keeps a **Walmart list** on the device. When
you're ready to shop, tap **Open in Walmart cart** on tosslist.local and
walmart.com opens with everything in your cart. Check out there as usual.

Tosslist never needs your Walmart password.

### A. Create a Walmart developer app

1. Go to **walmart.io** and sign in with your normal Walmart.com account.
2. Create a new application. Use any name, like *Tosslist*. Walmart may review
   the application before it works.
3. Keep that page open. It will ask for a **public key** in a moment.

### B. Make the key on tosslist.local

1. Open **http://tosslist.local**, scroll to **Walmart key**, and tap
   **Create Walmart key**.
2. Tap **Copy public key** and paste the whole block (including the
   `BEGIN` and `END` lines) into walmart.io where it asks for your public key.
3. walmart.io then shows a **Consumer ID** and a **key version** (usually 1).
   Enter both under **Walmart app details** and tap **Save**.
4. Tap **Test Walmart connection**. A brand-new key can take a few minutes to
   start working, so try again shortly if it says no at first.
5. Under **Where scans go**, check **Walmart list** and tap **Save**.

### Good to know

- **Scanning the same thing twice** makes the quantity 2 on the Walmart list.
- **Remove an item** with the ✕ next to it, before you open the cart.
- **After you check out**, tap **Done, clear the list** so the next order
  starts fresh. Tosslist can't tell when you've ordered.
- **Prices** are walmart.com prices, which can differ from your local store.
- The Walmart list lives on the device, so open it while you're on your home
  WiFi.

---

## What the green light means

| Light | Meaning |
|---|---|
| Solid on | Ready to scan |
| 2 quick blinks | Added (to Todoist, your Fred Meyer cart, and/or the Walmart list) |
| 1 long blink | No internet right now. Saved, and it sends automatically later |
| 5 fast blinks | Problem: open http://tosslist.local to see what happened |
| Slow pulse | Setup mode (see below), or nowhere to send scans yet |

The settings page also shows your **recent scans**, which is the easiest way
to see what the device did.

---

## Setup mode: new router, moving house, changed WiFi password

If the device can't get online within about 90 seconds of powering up, it
creates its own WiFi network called **Tosslist Setup**.

1. On your phone, join **Tosslist Setup**.
2. The setup page pops up on its own. (If it doesn't, open a browser and go to
   **http://10.42.0.1**.)
3. Pick your WiFi, type the password, tap **Save**.

After a power outage, the Pi often boots faster than your router. That's fine:
while in setup mode it re-checks for your saved WiFi every few minutes and
reconnects on its own.

---

## Power loss

Unplugging it is safe. Both programs start automatically on every power-up,
settings are saved carefully so a sudden power cut can't half-write them, and
anything scanned while offline is kept and sent once the internet is back.

---

## Updating Tosslist

When there's a new version on GitHub, SSH in (Step 4) and run:

```
cd ~/tosslist && git pull && sudo bash install.sh
```

Your settings, Todoist token, Fred Meyer connection, Walmart key and
Walmart list are kept.

---

## Troubleshooting

- **`tosslist.local` doesn't load:** wait another minute, and make sure
  your phone/computer is on the same WiFi. On some Windows PCs `.local` names
  don't work; use your phone instead.
- **Scans do nothing, light stays off:** check the scanner is in the USB
  data port, not the power port.
- **Item shows as "Unknown item 0123…":** the product isn't in the free
  databases. Rename it in Todoist. Next time it'll still say unknown, but your
  list is still useful.
- **See what the device is doing:** SSH in (Step 4) and run
  `journalctl -u tosslist -f` . Press Ctrl+C to stop watching.

---

## Optional: add a buzzer later

Wire an active buzzer's **+** to GPIO 18 and **−** to any ground pin. Then SSH
in and run:

```
sudo nano /var/lib/tosslist/config.json
```

Change `"buzzer_pin": null` to `"buzzer_pin": 18`, save (Ctrl+O, Enter,
Ctrl+X), then run `sudo systemctl restart tosslist`. It beeps in the
same patterns as the light.

---

## What's in this folder

| File | What it does |
|---|---|
| `scanner.py` | Reads the scanner, looks up the product, sends it where you chose |
| `kroger.py` | The Fred Meyer connection: sign-in, store search, prices, cart |
| `walmart.py` | The Walmart connection: key, product lookup, Walmart list, cart link |
| `setup_portal.py` | The settings page and the WiFi setup mode |
| `common.py` | Shared pieces: saving settings, the offline queue, the light |
| `install.sh` | One-time installer |
| `systemd/` | Tells the Pi to start both programs on every power-up |
| `tests/` | Checks that run on any computer: `python3 tests/test_tosslist.py` |

Product names come from **Open Food Facts** (open database, ODbL license) and
**UPCitemdb**'s free tier. Fred Meyer data comes from the **Kroger Public API**, and Walmart data from the
**Walmart Affiliate API**.
