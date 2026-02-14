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

    let typingBuffer = "";
    let currentLabelLength = 1;

    const getLabel = (i, length) => {
        let label = "";
        let temp = i;
        for (let j = 0; j < length; j++) {
            label = String.fromCharCode((temp % 26) + 65) + label;
            temp = Math.floor(temp / 26);
        }
        return label;
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

        const count = visibleTargets.size;
        let length = 1;
        while (Math.pow(26, length) < count) length++;
        currentLabelLength = length;

        const scrollX = window.scrollX;
        const scrollY = window.scrollY;

        let hintCount = 0;
        visibleTargets.forEach(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return;

            const code = getLabel(hintCount++, currentLabelLength);
            const span = document.createElement('span');
            span.className = 'kb-nav-hint';
            span.dataset.code = code;
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
        typingBuffer = "";
    }

    function activateHints() {
        if (precomputedFragment) {
            hintMap = precomputedHintMap;
            hintContainer.appendChild(precomputedFragment);
            hintsActive = true;
            precomputedFragment = null;
            precomputedHintMap = {};
            typingBuffer = "";
        }
    }

    window.addEventListener('keydown', (e) => {
        if (e.key === 'Shift') {
            if (!isShiftDown) {
                isShiftDown = true;
                otherKeyPressed = false;
                if (!hintsActive) prepareHints();
            }
            return;
        }

        if (hintsActive) {
            if (e.key === 'Escape') {
                deactivateHints();
            } else if (e.key === 'Backspace') {
                typingBuffer = typingBuffer.slice(0, -1);
                updateHintFiltering();
            } else if (e.key.length === 1 && /^[a-zA-Z]$/.test(e.key)) {
                typingBuffer += e.key.toUpperCase();
                updateHintFiltering();

                if (typingBuffer.length === currentLabelLength) {
                    if (hintMap[typingBuffer]) {
                        hintMap[typingBuffer].click();
                        if (hintMap[typingBuffer].tagName === 'INPUT') {
                            hintMap[typingBuffer].focus();
                        }
                    }
                    deactivateHints();
                }
            }
            e.preventDefault();
            e.stopPropagation();
        } else if (isShiftDown) {
            otherKeyPressed = true;
        }
    }, true);

    function updateHintFiltering() {
        const hints = hintContainer.querySelectorAll('.kb-nav-hint');
        hints.forEach(hint => {
            const code = hint.dataset.code;
            if (code.startsWith(typingBuffer)) {
                hint.classList.remove('kb-nav-hint-filtered');
                const matched = code.slice(0, typingBuffer.length);
                const remaining = code.slice(typingBuffer.length);
                hint.innerHTML = `<span class="kb-nav-hint-match">${matched}</span>${remaining}`;
            } else {
                hint.classList.add('kb-nav-hint-filtered');
            }
        });
    }

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

    console.log("Keyboard Navigator Prefix-Free: Tap 'Shift' to steer.");
})();
