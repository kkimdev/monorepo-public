(function() {
    let hintsActive = false;
    let otherKeyPressed = false;
    let hintMap = {};
    const hintContainer = document.createElement('div');
    hintContainer.id = 'kb-nav-container';
    document.body.appendChild(hintContainer);

    function getLabel(i) {
        let label = "";
        while (i >= 0) { label = String.fromCharCode((i % 26) + 97) + label; i = Math.floor(i / 26) - 1; }
        return label.toUpperCase();
    }

    function toggleNav() {
        if (hintsActive) {
            hintContainer.innerHTML = '';
            hintsActive = false;
            return;
        }
        hintMap = {};
        const targets = document.querySelectorAll('a, button, input, [role="button"], [onclick], [tabindex="0"]');
        let hintCount = 0;
        targets.forEach((el) => {
            const r = el.getBoundingClientRect();
            if (r.top < 0 || r.bottom > window.innerHeight || r.width === 0 || r.height === 0) return;

            const code = getLabel(hintCount++);
            const span = document.createElement('span');
            span.className = 'kb-nav-hint';
            span.innerText = code;
            span.style.top = (r.top + window.scrollY) + 'px';
            span.style.left = (r.left + window.scrollX) + 'px';
            hintContainer.appendChild(span);
            hintMap[code] = el;
        });
        hintsActive = true;
    }

    window.addEventListener('keydown', (e) => {
        if (e.key === 'Shift') return;
        if (hintsActive) {
            const char = e.key.toUpperCase();
            if (hintMap[char]) {
                hintMap[char].click();
                if (hintMap[char].tagName === 'INPUT') {
                    hintMap[char].focus();
                }
                toggleNav(); // Turn off after click
            } else if (e.key === 'Escape') {
                toggleNav();
            }
            e.preventDefault();
            e.stopPropagation();
        } else {
            otherKeyPressed = true;
        }
    }, true);

    window.addEventListener('keyup', (e) => {
        if (e.key === 'Shift' && !otherKeyPressed) toggleNav();
        if (e.key === 'Shift') otherKeyPressed = false;
    });

    console.log("Keyboard Navigator Active: Tap 'Shift' to steer.");
})();
