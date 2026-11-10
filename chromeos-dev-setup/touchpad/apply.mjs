// Invoke only in a context allowed to use chrome.terminalPrivate.
// Always discovers the numeric device ID; closes only the Crosh process it opens.
export async function applyTouchpad(api = globalThis.chrome?.terminalPrivate, {
  timeoutMs = 10000,
} = {}) {
  if (!api?.onProcessOutput || ['openTerminalProcess', 'sendInput', 'closeTerminalProcess', 'onTerminalResize']
      .some((method) => typeof api[method] !== 'function')) {
    throw new Error('terminalPrivate API is unavailable in this context');
  }
  const call = (method, ...args) => new Promise((resolve, reject) => {
    let expired = false;
    const timer = setTimeout(() => {
      expired = true;
      reject(new Error('Timed out calling ' + method));
    }, timeoutMs);
    try { api[method](...args, (value) => {
      clearTimeout(timer);
      const error = globalThis.chrome?.runtime?.lastError;
      if (expired) {
        if (method === 'openTerminalProcess' && typeof value === 'string' && value) {
          api.closeTerminalProcess(value, () => void globalThis.chrome?.runtime?.lastError);
        }
        return;
      }
      if (error) reject(new Error(error.message));
      else resolve(value);
    }); } catch (error) { clearTimeout(timer); reject(error); }
  });
  const strip = (text) => text.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '').replace(/\r/g, '');
  const streams = new Map();
  let pid;
  let waiting;
  const stream = (id) => {
    if (!streams.has(id)) streams.set(id, {text: '', decoder: new TextDecoder()});
    return streams.get(id);
  };
  const check = () => {
    if (pid === undefined || !waiting) return;
    const state = stream(pid);
    if (state.exited) waiting.reject(new Error('Crosh process exited'));
    else if (state.text.length > 262144) waiting.reject(new Error('Unexpectedly large Crosh response'));
    else if (/(?:^|\n)crosh> $/.test(strip(state.text))) waiting.resolve(strip(state.text));
  };
  const onOutput = (id, type, data) => {
    if (pid !== undefined && id !== pid) return;
    const state = stream(id);
    if (type === 'exit') state.exited = true;
    else state.text += typeof data === 'string' ? data : state.decoder.decode(data, {stream: true});
    if (state.text.length > 262144) {
      if (waiting) waiting.reject(new Error('Unexpectedly large Crosh response'));
      return;
    }
    check();
  };
  const prompt = () => new Promise((resolve, reject) => {
    const finish = (fn) => (value) => {
      clearTimeout(timer);
      waiting = undefined;
      fn(value);
    };
    const timer = setTimeout(() => waiting?.reject(new Error('Timed out waiting for Crosh prompt')), timeoutMs);
    waiting = {resolve: finish(resolve), reject: finish(reject)};
    check();
  });
  const run = async (command) => {
    stream(pid).text = '';
    const response = prompt();
    try {
      const sent = call('sendInput', pid, command + '\r').then((ok) => {
        if (!ok) throw new Error('Crosh input failed');
      });
      const [, text] = await Promise.all([sent, response]);
      if (!/\bmethod return\b/.test(text) || /org\.freedesktop\.DBus\.Error|\bERROR:/i.test(text)) {
        throw new Error('gesture_prop failed: ' + text.trim());
      }
      return text;
    } catch (error) {
      waiting?.reject(error);
      await response.catch(() => {});
      throw error;
    }
  };
  const touchpads = (text) => Array.from(text.matchAll(
    /dict entry\s*\(\s*int32\s+(\d+)\s+string\s+"((?:\\.|[^"\\])*)"\s*\)/g),
  (match) => ({id: Number(match[1]), name: JSON.parse('"' + match[2] + '"')}))
    .filter(({name}) => /\btouchpad\b/i.test(name) && !/\bmouse\b/i.test(name));
  const scalar = (text, type, writable = false) => {
    const match = text.match(new RegExp('boolean\\s+(true|false)\\s+uint32\\s+1\\s+' +
      'variant\\s+array\\s*\\[\\s*' + type + '\\s+([^\\s\\]]+)\\s*\\]'));
    if (!match) throw new Error('Unexpected gesture property response');
    if (writable && match[1] !== 'false') throw new Error('Timeout property is read-only');
    return match[2];
  };
  api.onProcessOutput.addListener(onOutput);
  try {
    pid = await call('openTerminalProcess', 'crosh', []);
    if (typeof pid !== 'string' || !pid) throw new Error('Could not open Crosh');
    if (!await call('onTerminalResize', pid, 120, 40)) throw new Error('Could not size Crosh terminal');
    await prompt();
    const matches = touchpads(await run('gesture_prop devices'));
    if (matches.length !== 1) throw new Error('Expected exactly one touchpad; found ' + matches.length);
    const {id, name} = matches[0];
    const listed = await run(`gesture_prop list ${id}`);
    const names = new Set(Array.from(listed.matchAll(/string\s+"((?:\\.|[^"\\])*)"/g),
      (match) => JSON.parse('"' + match[1] + '"')));
    for (const [flag, expected] of [['Device Touchpad', 'true'], ['Device Mouse', 'false']]) {
      if (names.has(flag) && scalar(await run(`gesture_prop get ${id} "${flag}"`), 'boolean') !== expected) {
        throw new Error('Selected device is not a verified touchpad: ' + flag);
      }
    }
    const properties = ['Change Timeout', 'Motion Tap Prevent Timeout'];
    const before = {};
    for (const property of properties) {
      if (!names.has(property)) throw new Error('Missing touchpad property: ' + property);
      const value = Number(scalar(await run(`gesture_prop get ${id} "${property}"`), 'double', true));
      if (!Number.isFinite(value)) throw new Error('Invalid timeout property value');
      before[property] = value;
    }
    const current = touchpads(await run('gesture_prop devices'));
    if (current.length !== 1 || current[0].id !== id || current[0].name !== name) {
      throw new Error('Touchpad changed during discovery');
    }
    for (const property of properties) {
      await run(`gesture_prop set ${id} "${property}" array:double:0.0`);
      if (Number(scalar(await run(`gesture_prop get ${id} "${property}"`), 'double')) !== 0) {
        throw new Error('Timeout did not read back as zero: ' + property);
      }
    }
    return {id, name, before, applied: Object.fromEntries(properties.map((p) => [p, 0]))};
  } finally {
    if (waiting) waiting.reject(new Error('Closing Crosh helper'));
    try {
      if (pid !== undefined) await call('closeTerminalProcess', pid).catch(() => {});
    } finally {
      api.onProcessOutput.removeListener(onOutput);
    }
  }
}
