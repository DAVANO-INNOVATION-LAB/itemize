/* itemize — DOM and interaction only.
 *
 * All rules, parsing, action ranking and letter text live in web/rules.js,
 * which is shared with the Node parity harness so this app and the Python CLI
 * cannot silently disagree. Nothing here transmits your bill anywhere: the only
 * network request is for this page's own data/*.json at load.
 */
'use strict';

const I = window.ITEMIZE;
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const fmt = I.fmt;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const LABEL = { high: 'High', warn: 'Review', notice: 'Question', info: 'Note' };
const DRAFT_KEY = 'itemize.draft.v1';

let ref = new I.Reference({});
let lastLines = [], lastFindings = [], lastCtx = null, eobRows = null;
let linesConfirmed = false;
let hasRun = false;

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
    drg: ($('#drg').value || '').trim(),
    plan_funding: (document.querySelector('input[name="funding"]:checked') || {}).value || null,
  });
}

/* The state list is built from the data file rather than written into the HTML,
   so it cannot claim a state is researched when states.json says otherwise. */
const STATE_NAMES = {
  AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California',
  CO: 'Colorado', CT: 'Connecticut', DE: 'Delaware', DC: 'District of Columbia',
  FL: 'Florida', GA: 'Georgia', HI: 'Hawaii', ID: 'Idaho', IL: 'Illinois',
  IN: 'Indiana', IA: 'Iowa', KS: 'Kansas', KY: 'Kentucky', LA: 'Louisiana',
  ME: 'Maine', MD: 'Maryland', MA: 'Massachusetts', MI: 'Michigan', MN: 'Minnesota',
  MS: 'Mississippi', MO: 'Missouri', MT: 'Montana', NE: 'Nebraska', NV: 'Nevada',
  NH: 'New Hampshire', NJ: 'New Jersey', NM: 'New Mexico', NY: 'New York',
  NC: 'North Carolina', ND: 'North Dakota', OH: 'Ohio', OK: 'Oklahoma', OR: 'Oregon',
  PA: 'Pennsylvania', RI: 'Rhode Island', SC: 'South Carolina', SD: 'South Dakota',
  TN: 'Tennessee', TX: 'Texas', UT: 'Utah', VT: 'Vermont', VA: 'Virginia',
  WA: 'Washington', WV: 'West Virginia', WI: 'Wisconsin', WY: 'Wyoming',
};

function buildStateOptions(states) {
  const known = new Set(Object.keys((states && states.states) || {}));
  const sel = $('#state');
  const opt = (code, name) => `<option value="${code}">${esc(name)}</option>`;
  const grp = (label, codes) => codes.length
    ? `<optgroup label="${esc(label)}">${codes.map((c) => opt(c, STATE_NAMES[c])).join('')}</optgroup>`
    : '';
  const all = Object.keys(STATE_NAMES).sort((a, b) =>
    STATE_NAMES[a].localeCompare(STATE_NAMES[b]));
  sel.innerHTML = '<option value="">Rather not say</option>'
    + grp('Researched', all.filter((c) => known.has(c)))
    + grp('Not yet researched — we will say so rather than guess',
      all.filter((c) => !known.has(c)));
}

/* --------------------------------------------------------- parse stats */
function parseStats(text, lines) {
  const raw = text.split('\n').filter((l) => l.trim()).length;
  // One header row is expected for delimited input; everything else that did not
  // become a charge line is genuinely unaccounted for.
  const unread = Math.max(0, raw - lines.length - 1);
  return { raw, parsed: lines.length, unread };
}

/* -------------------------------------------------------------- render */
function render(lines, findings, ctx, stats) {
  const total = lines.reduce((s, l) => s + l.charge, 0);
  // Line-level disputes and whole-bill protections are never added together:
  // an entitlement covers the whole balance, so summing them can produce a
  // "disputable" figure larger than the bill itself.
  const lineLevel = findings.filter((f) => f.recoverable && f.lines.length);
  const wholeBill = findings.filter((f) => f.recoverable && !f.lines.length);
  const recoverable = Math.min(lineLevel.reduce((s, f) => s + f.amount, 0), total);

  const stat = [
    ['Lines reviewed', String(lines.length)],
    ['Total charges', fmt(total)],
    ['Findings', String(findings.length)],
  ];
  if (stats && stats.unread > 0) {
    stat.push(['Input rows not read', String(stats.unread), stats.unread > lines.length * 0.25]);
  }
  if (recoverable > 0) stat.push(['Disputable line items', fmt(recoverable), true]);
  if (wholeBill.length) stat.push(['Whole-bill protections', String(wholeBill.length), true]);

  $('#summary').innerHTML = stat.map(([k, v, flag]) =>
    `<div class="stat${flag ? ' flag' : ''}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');

  renderParseCheck(lines, stats);
  renderActions(findings, ctx);

  const lineRefs = (idxs) => (idxs || []).map((n) =>
    `<a class="lineref" href="#line-${n}">${n}</a>`).join(' ');

  $('#findings').innerHTML = findings.length ? findings.map((f) => `
    <div class="finding ${f.severity}">
      <span class="badge">${LABEL[f.severity]}</span>
      <h4>${esc(f.title)}</h4>
      <p>${esc(f.detail)}</p>
      ${f.amount ? `<p class="amount">Amount ${f.recoverable ? (f.lines.length ? 'disputable' : 'covered by this protection') : 'implicated'}: ${fmt(f.amount)}</p>` : ''}
      ${f.lines && f.lines.length ? `<p class="lines">On ${f.lines.length === 1 ? 'line' : 'lines'} ${lineRefs(f.lines)}</p>` : ''}
      <div class="source"><strong>Source:</strong> ${esc(f.citation)}</div>
    </div>`).join('')
    : `<div class="finding info"><span class="badge">Note</span>
       <h4>No findings from these checks</h4>
       <p>This does <strong>not</strong> mean the bill is correct — it means these particular
       checks did not fire. CPT-coded lines are not benchmarked in this tier.</p></div>`;

  const cell = (label, value, cls) =>
    `<td${cls ? ` class="${cls}"` : ''}><span class="lbl" aria-hidden="true">${label}</span>`
    + `<span class="val">${value}</span></td>`;
  $('#linetable tbody').innerHTML = lines.map((l) => `<tr id="line-${l.idx}">`
    + cell('Line', l.idx) + cell('Date', esc(l.date || '—')) + cell('Code', esc(l.code || '—'))
    + cell('Description', esc((l.desc || ref.describe(l.code) || '—').slice(0, 90)), 'desc')
    + cell('Units', l.units, 'num') + cell('Charge', fmt(l.charge), 'num')
    + '</tr>').join('');

  const picks = I.suggestLetters(ctx, findings);
  $('#letters').innerHTML = picks.length
    ? picks.map((k) => `<button class="secondary letterbtn" data-letter="${k}">${esc(I.LETTERS[k].label)}</button>`).join('')
    : '<p class="hint">No letter suggested for this combination yet.</p>';
  $$('.letterbtn').forEach((b) => b.addEventListener('click', () => showLetter(b.dataset.letter)));

  $('#results').classList.remove('hidden');
  announce(lines, findings, recoverable);
}

/* The single live region. A screen reader gets one sentence per run rather than
   the whole audit re-read every time a checkbox changes. */
function announce(lines, findings, recoverable) {
  const need = findings.filter((f) => f.severity === 'high').length;
  const bits = [`${findings.length} finding${findings.length === 1 ? '' : 's'} from `
    + `${lines.length} line${lines.length === 1 ? '' : 's'}`];
  if (need) bits.push(`${need} marked high`);
  if (recoverable > 0) bits.push(`${fmt(recoverable)} in disputable line items`);
  const el = $('#resultstatus');
  el.textContent = (hasRun ? 'Updated: ' : '') + bits.join(', ') + '.';
  el.classList.remove('pulse');
  void el.offsetWidth;                       // restart the animation
  if (hasRun) el.classList.add('pulse');
  hasRun = true;
}

function renderParseCheck(lines, stats) {
  const box = $('#parsecheck');
  if (linesConfirmed) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const extra = stats && stats.unread > 0
    ? ` ${stats.unread} row${stats.unread === 1 ? '' : 's'} of what you pasted did not become
       a charge line — that is often a header, an address or a footer, but check that it is
       not a charge.`
    : '';
  $('#parsecheckbody').innerHTML = `We read <strong>${lines.length}</strong> charge
    line${lines.length === 1 ? '' : 's'}.${extra} Layouts vary and columns get reordered, so
    compare the <a href="#linestable">lines reviewed</a> below against your paper bill before
    you act on anything here or send the evidence packet.`;
}

function renderActions(findings, ctx) {
  const acts = I.nextActions(findings, ctx);
  if (!acts.length) { $('#actions').innerHTML = ''; return; }
  const lineRefs = (idxs) => (idxs || []).map((n) =>
    `<a class="lineref" href="#line-${n}">${n}</a>`).join(' ');
  $('#actions').innerHTML = `
    <div class="actions">
      <h3>What to do first</h3>
      <p class="hint">Ranked by what actually moves a balance, not by how alarming it
      reads. Everything else is below.</p>
      <ol>${acts.map((a) => `
        <li>
          <span class="act-title">${esc(a.title)}</span>
          ${a.amount ? `<span class="act-amount">${fmt(a.amount)}</span>` : ''}
          <p>${esc(a.why)}</p>
          ${a.lines.length ? `<p class="lines">Lines ${lineRefs(a.lines)}</p>` : ''}
          ${a.letter ? `<button class="secondary letterbtn" data-letter="${a.letter}">Draft: ${esc(I.LETTERS[a.letter].label)}</button>` : ''}
        </li>`).join('')}</ol>
    </div>`;
  $$('#actions .letterbtn').forEach((b) =>
    b.addEventListener('click', () => showLetter(b.dataset.letter)));
}

/* ------------------------------------------------------------ evidence */
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

  const acts = I.nextActions(findings, ctx);
  if (acts.length) {
    out.push('', '## What to do first', '');
    acts.forEach((a, i) => {
      out.push(`${i + 1}. **${a.title}**` + (a.amount ? ` — ${fmt(a.amount)}` : ''));
      out.push(`   ${a.why}`);
      if (a.lines.length) out.push(`   Lines: ${a.lines.join(', ')}`);
      out.push('');
    });
  }

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
    '* **NADAC is acquisition cost**, not an allowed amount; a dispensing fee and margin belong on top.',
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

/* ------------------------------------------------------------- letters */
let openLetter = null;

function letterText(kind) {
  const total = lastLines.reduce((s, l) => s + l.charge, 0);
  const disputable = lastFindings.filter((f) => f.recoverable && f.lines.length);
  return I.LETTERS[kind].build(lastCtx, disputable,
    kind === 'dispute' ? disputable.reduce((s, f) => s + f.amount, 0) : total);
}

function showLetter(kind) {
  openLetter = kind;
  $('#lettertext').value = letterText(kind);
  $('#letterbox').classList.remove('hidden');
  $('#copystatus').textContent = '';
  $('#letterbox').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

$('#copyletter').addEventListener('click', async () => {
  const ta = $('#lettertext');
  try {
    await navigator.clipboard.writeText(ta.value);
    $('#copystatus').textContent = 'Copied. Paste it into an email or a document.';
    $('#copystatus').className = 'filestatus ok';
  } catch (err) {
    // Clipboard access is denied in some contexts; selecting the text is a
    // working fallback rather than a dead end.
    ta.focus();
    ta.select();
    $('#copystatus').textContent = 'Your browser blocked the clipboard. The text is '
      + 'selected — press Ctrl-C (or Cmd-C) to copy it.';
    $('#copystatus').className = 'filestatus warn';
  }
});
$('#downloadletter').addEventListener('click', () => {
  if (openLetter) save($('#lettertext').value, `itemize-letter-${openLetter}.txt`, 'text/plain');
});
$('#closeletter').addEventListener('click', () => {
  $('#letterbox').classList.add('hidden');
  openLetter = null;
});

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
    if (!confirmOverwrite(`Replace what is in the box with “${f.name}”?`)) {
      status('Kept what you had. Nothing was replaced.');
      return;
    }
    $('#bill').value = text;
    const n = text.split('\n').filter((l) => l.trim()).length;
    status(`Read ${n} lines from “${f.name}”. Check them against your paper bill before `
      + 'reviewing — layouts vary, and columns can be reordered.', 'ok');
    runAudit();
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
    if (lastLines.length) runAudit();
  } catch (err) {
    $('#eobstatus').textContent = err.message;
    $('#eobstatus').className = 'filestatus warn';
  }
}

function confirmOverwrite(question) {
  return !$('#bill').value.trim() || window.confirm(question);
}

/* ----------------------------------------------------------- the audit */
function runAudit() {
  const text = $('#bill').value.trim();
  if (!text) { $('#bill').focus(); return; }
  lastLines = I.parseBill(text);
  lastCtx = readContext();
  if (!lastLines.length) {
    $('#findings').innerHTML = `<div class="finding warn"><span class="badge">Review</span>
      <h4>Could not read any charge lines</h4>
      <p>Expected a table with code, quantity and charge columns, or bill text copied
      from a PDF. Try the example button to see a working format.</p></div>`;
    $('#summary').innerHTML = '';
    $('#actions').innerHTML = '';
    $('#linetable tbody').innerHTML = '';
    $('#letters').innerHTML = '';
    $('#parsecheck').classList.add('hidden');
    $('#resultstatus').textContent = 'No charge lines could be read from what you pasted.';
    $('#results').classList.remove('hidden');
    setDownloadEnabled(false);
    return;
  }
  lastFindings = I.audit(lastLines, ref, lastCtx, eobRows);
  render(lastLines, lastFindings, lastCtx, parseStats(text, lastLines));
  setDownloadEnabled(linesConfirmed);
  persist();
}

function setDownloadEnabled(on) {
  $('#download').disabled = !on;
  $('#downloadhint').textContent = on
    ? 'You confirmed the lines match your bill.'
    : 'Confirm the lines above match your bill to enable the packet.';
}

/* -------------------------------------------------------- persistence */
/* Off by default and explicit. The privacy promise is that nothing leaves the
   device; keeping a copy ON the device is a different question, and it is the
   reader's to answer -- on a shared computer the answer is no. */
function persist() {
  if (!$('#keep').checked) return;
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({
      bill: $('#bill').value,
      ctx: {
        insured: (document.querySelector('input[name="insured"]:checked') || {}).value || '',
        deductible: (document.querySelector('input[name="deductible"]:checked') || {}).value || '',
        funding: (document.querySelector('input[name="funding"]:checked') || {}).value || '',
        emergency: $('#emergency').checked, oon: $('#oon').checked,
        innetfac: $('#innetfac').checked, nonprofit: $('#nonprofit').checked,
        ambulance: $('#ambulance').checked,
        gfe: $('#gfe').value, state: $('#state').value, drg: $('#drg').value,
      },
      saved: new Date().toISOString(),
    }));
    $('#keepstatus').textContent = 'Saved on this device only.';
    $('#keepstatus').className = 'filestatus ok';
  } catch (err) {
    $('#keepstatus').textContent = 'This browser refused to store a copy '
      + '(private browsing, or storage is full). Your work is still on screen.';
    $('#keepstatus').className = 'filestatus warn';
  }
}

function forget(quiet) {
  try { localStorage.removeItem(DRAFT_KEY); } catch (err) { /* nothing to remove */ }
  if (!quiet) {
    $('#keepstatus').textContent = 'Deleted from this device.';
    $('#keepstatus').className = 'filestatus ok';
  }
}

function restore() {
  let d = null;
  try { d = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); } catch (err) { d = null; }
  if (!d) return false;
  $('#keep').checked = true;
  $('#bill').value = d.bill || '';
  const c = d.ctx || {};
  const setRadio = (name, val) => {
    const el = document.querySelector(`input[name="${name}"][value="${val || ''}"]`);
    if (el) el.checked = true;
  };
  setRadio('insured', c.insured);
  setRadio('deductible', c.deductible);
  setRadio('funding', c.funding);
  $('#emergency').checked = !!c.emergency;
  $('#oon').checked = !!c.oon;
  $('#innetfac').checked = !!c.innetfac;
  $('#nonprofit').checked = !!c.nonprofit;
  $('#ambulance').checked = !!c.ambulance;
  $('#gfe').value = c.gfe || '';
  $('#drg').value = c.drg || '';
  $('#keepstatus').textContent = `Restored the copy saved on this device`
    + (d.saved ? ` on ${d.saved.slice(0, 10)}.` : '.');
  $('#keepstatus').className = 'filestatus ok';
  return c.state || '';
}

/* -------------------------------------------------------------- events */
$('#run').addEventListener('click', () => {
  // A new run is a new bill; the reader has to look at the lines again.
  linesConfirmed = false;
  runAudit();
  $('#results').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

$('#file').addEventListener('change', (e) => { handleFile(e.target.files[0]); e.target.value = ''; });
$('#eobfile').addEventListener('change', (e) => { handleEob(e.target.files[0]); e.target.value = ''; });

$('#download').addEventListener('click', () => {
  if ($('#download').disabled) return;
  save(toMarkdown(lastLines, lastFindings, lastCtx), 'itemize-review.md');
});
$('#print').addEventListener('click', () => window.print());

$('#confirmlines').addEventListener('click', () => {
  linesConfirmed = true;
  $('#parsecheck').classList.add('hidden');
  setDownloadEnabled(true);
});
$('#rejectlines').addEventListener('click', () => {
  linesConfirmed = false;
  setDownloadEnabled(false);
  $('#parsecheckbody').innerHTML = 'Then do not send anything based on this reading. '
    + 'Correct the text in the box above by hand — the lines are read from what is there, '
    + 'so fixing a row and reviewing again is enough. If the layout is defeating the '
    + 'parser, ask the billing office for a CSV export or type the lines in as '
    + '<code>date, code, description, units, charge</code>.';
});

$('#clear').addEventListener('click', () => {
  $('#bill').value = '';
  $('#results').classList.add('hidden');
  $('#letterbox').classList.add('hidden');
  lastLines = []; lastFindings = []; eobRows = null;
  linesConfirmed = false; hasRun = false;
  status(''); $('#eobstatus').textContent = '';
  if ($('#keep').checked) persist();
});

$('#demo').addEventListener('click', () => {
  if (!confirmOverwrite('Replace what is in the box with the example bill?')) return;
  $('#bill').value = `Date of Service,Code,NDC,Description,Qty,Charges
2026-03-14,99283,,EMERGENCY DEPT VISIT LEVEL 3,1,"1,842.00"
2026-03-14,J1885,00409-3799-01,KETOROLAC TROMETHAMINE INJ 15MG,2,180.00
2026-03-14,J1885,00409-3799-01,KETOROLAC TROMETHAMINE INJ 15MG,2,180.00
2026-03-14,A4550,,SURGICAL TRAY,1,68.00
2026-03-14,A9270,,SELF ADMIN DRUG - NON COVERED,1,142.50
2026-03-14,J3490,,UNCLASSIFIED DRUGS,3,96.00
2026-03-14,CHG40021,,PHARMACY GENERAL CLASSIFICATION,1,412.75
2026-03-14,,,ROOM AND BOARD SEMI PRIVATE,1,"2,310.00"
2026-03-14,E0114,,CRUTCHES UNDERARM PAIR,1,124.00`;
  linesConfirmed = false;
  runAudit();
  $('#results').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

$('#keep').addEventListener('change', () => {
  if ($('#keep').checked) persist();
  else forget(false);
});
$('#forget').addEventListener('click', () => { $('#keep').checked = false; forget(false); });

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

/* Re-run when context changes, so the answers visibly matter. The findings must
   never change under the reader without saying so -- announce() handles that. */
$$('#context input, #context select').forEach((el) => {
  const ev = (el.tagName === 'INPUT' && el.type === 'text') ? 'change' : 'change';
  el.addEventListener(ev, () => {
    if (lastLines.length) runAudit();
    else persist();
  });
});

/* Highlight a line when a finding points at it. */
window.addEventListener('hashchange', flashTarget);
function flashTarget() {
  const m = /^#line-(\d+)$/.exec(location.hash || '');
  if (!m) return;
  const row = document.getElementById(`line-${m[1]}`);
  if (!row) return;
  $$('#linetable tr.flash').forEach((r) => r.classList.remove('flash'));
  row.classList.add('flash');
  row.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/* --------------------------------------------------------------- boot */
Promise.all(['hcpcs', 'asp', 'dmepos', 'nadac', 'drg', 'states', 'manifest'].map((n) =>
  fetch(`data/${n}.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
)).then(([hcpcs, asp, dmepos, nadac, drg, states, manifest]) => {
  ref = new I.Reference({ hcpcs, asp, dmepos, nadac, drg, states, manifest });
  buildStateOptions(states);
  const savedState = restore();
  if (savedState) $('#state').value = savedState;

  const sc = Object.keys((states && states.states) || {}).length;
  const n = Object.keys(ref.hcpcs).length;
  const priced = new Set([...Object.keys(ref.asp), ...Object.keys(ref.dmepos)]).size;
  const nd = Object.keys(ref.nadac).length;
  const dr = Object.keys(ref.drg).length;
  $('#provenance').textContent = n
    ? `Reference data: ${n.toLocaleString()} HCPCS Level II codes, ${priced.toLocaleString()} `
      + `Medicare price benchmarks (Part B drugs and DMEPOS)`
      + (nd ? `, ${nd.toLocaleString()} drug acquisition costs by NDC` : '')
      + (dr ? `, and ${dr.toLocaleString()} national MS-DRG averages` : '')
      + `, built ${ref.manifest.built || 'unknown'}. `
      + `State-law entries verified for ${sc} states — the rest report "not researched" rather than guessing.`
    : 'Reference data not found — run tools/build_data.py. Structural checks still work.';

  if ($('#bill').value.trim()) runAudit();
});

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {
    /* Offline support is a bonus; the page works without it. */
  });
}
