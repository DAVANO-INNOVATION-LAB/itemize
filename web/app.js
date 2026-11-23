/* itemize — DOM and interaction only.
 *
 * All rules, parsing, action ranking and letter text live in web/rules.js,
 * which is shared with the Node parity harness so this app and the Python CLI
 * cannot silently disagree. Nothing here transmits your bill anywhere: the only
 * network requests load public reference data and optional same-origin OCR assets.
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
let source = 'manual', fileRequest = null, retryFile = null;
let replacing = false, hasReview = false, verificationRequested = false;

function showStage(stage) {
  const reviewing = stage === 'review';
  if (reviewing) hasReview = true;
  $('#workspace').setAttribute('aria-labelledby', reviewing ? 'result-title' : 'intake-title');
  for (const id of ['dropzone', 'privacy-caption', 'manual']) document.getElementById(id).classList.toggle('hidden', reviewing);
  $('#backreview').classList.toggle('hidden', reviewing || !hasReview);
  $('#results').classList.toggle('hidden', !reviewing);
}

function returnToIntake() {
  replacing = hasReview;
  showStage('intake');
  $('#manual').open = false;
  const button = $('#takephoto').disabled ? $('#choosefile') : $('#takephoto');
  button.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

function focusResult() {
  $('#result-title').focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

function resetReview() {
  lastLines = []; lastFindings = []; lastCtx = null;
  linesConfirmed = false;
  hasReview = false; replacing = false; verificationRequested = false;
  $('#verify-more').open = false;
  $('#results').classList.add('hidden');
  $('#letterbox').classList.add('hidden');
  $('#lettertext').value = '';
  $('#copystatus').textContent = '';
  for (const id of ['technical', 'charges', 'exporttools']) document.getElementById(id).open = false;
  setDownloadEnabled(false);
}

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
  // A flat list, deliberately. This was grouped by how much had been researched
  // back when coverage was partial; every jurisdiction now has both ground
  // ambulance and hospital financial-assistance law recorded, so a "researched"
  // group would contain all of them and tell the reader nothing. What is still
  // uneven — state-run assistance programmes — is disclosed in the findings
  // themselves, where it is attached to the state it applies to.
  const all = (states && states.states) || {};
  const sel = $('#state');
  const codes = Object.keys(STATE_NAMES)
    .filter((c) => all[c])
    .sort((a, b) => STATE_NAMES[a].localeCompare(STATE_NAMES[b]));
  const missing = Object.keys(STATE_NAMES)
    .filter((c) => !all[c])
    .sort((a, b) => STATE_NAMES[a].localeCompare(STATE_NAMES[b]));
  const opt = (c) => `<option value="${c}">${esc(STATE_NAMES[c])}</option>`;
  sel.innerHTML = '<option value="">Rather not say</option>'
    + codes.map(opt).join('')
    + (missing.length
      ? `<optgroup label="Not yet researched — we will say so rather than guess">${
        missing.map(opt).join('')}</optgroup>`
      : '');
}

/* --------------------------------------------------------- parse stats */
function renderReadingNote(stats) {
  const note = $('#reading-note');
  const ignored = stats?.ignored || 0;
  const unread = stats?.unread || 0;
  const details = [];
  if (ignored) details.push(`Left out ${ignored} non-charge ${ignored === 1 ? 'row' : 'rows'}.`);
  if (unread) details.push(`Couldn’t read ${unread} other ${unread === 1 ? 'row' : 'rows'} as charges. Check the original for anything we missed.`);
  note.classList.toggle('has-unread', unread > 0);
  note.classList.toggle('hidden', details.length === 0);
  note.textContent = details.join(' ');
}

/* -------------------------------------------------------------- render */
function render(lines, findings, ctx, stats) {
  const total = lines.reduce((s, l) => s + l.charge, 0);
  // Line-level disputes and whole-bill protections are never added together:
  // an entitlement covers the whole balance, so summing them can produce a
  // "disputable" figure larger than the bill itself.
  const wholeBill = findings.filter((f) => f.recoverable && !f.lines.length);
  const recoverable = I.disputableTotal(findings, lines);

  const stat = [
    ['Lines reviewed', String(lines.length)],
    ['Total charges', fmt(total)],
    ['Findings', String(findings.length)],
  ];
  if (recoverable > 0) stat.push(['Disputable line items', fmt(recoverable), true]);
  if (wholeBill.length) stat.push(['Whole-bill protections', String(wholeBill.length), true]);

  $('#summary').innerHTML = stat.map(([k, v, flag]) =>
    `<div class="stat${flag ? ' flag' : ''}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('');

  renderParseCheck(lines);
  renderOutcome(lines, findings, ctx);
  renderActions(findings, ctx, lines);

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

  $('#charges').classList.remove('hidden');
  if (!replacing) showStage('review');
  const tentative = !linesConfirmed && isTentative(lines);
  $('#charges').classList.toggle('hidden', tentative);
  for (const id of ['technical', 'exporttools']) document.getElementById(id).classList.toggle('hidden', tentative);
  announce(lines);
  renderReadingNote(stats);
}

/* The single live region. A screen reader gets one sentence per run rather than
   the whole audit re-read every time a checkbox changes. */
function announce(lines) {
  const el = $('#resultstatus');
  el.textContent = `${lines.length} charge${lines.length === 1 ? '' : 's'} read`
    + (source === 'photo' ? ' from your photo.' : '.');
}

function readableDescription(line) {
  return line.desc || ref.describe(line.code) || (line.code ? 'Code ' + line.code : 'Unlabeled charge');
}

function isTentative(lines) {
  return source === 'photo' || lines.some((line) => line.suspect_columns);
}

const VERIFY_INLINE_ROWS = 8;
function syncVerifyButton() {
  const more = $('#verify-more');
  const allShown = more.classList.contains('hidden') || more.open;
  const count = lastLines.length;
  const button = $('#confirmlines');
  button.disabled = !allShown;
  button.textContent = !allShown ? `Show all ${count} charges first`
    : count === 1 ? 'This amount matches' : `All ${count} amounts match`;
}

function renderParseCheck(lines) {
  const needsCheck = !linesConfirmed && (verificationRequested || isTentative(lines));
  $('#parsecheck').classList.toggle('hidden', !needsCheck);
  const first = $('#verify-first'), rest = $('#verify-rest'), more = $('#verify-more');
  if (!needsCheck) {
    first.replaceChildren();
    rest.replaceChildren();
    more.open = false;
    return;
  }
  $('#parsecheckbody').textContent = source === 'photo'
    ? 'Photo reading can miss a line or get a number wrong. Compare these charges with the original to finish.'
    : 'Compare these charges with the original before using or sharing the review.';
  const rows = lines.map((line) =>
    `<li><span>${esc(readableDescription(line))}</span><strong>${esc(fmt(line.charge))}</strong></li>`);
  first.innerHTML = rows.slice(0, VERIFY_INLINE_ROWS).join('');
  rest.innerHTML = rows.slice(VERIFY_INLINE_ROWS).join('');
  rest.start = VERIFY_INLINE_ROWS + 1;
  const remaining = lines.length - VERIFY_INLINE_ROWS;
  more.classList.toggle('hidden', remaining <= 0);
  if (remaining > 0) {
    $('#verify-more-label').textContent = `Show ${remaining} more charge${remaining === 1 ? '' : 's'}`;
  } else {
    more.open = false;
  }
  syncVerifyButton();
}

function renderOutcome(lines, findings, ctx) {
  const card = $('#outcome');
  if (!linesConfirmed && isTentative(lines)) {
    card.classList.add('hidden');
    card.replaceChildren();
    $('#result-title').textContent = source === 'photo'
      ? 'Almost done. Check the photo.' : 'Almost done. Check the charges.';
    return;
  }
  card.classList.remove('hidden');
  $('#result-title').textContent = 'Your receipt review';
  const action = I.nextActions(findings, ctx, 1, lines)[0];
  const duplicate = findings.find((f) => {
    if (f.rule !== 'exact_duplicate') return false;
    const charges = lines.filter((line) => f.lines.includes(line.idx));
    // Amount alone cannot establish even an apparent duplicate: require a
    // matching billing code or a matching, nonempty receipt description.
    return charges.every((line) => line.code)
      || new Set(charges.map((line) => line.desc.trim().toLowerCase())).size === 1
        && Boolean(charges[0]?.desc.trim());
  });
  const important = findings.find((f) => ['high', 'warn', 'notice'].includes(f.severity)
    && !['ref_stale', 'stale_data', 'suspect_columns'].includes(f.rule));
  const firstIssue = duplicate || important;
  const leadDuplicate = duplicate && (!action || ['dispute', 'itemized'].includes(action.key));
  const affected = firstIssue && lines.find((line) => firstIssue.lines.includes(line.idx));
  const structuralOnly = !lines.some((line) => line.code);
  let label = 'Review complete', headline = 'No issues found by these checks';
  let msg = 'This doesn’t prove the bill is correct or tell you what you owe.';
  if (leadDuplicate) {
    label = 'One thing to check';
    headline = duplicate.lines.length === 2 ? 'A charge appears twice' : 'A charge appears more than once';
    msg = 'Ask the billing office whether these entries are for separate services. If it was one service, ask them to correct the repeated charge.';
  } else if (action) {
    label = 'One next step';
    headline = {
      itemized: 'Ask for an itemized bill', eob: 'Check why the amounts differ',
      dispute: 'Ask about these charges', gfe: 'Check your original estimate',
      nsa: 'Ask about surprise billing', charity: 'Ask about financial assistance',
      cash: 'Ask for the cash price',
    }[action.key] || 'Ask about this bill';
    msg = plainAction(action);
  } else if (important) {
    label = 'One thing to check';
    headline = 'Ask about a charge that stood out';
    msg = 'Ask the billing office to explain this charge. A flag is a reason to ask, not proof of an error.';
  }
  const concrete = !!affected && (leadDuplicate || !action);
  card.classList.toggle('has-issue', Boolean(firstIssue || action));
  card.innerHTML = `<p class="outcome-label">${esc(label)}</p><h3>${esc(headline)}</h3>`
    + (concrete
      ? `<div class="flagged-charge"><span>${esc(readableDescription(affected))}</span><strong>${esc(fmt(affected.charge))}${leadDuplicate ? ' per entry' : ''}</strong></div>`
      : '')
    + `<p>${esc(msg)}</p>`
    + (structuralOnly ? '<p class="hint">This document doesn’t show billing codes, so those checks couldn’t run.</p>' : '')
    + (action && action.letter && linesConfirmed && (!leadDuplicate || action.key === 'dispute')
      ? '<button type="button" class="secondary" id="draftnext">Prepare a draft letter</button>' : '');
  $('#draftnext')?.addEventListener('click', () => showLetter(action.letter));
}

function plainAction(action) {
  return ({
    dispute: 'Ask the billing office to explain the flagged charges. Check the details below before asking them to correct anything.',
    itemized: 'Ask your provider for a bill that lists each service and what it costs.',
    eob: 'Ask your billing office why its charge differs from the amount your insurer says you owe.',
    gfe: 'Ask your billing office why this charge is higher than the estimate it gave you.',
    nsa: 'Ask your insurer or billing office whether surprise-billing protections apply.',
    charity: 'Ask the hospital whether it has a program that can lower the bill.',
    cash: 'Ask the billing office to compare these charges with its published cash price.',
  })[action.key] || action.title;
}

function renderActions(findings, ctx, lines) {
  const acts = I.nextActions(findings, ctx, undefined, lines);
  if (!acts.length) { $('#actions').innerHTML = ''; return; }
  const lineRefs = (idxs) => (idxs || []).map((n) =>
    `<a class="lineref" href="#line-${n}">${n}</a>`).join(' ');
  $('#actions').innerHTML = `
    <div class="actions">
      <h3>What to do first</h3>
      <p class="hint">Ranked by what actually moves a balance, not by how alarming it
      reads. Everything else is below. Overlapping findings count only once, using
      the largest amount per connected group. This conservative review amount
      is not guaranteed savings; individual findings are not additive.</p>
      <ol>${acts.map((a) => `
        <li>
          <span class="act-title">${esc(a.title)}</span>
          ${a.amount ? `<span class="act-amount">${fmt(a.amount)}</span>` : ''}
          <p>${esc(a.why)}</p>
          ${a.lines.length ? `<p class="lines">Lines ${lineRefs(a.lines)}</p>` : ''}
          ${a.letter ? `<button class="secondary letterbtn" data-letter="${a.letter}">Draft: ${esc(I.LETTERS[a.letter].label)}</button>` : ''}
        </li>`).join('')}</ol>
    </div>`;
}

/* ------------------------------------------------------------ evidence */
function toMarkdown(lines, findings, ctx) {
  const total = lines.reduce((s, l) => s + l.charge, 0);
  const wholeBill = findings.filter((f) => f.recoverable && !f.lines.length);
  const rec = I.disputableTotal(findings, lines);
  const out = ['# Itemized bill review', '',
    `- Lines parsed: **${lines.length}**`,
    `- Total charges: **${fmt(total)}**`,
    `- Findings: **${findings.length}**`];
  if (rec > 0) out.push(`- Directly disputable line items: **${fmt(rec)}**`);
  if (wholeBill.length) out.push(`- Whole-bill protections that may apply: **${wholeBill.length}** `
    + '(these cover the balance, and are not added to the line-item figure above)');
  out.push('', 'Overlapping findings count only once, using the largest amount per connected group. '
    + 'This conservative review amount is not guaranteed savings; individual findings are not additive.');

  const acts = I.nextActions(findings, ctx, undefined, lines);
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
    kind === 'dispute' ? I.disputableTotal(disputable, lastLines) : total);
}

function showLetter(kind) {
  if (!linesConfirmed) return;
  $('#exporttools').open = true;
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

const MAX_FILE_BYTES = 25 * 1024 * 1024;
const isPhoto = (f) => /^image\//.test(f.type) || /\.(jpe?g|png|webp|bmp|gif|heic|heif)$/i.test(f.name);

async function readFileText(f) {
  if (f.size > MAX_FILE_BYTES) throw new Error('Choose a file under 25 MB.');
  const isPdf = /\.pdf$/i.test(f.name || '') || f.type === 'application/pdf';
  if (isPdf) {
    let text;
    try { text = await PDF.extractText(await f.arrayBuffer()); }
    catch { throw new Error('We couldn’t open this PDF. Try another copy or paste the bill text.'); }
    if (!text.trim()) {
      throw new Error('This PDF looks like a scan and has no readable text. Try a clear photo of a page or a text PDF from your provider.');
    }
    return text;
  }
  if (isPhoto(f) || !/\.(csv|tsv|txt)$/i.test(f.name || '')) {
    throw new Error('Choose a photo, a PDF, or a CSV, TSV or text file.');
  }
  return f.text();
}

function requestState(busy, retry) {
  $('#receipt-controls').setAttribute('aria-busy', String(busy));
  $('#dropzone').classList.toggle('is-reading', busy);
  $('#read-indicator').classList.toggle('hidden', !busy);
  $('#cancelread').classList.toggle('hidden', !busy);
  $('#retryread').classList.toggle('hidden', !retry);
}

function cancelFile(announce = true) {
  if (!fileRequest) return;
  fileRequest.abort();
  fileRequest = null;
  requestState(false, Boolean(retryFile));
  if (announce) status('Reading canceled. Your previous review was not changed.');
}

async function handleFile(f) {
  if (!f) return;
  cancelFile(false);
  const request = new AbortController();
  fileRequest = request;
  retryFile = f;
  const photo = isPhoto(f);
  requestState(true, false);
  status(photo ? 'Reading the photo on this device…' : 'Reading your file on this device…');
  try {
    if (f.size > MAX_FILE_BYTES) throw new Error('Choose a file under 25 MB.');
    const text = photo ? await Receipt.readPhoto(f, request.signal) : await readFileText(f);
    if (fileRequest !== request) return;
    if (!replacing && !confirmOverwrite('Replace the current receipt with this one?')) {
      retryFile = null;
      status('Kept your current receipt.');
      return;
    }
    await referencesReady;
    if (fileRequest !== request) return;
    resetReview();
    eobSequence += 1;
    eobRows = null;
    $('#eobstatus').textContent = '';
    $('#bill').value = text;
    source = photo ? 'photo' : 'file';
    runAudit();
    focusResult();
    status('');
    retryFile = null;
  } catch (err) {
    if (fileRequest === request) status(err.message, 'warn');
  } finally {
    if (fileRequest === request) {
      fileRequest = null;
      requestState(false, Boolean(retryFile));
    }
  }
}

let eobSequence = 0;
async function handleEob(f) {
  if (!f) return;
  const sequence = ++eobSequence;
  try {
    if (isPhoto(f)) throw new Error('Choose a text PDF or CSV for your insurer’s statement.');
    const text = await readFileText(f);
    if (sequence !== eobSequence) return;
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
    if (sequence !== eobSequence) return;
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
  const { lines, stats } = I.parseBillDetails(text);
  lastLines = lines;
  lastCtx = readContext();
  if (!lastLines.length) {
    $('#result-title').textContent = 'Your receipt review';
    $('#outcome').classList.remove('has-issue');
    $('#outcome').innerHTML = '<p class="outcome-label">More information needed</p><h3>We couldn’t find individual charges</h3>'
      + '<p>If this receipt only shows a total, ask your provider for a bill that lists each charge. '
      + 'If you’re using a photo, try a clearer picture of the full page.</p>';
    $('#outcome').classList.remove('hidden');
    for (const id of ['parsecheck', 'technical', 'charges', 'exporttools']) document.getElementById(id).classList.add('hidden');
    $('#resultstatus').textContent = 'No individual charges read.';
    renderReadingNote(stats);
    if (!replacing) showStage('review');
    setDownloadEnabled(false);
    return;
  }
  lastFindings = !linesConfirmed && isTentative(lastLines) ? [] : I.audit(lastLines, ref, lastCtx, eobRows);
  render(lastLines, lastFindings, lastCtx, stats);
  setDownloadEnabled(linesConfirmed);
  persist();
}

function setDownloadEnabled(on) {
  $('#download').disabled = !on;
  $('#print').disabled = !on;
  $('#results').classList.toggle('verified', on);
  $$('.letterbtn').forEach((button) => { button.disabled = !on; });
  $('#exportverify').classList.toggle('hidden', on);
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
      bill: $('#bill').value, source,
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
  source = d.source === 'photo' ? 'photo' : 'manual';
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
  cancelFile(false);
  resetReview();
  // A new run is a new bill; the reader has to look at the lines again.
  linesConfirmed = false;
  runAudit();
  if (!$('#results').classList.contains('hidden')) focusResult();
});

for (const [button, input] of [['takephoto', 'camera'], ['choosefile', 'file'], ['chooseeob', 'eobfile']]) {
  document.getElementById(button).addEventListener('click', () => document.getElementById(input).click());
}
for (const id of ['file', 'camera']) {
  document.getElementById(id).addEventListener('change', (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    handleFile(file);
  });
}
// Keep the shorter PDF route useful when a static host did not stage photo OCR.
fetch('vendor/ocr/tesseract.min.js', { method: 'HEAD' })
  .then((r) => {
    if (!r.ok || /text\/html/i.test(r.headers.get('content-type') || '')) {
      $('#intake-note').textContent = 'Photo reading isn’t installed here. Drop a text PDF or choose a file.';
      $('#takephoto').disabled = true;
    }
  }).catch(() => { /* Offline: the browser may already have the OCR assets. */ });
$('#cancelread').addEventListener('click', () => {
  cancelFile();
  $('#choosefile').focus();
});
$('#retryread').addEventListener('click', () => handleFile(retryFile));
$('#reviewagain').addEventListener('click', returnToIntake);
$('#backreview').addEventListener('click', () => {
  cancelFile(false); retryFile = null; requestState(false, false); status('');
  replacing = false;
  showStage('review');
  focusResult();
});
$('#bill').addEventListener('input', () => {
  cancelFile();
  resetReview();
  showStage('intake');
});
$('#eobfile').addEventListener('change', (e) => { handleEob(e.target.files[0]); e.target.value = ''; });

$('#download').addEventListener('click', () => {
  if ($('#download').disabled) return;
  save(toMarkdown(lastLines, lastFindings, lastCtx), 'itemize-review.md');
});
$('#print').addEventListener('click', () => {
  if (!linesConfirmed) return;
  const technical = $('#technical'), charges = $('#charges');
  const wasOpen = [technical.open, charges.open];
  technical.open = charges.open = true;
  window.print();
  [technical.open, charges.open] = wasOpen;
});

$('#exportverify').addEventListener('click', () => {
  verificationRequested = true;
  renderParseCheck(lastLines);
  $('#verify-photos').focus();
});
$('#verify-more').addEventListener('toggle', syncVerifyButton);
$('#confirmlines').addEventListener('click', () => {
  if ($('#confirmlines').disabled || !lastLines.length || $('#parsecheck').classList.contains('hidden')) return;
  linesConfirmed = true;
  verificationRequested = false;
  runAudit();
  focusResult();
});
$('#rejectlines').addEventListener('click', () => {
  linesConfirmed = false;
  setDownloadEnabled(false);
  returnToIntake();
  status('Try another photo or choose a text PDF. You can also edit the extracted text.', 'warn');
});

$('#clear').addEventListener('click', () => {
  cancelFile(false);
  retryFile = null;
  requestState(false, false);
  $('#bill').value = '';
  resetReview();
  source = 'manual'; eobSequence += 1; eobRows = null;
  status(''); $('#eobstatus').textContent = '';
  showStage('intake');
  if ($('#keep').checked) persist();
});

$('#demo').addEventListener('click', () => {
  if (!confirmOverwrite('Replace what is in the box with the example bill?')) return;
  cancelFile(false);
  retryFile = null;
  requestState(false, false);
  resetReview();
  source = 'manual';
  eobSequence += 1;
  eobRows = null;
  status('');
  $('#eobstatus').textContent = '';
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
  if (!$('#results').classList.contains('hidden')) focusResult();
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
  $('#charges').open = true;
  row.classList.add('flash');
  row.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/* --------------------------------------------------------------- boot */
const referencesReady = Promise.all(['hcpcs', 'asp', 'dmepos', 'nadac', 'drg', 'states', 'manifest'].map((n) =>
  fetch(`data/${n}.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
)).then(([hcpcs, asp, dmepos, nadac, drg, states, manifest]) => {
  ref = new I.Reference({ hcpcs, asp, dmepos, nadac, drg, states, manifest });
  buildStateOptions(states);
  const savedState = fileRequest ? '' : restore();
  if (savedState) $('#state').value = savedState;

  const name = ref.manifest.built ? ` (built ${ref.manifest.built})` : '';
  $('#provenance').textContent = Object.keys(ref.hcpcs).length
    ? `Federal reference data is available${name}. Each finding lists its source and limits.`
    : 'Federal reference data didn’t load. Checks like repeated charges still work.';

  if (!fileRequest && $('#bill').value.trim()) runAudit();
});

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {
    /* Offline support is a bonus; the page works without it. */
  });
}
