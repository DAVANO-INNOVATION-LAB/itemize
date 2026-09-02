/* Parity harness: read {lines, ref, context, eob} as JSON on stdin, run the
 * browser rule engine, print findings as JSON. Used by tests/test_parity.py to
 * diff the JS engine against the Python one over identical input. */
'use strict';

const path = require('path');
const ITEMIZE = require(path.join(__dirname, '..', 'web', 'rules.js'));

let buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (d) => { buf += d; });
process.stdin.on('end', () => {
  const input = JSON.parse(buf);

  // Parsing mode: compare the two parsers directly.
  // EVERY field the rules can read is compared. Comparing only a subset is how
  // `unit_price` came to be parsed by the Python engine and silently dropped by
  // this one, disabling unit_price_arithmetic in the browser while the CLI kept
  // firing it -- invisible to a harness that only diffed idx/code/units/charge/date.
  if (input.mode === 'parse') {
    const lines = ITEMIZE.parseBill(input.text || '');
    process.stdout.write(JSON.stringify(lines.map((l) => ({
      idx: l.idx, code: l.code, desc: l.desc || '', units: l.units,
      charge: Math.round(l.charge * 100) / 100, date: l.date,
      modifiers: l.modifiers || [], revenue_code: l.revenue_code || '',
      unit_price: Math.round((l.unit_price || 0) * 100) / 100,
      ndc: l.ndc || '',
      suspect_columns: !!l.suspect_columns,
    }))));
    return;
  }
  if (input.mode === 'parse_eob') {
    process.stdout.write(JSON.stringify(ITEMIZE.parseEob(input.text || '')));
    return;
  }
  if (input.mode === 'actions') {
    const ref2 = new ITEMIZE.Reference(input.ref || {});
    const ctx2 = input.context ? new ITEMIZE.Context(input.context) : null;
    const found = ITEMIZE.audit(input.lines || [], ref2, ctx2, input.eob || null);
    process.stdout.write(JSON.stringify(ITEMIZE.nextActions(found, ctx2).map((a) => ({
      key: a.key, letter: a.letter, amount: Math.round(a.amount * 100) / 100,
      lines: a.lines,
    }))));
    return;
  }
  const ref = new ITEMIZE.Reference(input.ref || {});
  const ctx = input.context ? new ITEMIZE.Context(input.context) : null;
  const findings = ITEMIZE.audit(input.lines || [], ref, ctx, input.eob || null);
  process.stdout.write(JSON.stringify(findings.map((f) => ({
    rule: f.rule,
    severity: f.severity,
    lines: (f.lines || []).slice().sort((a, b) => a - b),
    amount: Math.round(f.amount * 100) / 100,
    recoverable: !!f.recoverable,
  }))));
});
