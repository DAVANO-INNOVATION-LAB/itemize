"""Core types shared by the parser, rules and evidence writer."""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict

def user_data_dir():
    """Per-user data directory, following each platform's own convention."""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "itemize")


def default_data_dir():
    """Where reference data lives, in priority order.

    1. $ITEMIZE_DATA, for anyone who wants to be explicit.
    2. `web/data` in a source checkout, which is what the repo ships and what
       the browser app serves.
    3. The per-user data directory, which is the only writable option once the
       package is pip-installed and there is no checkout to write into.

    Without (3) an installed copy has nowhere to put a refresh, which is why
    `itemize data --refresh` exists.
    """
    env = os.environ.get("ITEMIZE_DATA")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.join(os.path.dirname(here), "web", "data")
    if os.path.isdir(repo):
        return repo
    return user_data_dir()


DATA_DIR = default_data_dir()

# Severity ordering, worst first.
SEVERITIES = ("high", "warn", "notice", "info")

# How long each dataset stays believable before we say so out loud, in days.
#
# This measures when YOU last refreshed, not when CMS last published -- that is
# the number a reader can act on. Reference data going quietly stale is the
# failure mode this project is least able to detect from the inside, so it is
# reported as a finding rather than left in a footer nobody reads.
REFRESH_DAYS = {
    "hcpcs": 120,     # CMS updates quarterly
    "asp": 120,       # quarterly
    "dmepos": 120,    # quarterly
    "nadac": 45,      # weekly, but it moves by cents; 45 days is generous
    "drg": 400,       # annual
}

RE_CPT_I = re.compile(r"^\d{5}$")
RE_CPT_II = re.compile(r"^\d{4}F$")
RE_CPT_III = re.compile(r"^\d{4}T$")
RE_CDT = re.compile(r"^D\d{4}$")
RE_HCPCS_II = re.compile(r"^[A-CE-Z]\d{4}$")


def normalize_drg(drg):
    """MS-DRG to the 3-digit form CMS keys on, or "" if there isn't one.

    Readers copy the DRG off a UB-04 in whatever shape it was printed: "470",
    "0470", "DRG 470". Leading zeros are stripped before padding, because
    zfill alone leaves "0470" four characters long and the lookup silently
    misses. Mirrors normalizeDrg in web/rules.js.
    """
    code = re.sub(r"\D", "", str(drg or ""))
    if not code:
        return ""
    return (code.lstrip("0") or "0").zfill(3)


def classify(code):
    """'public' (HCPCS Level II), 'ama' (CPT), 'ada' (CDT), or 'unknown'."""
    c = (code or "").strip().upper()
    if RE_CPT_I.match(c) or RE_CPT_II.match(c) or RE_CPT_III.match(c):
        return "ama"
    if RE_CDT.match(c):
        return "ada"
    if RE_HCPCS_II.match(c):
        return "public"
    return "unknown"


@dataclass
class Line:
    idx: int
    code: str = ""
    desc: str = ""
    units: float = 1.0
    charge: float = 0.0
    date: str = ""
    modifiers: list = field(default_factory=list)
    revenue_code: str = ""
    unit_price: float = 0.0
    # National Drug Code, normalised to 11 digits. Drug lines on a hospital bill
    # frequently carry one alongside the HCPCS code; it is what lets us price a
    # line against NADAC rather than only against the Part B ASP limits.
    ndc: str = ""
    raw: str = ""

    @property
    def unit_charge(self):
        return self.charge / self.units if self.units else self.charge


@dataclass
class Finding:
    rule: str
    severity: str
    title: str
    detail: str
    lines: list = field(default_factory=list)
    citation: str = ""
    # Dollars *implicated* by this finding -- NOT a claim that this much was
    # overcharged. A Medicare benchmark is not what a hospital "should" charge;
    # commercial rates are legitimately higher. Only duplicate-billing findings
    # represent a directly recoverable amount.
    amount: float = 0.0
    recoverable: bool = False

    def to_dict(self):
        return asdict(self)


class Reference:
    """Public-domain reference data produced by tools/build_data.py."""

    def __init__(self, data_dir=DATA_DIR):
        self.dir = data_dir
        self.hcpcs = self._load("hcpcs.json", {})
        self.asp = self._load("asp.json", {})
        self.dmepos = self._load("dmepos.json", {})
        self.nadac = self._load("nadac.json", {})
        self.drg = self._load("drg.json", {})
        self.states_raw = self._load("states.json", {})
        self.manifest = self._load("manifest.json", {})

    def _load(self, name, default):
        path = os.path.join(self.dir, name)
        if not os.path.exists(path):
            return default
        with open(path) as f:
            return json.load(f)

    @property
    def available(self):
        return bool(self.hcpcs)

    def dataset_ages(self, today=None):
        """[(dataset, age_days, limit_days)] for every source with a date.

        A source records its own `retrieved`; the manifest's `built` is only a
        fallback for data written before that field existed. A dataset carried
        forward by `--skip` keeps its original date, so a partial rebuild cannot
        make stale files look fresh.
        """
        today = today or datetime.date.today()
        out = []
        for src in self.manifest.get("sources", []):
            name = src.get("dataset")
            stamp = src.get("retrieved") or self.manifest.get("built") or ""
            if not name or not stamp:
                continue
            try:
                when = datetime.date.fromisoformat(str(stamp)[:10])
            except ValueError:
                continue
            out.append((name, (today - when).days, REFRESH_DAYS.get(name, 180)))
        return sorted(out)

    def stale_datasets(self, today=None):
        return [(n, age, lim) for n, age, lim in self.dataset_ages(today) if age > lim]

    def describe(self, code):
        return self.hcpcs.get((code or "").upper(), "")

    def dmepos_fee(self, code):
        """(fee, category) or (None, None). Median non-rural state allowance."""
        rec = self.dmepos.get((code or "").upper())
        if not rec:
            return None, None
        return rec.get("fee"), rec.get("cat", "")

    def asp_limit(self, code):
        """(limit_per_dose_unit, dose_label) or (None, None)."""
        rec = self.asp.get((code or "").upper())
        if not rec:
            return None, None
        return rec.get("limit"), rec.get("dose", "")

    def nadac_price(self, ndc):
        """(price_per_unit, pricing_unit, description, is_brand) or None.

        NADAC is the surveyed *acquisition* cost -- what a pharmacy paid, not an
        allowed amount. Every dispensed drug is legitimately billed above it.
        """
        rec = self.nadac.get((ndc or "").strip())
        if not rec:
            return None
        return rec.get("p"), rec.get("u", ""), rec.get("d", ""), bool(rec.get("b"))

    def drg_stats(self, drg):
        """National averages for an MS-DRG, or None. Keys are zero-padded to 3."""
        code = normalize_drg(drg)
        return self.drg.get(code) if code else None

    def cite(self, dataset):
        """Provenance for a dataset: file, url, sha256 prefix, retrieval date.

        The date is the SOURCE's own `retrieved`, not the manifest's build date:
        a dataset carried forward by `--skip` was fetched earlier, and citing
        today's build date for it would put a false date in front of a billing
        office.
        """
        for s in self.manifest.get("sources", []):
            if s.get("dataset") == dataset:
                when = s.get("retrieved") or self.manifest.get("built") or "unknown"
                asof = s.get("asof")
                return (f"{s.get('member') or dataset} from {s.get('url')} "
                        f"(sha256 {s.get('sha256', '')[:12]}, retrieved {when}"
                        + (f", prices effective {asof}" if asof else "") + ")")
        return f"{dataset} (provenance unavailable -- run tools/build_data.py)"
