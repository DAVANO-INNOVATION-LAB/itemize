/* itemize — DOM and interaction only.
 *
 * All rules, parsing and letter text live in web/rules.js, which is shared with
 * the Node parity harness so this app and the Python CLI cannot silently
 * disagree. Nothing here transmits your bill anywhere: the only network request
 * is for this page's own data/*.json at load.
 */
'use strict';

const I = window.ITEMIZE;
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const fmt = I.fmt;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const LABEL = { high: 'High', warn: 'Review', notice: 'Question', info: 'Note' };

let ref = new I.Reference({});
let lastLines = [], lastFindings = [], lastCtx = null, eobRows = null;

/* ------------------------------------------------------------- context */
function readContext() {
  const tri = (name) => {
    const el = document.querySelector(`input[name="${name}"]:checked`);
    if (!el || el.value === '') return null;
    return el.value === 'yes';
  };
  const gfe = parseFloat(($('#gfe').value || '').replace(/[^0-9.]/g, ''));
  return new I.Context({
    insured: tri('insured'),
    deductible_met: tri('deductible'),
    emergency: $('#emergency').checked || null,
    out_of_network: $('#oon').checked || null,
    in_network_facility: $('#innetfac').checked || null,
    nonprofit_hospital: $('#nonprofit').checked || null,
    ground_ambulance: $('#ambulance').checked || null,
    good_faith_estimate: isNaN(gfe) ? null : gfe,
    state: ($('#state').value || '').trim().toUpperCase(),
    plan_funding: (document.querySelector('input[name="funding"]:checked') || {}).value || null,
  });
}

/* -------------------------------------------------------------- render */
function render(lines, findings, ctx) {
  const total = lines.reduce((s, l) => s + l.charge, 0);
  // Line-level disputes and whole-bill protections are never added together:
  // an entitlement covers the whole balance, so summing them can produce a
  // "disputable" figure larger than the bill itself.
  const lineLevel = findings.filter((f) => f.recoverable && f.lines.length);
  const wholeBill = findings.filter((f) => f.recoverable && !f.lines.length);
  const recoverable = Math.min(lineLevel.reduce((s, f) => s + f.amount, 0), total);

  const stats = [
    ['Lines reviewed', String(lines.length)],
    ['Total charges', fmt(total)],
    ['Findings', String(findings.length)],
  ];
  if (recoverable > 0) stats.push(['Disputable line items', fmt(recoverable), true]);
  if (wholeBill.length) stats.push(['Whole-bill protections', String(wholeBill.length), true]);

  $('#summary').innerHTML = stats.map(([k, v, flag]) =>
    `<div class="stat${flag ? ' flag' : ''}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');

  $('#findings').innerHTML = findings.length ? findings.map((f) => `
    <div class="finding ${f.severity}">
      <span class="badge">${LABEL[f.severity]}</span>
      <h3>${esc(f.title)}</h3>
      <p>${esc(f.detail)}</p>
      ${f.amount ? `<p class="amount">Amount ${f.recoverable ? (f.lines.length ? 'disputable' : 'covered by this protection') : 'implicated'}: ${fmt(f.amount)}</p>` : ''}
      <div class="source"><strong>Source:</strong> ${esc(f.citation)}</div>
    </div>`).join('')
    : `<div class="finding info"><span class="badge">Note</span>
       <h3>No findings from these checks</h3>
       <p>This does <strong>not</strong> mean the bill is correct — it means these particular
       checks did not fire. CPT-coded lines are not benchmarked in this tier.</p></div>`;

  const cell = (label, value, cls) =>
    `<td${cls ? ` class="${cls}"` : ''}><span class="lbl" aria-hidden="true">${label}</span>`
    + `<span class="val">${value}</span></td>`;
  $('#linetable tbody').innerHTML = lines.map((l) => '<tr>'
    + cell('Line', l.idx) + cell('Date', esc(l.date || '—')) + cell('Code', esc(l.code || '—'))
    + cell('Description', esc((l.desc || ref.describe(l.code) || '—').slice(0, 70)), 'desc')
    + cell('Units', l.units, 'num') + cell('Charge', fmt(l.charge), 'num')
    + '</tr>').join('');

  const picks = I.suggestLetters(ctx, findings);
  $('#letters').innerHTML = picks.length
    ? picks.map((k) => `<button class="secondary letterbtn" data-letter="${k}">${esc(I.LETTERS[k].label)}</button>`).join('')
    : '<p class="hint">No letter suggested for this combination yet.</p>';
  $$('.letterbtn').forEach((b) => b.addEventListener('click', () => downloadLetter(b.dataset.letter)));

  $('#results').classList.remove('hidden');
  $('#results').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function toMarkdown(lines, findings, ctx) {
  const total = lines.reduce((s, l) => s + l.charge, 0);
  const lineLevel = findings.filter((f) => f.recoverable && f.lines.length);
  const wholeBill = findings.filter((f) => f.recoverable && !f.lines.length);
  const rec = Math.min(lineLevel.reduce((s, f) => s + f.amount, 0), total);
  const out = ['# Itemized bill review', '',
    `- Lines parsed: **${lines.length}**`,
    `- Total charges: **${fmt(total)}**`,
    `- Findings: **${findings.length}**`];
  if (rec > 0) out.push(`- Directly disputable line items: **${fmt(rec)}**`);
  if (wholeBill.length) out.push(`- Whole-bill protections that may apply: **${wholeBill.length}** `
    + '(these cover the balance, and are not added to the line-item figure above)');
  out.push('', '## Findings', '');
  findings.forEach((f, i) => {
    out.push(`### ${i + 1}. [${LABEL[f.severity].toUpperCase()}] ${f.title}`, '', f.detail, '');
    if (f.amount) out.push(`*Amount ${f.recoverable ? 'disputable' : 'implicated'}: ${fmt(f.amount)}*`, '');
    out.push(`> **Source:** ${f.citation}`, '');
  });
  out.push('## Lines reviewed', '', '| # | Date | Code | Description | Units | Charge |',
    '|---|------|------|-------------|-------|--------|');
  for (const l of lines) {
    out.push(`| ${l.idx} | ${l.date || '-'} | ${l.code || '-'} | `
      + `${(l.desc || ref.describe(l.code) || '').replace(/\|/g, '/').slice(0, 60)} | ${l.units} | ${fmt(l.charge)} |`);
  }
  out.push('', '## Data sources', '');
  for (const s of (ref.manifest.sources || [])) {
    out.push(`- **${s.dataset}** — \`${s.member}\`  `, `  ${s.url}  `,
      `  sha256 \`${(s.sha256 || '').slice(0, 32)}…\`, ${s.kept} records retained`);
  }
  out.push(`- Reference data built: ${ref.manifest.built || 'unknown'}`, '',
    '## What this is, and what it is not', '',
    'Produced by `itemize`, an open-source tool. **Information, not advice.**', '',
    '* A **Medicare benchmark is not a price cap.** Hospitals are not required to bill Medicare rates.',
    '* Only line-item findings marked disputable represent a directly recoverable amount.', '',
    'Verify anything here before relying on it. The sources are listed so you can.');
  return out.join('\n');
}

function save(text, name, type) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: type || 'text/markdown' }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

function downloadLetter(kind) {
  const total = lastLines.reduce((s, l) => s + l.charge, 0);
  const disputable = lastFindings.filter((f) => f.recoverable && f.lines.length);
  const text = I.LETTERS[kind].build(lastCtx, disputable,
    kind === 'dispute' ? disputable.reduce((s, f) => s + f.amount, 0) : total);
  save(text, `itemize-letter-${kind}.txt`, 'text/plain');
}

/* --------------------------------------------------------------- files */
function status(msg, kind) {
  const el = $('#filestatus');
  el.textContent = msg || '';
  el.className = 'filestatus' + (kind ? ' ' + kind : '');
}

async function readFileText(f) {
  const isPdf = /\.pdf$/i.test(f.name || '') || f.type === 'application/pdf';
  if (/^image\//.test(f.type)) {
    throw new Error(`“${f.name}” is a photo. This tool reads text, not images — it cannot `
      + 'read a picture of a bill. Ask your provider’s portal for a PDF or CSV, or type '
      + 'the lines in manually (date, code, description, units, charge).');
  }
  if (isPdf) {
    const text = await PDF.extractText(await f.arrayBuffer());
    if (!text.trim()) {
      throw new Error(`“${f.name}” has no text layer — it is most likely a scan or photo `
        + 'saved as a PDF. Nothing was extracted. Ask the billing office for a '
        + 'machine-readable itemized statement, or type the lines in manually.');
    }
    return text;
  }
  return f.text();
}

async function handleFile(f) {
  if (!f) return;
  try {
    status(`Reading “${f.name}” on this device…`);
    const text = await readFileText(f);
    $('#bill').value = text;
    const n = text.split('\n').filter((l) => l.trim()).length;
    status(`Read ${n} lines from “${f.name}”. Check them against your paper bill before `
      + 'reviewing — layouts vary, and columns can be reordered.', 'ok');
    $('#run').click();
  } catch (err) {
    status(err.message, 'warn');
  }
}

async function handleEob(f) {
  if (!f) return;
  try {
    const text = await readFileText(f);
    eobRows = I.parseEob(text);
    if (!eobRows.length) {
      $('#eobstatus').textContent = `Could not read any rows from “${f.name}”. `
        + 'An EOB export needs a patient-responsibility column.';
      $('#eobstatus').className = 'filestatus warn';
      eobRows = null;
      return;
    }
    $('#eobstatus').textContent = `Loaded ${eobRows.length} EOB rows from “${f.name}”. `
      + 'These are compared against your bill.';
    $('#eobstatus').className = 'filestatus ok';
    if (lastLines.length) $('#run').click();
  } catch (err) {
    $('#eobstatus').textContent = err.message;
    $('#eobstatus').className = 'filestatus warn';
  }
}

/* -------------------------------------------------------------- events */
$('#run').addEventListener('click', () => {
  const text = $('#bill').value.trim();
  if (!text) { $('#bill').focus(); return; }
  lastLines = I.parseBill(text);
  lastCtx = readContext();
  if (!lastLines.length) {
    $('#findings').innerHTML = `<div class="finding warn"><span class="badge">Review</span>
      <h3>Could not read any charge lines</h3>
      <p>Expected a table with code, quantity and charge columns, or bill text copied
      from a PDF. Try the example button to see a working format.</p></div>`;
    $('#summary').innerHTML = '';
    $('#linetable tbody').innerHTML = '';
    $('#letters').innerHTML = '';
    $('#results').classList.remove('hidden');
    return;
  }
  lastFindings = I.audit(lastLines, ref, lastCtx, eobRows);
  render(lastLines, lastFindings, lastCtx);
});

$('#file').addEventListener('change', (e) => handleFile(e.target.files[0]));
$('#eobfile').addEventListener('change', (e) => handleEob(e.target.files[0]));
$('#download').addEventListener('click', () =>
  save(toMarkdown(lastLines, lastFindings, lastCtx), 'itemize-review.md'));

$('#clear').addEventListener('click', () => {
  $('#bill').value = '';
  $('#results').classList.add('hidden');
  lastLines = []; lastFindings = []; eobRows = null;
  status(''); $('#eobstatus').textContent = '';
});

$('#demo').addEventListener('click', () => {
  $('#bill').value = `Date of Service,Code,Description,Qty,Charges
2026-03-14,99283,EMERGENCY DEPT VISIT LEVEL 3,1,"1,842.00"
2026-03-14,J1885,KETOROLAC TROMETHAMINE INJ 15MG,2,180.00
2026-03-14,J1885,KETOROLAC TROMETHAMINE INJ 15MG,2,180.00
2026-03-14,A4550,SURGICAL TRAY,1,68.00
2026-03-14,A9270,SELF ADMIN DRUG - NON COVERED,1,142.50
2026-03-14,J3490,UNCLASSIFIED DRUGS,3,96.00
2026-03-14,CHG40021,PHARMACY GENERAL CLASSIFICATION,1,412.75
2026-03-14,,ROOM AND BOARD SEMI PRIVATE,1,"2,310.00"
2026-03-14,E0114,CRUTCHES UNDERARM PAIR,1,124.00`;
  $('#run').click();
});

const drop = $('#dropzone');
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.add('over');
}));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.remove('over');
}));
drop.addEventListener('drop', (e) => {
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) handleFile(f);
});

/* Re-run when context changes, so the answers visibly matter. */
$$('#context input, #context select').forEach((el) => el.addEventListener('change', () => {
  if (lastLines.length) $('#run').click();
}));

/* --------------------------------------------------------------- boot */
Promise.all(['hcpcs', 'asp', 'dmepos', 'states', 'manifest'].map((n) =>
  fetch(`data/${n}.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
)).then(([hcpcs, asp, dmepos, states, manifest]) => {
  ref = new I.Reference({ hcpcs, asp, dmepos, states, manifest });
  const sc = Object.keys((states && states.states) || {}).length;
  const n = Object.keys(ref.hcpcs).length;
  const priced = new Set([...Object.keys(ref.asp), ...Object.keys(ref.dmepos)]).size;
  $('#provenance').textContent = n
    ? `Reference data: ${n.toLocaleString()} HCPCS Level II codes and ${priced.toLocaleString()} `
      + `Medicare price benchmarks (Part B drugs and DMEPOS), built ${ref.manifest.built || 'unknown'}. `
      + `State-law entries verified for ${sc} states — the rest report "not researched" rather than guessing.`
    : 'Reference data not found — run tools/build_data.py. Structural checks still work.';
});

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {
    /* Offline support is a bonus; the page works without it. */
  });
}
