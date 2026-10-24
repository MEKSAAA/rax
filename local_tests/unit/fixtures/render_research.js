// Runs the dashboard's renderResearch() on a session detail, with a stand-in DOM, and prints
// the stat rows it rendered (or the error it threw). Usage: node render_research.js app.js data.json
const fs = require('fs');
const vm = require('vm');

const [script, input] = process.argv.slice(2);

// Every property of a stand-in element is another callable stand-in, so any DOM call works;
// assigned properties and children are kept for the result.
function element(tag) {
  const store = {tag, children: [], dataset: {}};
  store.append = (...nodes) => { store.children.push(...nodes); };
  store.replaceChildren = (...nodes) => { store.children = nodes; };
  return new Proxy(function () {}, {
    get: (_, key) => (key in store ? store[key] : typeof key === 'symbol' ? undefined : element()),
    set: (_, key, value) => { store[key] = value; return true; },
    apply: () => element(),
  });
}

const elements = {};
const context = {
  console, URLSearchParams, JSON,
  location: {hash: '#token=test'},
  document: {getElementById: id => (elements[id] ||= element(id)), createElement: element,
             addEventListener() {}},
  navigator: {}, confirm: () => false,
  fetch: () => new Promise(() => {}),  // Never answers: only the call below renders.
  setInterval: () => 0,
  Option: function (label, value) { return Object.assign(element('option'), {label, value}); },
};
const probe = `
data = JSON.parse(${JSON.stringify(fs.readFileSync(input, 'utf8'))});
let error = null;
try { renderResearch(); } catch (exception) { error = String(exception); }
console.log(JSON.stringify({error, rows: document.getElementById('stats').children.map(
  row => [row.children[0].textContent, row.children[1].textContent, row.children[1].title ?? null])}));
`;
vm.runInNewContext(fs.readFileSync(script, 'utf8') + '\n' + probe, context);
