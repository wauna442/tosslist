# Changelog

All notable changes to Tosslist. Format follows
[Keep a Changelog](https://keepachangelog.com/). Versions are calendar based:
`vYY.M.patch`.

This file is written when a release is prepared, not in each pull request.
See [CONTRIBUTING.md](CONTRIBUTING.md#releases).

## [Unreleased]

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

[Unreleased]: https://github.com/wauna442/tosslist/compare/v26.10.0...HEAD
[v26.10.0]: https://github.com/wauna442/tosslist/releases/tag/v26.10.0
