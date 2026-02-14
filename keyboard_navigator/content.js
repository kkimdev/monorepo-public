(function() {
    const hexToRgba = (hex, opacity) => {
        let r = parseInt(hex.slice(1, 3), 16);
        let g = parseInt(hex.slice(3, 5), 16);
        let b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r}, ${g}, ${b}, ${opacity})`;
    };

    const applyColors = (bgColor, accentColor) => {
        document.documentElement.style.setProperty('--kb-nav-bg', hexToRgba(bgColor, 0.85));
        document.documentElement.style.setProperty('--kb-nav-accent', accentColor);
    };

    // Load initial colors
    if (typeof chrome !== 'undefined' && chrome.storage) {
        chrome.storage.local.get(['bgColor', 'accentColor'], (result) => {
            const bg = result.bgColor || '#fff176';
            const accent = result.accentColor || '#ffd700';
            applyColors(bg, accent);
        });

        // Listen for changes
        chrome.storage.onChanged.addListener(() => {
            chrome.storage.local.get(['bgColor', 'accentColor'], (result) => {
                const bg = result.bgColor || '#fff176';
                const accent = result.accentColor || '#ffd700';
                applyColors(bg, accent);
            });
        });
    }

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

    // Object Pooling for performance
    const spanPool = [];
    function getSpanFromPool() {
        const span = spanPool.pop() || document.createElement('span');
        span.className = 'kb-nav-hint';
        span.style.display = 'inline-block';
        return span;
    }
    function releaseSpanToPool(span) {
        span.style.display = 'none';
        span.innerHTML = '';
        spanPool.push(span);
    }

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
    }, { threshold: 0, rootMargin: '200px' });

    const updateTargets = () => {
        if (!document.body) return;
        const selectors = 'a, button, input, textarea, select, label, summary, [role="button"], [role="link"], [role="checkbox"], [role="menuitem"], [onclick], [tabindex="0"], [contenteditable="true"], [role="textbox"], [hx-get], [hx-post], [hx-put], [hx-delete], [hx-patch]';
        let found = Array.from(document.querySelectorAll(selectors));

        // Optimized filtering: Only check parents for specific wrapper types
        const wrapperSelector = 'a, button, label, [role="button"], [role="link"]';
        found = found.filter(el => {
            let parent = el.parentElement;
            while (parent && parent !== document.body) {
                if (parent.matches(wrapperSelector)) return false;
                parent = parent.parentElement;
            }
            return true;
        });

        found.forEach(el => {
            if (!targets.has(el)) {
                targets.add(el);
                observer.observe(el);
            }
        });

        // Periodic visibility cleanup for active hints
        if (hintsActive) {
            targets.forEach(el => {
                const state = motionState.get(el);
                if (state) state.isVisible = isElementVisible(el);
            });
        }

        ensureLabelCapacity();

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

    function ensureLabelCapacity() {
        if (hintsActive) return;

        const totalCount = targets.size;
        let length = 1;
        while (Math.pow(26, length) < totalCount) length++;

        if (length !== currentLabelLength) {
            currentLabelLength = length;
            labelMap = new WeakMap();
            hintMap = {};
            nextLabelIndex = 0;
            elementToHintMap.forEach((span) => releaseSpanToPool(span));
            elementToHintMap.clear();
        }
    }

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

    function isElementVisible(el) {
        // Modern API check (Chrome 105+)
        if (typeof el.checkVisibility === 'function') {
            return el.checkVisibility({
                checkOpacity: true,
                checkVisibilityCSS: true
            });
        }

        // Fallback for older browsers or specific edge cases
        const style = window.getComputedStyle(el);
        return style.display !== 'none' &&
               style.visibility !== 'hidden' &&
               style.opacity !== '0' &&
               style.pointerEvents !== 'none';
    }

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

            // Pre-prepare targets and hints in the background
            hintContainer.style.display = 'none';
            updateTargets();
            ensureLabelCapacity();

            refreshVisibleHints(true);
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

    function refreshVisibleHints(force = false, isScrolling = false) {
        if (!hintsActive && !force) return;

        const scrollX = window.scrollX;
        const scrollY = window.scrollY;

        // --- READ PHASE ---
        // Batch getBoundingClientRect and textContent to avoid layout thrashing
        const rects = new Map();
        const textCache = new Map();
        const targetsToProcess = Array.from(visibleTargets);

        targetsToProcess.forEach(el => {
            let state = motionState.get(el);

            // Optimization: Skip DOM reads for static elements during scroll
            if (isScrolling && state && state.mode === 'static') return;

            const rect = el.getBoundingClientRect();

            // Performance: During scroll, we trust the cached visibility from updateTargets or initial render
            // to avoid layout thrashing. We only re-check if we don't have a state yet.
            const isVisible = (state && typeof state.isVisible !== 'undefined') ? state.isVisible : isElementVisible(el);

            if (rect.width > 0 && rect.height > 0 && isVisible) {
                rects.set(el, rect);
                if (!isScrolling) textCache.set(el, el.textContent.trim());
                if (state) state.isVisible = true;
            } else {
                rects.set(el, null);
                if (state) state.isVisible = false;
            }
        });

        // --- PROCESSING PHASE ---
        const seenUrls = new Map();
        let sortedTargets = targetsToProcess;
        if (!isScrolling) {
            sortedTargets = targetsToProcess.sort((a, b) => {
                const aText = textCache.get(a);
                const bText = textCache.get(b);
                if (aText && !bText) return 1;
                if (!aText && bText) return -1;
                return 0;
            });
        }

        // --- WRITE PHASE ---
        sortedTargets.forEach(el => {
            let span = elementToHintMap.get(el);
            let state = motionState.get(el);

            // Skip ALL logic for static elements during scroll
            if (isScrolling && span && state && state.mode === 'static') return;

            let code = labelMap.get(el);
            const rect = rects.get(el);

            if (!rect || rect.width === 0 || rect.height === 0) {
                const existing = elementToHintMap.get(el);
                if (existing) {
                    releaseSpanToPool(existing);
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
                    if (eRect) {
                        const dx = rect.left - eRect.left;
                        const dy = rect.top - eRect.top;
                        const dist = Math.sqrt(dx*dx + dy*dy);

                        if (dist < 300 || Math.abs(dy) < 20) {
                            if (span) {
                                releaseSpanToPool(span);
                                elementToHintMap.delete(el);
                            }
                            return;
                        }
                    }
                }
                seenUrls.set(normalized, el);
            }

            if (!code) {
                code = getLabel(nextLabelIndex++, currentLabelLength);
                labelMap.set(el, code);
                hintMap[code] = el;
            }

            // Span/state already defined at top of loop
            if (!rect || rect.width === 0 || rect.height === 0) {
                if (span) {
                    releaseSpanToPool(span);
                    elementToHintMap.delete(el);
                }
                return;
            };

            const targetTop = rect.top;
            const targetLeft = rect.left;
            const docTop = targetTop + scrollY;
            const docLeft = targetLeft + scrollX;

            if (!span) {
                span = getSpanFromPool();
                span.dataset.code = code;
                span.innerText = code;
                // Assume static by default for immediate anchoring
                state = { docTop, docLeft, lastTop: targetTop, lastScrollY: scrollY, mode: 'static' };
                motionState.set(el, state);
                span.style.display = 'inline-block';
                span.style.transform = `translate3d(${Math.round(docLeft)}px, ${Math.round(docTop)}px, 0)`;
                elementToHintMap.set(el, span);
                hintContainer.appendChild(span);
            } else {
                const deltaScroll = scrollY - state.lastScrollY;
                if (Math.abs(deltaScroll) > 1) { // Only check if scroll actually moved
                    const deltaTop = targetTop - state.lastTop;
                    let currentBehavior = 'unknown';

                    if (Math.abs(deltaTop) < 0.1) {
                        currentBehavior = 'fixed';
                    } else if (Math.abs(deltaTop + deltaScroll) < 1) {
                        currentBehavior = 'static';
                    }

                    if (currentBehavior !== 'unknown') {
                        // If behavior changed, update mode
                        state.mode = currentBehavior;
                    }

                    // If it's fixed or we are unsure, we MUST update to stay in sync.
                    // If it's static, the loop-start optimization (line 292) handles skipping it.
                    if (state.mode !== 'static') {
                        span.style.transform = `translate3d(${Math.round(docLeft)}px, ${Math.round(docTop)}px, 0)`;
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
                el.classList.add('kb-nav-target-highlight');
            } else {
                span.innerText = code;
                if (typingBuffer) {
                    span.classList.add('kb-nav-hint-filtered');
                } else {
                    span.classList.remove('kb-nav-hint-filtered');
                }
                el.classList.remove('kb-nav-target-highlight');
            }
        });

        // Cleanup
        elementToHintMap.forEach((span, el) => {
            if (!visibleTargets.has(el)) {
                releaseSpanToPool(span);
                elementToHintMap.delete(el);
                motionState.delete(el);
            }
        });
    }

    function deactivateHints() {
        if (!hintsActive) return;

        hintContainer.classList.add('kb-nav-closing');

        // Wait for exit animation (150ms) before actual removal
        setTimeout(() => {
            hintContainer.style.display = 'none';
            // Recycle all spans instead of clearing innerHTML
            // Recycle all spans and cleanup targets
            elementToHintMap.forEach((span, el) => {
                releaseSpanToPool(span);
                el.classList.remove('kb-nav-target-highlight');
                el.classList.remove('kb-nav-clicked');
            });
            hintContainer.classList.remove('kb-nav-closing');
            hintsActive = false;
            hintMap = {};
            elementToHintMap.clear();
            typingBuffer = "";
        }, 150);
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
        if (hintsActive) refreshVisibleHints(false, true);
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
                    if (targetEl && elementToHintMap.has(targetEl)) {
                        targetEl.classList.add('kb-nav-clicked');

                        setTimeout(() => {
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
                            deactivateHints();
                        }, 200);
                    } else {
                        deactivateHints();
                    }
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
            const el = hintMap[code];
            if (code.startsWith(typingBuffer)) {
                hint.classList.remove('kb-nav-hint-filtered');
                const matched = code.slice(0, typingBuffer.length);
                const remaining = code.slice(typingBuffer.length);
                hint.innerHTML = `<span class="kb-nav-hint-match">${matched}</span>${remaining}`;
                if (typingBuffer && el) {
                    el.classList.add('kb-nav-target-highlight');
                } else if (el) {
                    el.classList.remove('kb-nav-target-highlight');
                }
            } else {
                hint.classList.add('kb-nav-hint-filtered');
                if (el) {
                    el.classList.remove('kb-nav-target-highlight');
                }
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
