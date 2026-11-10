const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../touchpad/apply.mjs'), 'utf8');
// Mocked protocol checks only: these tests do not apply settings on ChromeOS.
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const name = 'FTCS1000:00 2808:0106 Touchpad';
const properties = ['Device Touchpad', 'Device Mouse', 'Change Timeout', 'Motion Tap Prevent Timeout'];
const method = body => 'method return time=1 sender=:1.1 -> destination=:1.2 serial=1 reply_serial=2\n' + body;
const scalar = (type, value, readOnly = false) => method(`   boolean ${readOnly}\n   uint32 1\n   variant       array [\n      ${type} ${value}\n   ]\n`);

function fixture(options = {}) {
  const listeners = new Set();
  const commands = [], closed = [], writes = [], acknowledgements = [], resizes = [], waits = [];
  const values = new Map([['Change Timeout', 0.2], ['Motion Tap Prevent Timeout', 0.05]]);
  let enumerations = 0;
  const id = options.id || 12;
  const deviceName = options.name || name;
  const pid = 'helper-process';
  // Captured from rustyline 9.1.1 refresh_line: redraw and absolute column move.
  const prompt = options.rustylinePrompt
    ? '\u001b[?2004h\r\u001b[0K\u001b[1;33mcrosh>\u001b[0m \r\u001b[7C'
    : '\u001b[32mcrosh> \u001b[0m';
  let dispatching = false, currentType;
  // Model Chromium's event binding: dispatch listeners, then automatically ack.
  // https://chromium.googlesource.com/chromium/src/+/406edae7960e6d0bce2f9d4dc86cd5702c7f6da0/chrome/renderer/resources/extensions/terminal_private_custom_bindings.js
  const output = (id, type, data) => {
    currentType = type;
    dispatching = true;
    try { for (const listener of listeners) listener(id, type, data); }
    finally { dispatching = false; api.ackOutput(id); }
  };
  const queue = [];
  let blocked = false, emitted = 0;
  const pump = () => {
    if (blocked || !queue.length || closed.length) return;
    blocked = true;
    emitted++;
    output(pid, 'stdout', queue.shift());
  };
  function emit(text) {
    // An unrelated active terminal must never supply this helper's prompt or result.
    output('existing-user-terminal', 'stdout', 'method return\nvariant array [ double 999 ]\ncrosh> ');
    if (options.bytes) {
      const bytes = new TextEncoder().encode(text);
      for (let i = 0; i < bytes.length; i += 3) queue.push(bytes.slice(i, i + 3).buffer);
    } else queue.push(text);
    pump();
  }
  function answer(command) {
    if (command === 'gesture_prop devices') {
      enumerations++;
      if (options.serviceError) return 'Error org.freedesktop.DBus.Error.' + (options.serviceError === true ? 'ServiceUnknown' : options.serviceError) + ': unavailable\n';
      if (options.unknownCommand) return 'ERROR: unknown command: gesture_prop\n';
      const current = options.changeDuringDiscovery && enumerations > 1 ? id + 1 : id;
      const currentName = options.renameDuringDiscovery && enumerations > 1 ? 'OTHER Vendor Touchpad' : deviceName;
      const mouse = deviceName.replace(/\btouchpad\b/i, 'Mouse');
      const rows = options.missing ? [[id - 1, mouse]] : [[id - 1, mouse], [current, currentName]];
      if (options.duplicate || options.extraDuringDiscovery && enumerations > 1) {
        rows.push([id + 1, 'ELAN0001:00 Precision Touchpad']);
      }
      if (options.mouseWithTouchpad) rows.push([id + 2, 'External Touchpad Mouse']);
      return method(` uint32 ${rows.length}\n array [\n` + rows.map(([n, label]) =>
        ` dict entry(\n int32 ${n}\n string "${label}"\n )\n`).join('') + ']\n');
    }
    if (command === `gesture_prop list ${id}`) {
      const listed = properties.filter(p => !(options.noFlags && p.startsWith('Device ')) && p !== options.missingProperty);
      return method(' uint32 ' + listed.length + '\n array [\n' + listed.map(p => ` string "${p}"\n`).join('') + ']\n');
    }
    const parsed = command.match(/^gesture_prop (get|set) (\d+) "([^"]+)"(?: array:double:0\.0)?$/);
    assert.ok(parsed, 'unexpected command: ' + command);
    const [, verb, device, property] = parsed;
    assert.equal(Number(device), id, 'must target only discovered touchpad ID');
    if (verb === 'set') {
      writes.push({id: Number(device), property});
      values.set(property, options.badReadback ? 0.1 : 0);
      return method('');
    }
    if (property === 'Device Touchpad') return scalar('boolean', options.wrongTouchpad ? 'false' : 'true');
    if (property === 'Device Mouse') return scalar('boolean', options.wrongMouse ? 'true' : 'false');
    if (property === options.malformedProperty) return method(' boolean false\n uint32 2\n variant array [ double 0.2 double 0.3 ]\n');
    return scalar('double', options.nan ? 'nan' : values.get(property), property === options.readOnly);
  }
  const api = {
    onProcessOutput: {addListener: fn => listeners.add(fn), removeListener: fn => listeners.delete(fn)},
    ackOutput(process) {
      assert.equal(dispatching, false, 'helper must leave acknowledgement to the browser API binding');
      if (process !== pid || currentType === 'exit') return;
      assert.ok(blocked, 'must only acknowledge an outstanding own output event');
      acknowledgements.push(process);
      if (options.ignoreAcknowledgement) return;
      blocked = false;
      queueMicrotask(pump);
    },
    openTerminalProcess(process, args, callback) {
      assert.equal(process, 'crosh'); assert.deepEqual(Array.from(args), []);
      if (options.openNeverReplies) return;
      setTimeout(() => {
        if (!options.noPrompt && !options.promptAfterResize) emit('\u001b[1;33mWelcome\u001b[0m\r\n' + prompt);
        callback(pid); // Output intentionally arrives before the open callback.
      }, options.lateOpen ? 50 : 0);
    },
    onTerminalResize(process, width, height, callback) {
      assert.equal(process, pid, 'resize only the helper-owned PTY');
      assert.deepEqual([width, height], [120, 40]);
      assert.equal(commands.length, 0, 'set size before issuing any command');
      resizes.push({process, width, height});
      if (options.resizeNeverReplies) return;
      if (options.promptAfterResize) emit('Welcome\r\n' + prompt);
      callback(!options.resizeFails);
    },
    sendInput(process, input, callback) {
      assert.equal(process, pid); assert.ok(input.endsWith('\r'));
      assert.equal(resizes.length, 1, 'initialize PTY dimensions before command echo');
      const command = input.slice(0, -1); commands.push(command);
      if (options.inputNeverReplies) return;
      if (options.inputFails) { callback(false); return; }
      setTimeout(() => {
        callback(true);
        if (options.exitDuringCommand) { output(pid, 'exit', ''); return; }
        if (options.stalePrompt) emit('\r\u001b[2K\u001b[32mcrosh> \u001b[0m');
        if (options.echoRedraw) {
          // Rustyline can redraw the prompt and its current input on terminal resize.
          for (let length = 1; length <= command.length; length++) {
            emit('\r\u001b[2K\u001b[32mcrosh> \u001b[0m' + command.slice(0, length));
          }
          emit('\r\n\u001b[32mcrosh> \u001b[0m');
        }
        setTimeout(() => {
          if (options.commandTimeout) return;
          const echo = options.noEcho || options.echoRedraw ? '' : command + '\r\n';
          emit(echo + answer(command) + (options.noReplyPrompt ? '' : prompt));
        }, 0);
      }, 0);
    },
    closeTerminalProcess(process, callback) {
      closed.push(process);
      if (!options.closeNeverReplies) callback(true);
    },
  };
  const context = vm.createContext({chrome: {terminalPrivate: api, runtime: {}}, TextDecoder, setTimeout, clearTimeout});
  vm.runInContext(source.replace('export async function', 'async function'), context);
  return {run: () => context.applyTouchpad(undefined, {
    timeoutMs: options.timeoutMs || 1000, onWait: (label, timeoutMs) => waits.push({label, timeoutMs}),
  }), listeners, closed, writes, commands, pid, acknowledgements, resizes, waits, get emitted() {return emitted;}};
}

async function success(label, options) {
  const test = fixture(options);
  const result = await test.run();
  assert.equal(result.id, options?.id || 12);
  assert.equal(result.name, options?.name || name);
  assert.deepEqual(test.writes.map(w => w.property), properties.slice(2));
  assert.equal(result.before['Change Timeout'], 0.2);
  assert.equal(result.before['Motion Tap Prevent Timeout'], 0.05);
  assert.equal(result.applied['Change Timeout'], 0);
  assert.equal(result.applied['Motion Tap Prevent Timeout'], 0);
  assert.equal(test.listeners.size, 0);
  assert.deepEqual(test.closed, [test.pid]);
  assert.deepEqual(test.resizes, [{process: test.pid, width: 120, height: 40}]);
  assert.equal(test.acknowledgements.length, test.emitted, 'every own output event must release backpressure');
  console.log('PASS', label);
}
async function failure(label, options, error, expectedWrites = 0) {
  const test = fixture(options);
  await assert.rejects(test.run(), error);
  if (options.lateOpen) await sleep(70);
  assert.equal(test.writes.length, expectedWrites);
  assert.equal(test.listeners.size, 0);
  assert.deepEqual(test.closed, options.openNeverReplies ? [] : [test.pid]);
  console.log('PASS', label);
}
(async () => {
  await success('ID12 touchpad selected, Mouse sibling excluded, ANSI welcome before callback');
  await success('ID73 dynamically rediscovered, three-byte ArrayBuffer chunks', {id: 73, bytes: true});
  await success('arbitrary other vendor and prefix', {name: 'SYNA3602:01 06CB:CD51 Touchpad', id: 104});
  await success('case-insensitive touchpad word', {name: 'Acme Precision tOuChPaD', id: 37});
  await success('Mouse sibling containing Touchpad is excluded', {name: 'Cypress Touchpad', mouseWithTouchpad: true});
  await success('optional Device flags absent', {noFlags: true});
  await success('initial prompt waits for initialized PTY size', {promptAfterResize: true});
  await success('real rustyline prompt cursor-position suffix in one output event', {rustylinePrompt: true});
  await success('real rustyline prompt fragmented through cursor-position suffix', {rustylinePrompt: true, bytes: true});
  await success('resize-only rustyline prompt retains redraw cursor-position suffix', {rustylinePrompt: true, promptAfterResize: true});
  await success('real rustyline prompt plus stale resize redraws and fragmented reply', {rustylinePrompt: true, bytes: true, echoRedraw: true});
  await success('late resize prompt cannot complete an outstanding command', {stalePrompt: true});
  await success('stale prompt and no-echo replies in fragmented byte events', {stalePrompt: true, noEcho: true, bytes: true});
  await success('incremental echo redraw and empty prompt before DBus reply', {echoRedraw: true, bytes: true});
  await failure('resize rejected before any gesture command', {resizeFails: true}, /Could not size/);
  await failure('resize callback timeout still cleans own process', {resizeNeverReplies: true, timeoutMs: 20}, /Timed out/);
  await failure('touchpad word absent', {missing: true}, /found 0/);
  await failure('touchpad substring is not a word', {name: 'Vendor SuperTouchpad'}, /found 0/);
  await failure('different touchpad names are ambiguous', {duplicate: true}, /found 2/);
  await failure('advertised Device Touchpad false', {wrongTouchpad: true}, /verified touchpad/);
  await failure('advertised Device Mouse true', {wrongMouse: true}, /verified touchpad/);
  await failure('required timeout property absent', {missingProperty: 'Motion Tap Prevent Timeout'}, /Missing touchpad/);
  await failure('multi-value timeout rejected', {malformedProperty: 'Change Timeout'}, /Unexpected gesture/);
  await failure('read-only second timeout rejected before any write', {readOnly: 'Motion Tap Prevent Timeout'}, /read-only/);
  await failure('nonfinite timeout rejected', {nan: true}, /Invalid timeout/);
  await failure('ID changed during discovery', {changeDuringDiscovery: true}, /changed during/);
  await failure('name changed with same ID during discovery', {renameDuringDiscovery: true}, /changed during/);
  await failure('another touchpad appeared during discovery', {extraDuringDiscovery: true}, /changed during/);
  await failure('unavailable DBus service', {serviceError: true}, error => /gesture_prop devices/.test(error.message) && error.retryable === true);
  await failure('DBus NameHasNoOwner is transient', {serviceError: 'NameHasNoOwner'}, error => error.retryable === true);
  await failure('DBus NoReply is transient', {serviceError: 'NoReply'}, error => error.retryable === true);
  await failure('DBus AccessDenied is not retried or swallowed', {serviceError: 'AccessDenied', stalePrompt: true}, error => /AccessDenied/.test(error.message) && !error.retryable);
  await failure('unknown command is not retried or swallowed', {unknownCommand: true, echoRedraw: true}, error => /unknown command/.test(error.message) && /gesture_prop devices/.test(error.message) && !error.retryable);
  await failure('nonzero readback fails visibly', {badReadback: true}, /read back as zero/, 1);
  await failure('owned process exit', {exitDuringCommand: true}, /process exited/);
  await failure('input rejected', {inputFails: true}, /Crosh input failed/);
  await failure('initial prompt timeout', {noPrompt: true, timeoutMs: 20}, /Timed out/);
  await failure('command prompt timeout identifies command', {commandTimeout: true, timeoutMs: 20}, error => /Timed out.*gesture_prop devices/.test(error.message) && error.retryable === true);
  await failure('prompt-only redraw timeout includes escaped own output only', {stalePrompt: true, commandTimeout: true, timeoutMs: 20}, error =>
    /Timed out.*gesture_prop devices/.test(error.message) && error.retryable === true &&
    error.message.includes('\\u001b[32mcrosh> ') && !error.message.includes('999'));
  await failure('DBus reply without a final prompt remains blocked', {noReplyPrompt: true, timeoutMs: 20}, /Timed out.*gesture_prop devices/);
  await failure('input API callback timeout', {inputNeverReplies: true, timeoutMs: 20}, /Timed out/);
  await failure('open API callback timeout', {openNeverReplies: true, timeoutMs: 20}, /Timed out/);
  await failure('late-open owned process is closed', {lateOpen: true, timeoutMs: 20}, /Timed out/);
  await failure('cleanup API timeout still removes listener', {noPrompt: true, closeNeverReplies: true, timeoutMs: 20}, /Timed out/);
  await failure('blocked API acknowledgement times out without writes', {bytes: true, ignoreAcknowledgement: true, timeoutMs: 20}, /Timed out/);
  const countdown = fixture({timeoutMs: 321});
  await countdown.run();
  assert.deepEqual(countdown.waits.slice(0, 3).map(wait => wait.label), [
    'Opening ChromeOS terminal', 'Preparing ChromeOS terminal', 'Waiting for ChromeOS terminal',
  ]);
  assert.equal(countdown.waits.at(-1).label, 'Finishing touchpad setup');
  assert.equal(countdown.waits.filter(wait => wait.label === 'Applying touchpad settings').length, countdown.commands.length);
  assert.equal(countdown.waits.length, countdown.commands.length + 4, 'sendInput must not reset the command countdown');
  assert.ok(countdown.waits.every(wait => wait.timeoutMs === 321), 'every wait reports its actual timeout');
  console.log('PASS readiness and command waits expose matching deadlines without raw commands');
  const missing = vm.createContext({TextDecoder, setTimeout, clearTimeout});
  vm.runInContext(source.replace('export async function', 'async function'), missing);
  await assert.rejects(missing.applyTouchpad(), /terminalPrivate API is unavailable/);
  console.log('PASS unavailable terminalPrivate context fails clearly');
  console.log('47 protocol tests passed with modeled Chromium automatic-ack and backpressure. Mocked runtime only; actual ChromeOS application remains unverified.');
})().catch(error => { console.error(error); process.exitCode = 1; });
