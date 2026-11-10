import {applyTouchpad} from './apply.mjs';

const status = document.getElementById('status');
const result = document.getElementById('result');
let countdown;
let attempt = 1;
const showWait = (message, timeoutMs) => {
  clearInterval(countdown);
  const deadline = Date.now() + timeoutMs;
  const render = () => {
    const remaining = Math.max(0, deadline - Date.now()) / 1000;
    status.textContent = `${message} (${attempt}/3) · ${remaining.toFixed(1)}s remaining`;
  };
  render();
  countdown = setInterval(render, 100);
};
try {
  let applied;
  for (; ; attempt++) {
    try {
      applied = await applyTouchpad(undefined, {onWait: showWait});
      break;
    } catch (error) {
      if (!error.retryable || attempt === 3) throw error;
      showWait('Retrying touchpad setup', attempt * 1000);
      await new Promise(resolve => setTimeout(resolve, attempt * 1000));
    }
  }
  clearInterval(countdown);
  await chrome.storage.local.set({
    crosTouchpadResult: {...applied, appliedAt: new Date().toISOString()},
  });
  status.textContent = 'Both touchpad settings were applied and verified.';
  result.textContent = `${applied.name}\nCurrent device ID: ${applied.id}\n` +
    'Change Timeout: 0\nMotion Tap Prevent Timeout: 0';
  if (!new URL(location.href).searchParams.has('review')) {
    const tab = await chrome.tabs.getCurrent();
    await chrome.tabs.remove(tab.id);
  }
} catch (error) {
  document.title = 'Touchpad setup failed';
  status.textContent = 'Touchpad setup could not finish. Reload this page to retry.';
  result.textContent = error.message;
} finally {
  clearInterval(countdown);
}
