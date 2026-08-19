/* itemize — pure logic, no DOM.
 *
 * Mirrors itemize/*.py. Loadable in the browser (window.ITEMIZE) and in Node
 * (module.exports), which is what lets tests/test_parity.py run both engines
 * over the same fixture and diff the findings. Two implementations claiming to
 * agree, with nothing enforcing it, is how a CLI and a web app end up telling
 * the same person different things about their bill.
 *
 * When you change a rule here, change itemize/rules.py, and vice versa.
 */
'use strict';

(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ITEMIZE = api;
}(typeof self !== 'undefined' ? self : this, function () {

  const ASP_NOTICE_MULTIPLE = 3.0, ASP_HIGH_MULTIPLE = 10.0;
  const DME_NOTICE_MULTIPLE = 3.0, DME_HIGH_MULTIPLE = 10.0;
  // NADAC is acquisition cost -- what the pharmacy paid the wholesaler -- not an
  // allowed amount. A dispensing fee and a real margin sit on top of it
  // legitimately, so these thresholds are deliberately far higher than the ASP ones.
  const NADAC_NOTICE_MULTIPLE = 10.0, NADAC_HIGH_MULTIPLE = 50.0;
  const DRG_NOTICE_MULTIPLE = 2.0;
  const GFE_DISPUTE_THRESHOLD = 400.0;
  const SEV_ORDER = { high: 0, warn: 1, notice: 2, info: 3 };
  const PRICE_RULES = new Set(['asp_benchmark', 'dmepos_benchmark', 'nadac_benchmark']);

  const UNCLASSIFIED = {
    J3490: 'Unclassified drugs', J3590: 'Unclassified biologics',
    J9999: 'Not otherwise classified, antineoplastic drugs',
    C9399: 'Unclassified drugs or biologicals',
    A9270: 'Non-covered item or service',
    E1399: 'Durable medical equipment, miscellaneous',
    A4649: 'Surgical supply; miscellaneous',
  };

  const MODIFIER_NOTES = {
    25: 'Separate evaluation and management service on the same day as a procedure. Legitimate when a genuinely separate problem was addressed; also the most common way a routine visit is billed twice.',
    59: 'Distinct procedural service — the modifier used to bypass a bundling edit. Ask what made the services distinct: separate session, separate site, or separate encounter.',
    XU: 'Unusual non-overlapping service; a more specific successor to modifier 59 and subject to the same question.',
    XS: 'Separate structure; a more specific successor to modifier 59.',
    XE: 'Separate encounter; a more specific successor to modifier 59.',
    XP: 'Separate practitioner; a more specific successor to modifier 59.',
  };

  const REVENUE_FAMILIES = {
    '025': ['pharmacy', ['J', 'Q', 'C', 'S']],
    '026': ['IV therapy', ['J', 'Q', 'C', 'S']],
    '027': ['medical/surgical supplies', ['A', 'B', 'E', 'K', 'L']],
    '029': ['durable medical equipment', ['E', 'K', 'L']],
    '030': ['laboratory', ['P', 'G']],
    '031': ['laboratory pathology', ['P', 'G']],
    '032': ['radiology diagnostic', ['R', 'G']],
    '042': ['physical therapy', ['G', 'S']],
    '045': ['emergency room', []],
    '047': ['audiology', ['V']],
  };

  const STRUCTURAL = 'Structural finding -- derived from the bill itself, no external source.';
  const EOB_CITE = 'Cross-check between your itemized bill and your own EOB; no external data source.';

  /* ------------------------------------------------------------- codes */
  const RE_CPT_I = /^\d{5}$/, RE_CPT_II = /^\d{4}F$/, RE_CPT_III = /^\d{4}T$/;
  const RE_CDT = /^D\d{4}$/, RE_HCPCS_II = /^[A-CE-Z]\d{4}$/;

  /* MS-DRG to the 3-digit form CMS keys on, or '' if there isn't one. Leading
   * zeros are stripped before padding: padStart alone leaves '0470' four
   * characters long and the lookup silently misses. Mirrors normalize_drg. */
  function normalizeDrg(drg) {
    const digits = String(drg == null ? '' : drg).replace(/\D/g, '');
    if (!digits) return '';
    return (digits.replace(/^0+/, '') || '0').padStart(3, '0');
  }

  function classify(code) {
    const c = (code || '').trim().toUpperCase();
    if (RE_CPT_I.test(c) || RE_CPT_II.test(c) || RE_CPT_III.test(c)) return 'ama';
    if (RE_CDT.test(c)) return 'ada';
    if (RE_HCPCS_II.test(c)) return 'public';
    return 'unknown';
  }

  /* --------------------------------------------------------- reference */
  function Reference(data) {
    data = data || {};
    this.hcpcs = data.hcpcs || {};
    this.asp = data.asp || {};
    this.dmepos = data.dmepos || {};
    this.nadac = data.nadac || {};
    this.drg = data.drg || {};
    this.states = data.states || {};
    this.manifest = data.manifest || {};
  }
  /* (price_per_unit, pricing_unit, description, is_brand) or null. */
  Reference.prototype.nadacPrice = function (ndc) {
    const r = this.nadac[String(ndc || '').trim()];
    return r ? [r.p, r.u || '', r.d || '', !!r.b] : null;
  };
  Reference.prototype.drgStats = function (drg) {
    const code = normalizeDrg(drg);
    return code ? (this.drg[code] || null) : null;
  };
  Reference.prototype.describe = function (c) { return this.hcpcs[(c || '').toUpperCase()] || ''; };
  Reference.prototype.aspLimit = function (c) {
    const r = this.asp[(c || '').toUpperCase()];
    return r ? [r.limit, r.dose || ''] : [null, null];
  };
  Reference.prototype.dmeposFee = function (c) {
    const r = this.dmepos[(c || '').toUpperCase()];
    return r ? [r.fee, r.cat || ''] : [null, null];
  };
  Reference.prototype.cite = function (dataset) {
    const s = (this.manifest.sources || []).find((x) => x.dataset === dataset);
    if (!s) return `${dataset} (provenance unavailable -- run tools/build_data.py)`;
    return `${s.member} from ${s.url} (sha256 ${(s.sha256 || '').slice(0, 12)}, retrieved ${this.manifest.built || 'unknown'})`;
  };

  /* ----------------------------------------------------------- context */
  function Context(o) {
    o = o || {};
    ['insured', 'deductible_met', 'emergency', 'out_of_network', 'in_network_facility',
      'nonprofit_hospital', 'ground_ambulance'].forEach((k) => {
      this[k] = (o[k] === undefined) ? null : o[k];
    });
    this.good_faith_estimate = (o.good_faith_estimate === undefined) ? null : o.good_faith_estimate;
    this.state = o.state || '';
    // MS-DRG for an inpatient stay -- UB-04 form locator 71. Asked for, never
    // derived: itemize does not group a bill into a DRG itself.
    this.drg = o.drg || '';
    this.plan_funding = o.plan_funding || null;
  }
  Object.defineProperty(Context.prototype, 'self_pay', {
    get() { return this.insured === false; },
  });
  Object.defineProperty(Context.prototype, 'pays_full_allowed', {
    get() { return this.self_pay || (this.insured === true && this.deductible_met === false); },
  });
  Object.defineProperty(Context.prototype, 'state_law_applies', {
    get() { return this.plan_funding !== 'self_funded'; },
  });
  Object.defineProperty(Context.prototype, 'insurer_is_paying', {
    get() { return this.insured === true && this.deductible_met === true; },
  });

  /* ------------------------------------------------------------ helpers */
  const F = (o) => Object.assign({
    rule: '', severity: 'info', title: '', detail: '', lines: [],
    citation: '', amount: 0, recoverable: false,
  }, o);
  const fmt = (n) => '$' + Number(n).toLocaleString('en-US',
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const g = (n) => (Math.round(n * 1000) / 1000);
  const sum = (a, f) => a.reduce((s, x) => s + (f ? f(x) : x), 0);

  function money(s) {
    if (s == null) return 0;
    const m = String(s).match(/-?\$?\s*([\d,]+\.\d{2}|[\d,]+)/);
    if (!m) return 0;
    const v = parseFloat(m[1].replace(/,/g, ''));
    return isNaN(v) ? 0 : v;
  }
  function num(s, d) {
    d = d === undefined ? 1 : d;
    const v = parseFloat(String(s == null ? '' : s).replace(/,/g, '').trim());
    return (isNaN(v) || v <= 0) ? d : v;
  }

  /* ----------------------------------------------------------- parsing */
  const ALIASES = {
    code: ['code', 'hcpcs', 'cpt', 'cpt/hcpcs', 'hcpcs/cpt', 'procedure code', 'proc code',
      'service code', 'billing code', 'cpt code'],
    desc: ['description', 'desc', 'service', 'service description', 'item', 'procedure',
      'detail', 'charge description'],
    units: ['units', 'unit', 'qty', 'quantity', 'svc units', 'days/units'],
    charge: ['charge', 'charges', 'amount', 'billed', 'billed amount', 'total',
      'total charges', 'line total', 'amt'],
    date: ['date', 'service date', 'date of service', 'dos', 'svc date'],
    modifiers: ['modifier', 'modifiers', 'mod'],
    revenue_code: ['revenue code', 'rev code', 'rev cd', 'revcode'],
    unit_price: ['unit price', 'price', 'rate', 'unit charge', 'charge per unit',
      'unit cost', 'each'],
    ndc: ['ndc', 'ndc code', 'ndc number', 'national drug code', 'drug code'],
  };

  function splitRow(line, delim) {
    const out = []; let cur = '', q = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        if (q && line[i + 1] === '"') { cur += '"'; i++; } else q = !q;
      } else if (ch === delim && !q) { out.push(cur); cur = ''; }
      else cur += ch;
    }
    out.push(cur);
    return out.map((s) => s.trim());
  }

  function mapHeaders(header) {
    const out = {};
    header.forEach((h, i) => {
      const k = (h || '').trim().toLowerCase();
      for (const f in ALIASES) if (!(f in out) && ALIASES[f].includes(k)) { out[f] = i; return; }
    });
    header.forEach((h, i) => {
      const k = (h || '').trim().toLowerCase();
      for (const f in ALIASES) {
        if (f in out) continue;
        if (ALIASES[f].some((n) => k.includes(n))) { out[f] = i; return; }
      }
    });
    return out;
  }

  function parseDelimited(text) {
    const raw = text.split('\n').filter((l) => l.trim());
    if (!raw.length) return [];
    const delim = [',', '\t', '|', ';']
      .map((d) => [d, (raw[0].match(new RegExp(`\\${d}`, 'g')) || []).length])
      .sort((a, b) => b[1] - a[1])[0][0];

    const rows = raw.map((l) => splitRow(l, delim));
    let hdrI = -1, cols = {};
    for (let i = 0; i < Math.min(rows.length, 25); i++) {
      const m = mapHeaders(rows[i]);
      if ('charge' in m && ('code' in m || 'desc' in m)) { hdrI = i; cols = m; break; }
    }
    if (hdrI < 0) return [];

    const lines = [];
    for (let i = hdrI + 1; i < rows.length; i++) {
      const row = rows[i];
      const g = (f) => (cols[f] != null && cols[f] < row.length ? row[cols[f]].trim() : '');
      const code = g('code').toUpperCase(), desc = g('desc'), chargeRaw = g('charge');
      if (!chargeRaw && !code && !desc) continue;
      if (!code && /\b(total|balance|subtotal|amount due)\b/i.test(desc)) continue;
      lines.push({
        idx: lines.length + 1, code, desc,
        units: num(g('units')), charge: money(chargeRaw), date: g('date'),
        modifiers: g('modifiers').split(/[,\s/]+/).filter(Boolean).map((s) => s.toUpperCase()),
        revenue_code: g('revenue_code'),
        unit_price: money(g('unit_price')),
        ndc: normalizeNdc(g('ndc')) || normalizeNdc(code) || normalizeNdc(desc),
      });
    }
    return lines;
  }

  const RE_CODEISH = /^[A-Za-z]{0,4}\d{3,6}$/;
  const RE_DATEISH = /\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b/;

  /* National Drug Codes, normalised to the 11-digit 5-4-2 form CMS prices in.
   *
   * Hyphenated NDCs are zero-padded segment-wise, which is unambiguous. A bare
   * 10-digit run is NOT: it could be 4-4-2, 5-3-2 or 5-4-1, and choosing wrong
   * silently points at a different drug. We decline those rather than guess --
   * a confident wrong price is the failure mode this project exists to avoid.
   * Mirrors normalize_ndc in itemize/parse.py. */
  function normalizeNdc(s) {
    const t = String(s == null ? '' : s).trim();
    if (!t) return '';
    const hy = t.match(/\b(\d{4,5})-(\d{3,4})-(\d{1,2})\b/);
    if (hy) {
      return hy[1].padStart(5, '0') + hy[2].padStart(4, '0') + hy[3].padStart(2, '0');
    }
    const bare = t.match(/\b(\d{11})\b/);
    return bare ? bare[1] : '';
  }

  /* Read a line already split into columns (PDF text layers, aligned statements).
   * Positional reading beats pattern-matching one run-on string. Mirrors
   * _from_columns in itemize/parse.py. */
  function fromColumns(parts) {
    let chargeI = -1;
    for (let i = parts.length - 1; i >= 0; i--) {
      if (/^\$?[\d,]+\.\d{2}$/.test(parts[i].trim())) { chargeI = i; break; }
    }
    if (chargeI < 0) return null;
    const charge = money(parts[chargeI]);
    if (charge <= 0) return null;

    let date = '';
    for (const s of parts) { const m = s.match(RE_DATEISH); if (m) { date = m[1]; break; } }

    let code = '';
    for (let i = 0; i < parts.length; i++) {
      if (i === chargeI) continue;
      const t = parts[i].trim();
      if (t === date || RE_DATEISH.test(t)) continue;
      if (RE_CODEISH.test(t)) { code = t.toUpperCase(); break; }
    }

    let units = 1;
    for (let i = chargeI - 1; i >= 0; i--) {
      const t = parts[i].trim();
      if (/^\d{1,4}(\.\d+)?$/.test(t) && t.toUpperCase() !== code) { units = num(t); break; }
    }

    let desc = '';
    for (let i = 0; i < parts.length; i++) {
      const t = parts[i].trim();
      if (i === chargeI || t.toUpperCase() === code || t === date) continue;
      if (t.length > desc.length && /[A-Za-z]{3}/.test(t)) desc = t;
    }
    return { code, desc: desc.slice(0, 160), units, charge, date };
  }

  function parseText(text) {
    const lines = [];
    for (const raw of text.split('\n')) {
      const s = raw.trim();
      if (s.length < 8) continue;
      if (/\b(total|balance due|subtotal|amount due|page \d)\b/i.test(s)) continue;
      const monies = s.match(/-?\$?\s*([\d,]+\.\d{2}|[\d,]+)/g);
      if (!monies) continue;

      const cols = s.split(/\s{2,}/);
      if (cols.length >= 3) {
        const got = fromColumns(cols);
        if (got) {
          lines.push({
            idx: lines.length + 1, code: got.code, desc: got.desc, units: got.units,
            charge: got.charge, date: got.date, modifiers: [], revenue_code: '',
            unit_price: 0, ndc: normalizeNdc(s),
          });
          continue;
        }
      }

      const charge = money(monies[monies.length - 1]);
      if (charge <= 0) continue;
      const cm = s.match(/\b(\d{5}|[A-Za-z]\d{4})\b/);
      const dm = s.match(/\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b/);
      const um = s.match(/\b(\d{1,3})\b(?=[^\d]*[\d,]+\.\d{2}\s*$)/);
      let desc = cm ? s.slice(s.indexOf(cm[1]) + cm[1].length) : s;
      desc = desc.replace(/[\d,]*\.?\d*\s*$/, '').replace(/^[\s.\t|-]+|[\s.\t|-]+$/g, '');
      lines.push({
        idx: lines.length + 1, code: cm ? cm[1].toUpperCase() : '', desc: desc.slice(0, 160),
        units: um ? num(um[1]) : 1, charge, date: dm ? dm[1] : '', modifiers: [], revenue_code: '',
        unit_price: 0, ndc: normalizeNdc(s),
      });
    }
    return lines;
  }

  function parseBill(text) {
    text = text.replace(/\r\n?/g, '\n');
    const d = parseDelimited(text);
    return d.length ? d : parseText(text);
  }


  /* --------------------------------------------------------- eob parsing */
  const EOB_ALIASES = {
    code: ['code', 'hcpcs', 'cpt', 'procedure', 'service code', 'proc'],
    billed: ['billed', 'charged', 'charges', 'amount billed', 'provider charge', 'submitted', 'billed amount'],
    allowed: ['allowed', 'allowed amount', 'plan allowance', 'eligible', 'negotiated', 'contracted'],
    plan_paid: ['plan paid', 'paid', 'insurance paid', 'payer paid', 'we paid', 'benefit'],
    patient: ['patient responsibility', 'you owe', 'your responsibility', 'member responsibility', 'patient resp', 'you may owe', 'responsibility', 'patient'],
    date: ['date', 'service date', 'date of service', 'dos'],
  };

  function parseEob(text) {
    text = (text || '').replace(/\r\n?/g, '\n');
    const raw = text.split('\n').filter((l) => l.trim());
    if (!raw.length) return [];
    const delim = [',', '\t', '|', ';']
      .map((d) => [d, (raw[0].split(d).length - 1)])
      .sort((a, b) => b[1] - a[1])[0][0];
    const rows = raw.map((l) => splitRow(l, delim));

    const mapHdr = (header) => {
      const out = {};
      header.forEach((h, i) => {
        const k = (h || '').trim().toLowerCase();
        for (const f in EOB_ALIASES) if (!(f in out) && EOB_ALIASES[f].includes(k)) { out[f] = i; return; }
      });
      header.forEach((h, i) => {
        const k = (h || '').trim().toLowerCase();
        for (const f in EOB_ALIASES) {
          if (f in out) continue;
          if (EOB_ALIASES[f].some((n) => k.includes(n))) { out[f] = i; return; }
        }
      });
      return out;
    };
    const m2 = (s) => {
      const m = String(s || '').match(/-?\$?\s*([\d,]+\.\d{2}|[\d,]+)/);
      if (!m) return null;
      const v = parseFloat(m[1].replace(/,/g, ''));
      return isNaN(v) ? null : v;
    };

    let hdrI = -1, cols = {};
    for (let i = 0; i < Math.min(rows.length, 25); i++) {
      const m = mapHdr(rows[i]);
      if ('patient' in m && ('code' in m || 'allowed' in m)) { hdrI = i; cols = m; break; }
    }
    if (hdrI < 0) return [];

    const out = [];
    for (let i = hdrI + 1; i < rows.length; i++) {
      const row = rows[i];
      const gg = (f) => (cols[f] != null && cols[f] < row.length ? row[cols[f]].trim() : '');
      const code = gg('code').toUpperCase();
      const patient = m2(gg('patient'));
      if (patient == null && !code) continue;
      if (/\b(total|subtotal)\b/i.test(row.join(' ')) && !code) continue;
      out.push({
        code, date: gg('date'), billed: m2(gg('billed')), allowed: m2(gg('allowed')),
        plan_paid: m2(gg('plan_paid')), patient,
      });
    }
    return out;
  }

  /* ------------------------------------------------------------- rules */
  function ruleExactDuplicates(lines) {
    const groups = {};
    for (const l of lines) {
      if (!l.code && !l.desc) continue;
      const k = [(l.code || '').toUpperCase(), (l.date || '').trim(),
        l.charge.toFixed(2), g(l.units)].join('|');
      (groups[k] = groups[k] || []).push(l);
    }
    const out = [];
    for (const k of Object.keys(groups)) {
      const grp = groups[k];
      if (grp.length < 2 || grp[0].charge <= 0) continue;
      const extra = (grp.length - 1) * grp[0].charge;
      const label = grp[0].code || (grp[0].desc || '').slice(0, 40);
      out.push(F({
        rule: 'exact_duplicate', severity: 'high',
        title: `${label} billed ${grp.length}x identically`,
        detail: `Lines ${grp.map((l) => l.idx).join(', ')} are identical: ${grp[0].units} unit(s) at ${fmt(grp[0].charge)}`
          + (grp[0].date ? ` on ${grp[0].date}` : '')
          + `. If this was a single service, ${fmt(extra)} is duplicated. Ask the billing office to produce the medical record entry supporting each occurrence separately.`,
        lines: grp.map((l) => l.idx), citation: STRUCTURAL, amount: extra, recoverable: true,
      }));
    }
    return out;
  }

  function benchmark(lines, ref, kind) {
    const isAsp = kind === 'asp';
    const table = isAsp ? ref.asp : ref.dmepos;
    if (!Object.keys(table).length) return [];
    const notice = isAsp ? ASP_NOTICE_MULTIPLE : DME_NOTICE_MULTIPLE;
    const high = isAsp ? ASP_HIGH_MULTIPLE : DME_HIGH_MULTIPLE;
    const rate = (c) => (isAsp ? ref.aspLimit(c) : ref.dmeposFee(c));

    const groups = {};
    for (const l of lines) {
      const [r] = rate(l.code);
      if (!r || r <= 0 || l.charge <= 0) continue;
      const allowed = r * (l.units || 1);
      if (allowed <= 0 || l.charge / allowed < notice) continue;
      const k = [(l.code || '').toUpperCase(), g(l.units), l.charge.toFixed(2)].join('|');
      (groups[k] = groups[k] || []).push(l);
    }
    const out = [];
    for (const k of Object.keys(groups)) {
      const grp = groups[k], l0 = grp[0];
      const [r, extra] = rate(l0.code);
      const allowed = r * (l0.units || 1), mult = l0.charge / allowed;
      const d = ref.describe(l0.code);
      const idxs = grp.map((x) => x.idx);
      const where = idxs.length === 1 ? `Line ${idxs[0]}` : `Lines ${idxs.join(', ')} (each)`;
      out.push(F({
        rule: isAsp ? 'asp_benchmark' : 'dmepos_benchmark',
        severity: mult >= high ? 'high' : 'warn',
        title: isAsp
          ? `${l0.code} billed at ${Math.round(mult).toLocaleString()}x the Medicare allowed amount`
          : `${l0.code} billed at ${Math.round(mult).toLocaleString()}x the Medicare equipment allowance`,
        detail: isAsp
          ? `${where}: ${l0.units} unit(s) of ${l0.code}${d ? ` (${d})` : ''} billed at ${fmt(l0.charge)}. Medicare's Part B payment limit is $${r.toFixed(4)}${extra ? ` per ${extra}` : ''}, so the same quantity allows ${fmt(allowed)}. A hospital is not required to bill Medicare rates, so this is not by itself an error -- but a multiple this large is worth disputing, and is strong leverage in a request for a self-pay or charity adjustment.`
          : `${where}: ${l0.units} unit(s) of ${l0.code}${d ? ` (${d})` : ''} billed at ${fmt(l0.charge)}. Medicare's DMEPOS allowance is ${fmt(r)} per unit (median across states), so the same quantity allows ${fmt(allowed)}. Equipment and supplies are frequently marked up far above the schedule; ask whether the item was rented or purchased, and whether a rental cap applies.`,
        lines: idxs, citation: ref.cite(isAsp ? 'asp' : 'dmepos'),
        amount: l0.charge * grp.length,
      }));
    }
    return out;
  }

  const ruleAspBenchmark = (lines, ref) => benchmark(lines, ref, 'asp');
  const ruleDmeposBenchmark = (lines, ref) => benchmark(lines, ref, 'dmepos');

  const NADAC_UNIT_LABEL = { EA: 'each', ML: 'per mL', GM: 'per gram' };

  /* Drug lines carrying an NDC, against what pharmacies pay to acquire them.
   * Reaches the outpatient pharmacy side ASP cannot: ASP prices ~900
   * physician-administered J-codes, NADAC ~32,000 NDCs. Where a line has both,
   * ASP wins and this stands down -- the same dollars must not be counted twice.
   * Mirrors rule_nadac_benchmark in itemize/rules.py. */
  function ruleNadacBenchmark(lines, ref) {
    if (!Object.keys(ref.nadac).length) return [];
    const groups = {};
    for (const l of lines) {
      if (!l.ndc) continue;
      if (ref.aspLimit(l.code)[0]) continue;
      const rec = ref.nadacPrice(l.ndc);
      if (!rec) continue;
      const price = rec[0];
      if (!price || price <= 0 || l.charge <= 0) continue;
      const allowed = price * (l.units || 1);
      if (allowed <= 0 || l.charge / allowed < NADAC_NOTICE_MULTIPLE) continue;
      const k = [l.ndc, g(l.units), l.charge.toFixed(2)].join('|');
      (groups[k] = groups[k] || []).push(l);
    }
    const out = [];
    for (const k of Object.keys(groups)) {
      const grp = groups[k], l0 = grp[0];
      const [price, unit, desc] = ref.nadacPrice(l0.ndc);
      const allowed = price * (l0.units || 1), mult = l0.charge / allowed;
      const idxs = grp.map((x) => x.idx);
      const where = idxs.length === 1 ? `Line ${idxs[0]}` : `Lines ${idxs.join(', ')} (each)`;
      const unitLabel = NADAC_UNIT_LABEL[unit] || unit || 'per unit';
      out.push(F({
        rule: 'nadac_benchmark',
        severity: mult >= NADAC_HIGH_MULTIPLE ? 'high' : 'warn',
        title: `NDC ${l0.ndc} billed at ${Math.round(mult).toLocaleString()}x what pharmacies pay for it`,
        detail: `${where}: ${l0.units} unit(s) of ${desc || 'this drug'} (NDC ${l0.ndc}) billed at ${fmt(l0.charge)}. `
          + `The national average acquisition cost is $${price.toFixed(5)} ${unitLabel}, so the same quantity costs about ${fmt(allowed)} to buy. `
          + 'NADAC is what a pharmacy pays a wholesaler, not an allowed amount -- a dispensing fee and a real margin belong on top of it, so this is not an error. '
          + 'A multiple this large is still worth asking about, and is strong support for a financial-assistance request. '
          + `Check the units: NADAC prices ${unitLabel}, and a bill does not always count units the same way.`,
        lines: idxs, citation: ref.cite('nadac'), amount: l0.charge * grp.length,
      }));
    }
    return out;
  }

  /* An inpatient bill against the national average charge for its DRG.
   * Whole-bill context, never a line dispute: amount stays zero so it can never
   * be added to a recoverable total. Mirrors rule_drg_benchmark. */
  function ruleDrgBenchmark(lines, ref, ctx) {
    if (!ctx || !ctx.drg || !Object.keys(ref.drg).length) return [];
    const code = normalizeDrg(ctx.drg);
    const stats = ref.drgStats(ctx.drg);
    if (!stats) {
      return [F({
        rule: 'drg_unknown', severity: 'notice',
        title: `DRG ${code} is not in the national averages we ship`,
        detail: 'The CMS file covers DRGs with enough Medicare discharges to publish without identifying patients. '
          + 'Yours is not in it, so no comparison is possible here. Confirm the DRG with the billing office -- '
          + 'it is form locator 71 on a UB-04.',
        lines: [], citation: ref.cite('drg'),
      })];
    }
    const total = sum(lines, (l) => l.charge);
    const avg = stats.charge || 0;
    if (total <= 0 || avg <= 0) return [];
    const mult = total / avg;
    const high = mult >= DRG_NOTICE_MULTIPLE;
    let body = `Your bill totals ${fmt(total)}. Across ${(stats.n || 0).toLocaleString()} Medicare discharges nationally, `
      + `hospitals submitted an average charge of ${fmt(avg)} for DRG ${code} (${stats.desc || ''})`
      + (stats.pay ? `, and were paid an average of ${fmt(stats.pay)}.` : '.')
      + ' Submitted charges are list prices that almost nobody pays; the gap between the two columns is the ordinary '
      + 'state of hospital billing, not evidence of an error.';
    body += high
      ? ` Yours is ${mult.toFixed(1)}x the national average charge, which is worth asking about — but confirm first `
        + 'that this statement covers the whole stay and nothing else, because a partial bill or an added '
        + 'professional fee will skew the comparison.'
      : ` Yours is ${mult.toFixed(2)}x that average, which is unremarkable.`;
    return [F({
      rule: 'drg_benchmark',
      severity: high ? 'notice' : 'info',
      title: high
        ? `Bill is ${mult.toFixed(1)}x the national average charge for DRG ${code}`
        : `Bill is in line with the national average for DRG ${code}`,
      detail: body, lines: [], citation: ref.cite('drg'),
    })];
  }

  function ruleModifierFlags(lines) {
    const out = [];
    for (const l of lines) {
      for (const mod of (l.modifiers || [])) {
        const m = String(mod).trim().toUpperCase();
        if (!(m in MODIFIER_NOTES)) continue;
        out.push(F({
          rule: 'modifier_flag', severity: 'notice',
          title: `Line ${l.idx} carries modifier ${m}`,
          detail: `${l.code || 'This line'} was billed with modifier ${m}. ${MODIFIER_NOTES[m]} Ask the billing office to state, in writing, what justified the modifier on this line.`,
          lines: [l.idx],
          citation: 'Structural finding -- read from the modifier field on your own bill; no external source.',
          amount: l.charge,
        }));
      }
    }
    return out;
  }

  function ruleRevenueCodeMismatch(lines) {
    const out = [];
    for (const l of lines) {
      const rev = (l.revenue_code || '').replace(/\D/g, '');
      const code = (l.code || '').toUpperCase();
      if (rev.length < 3 || !code || classify(code) !== 'public') continue;
      const fam = REVENUE_FAMILIES[rev.slice(0, 3)];
      if (!fam) continue;
      const [label, prefixes] = fam;
      if (!prefixes.length || prefixes.indexOf(code[0]) !== -1) continue;
      out.push(F({
        rule: 'revenue_code_mismatch', severity: 'notice',
        title: `Line ${l.idx}: revenue code ${rev} (${label}) does not match code ${code}`,
        detail: `Revenue code ${rev} describes ${label}, but ${code} is not a code normally billed under it. This is sometimes a clerical mismatch and sometimes a charge posted to the wrong department. Ask which department provided the item and what code was sent to your plan.`,
        lines: [l.idx],
        citation: 'Structural finding -- compares the revenue code and HCPCS code on your own bill; no external source.',
        amount: l.charge,
      }));
    }
    return out;
  }

  function ruleUnitPriceArithmetic(lines) {
    const out = [];
    for (const l of lines) {
      const up = l.unit_price || 0;
      if (up <= 0 || l.units <= 0 || l.charge <= 0) continue;
      const expected = up * l.units, diff = l.charge - expected;
      if (Math.abs(diff) <= Math.max(0.02, expected * 0.005)) continue;
      out.push(F({
        rule: 'unit_price_arithmetic', severity: 'warn',
        title: `Line ${l.idx} does not add up`,
        detail: `${l.units} unit(s) at ${fmt(up)} each is ${fmt(expected)}, but the line is charged at ${fmt(l.charge)} — a difference of ${fmt(Math.abs(diff))}. Ask them to recalculate the line.`,
        lines: [l.idx],
        citation: 'Structural finding -- arithmetic on your own bill; no external source.',
        amount: Math.abs(diff), recoverable: diff > 0,
      }));
    }
    return out;
  }

  function ruleUnclassified(lines, ref) {
    const out = [];
    for (const l of lines) {
      const c = (l.code || '').toUpperCase();
      if (!(c in UNCLASSIFIED)) continue;
      const why = c === 'A9270'
        ? 'A9270 means the payer treats this as non-covered. Confirm you were given advance written notice before it was provided -- without it, you may not be responsible for the charge.'
        : 'This code means the item was not identified. You are entitled to know what you were charged for; ask for the NDC number (for a drug) or the manufacturer invoice (for a supply or device).';
      out.push(F({
        rule: 'unclassified_code', severity: 'warn',
        title: `${c} is an unspecified code (${UNCLASSIFIED[c]})`,
        detail: `Line ${l.idx}, ${fmt(l.charge)}. ${why}`,
        lines: [l.idx], citation: ref.cite('hcpcs'), amount: l.charge,
      }));
    }
    return out;
  }

  function ruleMissingCode(lines) {
    const bad = lines.filter((l) => !l.code && l.charge > 0);
    if (!bad.length) return [];
    const total = sum(bad, (l) => l.charge), n = bad.length;
    return [F({
      rule: 'missing_code', severity: 'warn',
      title: n === 1 ? '1 charge line carries no procedure code'
        : `${n} charge lines carry no procedure code`,
      detail: `${n === 1 ? 'Line' : 'Lines'} ${bad.slice(0, 20).map((l) => l.idx).join(', ')}`
        + `${n > 20 ? ' ...' : ''} ${n === 1 ? 'totals' : 'total'} ${fmt(total)} with no HCPCS/CPT code. `
        + 'A summary bill is not an itemized bill. Request a fully itemized statement showing a code, units and charge for every line before paying -- this alone often changes the total.',
      lines: bad.map((l) => l.idx), citation: STRUCTURAL, amount: total,
    })];
  }

  function ruleSameCodeSameDay(lines) {
    const groups = {};
    for (const l of lines) {
      if (!l.code || !l.date) continue;
      const k = l.code.toUpperCase() + '|' + l.date.trim();
      (groups[k] = groups[k] || []).push(l);
    }
    const out = [];
    for (const k of Object.keys(groups)) {
      const grp = groups[k];
      if (grp.length < 2) continue;
      const keys = new Set(grp.map((l) => [l.charge.toFixed(2), g(l.units)].join('|')));
      if (keys.size === 1) continue;
      const [code, date] = k.split('|');
      const total = sum(grp, (l) => l.charge);
      out.push(F({
        rule: 'same_code_same_day', severity: 'notice',
        title: `${code} appears ${grp.length}x on ${date}`,
        detail: `Lines ${grp.map((l) => l.idx).join(', ')} bill ${code} multiple times on the same date for a combined ${fmt(total)}. This can be legitimate (repeat doses, bilateral procedures) but is also where double-posting hides. Ask which record entry supports each line.`,
        lines: grp.map((l) => l.idx), citation: STRUCTURAL, amount: total,
      }));
    }
    return out;
  }

  function ruleUnknownCode(lines, ref) {
    if (!Object.keys(ref.hcpcs).length) return [];
    return lines.filter((l) => l.code && classify(l.code) === 'unknown').map((l) => F({
      rule: 'unknown_code', severity: 'notice',
      title: `'${l.code}' is not a recognised HCPCS or CPT code`,
      detail: `Line ${l.idx}, ${fmt(l.charge)}. This looks like an internal chargemaster code rather than a standard billing code. Ask the billing office which HCPCS/CPT code was submitted to your insurer for this line -- the two should correspond.`,
      lines: [l.idx], citation: ref.cite('hcpcs'), amount: l.charge,
    }));
  }

  function ruleLicensed(lines) {
    const out = [];
    const ama = lines.filter((l) => classify(l.code) === 'ama');
    const ada = lines.filter((l) => classify(l.code) === 'ada');
    if (ama.length) out.push(F({
      rule: 'licensed_cpt', severity: 'info',
      title: `${ama.length} line(s) use CPT codes, which this tier cannot benchmark`,
      detail: `Lines ${ama.slice(0, 20).map((l) => l.idx).join(', ')}${ama.length > 20 ? ' ...' : ''} total ${fmt(sum(ama, (l) => l.charge))}. CPT codes and descriptions are copyright the American Medical Association, so itemize does not redistribute them. To check these for unbundling and units limits, run \`itemize ncci\` locally, which shows you CMS's AMA licence and downloads the NCCI edit files under your own acceptance.`,
      lines: ama.map((l) => l.idx),
      citation: 'HCPC record layout, CMS: CPT-4 codes and descriptions are used under the CMS/AMA agreement; other use violates the AMA copyright.',
    }));
    if (ada.length) out.push(F({
      rule: 'licensed_cdt', severity: 'info',
      title: `${ada.length} line(s) use dental CDT codes`,
      detail: 'CDT codes are copyright the American Dental Association and are likewise excluded from this tool’s bundled data.',
      lines: ada.map((l) => l.idx),
      citation: 'HCPC record layout, CMS: Level II D-series descriptors are copyright the American Dental Association.',
    }));
    return out;
  }

  // Order mirrors RULES in itemize/rules.py. The final sort is by severity then
  // amount, so registry order does not affect output -- but keeping them aligned
  // makes a diff between the two engines readable.
  const RULES = [ruleExactDuplicates, ruleAspBenchmark, ruleNadacBenchmark,
    ruleDmeposBenchmark, ruleDrgBenchmark,
    ruleModifierFlags, ruleRevenueCodeMismatch, ruleUnitPriceArithmetic,
    ruleUnclassified, ruleMissingCode, ruleSameCodeSameDay, ruleUnknownCode,
    ruleLicensed];

  /* ------------------------------------------------------------ rights */
  const CITE_GFE = 'No Surprises Act good-faith-estimate rules for uninsured and self-pay individuals, 45 CFR 149.610; patient-provider dispute resolution, 45 CFR 149.620. Verify current text at eCFR.gov.';
  const CITE_501R = 'Internal Revenue Code 501(r)(4) (written financial assistance policy required of tax-exempt hospitals) and 501(r)(5) (limitation on charges to FAP-eligible individuals to amounts generally billed); 26 CFR 1.501(r)-5. Verify at irs.gov.';
  const CITE_NSA_EMERGENCY = 'No Surprises Act balance-billing protections for emergency services, 45 CFR 149.410. Verify at eCFR.gov.';
  const CITE_NSA_FACILITY = 'No Surprises Act protections for non-emergency services by non-participating providers at participating facilities, 45 CFR 149.420. Verify at eCFR.gov.';
  const CITE_AMBULANCE = 'Ground ambulance services are excluded from the federal No Surprises Act; protection depends on state law. See the federal Advisory Committee on Ground Ambulance and Patient Billing (GAPB), cms.gov.';

  function rights(lines, ref, ctx) {
    const out = [], total = sum(lines, (l) => l.charge);
    if (ctx.self_pay) {
      if (ctx.good_faith_estimate == null) {
        out.push(F({
          rule: 'right_gfe_missing', severity: 'high',
          title: 'You were entitled to a Good Faith Estimate before this care',
          detail: `You told us you are uninsured or self-pay. Providers must give uninsured and self-pay patients a written Good Faith Estimate of expected charges in advance of scheduled care. If you never received one, say so in writing and ask for it now. This bill totals ${fmt(total)}. If a Good Faith Estimate exists and the bill exceeds it by $${GFE_DISPUTE_THRESHOLD.toFixed(0)} or more, you can start the federal patient-provider dispute resolution process rather than negotiating alone. Almost nobody uses this route, and it costs the provider more than settling.`,
          lines: [], citation: CITE_GFE, amount: total,
        }));
      } else {
        const over = total - ctx.good_faith_estimate;
        if (over >= GFE_DISPUTE_THRESHOLD) out.push(F({
          rule: 'right_gfe_exceeded', severity: 'high',
          title: `Bill exceeds your Good Faith Estimate by ${fmt(over)} — you can dispute it federally`,
          detail: `Your Good Faith Estimate was ${fmt(ctx.good_faith_estimate)} and this bill totals ${fmt(total)}, a difference of ${fmt(over)}. Because that is at least $${GFE_DISPUTE_THRESHOLD.toFixed(0)}, you may use the federal patient-provider dispute resolution process. Start it within the deadline stated in your estimate paperwork, and keep the estimate — it is the evidence.`,
          lines: [], citation: CITE_GFE, amount: over, recoverable: true,
        }));
      }
    }
    if (ctx.nonprofit_hospital === true) out.push(F({
      rule: 'right_charity_care', severity: 'high',
      title: 'This hospital is required to have a financial assistance policy',
      detail: 'Tax-exempt hospitals must maintain a written Financial Assistance Policy, must publicise it, and may not charge patients who qualify under it more than the amounts generally billed to insured patients. Ask for the Financial Assistance Policy and the application by name — eligibility is often far higher up the income scale than people assume, and applying can reduce or erase the balance regardless of whether any individual line is coded correctly. Ask them to place the account on hold while your application is pending.',
      lines: [], citation: CITE_501R, amount: total,
    }));
    if (ctx.emergency === true) out.push(F({
      rule: 'right_nsa_emergency', severity: 'high',
      title: 'Emergency care: balance billing above in-network cost sharing is prohibited',
      detail: 'You indicated this was emergency care. For emergency services, you generally cannot be billed more than your plan’s in-network cost sharing, even if the provider or facility is out of network, and your cost sharing must count toward your in-network deductible and out-of-pocket maximum. If this bill charges you the difference between the provider’s charge and what your plan paid, say in writing that you believe it is a prohibited balance bill and ask them to rebill.',
      lines: [], citation: CITE_NSA_EMERGENCY, amount: total, recoverable: true,
    }));
    if (ctx.out_of_network === true && ctx.in_network_facility === true) out.push(F({
      rule: 'right_nsa_facility', severity: 'high',
      title: 'Out-of-network provider at an in-network facility: balance billing restricted',
      detail: 'You indicated an out-of-network provider treated you at an in-network facility. For most such services you cannot be balance billed beyond in-network cost sharing unless you gave written consent in advance on the required federal notice form. If you did not sign that specific form, ask them to produce it — if they cannot, the protection applies.',
      lines: [], citation: CITE_NSA_FACILITY, amount: total, recoverable: true,
    }));
    if (ctx.ground_ambulance === true) out.push(F({
      rule: 'right_ambulance_gap', severity: 'warn',
      title: 'Ground ambulance is not covered by the federal surprise-billing law',
      detail: `Ground ambulance rides were deliberately excluded from the federal No Surprises Act, so federal balance-billing protection does not apply. Roughly 22 states have enacted their own protections for people in fully-insured (not self-funded) plans. Check your state's rules${ctx.state ? ' in ' + ctx.state : ''} before paying, and check whether your plan is fully insured or self-funded — the answer decides whether state law reaches you at all.`,
      lines: [], citation: CITE_AMBULANCE, amount: 0,
    }));
    return out;
  }

  /* --------------------------------------------------------------- eob */
  function eobCrossCheck(lines, eobRows) {
    if (!eobRows || !eobRows.length) return [];
    const byCode = {};
    for (const r of eobRows) {
      if (r.code) (byCode[r.code.toUpperCase()] = byCode[r.code.toUpperCase()] || []).push(r);
    }
    const out = [];
    const billedTotal = sum(lines, (l) => l.charge);
    const eobTotal = sum(eobRows.filter((r) => r.patient != null), (r) => r.patient);

    const seen = new Set();
    for (const ln of lines) {
      const code = (ln.code || '').toUpperCase();
      const rows = byCode[code];
      if (!rows || seen.has(code)) continue;
      seen.add(code);
      const resp = sum(rows.filter((r) => r.patient != null), (r) => r.patient);
      const charged = sum(lines.filter((l) => (l.code || '').toUpperCase() === code), (l) => l.charge);
      const over = charged - resp;
      if (over > Math.max(0.01, resp * 0.01)) out.push(F({
        rule: 'eob_line_mismatch', severity: 'high',
        title: `${code}: billed ${fmt(charged)} but your EOB says you owe ${fmt(resp)}`,
        detail: `The itemized bill charges ${fmt(charged)} for ${code}, while your Explanation of Benefits puts your responsibility at ${fmt(resp)} — a difference of ${fmt(over)}. If your plan has already adjudicated this line, the provider generally may not bill you above the patient-responsibility figure. Send the EOB page with this line highlighted and ask them to adjust or to explain the difference in writing.`,
        lines: lines.filter((l) => (l.code || '').toUpperCase() === code).map((l) => l.idx),
        citation: EOB_CITE, amount: over, recoverable: true,
      }));
    }

    const overTotal = billedTotal - eobTotal;
    if (eobTotal > 0 && overTotal > Math.max(1.0, eobTotal * 0.02)) out.push(F({
      rule: 'eob_total_mismatch', severity: 'high',
      title: `Statement totals ${fmt(billedTotal)}; EOB patient responsibility totals ${fmt(eobTotal)}`,
      detail: `Across every line, your EOB puts your responsibility at ${fmt(eobTotal)} but this statement asks for ${fmt(billedTotal)}, a difference of ${fmt(overTotal)}. A statement showing full charges rather than your post-adjudication balance is common and is not always an error — but you should not pay from the statement until the two agree. Ask for a statement that reflects the processed claim.`,
      lines: [], citation: EOB_CITE, amount: overTotal,
    }));

    const unseen = lines.filter((l) => l.code && !byCode[(l.code || '').toUpperCase()]);
    if (Object.keys(byCode).length && unseen.length) {
      const total = sum(unseen, (l) => l.charge);
      out.push(F({
        rule: 'eob_line_absent', severity: 'warn',
        title: `${unseen.length} billed line(s) do not appear on your EOB`,
        detail: `Lines ${unseen.slice(0, 20).map((l) => l.idx).join(', ')}${unseen.length > 20 ? ' ...' : ''} totalling ${fmt(total)} are on the bill but not on the EOB. Either they were never submitted to your plan, or they were submitted under different codes. Ask which — if a claim was never filed, ask them to file it before billing you.`,
        lines: unseen.map((l) => l.idx), citation: EOB_CITE, amount: total,
      }));
    }
    return out;
  }


  /* ----------------------------------------------------------- letters */
  const L_HEADER = (recipient) => `[Your name]
[Your address]
[Account or statement number]
[Date]

${recipient}

Re: Account [number] — [patient name], date(s) of service [dates]
`;
  const L_CLOSE = `
I am asking for a written response. Please treat this letter as a request to
place the account on hold, and not to refer it to collections, while the
question is open.

Sincerely,
[Your name]
[Phone] / [Email]
`;
  const BILLING = '[Hospital/provider billing department]';

  function findingsBlock(findings, limit) {
    limit = limit || 12;
    if (!findings.length) return '  [No specific line items — see attached statement.]\n';
    let out = '';
    findings.slice(0, limit).forEach((f, i) => {
      out += `  ${i + 1}. ${f.title}\n     Line(s): ${(f.lines || []).join(', ') || '—'}\n     Source: ${f.citation}\n`;
    });
    if (findings.length > limit) out += `  …and ${findings.length - limit} further item(s) in the attached packet.\n`;
    return out;
  }

  const LETTERS = {
    itemized: {
      label: 'Request a fully itemized bill',
      build: () => L_HEADER(BILLING) + `
I am requesting a fully itemized statement for this account.

The statement I received shows summary or departmental totals. Please send an
itemized bill showing, for every line: date of service, the HCPCS or CPT code
billed, any modifiers, the revenue code, the number of units, and the charge
per unit.

If any line was billed to my insurer, please also state the claim number and the
date it was submitted.

I am not refusing to pay. I am asking to see what I am being asked to pay for
before I do.
` + L_CLOSE,
    },
    dispute: {
      label: 'Dispute specific line items',
      build: (ctx, findings, total) => L_HEADER(BILLING) + `
I have reviewed the itemized statement for this account and I am disputing the
items below in writing.

` + findingsBlock(findings) + `
For each item, please either correct the charge or send me a written explanation
that identifies the medical record entry supporting it.

Where I have cited a federal file above, I have attached the relevant page. I am
happy to be shown that I have misread it.
` + (total ? `\nThe disputed amount is ${fmt(total)} of the balance.\n` : '') + L_CLOSE,
    },
    assistance: {
      label: 'Apply for hospital financial assistance',
      build: () => L_HEADER('[Hospital financial assistance office]') + `
I am requesting a copy of your Financial Assistance Policy and the application
form, and I am applying for assistance on this account.

Please confirm in writing:

  1. The income and asset thresholds used to determine eligibility.
  2. The documents you need from me, and the deadline for submitting them.
  3. The "amounts generally billed" figure that applies to this account if I am
     found eligible.
  4. That collection activity on this account is suspended while my application
     is pending.

If this facility is not a tax-exempt hospital, please tell me so and send me
your charity care or self-pay discount policy instead.
` + L_CLOSE,
    },
    gfe: {
      label: 'Dispute a bill that exceeds a Good Faith Estimate',
      build: (ctx, findings, total) => {
        const est = (ctx && ctx.good_faith_estimate) || 0, billed = total || 0;
        return L_HEADER(BILLING) + `
I received a Good Faith Estimate of ${fmt(est)} for this care. The bill I
have received is ${fmt(billed)} — ${fmt(billed - est)} more than the estimate.

Please send me, in writing:

  1. An itemised explanation of each charge that was not in the Good Faith
     Estimate.
  2. The name and contact details of the person who prepared the estimate.

I am aware of the federal patient-provider dispute resolution process available
to uninsured and self-pay patients when a bill substantially exceeds a Good
Faith Estimate, and I intend to use it if this is not resolved.

I would prefer to resolve it directly with you first.
` + L_CLOSE;
      },
    },
    appeal: {
      label: 'Appeal an insurance denial',
      build: () => L_HEADER('[Insurance company — appeals department]') + `
I am appealing the denial of the claim referenced above.

Please provide, as part of this appeal:

  1. The specific plan provision relied on for the denial.
  2. The clinical criteria applied, and the credentials of the reviewer.
  3. All documents, records and other information relevant to my claim.

If this appeal is denied, I am requesting the denial letter state my right to an
independent external review, the deadline for requesting it, and how to do so.

Fewer than one per cent of denied claims are appealed. I am appealing this one.
` + L_CLOSE,
    },
  };

  /* What to do first. Mirrors ACTION_SPECS / next_actions in itemize/rules.py.
   *
   * Findings arrive sorted by severity, which is a reading order, not an action
   * plan. These are ordered by what actually moves a balance, and capped --
   * a list of nine "next steps" is the same problem again. Asking for an
   * itemized bill outranks everything because the rest cannot be checked
   * without one. */
  const ACTION_SPECS = [
    ['itemized', ['missing_code'], 'itemized',
      'Ask for a fully itemized bill first',
      'Some lines carry no procedure code, so there is nothing to check them against. '
      + 'Every other question here is worth more once you have a statement showing a '
      + 'code, a quantity and a charge on every line.'],
    ['eob', ['eob_line_mismatch', 'eob_total_mismatch', 'eob_line_absent'], 'dispute',
      'Challenge the bill against your own EOB',
      'Your plan has already decided what you owe. A provider billing more than that is '
      + 'the clearest error there is, and your insurer will not catch it for you.'],
    ['gfe', ['right_gfe_exceeded'], 'gfe',
      'Start the federal dispute process',
      'This bill exceeds your Good Faith Estimate by $400 or more, which opens '
      + 'patient-provider dispute resolution. Almost nobody uses it, and it costs the '
      + 'provider more than settling.'],
    ['dispute', [], 'dispute',
      'Dispute the duplicated and mis-added lines',
      'These are arithmetic and duplication, not price arguments — the kind of finding a '
      + 'billing office corrects rather than debates.'],
    ['nsa', ['right_nsa_emergency', 'right_nsa_facility'], null,
      'Assert your surprise-billing protection',
      'Federal law may prohibit this balance bill outright, which outranks every coding '
      + 'question on this page.'],
    ['charity', ['right_charity_care', 'state_charity_all_hospitals',
      'state_charity_threshold', 'state_assistance_program'], 'assistance',
      'Apply for financial assistance',
      'A financial assistance policy can cover the whole balance rather than a line of '
      + 'it, and applying does not stop you disputing anything else.'],
    ['cash', ['mrf_cash_price'], null,
      "Ask for the hospital's own published cash price",
      'This is the hospital’s own attested number rather than a Medicare comparison, '
      + 'so it is the hardest one for a billing office to wave away.'],
  ];

  const MAX_ACTIONS = 3;

  function nextActions(findings, ctx, limit) {
    limit = limit === undefined ? MAX_ACTIONS : limit;
    const fired = new Set(findings.map((f) => f.rule));
    const out = [];
    for (const [key, ruleNames, letter, title, why] of ACTION_SPECS) {
      let hits;
      let amount = 0;
      if (key === 'dispute') {
        hits = findings.filter((f) => f.recoverable && f.lines.length);
        if (!hits.length) continue;
        amount = Math.round(sum(hits, (f) => f.amount) * 100) / 100;
      } else {
        if (!ruleNames.some((r) => fired.has(r))) continue;
        hits = findings.filter((f) => ruleNames.indexOf(f.rule) >= 0);
      }
      const lines = Array.from(new Set([].concat(...hits.map((f) => f.lines))))
        .sort((a, b) => a - b);
      out.push({ key, title, why, letter, amount, lines });
    }
    return out.slice(0, limit);
  }

  function suggestLetters(ctx, findings) {
    const out = [];
    if (findings.some((f) => f.rule === 'missing_code')) out.push('itemized');
    if (findings.some((f) => f.recoverable)) out.push('dispute');
    if (ctx && (ctx.nonprofit_hospital === true || ctx.self_pay)) out.push('assistance');
    if (ctx && ctx.good_faith_estimate != null) out.push('gfe');
    if (ctx && ctx.insured === true) out.push('appeal');
    return out.filter((v, i, a) => a.indexOf(v) === i);
  }


  /* ------------------------------------------------------------- states */
  /* Mirrors itemize/states.py. Three rules hold here and nowhere else:
   * the data is hand-curated rather than fetched; a missing state means NOT
   * RESEARCHED and never "no protection"; and every state protection is gated
   * on the plan being fully insured, because state insurance law does not
   * reach self-funded ERISA plans. */
  const DEFAULT_STALE_DAYS = 365;

  function ageDays(verified) {
    if (!verified) return null;
    const d = new Date(verified + 'T00:00:00Z');
    if (isNaN(d.getTime())) return null;
    const now = new Date();
    const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
    return Math.floor((today - d.getTime()) / 86400000);
  }

  function staleSuffix(meta, entry) {
    const limit = (meta && meta.stale_after_days) || DEFAULT_STALE_DAYS;
    const age = ageDays(entry && entry.verified);
    if (age == null || age <= limit) return '';
    return ` **This entry was last verified ${age} days ago (about ${(age / 365).toFixed(1)} years). `
      + 'State law changes every legislative session — re-check it before relying on it.**';
  }

  function cites(entry, meta) {
    const urls = (entry.citations || []).join('; ');
    let base = `Hand-curated state entry for ${entry.name}, verified ${entry.verified}.`;
    if (urls) base += ` Sources: ${urls}`;
    return base;
  }

  function stateFindings(lines, ref, ctx) {
    const code = ((ctx && ctx.state) || '').trim().toUpperCase();
    if (!code) return [];
    const bundle = ref.states || {};
    const table = bundle.states || {};
    const meta = bundle._meta || {};
    if (!Object.keys(table).length) return [];

    const entry = table[code];
    const total = sum(lines, (l) => l.charge);
    const out = [];

    if (!entry) {
      return [F({
        rule: 'state_not_researched', severity: 'notice',
        title: `We have not researched ${code} state law`,
        detail: `itemize does not yet have a verified entry for ${code}. That is **not** the same as saying you have no state protections — it means we do not know, and we would rather say so than guess. Federal protections in this packet still apply everywhere. For state law, contact your state department of insurance or attorney general's consumer line, both of which handle billing complaints for free.`,
        lines: [], citation: 'No entry in web/data/states.json. ' + (meta.note || ''),
      })];
    }

    const selfFunded = ctx.plan_funding === 'self_funded';
    const gate = selfFunded
      ? ' **You said your plan is self-funded, so state insurance law generally does not reach it — this protection probably does not apply to you.** Federal protections still do.'
      : '';
    const cite = cites(entry, meta) + staleSuffix(meta, entry);

    if (ctx.ground_ambulance === true) {
      const amb = entry.ambulance_balance_billing;
      const note = entry.ambulance_note || '';
      if (amb === true) {
        out.push(F({
          rule: 'state_ambulance_protected',
          severity: selfFunded ? 'notice' : 'high',
          title: `${entry.name} has ground-ambulance balance-billing protections`,
          detail: `Ground ambulance is carved out of the federal No Surprises Act, but ${entry.name} has enacted its own protection. ${note ? note + ' ' : ''}These state laws reach state-regulated (fully insured) plans only.${gate}`,
          lines: [], citation: cite, amount: 0,
        }));
      } else if (amb === false) {
        out.push(F({
          rule: 'state_ambulance_unprotected', severity: 'warn',
          title: `${entry.name} has no ground-ambulance protection we could find`,
          detail: `Ground ambulance is excluded from the federal No Surprises Act, and we found no ${entry.name} law filling that gap. You may be exposed to a balance bill. It is still worth negotiating directly and asking the service for its hardship policy — many have one and do not advertise it.`,
          lines: [], citation: cite, amount: 0,
        }));
      } else {
        out.push(F({
          rule: 'state_ambulance_unknown', severity: 'notice',
          title: `We have not verified ground-ambulance law for ${entry.name}`,
          detail: 'Around 22 states have enacted ground-ambulance protections; we have confirmed 16 of them by name and deliberately left the rest unrecorded rather than guess. Check with your state department of insurance.',
          lines: [], citation: cite, amount: 0,
        }));
      }
    }

    const cc = entry.charity_care || {};
    if (cc.applies_to === 'all') {
      out.push(F({
        rule: 'state_charity_all_hospitals', severity: 'high',
        title: `${entry.name} requires financial assistance at *all* hospitals`,
        detail: `Federal 501(r) only binds tax-exempt hospitals. ${entry.name} is reported to impose financial-assistance requirements on all hospitals, so you can ask for the policy even if this facility is for-profit. ${cc.note || ''}`
          + (cc.free_care_fpl ? ` Free care is reported below ${cc.free_care_fpl}% of the federal poverty level` : '')
          + (cc.discount_fpl ? `, with discounts up to ${cc.discount_fpl}%.` : '.'),
        lines: [], citation: cite, amount: total,
      }));
    } else if (cc.free_care_fpl) {
      out.push(F({
        rule: 'state_charity_threshold', severity: 'high',
        title: `${entry.name} sets a financial-assistance threshold at ${cc.free_care_fpl}% FPL`,
        detail: `${cc.note || ''} If your household income is at or below ${cc.free_care_fpl}% of the federal poverty level, ask for free care by name.`,
        lines: [], citation: cite, amount: total,
      }));
    }

    const prog = entry.state_program;
    if (prog) {
      out.push(F({
        rule: 'state_assistance_program', severity: 'high',
        title: `${entry.name} runs a state assistance programme: ${prog.name}`,
        detail: `${prog.note || ''}`
          + (prog.fpl_ceiling ? ` Reported to cover residents up to ${prog.fpl_ceiling}% of the federal poverty level.` : '')
          + " Ask the hospital's financial counsellor to screen you for it by name — this is separate from the hospital's own charity care, and you may qualify for both.",
        lines: [], citation: cite, amount: total,
      }));
    }

    if (entry.all_payer_rate_setting) {
      out.push(F({
        rule: 'state_all_payer_rates', severity: 'info',
        title: `${entry.name} regulates hospital rates on an all-payer basis`,
        detail: `${entry.all_payer_note || ''} Treat any Medicare-benchmark finding in this packet with extra caution here: the relationship between a charge and a Medicare rate is not what it is in other states.`,
        lines: [], citation: cite, amount: 0,
      }));
    }

    if (entry.debt_sol_years) {
      out.push(F({
        rule: 'state_debt_sol', severity: 'notice',
        title: `${entry.name} limits how long this debt can be sued on: ${entry.debt_sol_years} years`,
        detail: `The statute of limitations on this kind of debt is reported to be ${entry.debt_sol_years} years in ${entry.name}. Note that making a payment or acknowledging the debt in writing can restart that clock in many states — take advice before paying anything on an old balance.`,
        lines: [], citation: cite, amount: 0,
      }));
    }
    return out;
  }

  /* ------------------------------------------------------------- audit */
  function applyContext(findings, ctx) {
    if (!ctx) return findings;
    for (const f of findings) {
      if (!PRICE_RULES.has(f.rule)) continue;
      if (ctx.insurer_is_paying) {
        f.severity = 'info';
        f.detail += ' Because you are insured and have met your deductible, you pay your plan’s negotiated rate rather than this charge, so this comparison is context rather than a bill to dispute.';
      } else if (ctx.pays_full_allowed) {
        f.detail += ' You told us you are exposed to the full amount, so this gap is money you would actually pay.';
      }
    }
    return findings;
  }

  function audit(lines, ref, ctx, eobRows) {
    let out = [];
    for (const r of RULES) out.push(...r(lines, ref, ctx));
    if (ctx) { out.push(...rights(lines, ref, ctx)); out.push(...stateFindings(lines, ref, ctx)); }
    if (eobRows && eobRows.length) out.push(...eobCrossCheck(lines, eobRows));
    out = applyContext(out, ctx);
    out.sort((a, b) => (SEV_ORDER[a.severity] - SEV_ORDER[b.severity]) || (b.amount - a.amount));
    return out;
  }

  return {
    classify, Reference, Context, audit, rights, stateFindings, eobCrossCheck, applyContext,
    parseBill, parseDelimited, parseText, parseEob,
    LETTERS, suggestLetters, nextActions, MAX_ACTIONS,
    money, num, fmt, RULES, SEV_ORDER,
    ASP_NOTICE_MULTIPLE, ASP_HIGH_MULTIPLE, DME_NOTICE_MULTIPLE, DME_HIGH_MULTIPLE,
    GFE_DISPUTE_THRESHOLD,
  };
}));
