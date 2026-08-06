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
  if (input.mode === 'parse') {
    const lines = ITEMIZE.parseBill(input.text || '');
    process.stdout.write(JSON.stringify(lines.map((l) => ({
      idx: l.idx, code: l.code, units: l.units,
      charge: Math.round(l.charge * 100) / 100, date: l.date,
    }))));
    return;
  }
  if (input.mode === 'parse_eob') {
    process.stdout.write(JSON.stringify(ITEMIZE.parseEob(input.text || '')));
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
