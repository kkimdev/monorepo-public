(function() {
    let hintsActive = false;
    let otherKeyPressed = false;
    let hintMap = {};
    const hintContainer = document.createElement('div');
    hintContainer.id = 'kb-nav-container';

    let initialized = false;
    function tryInit() {
        if (initialized || !document.body) return;
        document.body.appendChild(hintContainer);
        mutationObserver.observe(document.body, { childList: true, subtree: true });
        updateTargets();
        initialized = true;
    }

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

    let activationMode = 'SAME_TAB'; // 'SAME_TAB' or 'NEW_TAB'
    let labelMap = new WeakMap();
    let elementToHintMap = new Map(); // Element -> Span
    let motionState = new WeakMap(); // Element -> { lastTop, lastScrollY, mode: 'unknown' | 'scrolling' | 'fixed' }
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
        if (!document.body) return;
        let found = Array.from(document.querySelectorAll('a, button, input, textarea, select, [role="button"], [role="link"], [role="checkbox"], [role="menuitem"], [onclick], [tabindex="0"], [contenteditable="true"], [role="textbox"]'));

        // Filter out targets that are descendants of other targets to avoid redundant hints
        const selector = 'a, button, [role="button"], [role="link"]';
        found = found.filter(el => !el.parentElement || !el.parentElement.closest(selector));

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
                motionState.delete(el);
            }
        }
    };

    let updateTimeout;
    const debouncedUpdate = () => {
        clearTimeout(updateTimeout);
        updateTimeout = setTimeout(updateTargets, 200);
    };

    let refreshRequested = false;
    const debouncedRefresh = () => {
        if (!hintsActive || refreshRequested) return;
        refreshRequested = true;
        requestAnimationFrame(() => {
            refreshRequested = false;
            refreshVisibleHints();
        });
    };

    const mutationObserver = new MutationObserver(debouncedUpdate);

    // Attempt init as early as possible
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', tryInit);
    } else {
        tryInit();
    }

    let isShiftDown = false;
    let lastMouseX = 0;
    let lastMouseY = 0;

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

            // Pre-prepare targets and hints in the background
            hintContainer.style.display = 'none';
            updateTargets();
            refreshVisibleHints();
        }
    }

    function normalizeUrl(urlStr) {
        if (!urlStr) return "";
        try {
            const url = new URL(urlStr, window.location.origin);
            let path = url.origin + url.pathname;
            // GitHub specific: Treat /blob/, /edit/, /tree/, /raw/ as the same for de-duplication
            path = path.replace(/\/(blob|edit|tree|raw|blame)\/[^/]+\//, '/.../');
            return path + url.search + url.hash;
        } catch (e) {
            return urlStr;
        }
    }

    function refreshVisibleHints() {
        if (!hintsActive) return;

        const scrollX = window.scrollX;
        const scrollY = window.scrollY;

        // --- READ PHASE ---
        // Batch getBoundingClientRect and textContent to avoid layout thrashing
        const rects = new Map();
        const textCache = new Map();
        const targetsToProcess = Array.from(visibleTargets);

        targetsToProcess.forEach(el => {
            rects.set(el, el.getBoundingClientRect());
            textCache.set(el, el.textContent.trim());
        });

        // --- PROCESSING PHASE ---
        const seenUrls = new Map();
        const sortedTargets = targetsToProcess.sort((a, b) => {
            const aText = textCache.get(a);
            const bText = textCache.get(b);
            if (aText && !bText) return 1;
            if (!aText && bText) return -1;
            return 0;
        });

        // --- WRITE PHASE ---
        sortedTargets.forEach(el => {
            let code = labelMap.get(el);
            const rect = rects.get(el);

            if (rect.width === 0 || rect.height === 0) {
                const existing = elementToHintMap.get(el);
                if (existing) {
                    existing.remove();
                    elementToHintMap.delete(el);
                }
                return;
            };

            // Advanced de-duplication for links
            const href = el.href || el.getAttribute('href');
            if ((el.tagName === 'A' || el.getAttribute('role') === 'link') && href) {
                const normalized = normalizeUrl(href);
                const existing = seenUrls.get(normalized);

                if (existing) {
                    const eRect = rects.get(existing);
                    const dx = rect.left - eRect.left;
                    const dy = rect.top - eRect.top;
                    const dist = Math.sqrt(dx*dx + dy*dy);

                    if (dist < 300 || Math.abs(dy) < 20) {
                        const existingSpan = elementToHintMap.get(el);
                        if (existingSpan) {
                            existingSpan.remove();
                            elementToHintMap.delete(el);
                        }
                        return;
                    }
                }
                seenUrls.set(normalized, el);
            }

            if (!code) {
                code = getLabel(nextLabelIndex++, currentLabelLength);
                labelMap.set(el, code);
                hintMap[code] = el;
            }

            let span = elementToHintMap.get(el);
            let state = motionState.get(el);
            const targetTop = rect.top;
            const targetLeft = rect.left;

            if (!span) {
                span = document.createElement('span');
                span.className = 'kb-nav-hint';
                span.dataset.code = code;
                span.innerText = code;
                state = { lastTop: targetTop, lastScrollY: scrollY, mode: 'unknown' };
                motionState.set(el, state);
                span.style.position = 'absolute';
                span.style.top = (targetTop + scrollY) + 'px';
                span.style.left = (targetLeft + scrollX) + 'px';
                elementToHintMap.set(el, span);
                hintContainer.appendChild(span);
            } else {
                const deltaScroll = scrollY - state.lastScrollY;
                if (Math.abs(deltaScroll) > 2) {
                    const deltaTop = targetTop - state.lastTop;
                    let currentBehavior = 'unknown';
                    if (Math.abs(deltaTop) < 1) {
                        currentBehavior = 'fixed';
                    } else if (Math.abs(deltaTop + deltaScroll) < 2) {
                        currentBehavior = 'scrolling';
                    }
                    if (currentBehavior !== 'unknown' && currentBehavior !== state.mode) {
                        state.mode = currentBehavior;
                    }

                    if (state.mode === 'fixed') {
                        span.style.position = 'fixed';
                        span.style.top = targetTop + 'px';
                        span.style.left = targetLeft + 'px';
                    } else {
                        span.style.position = 'absolute';
                        span.style.top = (targetTop + scrollY) + 'px';
                        span.style.left = (targetLeft + scrollX) + 'px';
                    }
                    state.lastTop = targetTop;
                    state.lastScrollY = scrollY;
                }
            }

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

        // Cleanup
        elementToHintMap.forEach((span, el) => {
            if (!visibleTargets.has(el)) {
                span.remove();
                elementToHintMap.delete(el);
                motionState.delete(el);
            }
        });
    }

    function deactivateHints() {
        hintContainer.style.display = 'none';
        hintContainer.innerHTML = '';
        hintsActive = false;
        hintMap = {};
        elementToHintMap.clear();
        typingBuffer = "";
    }

    function activateHints(mode = 'SAME_TAB') {
        hintsActive = true;
        activationMode = mode;
        typingBuffer = "";

        // Show pre-prepared hints
        hintContainer.style.display = 'block';
        refreshVisibleHints(); // Re-sync positions just in case
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
                if (hintsActive) {
                    deactivateHints();
                    otherKeyPressed = true; // Prevent re-activation on keyup
                } else {
                    otherKeyPressed = false;
                    prepareHints();
                }
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
                        if (activationMode === 'NEW_TAB' && targetEl.tagName === 'A' && targetEl.href) {
                            window.open(targetEl.href, '_blank');
                        } else {
                            targetEl.click();
                            const focusTags = ['INPUT', 'TEXTAREA', 'SELECT'];
                            if (focusTags.includes(targetEl.tagName) ||
                                targetEl.contentEditable === 'true' ||
                                targetEl.getAttribute('role') === 'textbox') {
                                targetEl.focus();
                            }
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
                    const mode = (e.code === 'ShiftRight') ? 'NEW_TAB' : 'SAME_TAB';
                    activateHints(mode);
                }
            }
            otherKeyPressed = false;
        }
    });

    window.addEventListener('blur', () => {
        isShiftDown = false;
        otherKeyPressed = false;
        if (hintsActive) deactivateHints();
    });

    // Prevent accidental trigger during Shift+Mouse/Touch actions (selection, drag, scroll, etc.)
    ['mousedown', 'wheel', 'touchstart', 'touchmove'].forEach(type => {
        window.addEventListener(type, () => {
            if (isShiftDown) otherKeyPressed = true;
        }, { passive: true });
    });

    window.addEventListener('mousemove', (e) => {
        if (isShiftDown && !otherKeyPressed) {
            const dx = Math.abs(e.screenX - lastMouseX);
            const dy = Math.abs(e.screenY - lastMouseY);
            // Threshold (3px) to avoid suppression by minor mouse jitter
            if (dx > 3 || dy > 3) {
                otherKeyPressed = true;
            }
        }
        lastMouseX = e.screenX;
        lastMouseY = e.screenY;
    }, { passive: true });

    console.log("Keyboard Navigator Prefix-Free: Tap 'Shift' to steer.");
})();
