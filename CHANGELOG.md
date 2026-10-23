# Changelog

All notable changes to Tosslist. Format follows
[Keep a Changelog](https://keepachangelog.com/). Versions are calendar based:
`vYY.M.patch`.

This file is written when a release is prepared, not in each pull request.
See [CONTRIBUTING.md](CONTRIBUTING.md#releases).

## [Unreleased]

## [v26.10.1] — 2026-10-23

Store routing, read-only protection, a settings PIN, Todoist alerts, undo,
teaching items, and sections by kind of item.

### Added
- **Store order and rules (#6).** Rank Fred Meyer and Walmart, then send
  each scan to every store, the **first in stock**, or the **cheapest in
  stock**. Todoist can get every scan, or only items no store took. Fred
  Meyer "low stock" counting as in stock is a setting.
- **Read-only mode (#13).** The Pi's system no longer writes to the SD card,
  so a power cut can't damage it. Settings, lists and queued scans still
  survive, and so does a WiFi network picked in setup mode. Install and
  update turn read-only mode off first and back on at the end, restarting
  the Pi by themselves.
- **`update.sh` (#13).** Updates to the newest release (`--main` for the
  newest change, `--check` to only look). It never moves backwards.
- **Nightly updates (#14).** Each night, new releases install by
  themselves. This can be turned off on the settings page, which also shows
  the version and has an **Update now** button.
- **Settings PIN (#20).** Changing settings needs a PIN; recent scans and
  the Walmart list stay open. A printable reset card scanned on the device
  clears a forgotten PIN.
- **Todoist alerts (#22).** When Tosslist needs a person (a store sign-in
  stopped working, scans stuck, an update failed), a red, due-today task
  appears in your list and closes by itself once it's fixed.
- **Undo (#23).** A printable undo card, plus an Undo button on each recent
  scan, takes the last scan back out of Todoist and the Walmart list.
- **Teach an item (#23).** Photograph a package. The Pi reads the text,
  searches Open Food Facts, Fred Meyer and Walmart, and remembers your pick
  for that barcode.
- **Kinds of items (#25).** Scans are sorted into sections of your Todoist
  list by UPCitemdb category. Each kind can skip the stores, so hardware
  stays off the grocery run.

### Changed
- Updating is now `sudo bash ~/tosslist/update.sh` instead of `git pull`.
- New Debian packages on the Pi: `overlayroot` (read-only mode) and
  `tesseract-ocr` (reading package text).

### Upgrading from v26.10.0
Run once over SSH: `cd ~/tosslist && git pull && sudo bash install.sh`.
The Pi restarts into read-only mode. Then create a PIN on the settings page
and print the PIN reset and undo cards. Later releases install by
themselves.

## [v26.10.0] — 2026-10-23

First release.

### Added
- **Scan to Todoist.** A USB barcode scanner on a Raspberry Pi adds each
  scanned item to a shared Todoist list, named from Open Food Facts or
  UPCitemdb, skipping items already on the list.
- **Phone-based setup.** With no known WiFi, the device opens a
  "Tosslist Setup" network and setup page. Settings live at
  `http://tosslist.local` afterwards, with recent scans.
- **Headless and power-safe.** Starts on power-up, survives power cuts,
  queues scans while offline and sends them later, and signals with the
  Pi's green light (optional buzzer).
- **Fred Meyer cart.** Kroger's official API adds the exact scanned product
  at your chosen store to your Fred Meyer cart, with its price. Sign-in
  happens on Fred Meyer's own page, and no password is stored.
- **Walmart list.** Walmart's official API looks up each scan with its
  walmart.com price. One tap opens the list in your Walmart cart. Tosslist
  generates the security key itself.
- **Choose destinations.** Todoist, Fred Meyer and Walmart can be turned on
  in any combination.
- **Install from GitHub.** The setup guide clones the repository on the
  Pi. Updating is one command.
- **Project governance.** Constitution, governance, contributing and
  security policies, pull request and issue templates, and automatic tests
  on every pull request, adapted from AetherSDR. `main` is protected:
  changes land only through reviewed pull requests with passing checks.

[Unreleased]: https://github.com/wauna442/tosslist/compare/v26.10.1...HEAD
[v26.10.1]: https://github.com/wauna442/tosslist/compare/v26.10.0...v26.10.1
[v26.10.0]: https://github.com/wauna442/tosslist/releases/tag/v26.10.0
