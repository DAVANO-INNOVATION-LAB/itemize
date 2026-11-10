/* Offline synthetic correctness harness. DOM stubs exercise output functions,
 * not layout, browser security, service workers or a real screen reader. */
'use strict';
const fs = require('fs'), vm = require('vm');
const I = require('../web/rules.js');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
if (input.mode === 'aggregate') {
  process.stdout.write(JSON.stringify(input.cases.map(c => ({
    total: I.disputableTotal(c.findings, c.lines),
    actions: I.nextActions(c.findings, null, undefined, c.lines),
  }))));
} else if (input.mode === 'eob') {
  process.stdout.write(JSON.stringify(input.cases.map(c => I.eobCrossCheck(c.lines, c.rows))));
} else if (input.mode === 'outputs') {
  const nodes = new Map();
  const document = {
    querySelector(s) {
      if (!nodes.has(s)) nodes.set(s, { innerHTML: '', textContent: '',
        classList: { add() {}, remove() {} } });
      return nodes.get(s);
    },
    querySelectorAll() { return []; },
  };
  const context = vm.createContext({ window: { ITEMIZE: I }, document, input });
  // Run the real rendering/markdown/letter functions before event binding.
  const source = fs.readFileSync(require.resolve('../web/app.js'), 'utf8');
  vm.runInContext(source.slice(0, source.indexOf("$('#copyletter').addEventListener")), context);
  vm.runInContext(`lastLines = input.lines; lastFindings = input.findings;
    render(lastLines, lastFindings, null, null);
    result = {markdown: toMarkdown(lastLines, lastFindings, null), letter: letterText('dispute')};`, context);
  process.stdout.write(JSON.stringify({ ...context.result,
    summary: nodes.get('#summary').innerHTML, actions: nodes.get('#actions').innerHTML,
    announcement: nodes.get('#resultstatus').textContent }));
}
