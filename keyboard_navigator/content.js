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

    let precomputedFragment = null;
    let precomputedHintMap = {};
    let isShiftDown = false;

    function prepareHints() {
        precomputedHintMap = {};
        precomputedFragment = document.createDocumentFragment();
        let hintCount = 0;

        const scrollX = window.scrollX;
        const scrollY = window.scrollY;

        visibleTargets.forEach(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return;

            const code = getLabel(hintCount++);
            const span = document.createElement('span');
            span.className = 'kb-nav-hint';
            span.innerText = code;
            span.style.top = (rect.top + scrollY) + 'px';
            span.style.left = (rect.left + scrollX) + 'px';

            precomputedFragment.appendChild(span);
            precomputedHintMap[code] = el;
        });
    }

    function deactivateHints() {
        hintContainer.innerHTML = '';
        hintsActive = false;
        hintMap = {};
    }

    function activateHints() {
        if (precomputedFragment) {
            hintMap = precomputedHintMap;
            hintContainer.appendChild(precomputedFragment);
            hintsActive = true;
            precomputedFragment = null;
            precomputedHintMap = {};
        }
    }

    window.addEventListener('keydown', (e) => {
        if (e.key === 'Shift') {
            if (!isShiftDown) {
                isShiftDown = true;
                if (!hintsActive) prepareHints();
            }
            return;
        }

        if (hintsActive) {
            const char = e.key.toUpperCase();
            if (hintMap[char]) {
                hintMap[char].click();
                if (hintMap[char].tagName === 'INPUT') {
                    hintMap[char].focus();
                }
                deactivateHints();
            } else if (e.key === 'Escape') {
                deactivateHints();
            }
            e.preventDefault();
            e.stopPropagation();
        } else {
            otherKeyPressed = true;
        }
    }, true);

    window.addEventListener('keyup', (e) => {
        if (e.key === 'Shift') {
            isShiftDown = false;
            if (!otherKeyPressed) {
                if (hintsActive) {
                    deactivateHints();
                } else {
                    activateHints();
                }
            }
            otherKeyPressed = false;
        }
    });

    console.log("Keyboard Navigator Optimized: Tap 'Shift' to steer.");
})();
