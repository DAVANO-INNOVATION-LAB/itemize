# Caveats

Read this before relying on anything `itemize` tells you, and before deploying
it for anyone else.

This document is deliberately long. A tool that tells people what to pay for
medical care can cause real harm by being confidently wrong, so every known
limit is written down rather than discovered later.

---

## 1. This is not advice

`itemize` produces **information, not advice**. It is not legal, medical,
financial or tax advice. Using it creates no attorney–client, clinician–patient
or advisory relationship of any kind. It cannot tell you what you owe.

Nothing here has been reviewed by an attorney, a certified medical coder, or a
patient advocate. Verify anything you intend to act on. Every finding names its
source so that you can.

The software is provided **as is, without warranty of any kind** (see LICENSE).

---

## 2. What the price benchmarks do and do not mean

**A Medicare benchmark is not a price cap.** Hospitals are under no obligation
to bill Medicare rates, and commercial rates are legitimately higher. A line
billed at 300× the Medicare allowance is *grounds to ask questions* and useful
leverage in a financial-assistance request. It is **not** proof of an error, and
`itemize` never reports it as one.

Only three kinds of finding are treated as directly disputable:

* exact duplicate line items,
* a bill exceeding your own EOB's stated patient responsibility,
* a bill exceeding a Good Faith Estimate by $400 or more.

Everything else is a question to ask.

**Whole-bill protections are never added to line-item disputes.** An entitlement
(charity care, a prohibited balance bill) covers the whole balance; summing it
with a duplicate charge would produce a "disputable" figure larger than the bill.
The two are reported separately and deliberately.

**In Maryland, benchmark reasoning behaves differently.** The state sets hospital
rates on an all-payer basis. The tool flags this, but treat Medicare comparisons
there with extra caution.

**DMEPOS figures are a national median.** The DMEPOS fee schedule varies by
state and by rural/non-rural status. `itemize` stores the median of the
non-rural state columns so it can show one comparable number without asking for
your state. Your local allowance may differ.

---

## 3. Coverage gaps — what is *not* checked

**No findings does not mean the bill is correct.** It means these particular
checks did not fire.

* **Only about a third of HCPCS Level II codes carry any price benchmark.**
* **CPT-coded lines are not benchmarked at all** in the open tier — and on a
  typical hospital bill, that is most lines. See §4.
* **Laboratory work is effectively invisible.** Lab tests are overwhelmingly
  CPT-coded, and the Clinical Lab Fee Schedule pages could not be located at a
  stable URL. Do not expect this gap to close in the open tier.
* **There is no units-of-service validation whatsoever.** Medically Unlikely
  Edits are AMA-gated, so billing 50 units of a once-daily service passes
  silently.
* **No check for services during a global surgical period**, observation-vs-
  inpatient status, or charges dated outside an admission window.
* **Preventive services billed with cost-sharing** are not detected (the code
  lists are CPT).
* Modifier and revenue-code findings are **heuristics**, not authoritative
  edits. They tell you what to ask, not what is wrong.
* "Unrecognised chargemaster code" is likewise a heuristic based on code shape.

---

## 4. Third-party rights — CPT, CDT

This project is built around a licensing boundary. See [NOTICE.md](NOTICE.md)
for the full statement.

* **CPT codes and descriptions are copyright the American Medical Association.**
  They are not redistributed here. `tools/build_data.py` filters them out and
  never writes them to disk.
* **CDT dental codes are copyright the American Dental Association.** Also
  excluded.
* **NCCI unbundling edits are AMA-gated.** `itemize ncci` shows you CMS's
  licence, requires you to type `accept`, and downloads to *your* machine under
  *your* licence. The project never redistributes them. The underlying file URLs
  are trivially reachable without the click-through; we do not use them that
  way, and neither should a fork.
* **A hosted service performing CPT-based analysis needs its own AMA
  distribution licence.** That is precisely the cost commercial bill-negotiation
  services have already paid, and it is why the CPT tier here is local-only.
* The MIT licence covers **the code only**. Reference data under `web/data/` is
  US government work product; see NOTICE.md.

If you believe any file in `web/data/` contains third-party-copyrighted content,
open an issue and it will be removed while the claim is checked.

---

## 5. State law — the least reliable part of this project

`web/data/states.json` is **hand-curated**. It is the only data file here with
no federal source behind it, and it is the most likely thing in the repository
to be wrong or out of date.

* **24 of 50 states have entries.** A missing state means **not researched** —
  which is never the same as "no protection". The tool says so explicitly.
* Of the ~22 states reported to have ground-ambulance protections, **16 are
  identified by name**. The remainder are recorded as `null` rather than guessed.
* Many recorded fields are `null` because a source did not name them. `null`
  means unknown, not absent.
* **Entries go stale.** Legislatures amend these every session. Each entry
  carries a `verified` date and announces its own age once past
  `stale_after_days`; the test suite fails the build when a shipped entry
  expires. This makes staleness loud, but it does not make the data current.
* **State protections are gated on plan funding.** State insurance law generally
  does not reach self-funded (ERISA) employer plans, which cover roughly
  two-thirds of workers with employer coverage. If you do not know which you
  have, the tool cannot tell you.
* Statutes of limitations, medical-debt credit reporting, wage garnishment and
  interest rules are **largely unmodelled**.

Treat state findings as a prompt to call your state department of insurance or
attorney general's consumer line — both handle billing complaints free.

---

## 6. Rights citations

Findings in `itemize/rights.py` cite statute and regulation (45 CFR 149, IRC
501(r), 26 CFR 1.501(r)-5) rather than a data file. They are quoted
conservatively and **should be checked against current text at eCFR.gov and
irs.gov** before being relied on. Regulations are amended, and a citation that
has moved is worse than none.

Thresholds such as the $400 Good Faith Estimate dispute floor are recorded as of
the date in `web/data/manifest.json` and may change.

---

## 7. Parsing is best-effort

* **PDF support reads text-layer PDFs only.** Scanned documents and photographs
  saved as PDFs have no text layer; the tool says so plainly rather than
  returning an empty result that reads like "nothing wrong".
* **Photographs of bills are not supported, on purpose.** OCR of a dense table
  from a phone camera gets digits wrong, and `$1,842.00` read as `$1,042.00`
  produces a confident, sourced, wrong dispute. That failure is silent and looks
  exactly like success. See *Why not photos?* in the README.
* Encrypted PDFs and CID/Type0 fonts with custom encodings are not handled.
* Column detection on loose text is heuristic. **Check the "Lines reviewed"
  table against your paper bill** before acting on anything.
* Lines that cannot be read are surfaced, never silently dropped — but a
  mis-parsed line is still possible.

---

## 8. Data freshness

CMS updates HCPCS, ASP and DMEPOS **quarterly**. The shipped reference data is
a snapshot; `web/data/manifest.json` records the source URL, SHA-256 and
retrieval date of every file it came from.

Re-run `python3 tools/build_data.py --out web/data` to refresh. If you deploy
this for others, that is your ongoing obligation, not the project's.

---

## 9. Privacy

* The browser app **never transmits your bill**. There is no server, no account,
  no analytics, and no scripts, fonts or images from other hosts. The only
  network requests are for the page's own `data/*.json` on the same origin.
* The service worker caches the **app shell and reference data** for offline
  use. It never caches anything about your bill, because nothing about your
  bill ever leaves the page.
* The CLI reads local files and writes local files. `itemize ncci` is the only
  command that reaches the network, and only after you accept the AMA licence.
* **If you self-host this, your host's access logs are yours to account for.**
  The privacy guarantee is a property of the code, not of your deployment.
* The evidence packet and letters are generated and downloaded locally. What you
  do with them afterwards — emailing a billing office, for example — is outside
  the tool's control and is not private.

---

## 10. Two engines

`itemize/rules.py` (Python) and `web/rules.js` (JavaScript) implement the same
rules so the CLI and the browser agree. `tests/test_parity.py` runs both over
identical input under Node and diffs the results; the suite fails if they
diverge.

If you fork and change a rule in one engine only, **the parity suite is what
stops your CLI and your web app telling the same person different things.** Do
not delete it. Node is required for it to run; CI fails the build rather than
letting it skip silently.

---

## 11. Scope

`itemize` reviews a bill. It does not:

* negotiate on your behalf,
* contact your provider or insurer,
* submit disputes, appeals or applications,
* store, transmit or track anything,
* estimate what care will cost before you receive it.

---

## Reporting a problem

If a finding is wrong, a citation has moved, or a state entry is out of date,
please open an issue. A finding a billing office can wave away is worse than no
finding at all, because it costs the reader credibility they may only get to
spend once.
