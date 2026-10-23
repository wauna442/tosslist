# Tosslist — Setup Guide

Scan an item as it goes in the trash. A few seconds later it shows up on a
shared Todoist list that you and your partner both see.

Plan on about 45 minutes the first time. You need: the Raspberry Pi, its power
supply, the microSD card, the OTG adapter, the barcode scanner, and a computer
with an SD card slot (or a USB SD card reader).

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

## Step 3 — Copy this project onto the SD card (2 min)

When Imager finishes, the card usually shows up on your computer as a drive
named **bootfs**. (If it doesn't, unplug the card and plug it back in.)

1. Unzip the `tosslist.zip` file you got from Claude.
2. Drag the whole **tosslist** folder onto the **bootfs** drive.
3. Eject the card properly (right-click → Eject), then remove it.

---

## Step 4 — Start the Pi (5 min)

1. Put the SD card in the Pi.
2. Plug the barcode scanner into the Pi's **data** USB port using the OTG
   adapter. (On a Pi Zero, it's the port closer to the middle, labeled USB.
   The one on the end, labeled PWR, is power only.)
3. Plug in the power. Wait **3 minutes** for the first startup to finish.

---

## Step 5 — Install the scanner software (10 min)

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

You're now controlling the Pi. Run the installer:

```
sudo bash /boot/firmware/tosslist/install.sh
```

It downloads what it needs and finishes with **"All done."** You can close the
window afterwards.

---

## Step 6 — Connect it to Todoist (3 min)

1. On your phone or computer (on your home WiFi), open
   **http://tosslist.local**
2. Paste your **API token**.
3. Keep the list name **Grocery List** or change it. Tap **Save**.
4. Scan something. The green light on the Pi blinks twice, and the item
   appears in Todoist under that list.

**Share the list with your partner:** in the Todoist app, open the list →
tap **Share** → invite their email. They need a free Todoist account.

---

## What the green light means

| Light | Meaning |
|---|---|
| Solid on | Ready to scan |
| 2 quick blinks | Added to the list |
| 1 long blink | No internet right now. Saved, and it sends automatically later |
| 5 fast blinks | Problem: open http://tosslist.local to see what happened |
| Slow pulse | Setup mode (see below), or Todoist isn't set up yet |

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

## Troubleshooting

- **`tosslist.local` doesn't load:** wait another minute, and make sure
  your phone/computer is on the same WiFi. On some Windows PCs `.local` names
  don't work; use your phone instead.
- **Scans do nothing, light stays off:** check the scanner is in the USB
  data port, not the power port.
- **Item shows as "Unknown item 0123…":** the product isn't in the free
  databases. Rename it in Todoist. Next time it'll still say unknown, but your
  list is still useful.
- **See what the device is doing:** SSH in (Step 5) and run
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
| `scanner.py` | Reads the scanner, looks up the product name, adds it to Todoist |
| `setup_portal.py` | The settings page and the WiFi setup mode |
| `common.py` | Shared pieces: saving settings, the offline queue, the light |
| `install.sh` | One-time installer |
| `systemd/` | Tells the Pi to start both programs on every power-up |

Product names come from **Open Food Facts** (open database, ODbL license) and
**UPCitemdb**'s free tier.
