// Local addition to the official Secure Shell development build.
async function startTouchpad(review = false) {
  const url = chrome.runtime.getURL('touchpad/page.html') + (review ? '?review' : '');
  const [window] = await chrome.windows.getAll({windowTypes: ['normal']});
  if (window) {
    await chrome.tabs.create({windowId: window.id, url, active: review});
  } else {
    // ChromeOS login can start the profile before any browser window exists.
    await chrome.windows.create({url, type: 'popup', focused: review});
  }
}

const start = (review) => startTouchpad(review).catch(
    error => console.error('Could not start touchpad setup:', error));
chrome.runtime.onStartup.addListener(() => start(false));
chrome.runtime.onInstalled.addListener(({reason}) => {
  if (reason === 'install' || reason === 'update') return start(true);
});
