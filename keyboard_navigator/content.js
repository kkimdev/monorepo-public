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

    let labelMap = new WeakMap();
    let elementToHintMap = new Map(); // Element -> Span
    let motionState = new WeakMap(); // Element -> { initialTop, initialScrollY, mode: 'unknown' | 'scrolling' | 'fixed' }
    let nextLabelIndex = 0;

    // Track visibility of elements
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                visibleTargets.add(entry.target);
            } else {
                visibleTargets.delete(entry.target);
            }
        });
        if (hintsActive) debouncedRefresh();
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
                elementToHintMap.delete(el);
            }
        }
    };

    let updateTimeout;
    const debouncedUpdate = () => {
        clearTimeout(updateTimeout);
        updateTimeout = setTimeout(updateTargets, 200);
    };

    let refreshTimeout;
    const debouncedRefresh = () => {
        clearTimeout(refreshTimeout);
        refreshTimeout = setTimeout(refreshVisibleHints, 16); // Faster for detection
    };

    // Initial scan and MutationObserver for dynamic content
    updateTargets();
    const mutationObserver = new MutationObserver(debouncedUpdate);
    mutationObserver.observe(document.body, { childList: true, subtree: true });

    let isShiftDown = false;

    function prepareHints() {
        // Clear previous session data if not active
        if (!hintsActive) {
            labelMap = new WeakMap();
            elementToHintMap.clear();
            motionState = new WeakMap();
            nextLabelIndex = 0;
            hintMap = {};

            // Stable length based on total potential targets
            const totalCount = targets.size;
            let length = 1;
            while (Math.pow(26, length) < totalCount) length++;
            currentLabelLength = length;
        }
    }

    function refreshVisibleHints() {
        if (!hintsActive) return;

        const scrollX = window.scrollX;
        const scrollY = window.scrollY;

        // Update/create hints for visible elements
        visibleTargets.forEach(el => {
            let code = labelMap.get(el);
            if (!code) {
                code = getLabel(nextLabelIndex++, currentLabelLength);
                labelMap.set(el, code);
                hintMap[code] = el;
            }

            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) {
                // Remove if hidden
                const existing = elementToHintMap.get(el);
                if (existing) {
                    existing.remove();
                    elementToHintMap.delete(el);
                }
                return;
            };

            let span = elementToHintMap.get(el);
            let state = motionState.get(el);

            if (!span) {
                span = document.createElement('span');
                span.className = 'kb-nav-hint';
                span.dataset.code = code;
                span.innerText = code;

                // Initial state: assume unknown, use absolute
                state = {
                    lastTop: rect.top,
                    lastScrollY: scrollY,
                    mode: 'unknown'
                };
                motionState.set(el, state);

                span.style.position = 'absolute';
                span.style.top = (rect.top + scrollY) + 'px';
                span.style.left = (rect.left + scrollX) + 'px';

                elementToHintMap.set(el, span);
                hintContainer.appendChild(span);
            } else {
                // Continuous Motion Re-evaluation
                const deltaScroll = scrollY - state.lastScrollY;
                if (Math.abs(deltaScroll) > 2) { // Threshold for significant movement
                    const deltaTop = rect.top - state.lastTop;

                    // Detect current behavior
                    let currentBehavior = 'unknown';
                    if (Math.abs(deltaTop) < 1) {
                        currentBehavior = 'fixed'; // Viewport stable -> fixed/sticky
                    } else if (Math.abs(deltaTop + deltaScroll) < 2) {
                        currentBehavior = 'scrolling'; // Document stable -> scrolling
                    }

                    // Handle behavior transitions
                    if (currentBehavior !== 'unknown' && currentBehavior !== state.mode) {
                        state.mode = currentBehavior;
                        if (state.mode === 'fixed') {
                            span.style.position = 'fixed';
                            span.style.top = rect.top + 'px';
                            span.style.left = rect.left + 'px';
                        } else {
                            span.style.position = 'absolute';
                            span.style.top = (rect.top + scrollY) + 'px';
                            span.style.left = (rect.left + scrollX) + 'px';
                        }
                    } else if (state.mode === 'fixed') {
                        // Keep fixed hints updated to viewport if they move slightly (e.g. horizontal scroll)
                        span.style.top = rect.top + 'px';
                        span.style.left = rect.left + 'px';
                    }

                    state.lastTop = rect.top;
                    state.lastScrollY = scrollY;
                }
            }

            // Restore matching visuals if buffer exists
            if (typingBuffer && code.startsWith(typingBuffer)) {
                const matched = code.slice(0, typingBuffer.length);
                const remaining = code.slice(typingBuffer.length);
                span.innerHTML = `<span class="kb-nav-hint-match">${matched}</span>${remaining}`;
                span.classList.remove('kb-nav-hint-filtered');
            } else {
                span.innerText = code;
                if (typingBuffer) {
                    span.classList.add('kb-nav-hint-filtered');
                } else {
                    span.classList.remove('kb-nav-hint-filtered');
                }
            }
        });

        // Remove hints for elements no longer in visibleTargets
        elementToHintMap.forEach((span, el) => {
            if (!visibleTargets.has(el)) {
                span.remove();
                elementToHintMap.delete(el);
                motionState.delete(el); // Clean up motion state
            }
        });
    }

    function deactivateHints() {
        hintContainer.innerHTML = '';
        hintsActive = false;
        hintMap = {};
        elementToHintMap.clear();
        typingBuffer = "";
    }

    function activateHints() {
        hintsActive = true;
        typingBuffer = "";
        refreshVisibleHints();
    }

    const allowedNavigationKeys = new Set([
        'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
        'PageUp', 'PageDown', 'Home', 'End', ' ', 'Tab'
    ]);

    window.addEventListener('scroll', () => {
        if (hintsActive) debouncedRefresh();
    }, { passive: true });

    function isAnyVisibleHintMatching(prefix) {
        for (const el of elementToHintMap.keys()) {
            const code = labelMap.get(el);
            if (code && code.startsWith(prefix)) return true;
        }
        return false;
    }

    window.addEventListener('keydown', (e) => {
        if (e.key === 'Shift') {
            if (!isShiftDown) {
                isShiftDown = true;
                otherKeyPressed = false;
                prepareHints();
            }
            return;
        }

        if (hintsActive) {
            // Allow navigation keys to pass through
            if (allowedNavigationKeys.has(e.key)) {
                return;
            }

            if (e.key === 'Escape') {
                deactivateHints();
            } else if (e.key === 'Backspace') {
                typingBuffer = typingBuffer.slice(0, -1);
                updateHintFiltering();
            } else if (e.key.length === 1 && /^[a-zA-Z]$/.test(e.key)) {
                const newBuffer = typingBuffer + e.key.toUpperCase();

                // Validate against visible hints
                if (!isAnyVisibleHintMatching(newBuffer)) {
                    deactivateHints();
                    return; // Dismiss and allow native key behavior
                }

                typingBuffer = newBuffer;
                updateHintFiltering();

                if (typingBuffer.length === currentLabelLength) {
                    const targetEl = hintMap[typingBuffer];
                    // Only click if it's currently visible
                    if (targetEl && elementToHintMap.has(targetEl)) {
                        targetEl.click();
                        if (targetEl.tagName === 'INPUT') {
                            targetEl.focus();
                        }
                    }
                    deactivateHints();
                }
            } else {
                // Any other key dismisses mode immediatly
                deactivateHints();
                return; // Let native behavior happen
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
