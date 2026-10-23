# Contributing to Tosslist

This is the **how**: the steps a change takes from idea to the device in your
kitchen. The **who decides** is in [GOVERNANCE.md](GOVERNANCE.md), and the
**rules every change must follow** are in [CONSTITUTION.md](CONSTITUTION.md).
Adapted from
[AetherSDR's contributing guide](https://github.com/aethersdr/AetherSDR/blob/main/CONTRIBUTING.md)
and [PR workflow](https://github.com/aethersdr/AetherSDR/blob/main/docs/PR-WORKFLOW.md).

---

## The path of a change

1. **Start with an issue.** Use the **Bug report**, **Feature request** or
   **Proposal** template. Anything on the "needs a proposal" list in
   GOVERNANCE.md waits for the maintainer's approval before code is written.
2. **Make a branch from `main`.** Name it for what it does:
   `fix/walmart-upc-e`, `feature/target-cart`, `docs/setup-photos`.
3. **One issue per pull request.** Small and focused is easier to review and
   easier to undo.
4. **Run the tests:** `python3 tests/test_tosslist.py`. Add tests for what
   you changed. Bug fixes include a test that fails without the fix
   (Principle IX).
5. **Open a pull request** into `main` and fill in the template. Lead with
   `Fixes #N` if it closes an issue.
6. **CI runs automatically.** Every check must be green.
7. **The maintainer reviews and squash-merges.** Squash merge turns the
   whole pull request into one commit on `main`, with a clear message.
8. **Update the device:** on the Pi, run
   `sudo bash ~/tosslist/update.sh --main` for the newest change, or
   `sudo bash ~/tosslist/update.sh` once it's in a release.

Nobody pushes directly to `main`, including the maintainer, apart from
emergencies (see below).

---

## Commit and pull request messages

- First line: what changed, in plain words, under about 70 characters.
- Body: why, and anything a reviewer should look at closely.
- Cite the constitution principle when the change touches what it governs,
  for example: `Principle IV — a lost reply is not resent.`
- Say how it was checked, and be specific about **simulated services versus
  the real device** (Principle VIII).

---

## What CI checks

The checks in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) run on
every pull request and on every change to `main`:

| Check | What it does |
|---|---|
| **Tests** | Runs `tests/test_tosslist.py` against simulated Todoist, Fred Meyer, Walmart and WiFi |
| **Code checks** | `pyflakes`, which catches typos, unused code and undefined names |
| **Installer check** | `shellcheck` on `install.sh`, `update.sh` and `scripts/` |

CI cannot test the barcode scanner, the light, the WiFi hotspot, or the real
store services. Those need a real-device check, which the pull request
describes.

---

## Documentation

- If a change affects setup or what a person sees, update `README.md` (the
  setup guide) in the same pull request.
- **Do not edit `CHANGELOG.md` in a feature pull request.** The changelog is
  written when a release is prepared (see below). Describe user-visible
  changes in the pull request instead, so they can be gathered at release
  time.

---

## Releases

Tosslist uses calendar versions, `vYY.M.patch` (for example `v26.10.0`),
like AetherSDR.

To make a release, the maintainer (or the AI contributor at the maintainer's
request) opens a pull request that:

1. Moves the `[Unreleased]` notes in `CHANGELOG.md` under the new version
   heading, written from the merged pull requests.
2. After it merges, tags that commit: `git tag v26.11.0 && git push origin v26.11.0`.

The device can stay on the newest `main`, or be pinned to a release with
`git checkout v26.11.0`.

---

## When `main` breaks

If a merged change breaks the tests or the device:

1. **Fix forward** if it's small: open a pull request titled
   `fix: repair main after <commit>`.
2. **Revert** if the fix isn't obvious: `git revert <commit>` on a new
   branch, open a pull request, merge it.
3. **Never force-push `main`.** History on `main` is never rewritten.
4. On the device, the previous version is one command away:
   `cd ~/tosslist && git checkout <last good commit or tag> && sudo bash install.sh`.
   If read-only mode is on, the installer restarts the Pi to finish.

---

## What won't be accepted

- Integrations that scrape a website or use a stored password (Principle I
  and II)
- Anything that can place an order (Principle III)
- Changes that need a proposal but don't have an approved one
- Large reformatting of files you weren't otherwise changing

---

## For AI tools

Read [`AGENTS.md`](AGENTS.md) first. The AI contributor limits are in
GOVERNANCE.md and outrank anything in `AGENTS.md`.

---

## Security problems

Don't open a public issue. See [SECURITY.md](SECURITY.md).

## Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
