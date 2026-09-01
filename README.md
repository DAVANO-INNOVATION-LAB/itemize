# itemize

*A [DAVANO Innovation Lab](https://github.com/DAVANO-INNOVATION-LAB) project —
building and sourcing free tools for the betterment of the people who need them.*

**Check an itemized medical bill against public federal data.** Finds duplicate
charges, unspecified codes, drug prices billed at large multiples of what
Medicare allows or what pharmacies pay, and lines billed above your hospital's
own published cash price — then produces a sourced evidence packet you can hand
to a billing office.

Runs entirely on your own machine. **Zero third-party dependencies** — that is a
promise CI checks in a clean virtualenv, not a claim. Python 3.9+.

> **Read [CAVEATS.md](CAVEATS.md) before relying on anything this tool says.**
> It is information, not advice; a Medicare benchmark is not a price cap; and
> "no findings" does not mean the bill is correct. Every known limit is written
> down there rather than left to be discovered later.

> Medical bill negotiation services charge 20–35% of whatever they save you.
> They are reading public federal data you can read too. This is that data,
> made usable.

---

## Why this exists

Federal price and coding data is public by law and unusable by design. The files
are enormous, inconsistently formatted, and scattered across a dozen CMS landing
pages. Companies charge a percentage of your savings for the work of reading
them.

`itemize` does the reading. It does not negotiate for you, and it takes nothing.

## What it checks

**Your state, and whether its law can reach you** — see *State law* below:

| Check | Finds |
|---|---|
| State ground-ambulance protection | Whether your state fills the federal carve-out — or explicitly that we haven't researched it |
| State charity-care mandate | States requiring financial assistance at *all* hospitals, not just tax-exempt ones |
| State assistance programme | e.g. the Massachusetts Health Safety Net |
| All-payer rate setting | Maryland, where Medicare benchmarks mean something different |
| Statute of limitations | How long the debt can be sued on |

**Your rights** — usually worth more than any coding error:

| Check | Applies when | Finds |
|---|---|---|
| Good Faith Estimate | you are self-pay | You were owed a written estimate; a bill exceeding it by $400+ can go to federal dispute resolution |
| Charity care (501(r)) | non-profit hospital | The hospital must have a financial assistance policy and cap what it charges you |
| No Surprises Act | emergency, or out-of-network at an in-network facility | Balance billing above in-network cost sharing may be prohibited outright |
| Ground ambulance | the bill includes one | Federally *un*protected — a deliberate carve-out; ~22 states fill the gap |

**Your EOB** — the strongest check for an insured reader, and needs no federal data:

| Check | Finds |
|---|---|
| EOB line cross-check | The provider billing you more than your EOB says you owe |
| EOB total cross-check | A statement asking for charges rather than your adjudicated balance |
| Lines absent from the EOB | Charges never submitted to your plan |

**The bill itself:**

| Check | Needs | Finds |
|---|---|---|
| Exact duplicates | nothing but the bill | The same code, date, units and charge billed twice |
| Same code, same day | nothing but the bill | Repeat postings that may be double-counted |
| Missing procedure codes | nothing but the bill | Summary lines masquerading as an itemized bill |
| Unit-price arithmetic | nothing but the bill | Lines where units × price ≠ the charge |
| Modifier flags | nothing but the bill | Modifiers 25/59/X-series — the usual unbundling argument |
| Revenue-code mismatch | nothing but the bill | A pharmacy revenue code on a non-drug line |
| Medicare ASP benchmark | CMS Part B payment limits | Drugs billed at 3×–1000× the Medicare allowed amount |
| Drug acquisition cost | CMS/Medicaid NADAC | Drugs identified by **NDC** billed at large multiples of what pharmacies pay to buy them — the whole outpatient pharmacy side ASP cannot see |
| MS-DRG benchmark | CMS inpatient averages | An inpatient bill against the national average charge for the same DRG |
| Medicare DMEPOS benchmark | CMS DMEPOS fee schedule | Equipment and supplies billed far above the schedule |
| Unspecified codes | CMS HCPCS Level II | `J3490`, `A9270`, `E1399` — "we won't say what this was" |
| Unrecognised codes | CMS HCPCS Level II | Internal chargemaster codes that don't match a billing code |
| NCCI unbundling | *your own* AMA licence | Code pairs CMS says can't be billed together |
| Stale reference data | the manifest | That a price benchmark is being drawn from figures CMS has since replaced |
| Published cash price | *your* hospital's price file | Lines billed above the discounted cash price the hospital publishes and attests to — see *Your hospital's own prices* below |

### State law

State law varies enormously — a self-pay patient in Boston has materially more
leverage than the same patient in Charleston — and it is gated by one question
most people never think about.

**`web/data/states.json` is hand-curated, and is the only data file here without
a federal source behind it.** Three rules keep it honest:

1. **A missing state means "not researched", never "no protection."** Ask about
   Wyoming and the tool says it does not know, and points you at your state
   department of insurance. Filling 50 states with plausible booleans would make
   a prettier map and would eventually tell someone they have no right that they
   in fact have.
2. **Every entry carries citations and a `verified` date**, and an entry past
   `stale_after_days` announces its own age inside the finding. Legislatures
   amend these every session; a stale entry that answers confidently is worse
   than no entry.
3. **State protections are gated on plan funding.** State insurance law
   generally does not reach self-funded (ERISA) employer plans, which cover
   roughly two-thirds of workers with employer coverage. Answer "self-funded"
   and every state protection is demoted with an explanation.

24 states currently have entries. Of the ~22 states reported to have
ground-ambulance protections, 16 are identified by name; the rest are left
`null` rather than guessed.

### Coverage context changes what you are shown

Who is exposed to the charge decides which findings matter. An insured reader
past their deductible pays the plan's negotiated rate, so a "300× Medicare"
headline is demoted to context. A self-pay reader sees it at full volume, plus
the entitlements above. Say nothing and nothing is assumed.

Every finding cites the federal file, URL and SHA-256 it came from. A billing
office dismisses an unsourced complaint; this one names its sources.

## Quickstart

```bash
python3 tools/build_data.py --out web/data
```

That fetches HCPCS Level II codes, Medicare Part B drug payment limits, the
DMEPOS fee schedule, NADAC drug acquisition costs and CMS's national MS-DRG
averages, with a provenance manifest. About **33%** of HCPCS Level II codes
carry a price benchmark; the rest have no published Medicare rate that is free
to redistribute. NADAC is ~90 MB to download — `--skip nadac,drg` makes a fast
partial rebuild, and a skipped dataset keeps the file already on disk along with
its old provenance entry, so the manifest never claims a build date the data
does not have.

**Install** (optional — the repo works as-is):

```bash
pip install .
itemize data --refresh
itemize --version
```

An installed copy has no `web/data` to read, so it keeps reference data in your
platform's user-data directory and fetches it with `itemize data --refresh`. The
data is deliberately **not** packaged in the wheel: CMS reissues it quarterly, and
a wheel pinned to one quarter would ship stale prices to everyone who installed
it, with nothing to signal it from the inside.

**Command line:**

```bash
python3 -m itemize.cli audit bill.csv --self-pay --nonprofit --emergency -o review.md
```

```bash
python3 -m itemize.cli audit bill.csv --eob eob.csv --insured --deductible-unmet
```

```bash
python3 -m itemize.cli audit bill.csv --self-pay --drg 470
```

```bash
python3 -m itemize.cli letter assistance
```

```bash
python3 -m itemize.cli audit bill.csv --format json      # for another program
```

### Knowing when the data went stale

Every price finding is only as good as the file behind it, and a stale file fails
silently — the numbers still look authoritative. So staleness is a **finding**,
not a footnote, and it appears in the browser and the evidence packet alike.

```bash
python3 -m itemize.cli data              # how old is each dataset?
python3 -m itemize.cli data --refresh    # re-download from CMS
```

| Dataset | CMS cadence | Reported stale after |
|---|---|---|
| HCPCS Level II | quarterly | 120 days |
| Part B ASP | quarterly | 120 days |
| DMEPOS | quarterly | 120 days |
| NADAC | weekly | 45 days |
| MS-DRG averages | annual | 400 days |

Each source records its **own** retrieval date, so a dataset carried forward by
`--skip` keeps the date it was actually fetched. A partial rebuild cannot make
stale files look fresh.

**Browser:**

```bash
python3 -m http.server 8811 --directory web
```

Then open `http://localhost:8811`. The page is static — no backend, no upload,
no account. It works offline once loaded, and you can host `web/` on GitHub
Pages as-is.

### Your hospital's own prices

The strongest finding for a self-pay reader is not a Medicare comparison — it is
the hospital's own number. Since 1 January 2026, enforced from 1 April 2026, the
CY2026 OPPS/ASC rule requires hospitals to publish payer-specific rates as
**actual dollar amounts** rather than formulas like "120% of Medicare", alongside
a gross charge and a discounted cash price, under a named attestation. That is
what makes these files usable by a patient.

```bash
python3 -m itemize.cli audit bill.csv --self-pay --mrf https://example-hospital.org/…/standardcharges.json
```

```bash
python3 -m itemize.cli mrf ./standardcharges.csv --codes J1885,A4550 --setting outpatient
```

**Mind the setting.** A hospital publishes a different price for inpatient and
outpatient care, and comparing across them is meaningless. Findings name the
setting a price came from and say so when the file publishes others for the same
code; `--mrf-setting inpatient|outpatient` restricts the lookup outright.

The file is **streamed** — these run to gigabytes — and only the codes on your
bill are retained. Nothing from it is written to disk, and nothing from it ships
with this project: an MRF contains CPT codes because the hospital publishes them,
and reading your own hospital's file on your own machine is not redistribution.
See [NOTICE.md](NOTICE.md).

This is CLI-only. The browser cannot fetch a hospital's file directly — those
servers do not send CORS headers, and a multi-gigabyte download is not something
to start in a page anyway.

### Teaching

Medical schools do not teach billing, and the health-systems-science critique is
that what teaching exists is didactic rather than hands-on. The practice bills
here are synthetic, every error in them is deliberate and recorded, and none of
it needs patient data, an IRB or a procurement cycle.

```bash
python3 -m itemize.cli teach list
python3 -m itemize.cli teach show ed-visit -o practice.csv   # for the student
python3 -m itemize.cli teach key ed-visit                    # for the instructor
python3 -m itemize.cli teach score ed-visit                  # what the engine catches
```

`teach score` is also a coverage check: the test suite fails if a case stops
being solvable, which is a more legible signal than a rule-level unit test
because it names the thing a reader would no longer be told.

**Accepted inputs:** CSV/TSV, plain text pasted from a statement, and **PDF**.
Recognised columns include date, code, **NDC**, description, units, unit price,
charge, revenue code and modifiers, in any order.
PDFs are read on-device by `web/pdf.js`, a dependency-free text-layer extractor
built on the browser's native `DecompressionStream`. Drag a file anywhere onto
the input area, or use *Open a file…*.

Photographs of bills are **not** supported, and neither are scanned PDFs — both
lack a text layer. The tool says so plainly rather than returning an empty
result that reads like "nothing wrong". See *Why not photos?* below.

## The CPT problem, and how this handles it

Most lines on a hospital bill carry **CPT codes**, which are copyright the
American Medical Association. CMS distributes the NCCI unbundling edits behind a
click-through AMA licence. Dental CDT codes are the ADA's.

So `itemize` splits in two:

* **Open tier** — everything above except unbundling. Ships with public-domain
  CMS data only. This is the browser app, and it is the useful-to-most-people
  tier: duplicates and drug markups are where a lot of the money is.
* **Licensed tier** — `itemize ncci` shows you CMS's AMA licence, waits for you
  to type `accept`, and downloads the edit files to your machine under *your*
  licence. The project never redistributes them.

```bash
python3 -m itemize.cli ncci --accept
python3 -m itemize.cli audit bill.csv --ncci -o review.md
```

The file URLs behind CMS's click-through are trivially reachable without it.
We don't use them that way. See [NOTICE.md](NOTICE.md).

## Why not photos?

Reading a photo of a bill needs OCR, and OCR of a dense table on a phone camera
gets digits wrong. `$1,842.00` misread as `$1,042.00` produces a confident,
sourced, **wrong** dispute — which costs the reader the credibility they may
only get to spend once with a billing office. The failure is silent and looks
exactly like a success.

Adding it would also mean vendoring ~15 MB of WASM OCR assets to keep the
"never leaves your device" promise, since sending the image to an OCR API would
break it outright.

If OCR is added later, the right shape is **assisted transcription**: extracted
text lands in the editable box for the reader to correct, and never runs
straight through to findings.

## Honest limitations

Read these before trusting the output.

* **A Medicare benchmark is not a price cap.** Hospitals are not obliged to bill
  Medicare rates and commercial rates are legitimately higher. A large multiple
  is leverage for a self-pay or financial-assistance request — not proof of an
  error. Only duplicate-billing findings are reported as directly disputable.
* **No findings does not mean the bill is correct.** It means these checks did
  not fire. CPT-coded lines are not benchmarked in the open tier at all.
* **Parsing is best-effort.** Bills arrive as CSV exports, as PDFs with wildly
  varying layouts, and as text mangled out of both. Lines that cannot be read are
  surfaced, never silently dropped — but check the "Lines reviewed" table against
  your paper bill. The PDF extractor handles text-layer PDFs only; it does not
  handle encrypted PDFs or CID fonts with custom encodings.
* **This is information, not advice.** Not legal, medical or financial advice.
* **Reference data goes stale.** CMS updates HCPCS, ASP and DMEPOS quarterly.
  Re-run `tools/build_data.py`; the manifest records when you last did.
* **Rights citations are to regulation, not to a data file.** They are quoted
  conservatively and should be checked against current text at eCFR.gov and
  irs.gov. Regulations get amended; a citation that has moved is worse than none.
* **Only about a third of HCPCS Level II codes have a price benchmark.** Lab
  work in particular is largely CPT-coded and therefore invisible to the open
  tier — see below.
* **NADAC is acquisition cost, not an allowed amount.** It is what a pharmacy
  paid a wholesaler. A dispensing fee and a real margin belong on top of it, so
  the thresholds here are deliberately far higher than the ASP ones, and a
  finding is still only a question. NADAC also prices per `EA`/`ML`/`GM`, and a
  bill does not always count units the same way.
* **A bare 10-digit NDC is declined rather than guessed.** It could be 4-4-2,
  5-3-2 or 5-4-1; padding the wrong segment points at a different drug at a
  different price, invisibly. Hyphenated NDCs are unambiguous and are used.
* **The DRG comparison is whole-bill context, never a line dispute.** It carries
  no dollar amount for exactly that reason. Submitted charges are list prices
  almost nobody pays, and your statement may not cover the whole stay.

## Privacy

The browser app never transmits your bill. There is no server, no analytics, no
fonts or scripts from other hosts. The only network request the page makes is
for its own `data/*.json` on the same origin, at load. You can disconnect from
the network before pasting and it still works.

The CLI reads local files and writes local files. `itemize ncci` is the only
command that reaches the network, and only after you accept the licence.

## Layout

```
tools/build_data.py   shim onto itemize/build_data.py, so the documented path works
itemize/model.py      types, code classification, reference data + provenance
itemize/context.py    coverage context (who is exposed to the charge)
itemize/parse.py      CSV/TSV, column-aware and loose-text bill parsing
itemize/rules.py      the audit rules
itemize/rights.py     entitlements: GFE, 501(r) charity care, No Surprises Act
itemize/states.py     state-law findings, staleness and the ERISA gate
itemize/eob.py        EOB parsing and cross-check
itemize/letters.py    letter drafts
itemize/evidence.py   markdown evidence packet
itemize/ncci.py       AMA-licensed tier (consent, fetch, unbundling)
itemize/mrf.py        hospital price transparency files (streamed, CLI-only tier)
itemize/build_data.py fetch CMS files, filter to the public-domain subset
itemize/teaching.py   practice bills with seeded errors, and the key
itemize/cli.py        command line
web/rules.js          the same engine for the browser AND the parity harness
web/pdf.js            dependency-free PDF text-layer extractor
web/app.js            DOM and interaction only
web/states.json       hand-curated state law (see the honesty rules above)
web/sw.js             service worker: network-first, so updates actually land
probe.py              endpoint reconnaissance for CMS data sources
tests/                unittest suite, including Python/JS parity
```

### Two engines, one behaviour

`web/rules.js` and `itemize/rules.py` implement the same rules. Nothing about
that is safe on trust, so `tests/test_parity.py` runs **both** over identical
input — via Node — and diffs which rules fired, at what severity, over which
lines, for how much, plus the ranked action list. Parsers are compared the same
way. Change a threshold in one engine and the suite fails.

**The parity check is only as good as the fields it compares.** It used to diff
five parsed fields, which is how the JS parser came to be missing a `unit_price`
header alias: the Python engine read a unit-price column and reported lines
where units × price ≠ the charge, and the browser silently reported nothing on
the same bill. Both engines now emit every field the rules can read, and the
harness diffs all of them. If you add a field to `Line`, add it to
`parse_shape()` too.

```bash
python3 -m unittest discover -s tests -v
```

Node is required for the parity tests. Without it they skip cleanly, and CI
fails the build rather than letting them skip silently.

## Contributing

Useful directions, roughly in order of value:

1. **More bill formats.** Real exports from real hospital portals are the single
   most useful contribution. Redact and add to `tests/fixtures/`.
2. **Spanish, and plain-language review.** Not attempted here on purpose:
   machine-translating benefits-law guidance for people in medical debt is
   exactly the confident-but-wrong failure this project is built to avoid. The
   strings are centralised enough to extract; the translation needs a human who
   knows the domain.
3. **More states in `web/data/states.json`.** 26 states have no entry, and the
   ~6 unidentified ambulance states need naming. Follow the rules in the file's
   `_meta` block: citations and a `verified` date on every entry, and `null`
   rather than a guess. Re-verifying an existing entry is as valuable as adding
   a new one — `tests/test_itemize.py` fails the build once any entry passes
   `stale_after_days`.
4. **A 501(c)(3) lookup** so the charity-care finding fires automatically instead
   of asking the reader whether their hospital is non-profit.
5. **MUE units caps** (AMA-gated, so tier 2) — there is still no units-of-service
   validation at all.
6. **Clinical Lab Fee Schedule.** Attempted and abandoned: the CLFS landing pages
   404, and lab work is overwhelmingly CPT-coded, so it belongs in the licensed
   tier rather than the open one. Do not expect to close this gap openly.
7. **More teaching cases** in `itemize/teaching.py`. Every seeded error needs a
   rule name, the lines it sits on, and a sentence on why it matters — the test
   suite checks all three, and that the engine still catches it.
8. **A redacted corpus of real bills.** This is the one thing a university can
   supply and a solo project structurally cannot: IRB cover, a patient
   population and a redaction protocol. Real exports remain the single most
   valuable contribution here.

**Not planned: international price comparison.** The canonical global reference,
MSH's *International Medical Products Price Guide*, was retired in June 2024
with data already nine years stale; EURIPID is closed to anyone but national
pricing authorities; and ex-manufacturer, retail, reimbursed and procurement
prices are four different numbers that do not normalise. More to the point, a US
billing office does not care what another country pays, so the finding would be
one they can wave away — which is worse than no finding.

Rules must cite a source. A finding a billing office can wave away is worse than
no finding, because it costs the reader credibility they may only get to spend
once.

## Licence

MIT — see [LICENSE](LICENSE). The MIT licence covers **the code only**.

* Third-party rights and the CPT (AMA) / CDT (ADA) boundary: [NOTICE.md](NOTICE.md)
* Every known limitation, in full: [CAVEATS.md](CAVEATS.md)

## About

Built and maintained under **DAVANO Innovation Lab**, which builds and sources
free tools for the betterment of the people who use them.

Medical bill negotiation services charge 20–35% of whatever they save you, for
reading public federal data. This project reads it for nothing and shows its
sources so you can check the reading. It takes no fee, collects no data, and has
nothing to sell you.

Contributions are welcome — see *Contributing* above. Corrections to state-law
entries and citations are as valuable as new features, and are the most likely
thing here to be out of date.
