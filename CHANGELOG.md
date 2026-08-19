# Changelog

## 0.2.0 — 2026-08-19

### Fixed

* **The browser silently skipped a rule the CLI reported.** `web/rules.js` had no
  `unit_price` header alias, so `parseDelimited` never populated the field and
  `unit_price_arithmetic` could not fire in the browser. Given a bill with a
  unit-price column, the CLI reported lines where units × price ≠ the charge and
  the web app reported nothing on the same bill.
* **The parity harness could not see it.** Parse-mode compared five fields
  (`idx`, `code`, `units`, `charge`, `date`). It now compares every field the
  rules can read, and `tests/test_parity.py` carries a regression case.
* Removed duplicate `money()` / `num()` declarations in `web/rules.js`.
* The NOTICE link in the browser disclaimer pointed at `https://github.com/` —
  the bare site root — on a page whose whole premise is that every claim is
  checkable.

### Added

* **NADAC drug acquisition costs.** ~32,000 NDCs at the latest surveyed price,
  public domain, refreshed weekly by CMS. Reaches the outpatient pharmacy side
  that Part B ASP's ~900 J-codes cannot. Bills are parsed for NDCs (hyphenated
  forms only; a bare 10-digit run is declined rather than guessed). Where a line
  has both an NDC and an ASP-priced code, ASP wins and NADAC stands down so the
  same dollars are never counted twice.
* **MS-DRG benchmark.** 767 national averages from CMS's inpatient by-geography
  file — the first procedure-level benchmark in the project, and reached without
  touching CPT, because MS-DRG and ICD-10-PCS are CMS-maintained. Whole-bill
  context only: it carries no dollar amount and can never be added to a
  recoverable total. `--drg` on the CLI, a field in the browser.
* **`--mrf`: your hospital's own published prices.** Streams a hospital price
  transparency machine-readable file (JSON or CSV, gigabyte-scale, no
  dependencies) and compares each line against the discounted cash price the
  hospital publishes and attests to. A line above the published *gross* charge is
  treated as directly disputable; a line above the *cash* price is the strongest
  question the tool can ask, but deliberately not counted as recoverable.
  Also `itemize mrf <file> --codes …` for a straight lookup.
* **`itemize teach`.** Five synthetic practice bills with seeded errors and an
  instructor key, for the health-systems-science gap: no PHI, no IRB, no
  procurement. `teach score` doubles as a coverage check and the test suite fails
  if a case stops being solvable.
* `--skip` on `tools/build_data.py` for partial rebuilds; a skipped dataset keeps
  its file and carries its old provenance forward marked `skipped_at`.

### Changed — interface

* **The bill comes first.** The context questions moved below the input.
* **"What to do first"** — at most three ranked next actions, derived in both
  engines and diffed by the parity suite, so the browser and the evidence packet
  never rank them differently.
* **The line check is now a gate.** Confirming the parsed lines match the paper
  bill is required before the evidence packet unlocks, and unread input rows are
  surfaced as a first-class statistic.
* **Findings link to the lines they cite**, and the target row highlights.
* **Print stylesheet.** Cmd-P produces the sourced packet rather than a
  screenshot of an app.
* **One live region** instead of `aria-live` on the whole results block, which
  made a screen reader re-read the entire audit on every checkbox change.
* **Letters open in an editable box** with copy-to-clipboard, rather than
  download-only.
* **Opt-in local draft**, off by default, with a visible delete.
* The state list is built from `states.json` at load, grouped into researched and
  not-yet-researched, so it can no longer drift from the data.
* "Load an example" and opening a file now confirm before overwriting your work.

## 0.1.0

Initial release.
