import {applyTouchpad} from './apply.mjs';

const status = document.getElementById('status');
const result = document.getElementById('result');
try {
  const applied = await applyTouchpad();
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
}
