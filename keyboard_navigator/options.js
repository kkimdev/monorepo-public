document.addEventListener('DOMContentLoaded', () => {
    const bgColor = document.getElementById('bgColor');
    const bgText = document.getElementById('bgText');
    const accentColor = document.getElementById('accentColor');
    const accentText = document.getElementById('accentText');
    const status = document.getElementById('status');

    // Load saved settings
    chrome.storage.local.get(['bgColor', 'accentColor'], (result) => {
        const bg = result.bgColor || '#fff176';
        const accent = result.accentColor || '#ffd700';

        bgColor.value = bg;
        bgText.value = bg.toUpperCase();
        accentColor.value = accent;
        accentText.value = accent.toUpperCase();
    });

    // Update text inputs when color picker moves
    bgColor.addEventListener('input', () => { bgText.value = bgColor.value.toUpperCase(); });
    accentColor.addEventListener('input', () => { accentText.value = accentColor.value.toUpperCase(); });

    // Save settings
    document.getElementById('save').addEventListener('click', () => {
        chrome.storage.local.set({
            bgColor: bgColor.value,
            accentColor: accentColor.value
        }, () => {
            status.textContent = 'Settings saved successfully!';
            setTimeout(() => { status.textContent = ''; }, 2000);
        });
    });

    // Reset settings
    document.getElementById('reset').addEventListener('click', () => {
        const defaultBg = '#fff176';
        const defaultAccent = '#ffd700';

        bgColor.value = defaultBg;
        bgText.value = defaultBg.toUpperCase();
        accentColor.value = defaultAccent;
        accentText.value = defaultAccent.toUpperCase();

        chrome.storage.local.set({
            bgColor: defaultBg,
            accentColor: defaultAccent
        }, () => {
            status.textContent = 'Restored defaults!';
            setTimeout(() => { status.textContent = ''; }, 2000);
        });
    });
});
