// Saves options to chrome.storage.sync
function saveOptions(): void {
    const checkbox = document.getElementById('snap-fix-checkbox') as HTMLInputElement;
    const avoidSnap = checkbox.checked;

    chrome.storage.sync.set({
        avoidChromeOSSnap: avoidSnap
    }, () => {
        // Update status to let user know options were saved.
        const status = document.getElementById('status') as HTMLDivElement;
        status.textContent = 'Options saved.';
        status.style.opacity = '1';
        setTimeout(() => {
            status.style.opacity = '0';
        }, 1500);
    });
}

// Restores checkbox state using the preferences stored in chrome.storage.
function restoreOptions(): void {
    // Default to 'true' if no value is stored yet.
    chrome.storage.sync.get({
        avoidChromeOSSnap: true
    }, (items) => {
        const checkbox = document.getElementById('snap-fix-checkbox') as HTMLInputElement;
        checkbox.checked = items.avoidChromeOSSnap;
    });
}

document.addEventListener('DOMContentLoaded', restoreOptions);
document.getElementById('snap-fix-checkbox')?.addEventListener('change', saveOptions);
