# Changelog

## 0.3.0 — 2026-09-01

A maturation pass over every feature, plus the dependency and reference-data
upgrades that had gone unattended.

### Fixed

* **A NUL byte crashed the parser on Python 3.9 and 3.10.** `csv.reader` raised
  `_csv.Error: line contains NUL` up to 3.10 and stopped doing so in 3.11, so a
  bill carrying a NUL — ordinary in text extracted from a PDF, and in some
  Windows exports — crashed outright on the interpreter floor this project
  claims to support, while passing on a newer one. Sanitising per field was too
  late; the csv reader chokes before any field is seen. Found by the new CI
  matrix on its first run, which is the whole reason for testing the floor
  rather than asserting it.
* **A published price could be attached to the wrong setting.** `itemize/mrf.py`
  folded every `standard_charges` entry of a record into one row, taking the last
  non-null value of each field while keeping the *first* setting label. A knee
  arthroscopy came back tagged `outpatient` carrying the **inpatient** cash price —
  $15,000 instead of $4,000 — so a day-surgery patient would have been shown a
  sourced, confident, wrong number. Each setting is now its own row, findings name
  the setting they came from and flag when the file publishes others for the same
  code, and `--mrf-setting` restricts the lookup.
* **The offline shell lost two datasets.** `web/sw.js` was never updated when
  NADAC and MS-DRG were added in 0.2.0, so the page that promises to work offline
  silently dropped both on reload. A test and a CI step now fail if any shipped
  dataset is missing from the shell.
* **Citations claimed the wrong retrieval date.** Every source was cited with the
  manifest's build date, so a dataset carried forward by `--skip` was presented to
  a billing office with a date it was not fetched on. Each source now records and
  cites its own `retrieved`.

### Added

* **Staleness is a finding, not a footnote.** Reference data going quietly out of
  date is the failure this project is least able to detect from the inside, so
  `stale_reference_data` fires in both engines with per-dataset windows — 45 days
  for weekly NADAC, 120 for the quarterly CMS files, 400 for the annual DRG
  averages — and is diffed by the parity suite.
* **`itemize data`** reports the age of every dataset; `--refresh` re-downloads
  from CMS.
* **Packaging.** `pyproject.toml`, an `itemize` console script, and `python3 -m
  itemize`. The builder moved into the package so an installed copy can refresh
  its own data. Reference data is deliberately *not* in the wheel — CMS reissues
  it quarterly, and a pinned wheel would ship stale prices with nothing to signal
  it. CI installs the wheel into a clean virtualenv and fails if anything at all
  was pulled in alongside it.
* **`--format json`** on `audit`, for another program to read.
* **`--version`**, with a test that fails if it drifts from `pyproject.toml`.
* Two teaching cases: `inpatient-stay` (the DRG comparison as context, carrying no
  disputable amount) and `ambulance-erisa`, which runs the same bill twice to show
  a state protection drop from HIGH to a note purely because the plan is
  self-funded — the single most consequential question in the tool.
* NADAC citations now carry the effective date of the prices, not just the
  retrieval date.

### Upgraded

* `actions/checkout`, `actions/setup-python` and `actions/setup-node` v4/v5/v4 → **v7**.
* CI Node 20 (EOL April 2026) → **24**.
* CI now tests a **Python 3.9 / 3.11 / 3.14 matrix**. 3.9 is past upstream
  end-of-life and is kept deliberately: this tool is for people in medical debt,
  who are least likely to be on a current interpreter, and supporting it costs
  nothing with no dependencies. Testing it turns the README's floor into something
  verified rather than asserted.
* Reference data rebuilt: HCPCS July → **October 2026** (8,725 → 8,769 codes),
  NADAC → 2026-09-02 (32,436 → 32,602 NDCs), ASP and DMEPOS re-fetched.

### Note

There are still no third-party dependencies, and there is no plan for any. The
PDF extractor, the gigabyte-scale MRF reader and the data build are all written
against the standard library on purpose: this tool reads people's medical bills,
and every dependency is one more thing a deployer must audit and one more way the
"nothing leaves your machine" promise can quietly stop being true.


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
