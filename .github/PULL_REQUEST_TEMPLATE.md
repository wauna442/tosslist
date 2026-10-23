<!--
Thanks for contributing to Tosslist. Before opening this pull request,
read CONTRIBUTING.md. AI tools: read AGENTS.md first.
-->

## Summary

<!-- What changes and why, in a short paragraph. If it closes an issue, start
with "Fixes #N". -->

## Intent

<!-- What will be true after this merges that isn't true now, in one or two
sentences, and anything deliberately left out. Review starts here: the change
is checked against this before it's read line by line. -->

## Constitution principle

<!-- Cite the principle(s) this change touches, by Roman numeral, or write
"None". For example: "Principle IV — a lost reply is not resent." -->

## Needs a proposal?

- [ ] No — this is a bug fix, docs, tests, CI, or matching improvement
- [ ] Yes — approved in #<!-- issue number -->

## How it was checked

- [ ] Tests pass: `python3 tests/test_tosslist.py`
- [ ] New or changed behavior has tests (bug fixes: a test that fails without the fix)
- [ ] Checked on the real device (describe below), **or**
- [ ] Only checked against simulated services — real-device check still needed:
      <!-- what to try on the Pi -->

## Checklist

- [ ] Branched from current `main`, one issue per pull request
- [ ] No passwords stored, no scraping, nothing that can place an order (Principles I–III)
- [ ] Persistent writes use `common.write_json` (Principle VII)
- [ ] `README.md` updated if setup or anything a person sees changed.
      **Not** `CHANGELOG.md` — that's updated at release time
- [ ] Security-sensitive change? Reported privately first per SECURITY.md
