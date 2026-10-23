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
3. Runs the installer. It downloads what Tosslist needs, turns on
   **read-only mode** (see "Power loss" below), and **restarts the Pi**. Your
   SSH window closes when it restarts. That's expected. Give it two minutes.

> **If the repo is private,** the clone command asks for a GitHub username and password.
> GitHub doesn't accept your normal password there. Either make the repo public
> (it contains no passwords or keys) or create a personal access token on
> GitHub and paste that as the password.

---

## Step 5 — Connect it to Todoist (3 min)

1. On your phone or computer (on your home WiFi), open
   **http://tosslist.local**
2. **Create a PIN** (4 to 8 digits). It keeps guests on your WiFi from
   changing settings. Then tap **Print the PIN reset card** (in the PIN
   section) and keep the printout somewhere safe.
3. Paste your **API token**.
4. Keep the list name **Grocery List** or change it. Tap **Save**.
5. Scan something. The green light on the Pi blinks twice, and the item
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

## Optional — Store order and rules

With more than one store set up, you can decide which store gets each scan.
On **http://tosslist.local**, under **Store order and rules**:

1. **Order your stores** with ↑ and ↓, most preferred first.
2. **Pick a rule:**
   - **Every store** (the default): each scan goes to every store that's on.
   - **First in stock:** goes to the first store in your order that carries
     it and has it in stock. Example: Fred Meyer first, Walmart second, so
     anything out of stock at your Fred Meyer goes to the Walmart list.
   - **Cheapest in stock:** goes to whichever store has it in stock for less.
3. **Low stock:** choose whether "low stock" at your Fred Meyer counts as in
   stock (checked) or moves the scan to your next store (unchecked).
4. **Todoist:** add every scan, or **only if no store took it**, which is handy
   for things neither store carries.
5. Tap **Save**.

### Good to know

- **Try a rule before switching.** While you're on Every store, Recent scans
  shows which store each other rule would have picked, for example
  "Would pick: First in stock → Walmart, Cheapest → Fred Meyer". Watch it for
  a week to see whether Fred Meyer's stock information matches the shelves.
- **Recent scans explains each decision**, for example
  "First in stock → Walmart. Fred Meyer: out of stock · Walmart: on Walmart
  list ($2.25)".
- **No internet means wait, not skip.** If a store can't be reached, the scan
  waits and is decided once the internet is back. A network hiccup never
  sends your groceries to your second-choice store.
- **A sign-in problem does move on.** If a store's sign-in has expired, the
  scan goes to your next store, and Recent scans says so. Reconnect that
  store on the settings page.
- **Walmart stock and prices are walmart.com's**, not your local store's.
  Fred Meyer's are for the store you picked.
- **"Stock not reported"** counts as in stock: if a store doesn't say, it's
  treated as having it.

---

## Undo a scan

Scanned something you meant to keep? Two ways to take it back:

- **The undo card.** Open **http://tosslist.local**, tap **undo card**
  (above Recent scans), and print it or download it. Scanning it right after
  a mistake removes that scan from Todoist and the Walmart list. Scanning it
  again undoes the scan before that, going back up to 24 hours. Tape it to
  the scanner or the trash can.
- **The Undo button** next to each recent scan on the settings page. No PIN
  needed.

Fred Meyer doesn't let apps remove cart items, so for those, Recent scans
reminds you to remove it in the Fred Meyer app. A scan still waiting for
the internet is simply cancelled. If Todoist can't be reached, the light
blinks 5 times; scan Undo again later and it finishes only what's left.

---

## Alerts in Todoist

Tosslist has no screen, so when something needs a person it adds a task to
your shared Todoist list, so you both see it. The task is **priority 1
(red) and due today**, which puts it at the top of Today and of lists sorted
by priority. Examples:

- **⚠️ Tosslist: Reconnect Fred Meyer**, when Fred Meyer's sign-in stops working
- **⚠️ Tosslist: Fix the Walmart connection**
- **⚠️ Tosslist: Scans are stuck waiting to send**, after 2 hours
- **⚠️ Tosslist: An update didn't finish**

Each task explains what to do. Once it's fixed, Tosslist closes the task by
itself (checks run about every 5 minutes). The same list appears under
**Needs attention** at the top of the settings page. Turn alerts off under
**Updates and alerts**.

If Todoist itself stops working, Tosslist can't alert you there. The light
blinks 5 times on each scan, and Recent scans shows what's wrong.

---

## Settings PIN

Changing settings needs your PIN. After you enter it, that phone or computer
stays unlocked for 30 minutes (or tap **Lock settings now**). Recent scans and
the Walmart list stay open to everyone in the house without a PIN.

- **Wrong PIN five times** locks entry for 5 minutes.
- **Change it** under **PIN → Change PIN**. Other phones that were unlocked
  are signed out.
- **Forgot it?** Scan your printed **PIN reset card** with Tosslist's scanner.
  The light blinks twice, and the settings page asks you to create a new
  PIN. Keep the card away from the device: anyone with the card and the
  scanner can reset the PIN. You can print it again any time from
  **PIN → Print the PIN reset card**.

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

## Power loss and read-only mode

Unplugging it is safe. Tosslist runs in **read-only mode**: the Pi's system
isn't written to the SD card at all (changes go to memory and vanish at the
next power-up), so a power cut can't damage it. That's the most common way
a Raspberry Pi stops working, and this prevents it.

Tosslist's own data — settings, store connections, the Walmart list, and
scans waiting for the internet — still survives. The SD card is unlocked
for just the instant each one is saved. A WiFi network you pick in setup mode
is saved permanently the same way.

Both programs start automatically on every power-up, and anything scanned
while offline is sent once the internet is back.

**Changing things over SSH:** in read-only mode, anything you change by hand
on the Pi (installing software, editing files) disappears at the next
restart. Tosslist's install and update scripts handle this for you. To work
on the Pi by hand, turn read-only mode off first:

```
sudo bash ~/tosslist/install.sh --no-readonly
```

The Pi restarts with read-only mode off and keeps it off. Turn it back on
with `sudo bash ~/tosslist/install.sh --readonly`.

---

## Updating Tosslist

**Automatically:** each night around 3:30–4:15 AM, Tosslist checks GitHub
for a new **release** and installs it by itself (with the two restarts
described below). It never installs unreleased changes. Turn this off under
**Automatic updates** on the settings page. The **Updates** section there
shows the version, whether read-only protection is on, and the result of the
last check, and has an **Update now** button.

**By hand:** SSH in (Step 4) and run:

```
sudo bash ~/tosslist/update.sh
```

This updates to the newest **release** (like `v26.11.0`). To get the newest
reviewed change on `main` before it's released, add `--main`. To just see
whether there's an update, add `--check`.

If there's an update, the Pi turns read-only mode off, restarts, installs
the update by itself, turns read-only mode back on, and restarts again.
Your SSH window closes at the first restart. Allow about five minutes in
total. If nothing is new, nothing restarts.

Your settings, Todoist token, Fred Meyer connection, Walmart key and
Walmart list are kept. The settings page shows the result of the last
update.

**Updating from an older version** (before read-only mode existed), run
this once instead: `cd ~/tosslist && git pull && sudo bash install.sh`

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
| `install.sh` | Installer; also switches read-only mode on or off |
| `update.sh` | Updates from GitHub, handling read-only mode's restarts |
| `scripts/` | Shared script helpers, and `resume.sh`, which finishes an install or update after a restart |
| `systemd/` | Tells the Pi to start both programs on every power-up |
| `tests/` | Checks that run on any computer: `python3 tests/test_tosslist.py` |

Product names come from **Open Food Facts** (open database, ODbL license) and
**UPCitemdb**'s free tier. Fred Meyer data comes from the **Kroger Public API**, and Walmart data from the
**Walmart Affiliate API**.

---

## Contributing and project rules

Changes go through pull requests that the maintainer reviews, with automatic
tests on each one. See [CONTRIBUTING.md](CONTRIBUTING.md) for how,
[GOVERNANCE.md](GOVERNANCE.md) for who decides, and
[CONSTITUTION.md](CONSTITUTION.md) for the principles every change follows —
including that Tosslist never stores a password and never places an order.
Security problems: [SECURITY.md](SECURITY.md). Release notes:
[CHANGELOG.md](CHANGELOG.md).
