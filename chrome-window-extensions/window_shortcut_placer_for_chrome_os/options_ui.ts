chrome.tabs.getCurrent((tab?: chrome.tabs.Tab) => {
    chrome.tabs.remove(tab.id);
});

chrome.tabs.create({ url: "chrome://extensions/shortcuts" });
