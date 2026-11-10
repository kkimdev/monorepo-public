// Run: node --experimental-vm-modules --disable-warning=ExperimentalWarning chromeos-dev-setup/tests/touchpad-startup.test.cjs
const fs = require('node:fs/promises');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.join(__dirname, '../touchpad');
// Mocked extension integration only; no browser windows or ChromeOS settings are changed.

async function startup({windows = [{id: 41}], event = 'startup', reason} = {}) {
  const listeners = {};
  const calls = [];
  const chrome = {
    runtime: {
      getURL: p => `chrome-extension://preview/${p}`,
      onStartup: {addListener: f => { listeners.startup = f; }},
      onInstalled: {addListener: f => { listeners.installed = f; }},
    },
    windows: {
      getAll: async options => { assert.deepEqual(Array.from(options.windowTypes), ['normal']); return windows; },
      create: async options => calls.push({kind: 'window', ...options}),
    },
    tabs: {create: async options => calls.push({kind: 'tab', ...options})},
  };
  vm.runInNewContext(await fs.readFile(path.join(root, 'startup.js'), 'utf8'), {chrome, console});
  await listeners[event]({reason});
  return calls;
}

async function page({review = false, error, failures = []} = {}) {
  const nodes = {status: {}, result: {}};
  const removed = [];
  const stored = [];
  const delays = [];
  let attempts = 0;
  const applied = {id: 73, name: 'Other Vendor Precision Touchpad', before: {}, applied: {}};
  const document = {getElementById: id => nodes[id], title: 'Touchpad setup'};
  const context = vm.createContext({
    URL, Date,
    setTimeout: (callback, delay) => { delays.push(delay); callback(); },
    location: {href: `chrome-extension://preview/page.html${review ? '?review' : ''}`},
    document,
    chrome: {
      storage: {local: {set: async item => stored.push(item)}},
      tabs: {getCurrent: async () => ({id: 91}), remove: async id => removed.push(id)},
    },
  });
  const helper = new vm.SyntheticModule(['applyTouchpad'], function() {
    this.setExport('applyTouchpad', async () => {
      const failure = failures[attempts++] || error;
      if (failure) throw failure;
      return applied;
    });
  }, {context});
  const module = new vm.SourceTextModule(await fs.readFile(path.join(root, 'page.mjs'), 'utf8'), {context});
  await module.link(specifier => { assert.equal(specifier, './apply.mjs'); return helper; });
  await module.evaluate();
  return {nodes, removed, stored, document, attempts, delays};
}

(async () => {
  let calls = await startup();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].kind, 'tab');
  assert.equal(calls[0].windowId, 41);
  assert.equal(calls[0].active, false);
  assert.equal(calls[0].url, 'chrome-extension://preview/page.html');
  console.log('PASS startup uses an inactive tab in an existing normal window');

  calls = await startup({windows: []});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].kind, 'window');
  assert.equal(calls[0].type, 'popup');
  assert.equal(calls[0].focused, false);
  assert.equal(calls[0].url, 'chrome-extension://preview/page.html');
  console.log('PASS login without a browser window creates an unfocused runner popup');

  for (const reason of ['install', 'update']) {
    calls = await startup({event: 'installed', reason});
    assert.equal(calls.length, 1);
    assert.equal(calls[0].active, true);
    assert.equal(calls[0].url, 'chrome-extension://preview/page.html?review');
  }
  console.log('PASS extension install/update keeps the review page visible');

  calls = await startup({event: 'installed', reason: 'chrome_update'});
  assert.equal(calls.length, 0);
  console.log('PASS browser update avoids a duplicate installed-event runner');

  let result = await page();
  assert.deepEqual(result.removed, [91]);
  assert.equal(result.stored.length, 1);
  assert.equal(result.stored[0].crosTouchpadResult.id, 73);
  assert.equal(result.stored[0].crosTouchpadResult.name, 'Other Vendor Precision Touchpad');
  assert.match(result.nodes.status.textContent, /verified/);
  assert.match(result.nodes.result.textContent, /Current device ID: 73/);
  console.log('PASS verified startup closes only its own tab and records the result');

  result = await page({review: true});
  assert.equal(result.removed.length, 0);
  assert.match(result.nodes.status.textContent, /verified/);
  console.log('PASS installation review remains open after success');

  result = await page({error: new Error('Missing touchpad property')});
  assert.equal(result.removed.length, 0);
  assert.equal(result.stored.length, 0);
  assert.equal(result.nodes.result.textContent, 'Missing touchpad property');
  assert.match(result.nodes.status.textContent, /could not finish/);
  assert.equal(result.document.title, 'Touchpad setup failed');
  assert.equal(result.attempts, 1);
  assert.deepEqual(result.delays, []);
  console.log('PASS failure remains visible and does not save a success result');

  const transient = () => Object.assign(new Error('Crosh is not ready'), {retryable: true});
  result = await page({failures: [transient(), transient()]});
  assert.equal(result.attempts, 3);
  assert.deepEqual(result.delays, [1000, 2000]);
  assert.deepEqual(result.removed, [91]);
  assert.equal(result.stored.length, 1);
  console.log('PASS startup retries temporary readiness errors and closes after verification');

  result = await page({review: true, failures: [transient()]});
  assert.equal(result.attempts, 2);
  assert.deepEqual(result.delays, [1000]);
  assert.deepEqual(result.removed, []);
  assert.match(result.nodes.status.textContent, /verified/);
  console.log('PASS first-install review also recovers from a temporary readiness error');

  result = await page({error: transient()});
  assert.equal(result.attempts, 3);
  assert.deepEqual(result.delays, [1000, 2000]);
  assert.deepEqual(result.removed, []);
  assert.deepEqual(result.stored, []);
  assert.equal(result.document.title, 'Touchpad setup failed');
  assert.match(result.nodes.result.textContent, /not ready/);
  console.log('PASS persistent readiness errors stop after three attempts and stay visible');
  console.log('10 startup/page integration checks passed with mocked Chrome APIs; actual ChromeOS startup remains unverified.');
})().catch(error => { console.error(error); process.exitCode = 1; });
