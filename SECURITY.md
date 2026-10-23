# Security Policy

## Reporting a problem

Please report security problems **privately**, not in a public issue:
use GitHub's
[private vulnerability reporting](https://github.com/wauna442/tosslist/security/advisories/new).

Include what you found, how to reproduce it, and what someone could do with
it. You'll get a reply within a week. Please allow time for a fix before
discussing it publicly.

## What Tosslist protects

The device holds credentials the household chose to grant (never passwords —
see [Constitution Principle II](CONSTITUTION.md#ii-never-store-a-password)):

| Credential | Where | What it can do |
|---|---|---|
| Todoist API token | `/var/lib/tosslist/config.json` | Full access to that Todoist account |
| Fred Meyer (Kroger) sign-in key | `/var/lib/tosslist/kroger_token.json` | Look up products, add to cart. Cannot order. |
| Kroger app Client ID and secret | `config.json` | Identify the developer app |
| Walmart private key | `/var/lib/tosslist/walmart_key.pem` | Sign product lookups. No account access. |

`/var/lib/tosslist` is readable only by the device's administrator (root)
account, which Tosslist's services run as.

## Known limits, by design

These are accepted for a home device. Reports that find a way around them
**beyond** what is described here are very welcome.

- **The settings page has no password and uses plain HTTP.** Anyone on the
  home WiFi can open `http://tosslist.local`. It never shows saved secrets
  back (fields say "Saved ✓"), but someone on the network could change
  settings or disconnect a store.
- **The setup hotspot is open** while the device is in setup mode, so the
  setup page works from any phone. It only appears when the device can't
  reach a known WiFi network.
- **Anyone with the SD card** can read the stored credentials. Revoke them
  from each service's side if the device is lost: Todoist (new API token),
  Kroger (remove the app from the account), walmart.io (delete the key).

## In scope

- Getting a credential off the device without physical access
- Making Tosslist place an order or change a cart beyond adding scanned items
- Crashing the services or losing the offline queue with crafted input
  (barcodes, API responses, WiFi network names, form fields)
- Anything that breaks [Principles I–IV or VI](CONSTITUTION.md)

## Supported versions

Only the latest `main` gets security fixes.
