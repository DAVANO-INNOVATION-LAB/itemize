/* Exercises the real receipt UI controller in a DOM stub with only synthetic fixtures. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const I = require('../web/rules.js');
const stored = new Map(), nodes = new Map(), jobs = new Map(), requests = [];
let allowReplace = true;
const element = (id) => {
  if (!nodes.has(id)) {
    const classes = new Set();
    const callbacks = {};
    nodes.set(id, {
      classList: {
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c),
        contains: (c) => classes.has(c),
        toggle: (c, yes) => yes ? classes.add(c) : classes.delete(c),
      },
      value: '', open: false, checked: false, disabled: false, textContent: '', innerHTML: '',
      addEventListener: (event, cb) => { callbacks[event] = cb; },
      event: (event, e) => callbacks[event](e),
      setAttribute() {}, focus() {}, scrollIntoView() {}, replaceChildren() { this.innerHTML = ''; },
    });
  }
  return nodes.get(id);
};
const html = fs.readFileSync('web/index.html','utf8');
for (const m of html.matchAll(/id="([^"]+)"/g)) {
  assert(!nodes.has('#'+m[1]), 'duplicate id: '+m[1]);
  element('#'+m[1]);
}
for (const id of ['state','gfe','drg']) {
  const field = html.indexOf('id="'+id+'"');
  assert(field > html.indexOf('id="context"') && field < html.indexOf('id="eobfile"'));
}
const doc = {
  querySelector: (s) => s.includes(':checked') ? null : element(s),
  getElementById: (id) => element('#'+id),
  querySelectorAll: () => [],
};
const env = vm.createContext({
  window: { ITEMIZE: I, addEventListener() {}, scrollTo() {}, confirm: () => allowReplace }, document: doc,
  Blob, Response, DecompressionStream, AbortController, navigator: {},
  localStorage: { setItem:(k,v)=>stored.set(k,v), getItem:(k)=>stored.get(k), removeItem:(k)=>stored.delete(k) },
  fetch: async (url, options) => {
    requests.push({ url, options });
    return url.startsWith('data/') ? { ok: true, json: async () => JSON.parse(fs.readFileSync('web/'+url)) }
      : { ok: true, headers: { get: ()=> 'text/javascript' } };
  },
  Receipt: { readPhoto: (f, signal) => new Promise((resolve) => jobs.set(f.name, {resolve,signal})) },
});
vm.runInContext(fs.readFileSync('web/pdf.js','utf8'),env);
vm.runInContext(fs.readFileSync('web/app.js','utf8'),env);
const run = (s) => vm.runInContext(s,env);
const hit = (id) => element('#'+id).event('click');
const filename = (name, type, bytes) => new File([bytes], name, {type});
async function main() {
  await run('referencesReady');
  const photo = filename('synthetic.png','image/png','test adapter placeholder');
  const charges = 'J1885 KETOROLAC 2 180.00\nJ1885 KETOROLAC 2 180.00\nTOTAL $360.00';
  env.newFile = photo;
  const reading = run('handleFile(newFile)');
  jobs.get(photo.name).resolve(charges);
  await reading;
  assert.match(element('#resultstatus').textContent,/2 charges read from your photo/);
  assert(element('#outcome').classList.contains('hidden'));
  assert(element('#download').disabled && element('#print').disabled);
  assert.equal(run('lastFindings.length'),0);
  assert.match(element('#verify-first').innerHTML,/180.00/);
  hit('confirmlines');
  assert(!element('#download').disabled);
  assert.match(element('#outcome').innerHTML,/A charge appears twice/);
  assert(run('lastFindings.length') > 0);
  assert(element('#dropzone').classList.contains('hidden'));
  assert.match(element('#outcome').innerHTML,/180.00 per entry/);
  hit('reviewagain');
  assert(!element('#dropzone').classList.contains('hidden'));
  hit('backreview');
  assert(element('#dropzone').classList.contains('hidden'));
  element('#keep').checked = true;
  element('#keep').event('change');
  assert(JSON.parse(stored.get('itemize.draft.v1')).source === 'photo');
  run('resetReview(); restore(); runAudit();');
  assert(element('#outcome').classList.contains('hidden'));
  assert(element('#download').disabled);

  const long = filename('long.png','image/png','test adapter placeholder');
  env.newFile = long;
  const longReading = run('handleFile(newFile)');
  jobs.get(long.name).resolve(Array.from({length: 10}, (_, i) =>
    `J1885 TEST CHARGE ${i} 180.00`).join('\n'));
  await longReading;
  assert.equal((element('#verify-first').innerHTML.match(/<li>/g) || []).length, 8);
  assert.equal((element('#verify-rest').innerHTML.match(/<li>/g) || []).length, 2);
  assert.match(element('#verify-more-label').textContent,/2 more charges/);
  assert(element('#confirmlines').disabled);
  hit('confirmlines');
  assert(element('#download').disabled);
  element('#verify-more').open = true;
  element('#verify-more').event('toggle');
  assert.equal(element('#confirmlines').textContent,'All 10 amounts match');
  hit('confirmlines');
  assert(!element('#download').disabled);

  const late = filename('late.png','image/png','old');
  env.newFile = late;
  const pending = run('handleFile(newFile)');
  hit('cancelread');
  assert(jobs.get(late.name).signal.aborted);
  jobs.get(late.name).resolve('J1885 FAKE 2 900.00');
  await pending;
  assert.match(element('#bill').value,/180.00/);
  assert(!element('#bill').value.includes('900.00'));

  const pdf = fs.readFileSync('tests/fixtures/sample_bill.pdf');
  env.newFile = filename('sample.pdf','application/pdf',pdf);
  allowReplace = false;
  await run('handleFile(newFile)');
  assert.match(element('#filestatus').textContent,/Kept your current receipt/);
  assert.match(element('#bill').value,/180.00/);
  allowReplace = true;
  await run('handleFile(newFile)');
  assert.notEqual(element('#bill').value,charges);
  assert(run('lastLines.length') > 0);
  assert(!element('#outcome').classList.contains('hidden'));
  assert(element('#download').disabled);

  env.newFile = filename('receipt.txt','text/plain','SYNTHETIC MEDICAL RECEIPT\nTOTAL $360.00');
  await run('handleFile(newFile)');
  assert.match(element('#outcome').innerHTML,/couldn’t find individual charges/);
  assert(run('lastLines.length') === 0);
  assert(element('#download').disabled);
  assert(requests.every(r=>!r.options?.body && (!r.options?.method || r.options.method==='HEAD')));
  console.log('Passed: photo guard, confirmation, restoring OCR guard, cancellation, replacing/keeping a result, native PDF extraction, total-only receipt, and no request bodies.');

}
main().catch(e=>{ console.error(e);process.exitCode=1; });
