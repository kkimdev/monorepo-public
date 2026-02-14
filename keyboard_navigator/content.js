(function() {
    let hintsActive = false;
    let otherKeyPressed = false;
    let hintMap = {};
    const hintContainer = document.createElement('div');
    hintContainer.id = 'kb-nav-container';
    document.body.appendChild(hintContainer);

    // Optimized state tracking
    const targets = new Set();
    const visibleTargets = new Set();

    const getLabel = (i) => {
        let label = "";
        while (i >= 0) {
            label = String.fromCharCode((i % 26) + 97) + label;
            i = Math.floor(i / 26) - 1;
        }
        return label.toUpperCase();
    };

    // Track visibility of elements
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                visibleTargets.add(entry.target);
            } else {
                visibleTargets.delete(entry.target);
            }
        });
    }, { threshold: 0.1 });

    const updateTargets = () => {
        const found = document.querySelectorAll('a, button, input, [role="button"], [onclick], [tabindex="0"]');
        found.forEach(el => {
            if (!targets.has(el)) {
                targets.add(el);
                observer.observe(el);
            }
        });

        // Cleanup elements no longer in DOM
        for (const el of targets) {
            if (!document.body.contains(el)) {
                targets.delete(el);
                visibleTargets.delete(el);
                observer.unobserve(el);
            }
        }
    };

    let updateTimeout;
    const debouncedUpdate = () => {
        clearTimeout(updateTimeout);
        updateTimeout = setTimeout(updateTargets, 200);
    };

    // Initial scan and MutationObserver for dynamic content
    updateTargets();
    const mutationObserver = new MutationObserver(debouncedUpdate);
    mutationObserver.observe(document.body, { childList: true, subtree: true });

    function toggleNav() {
        if (hintsActive) {
            hintContainer.innerHTML = '';
            hintsActive = false;
            return;
        }

        hintMap = {};
        const fragment = document.createDocumentFragment();
        let hintCount = 0;

        const scrollX = window.scrollX;
        const scrollY = window.scrollY;

        // visibleTargets is already populated by IntersectionObserver
        visibleTargets.forEach(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return;

            const code = getLabel(hintCount++);
            const span = document.createElement('span');
            span.className = 'kb-nav-hint';
            span.innerText = code;
            span.style.top = (rect.top + scrollY) + 'px';
            span.style.left = (rect.left + scrollX) + 'px';

            fragment.appendChild(span);
            hintMap[code] = el;
        });

        hintContainer.appendChild(fragment);
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
                toggleNav();
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

    console.log("Keyboard Navigator Optimized: Tap 'Shift' to steer.");
})();
