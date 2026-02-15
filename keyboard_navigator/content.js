(function() {
    const hexToRgba = (hex, opacity) => {
        let r = parseInt(hex.slice(1, 3), 16);
        let g = parseInt(hex.slice(3, 5), 16);
        let b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r}, ${g}, ${b}, ${opacity})`;
    };

    const applyColors = (bgColor, accentColor) => {
        const bg = hexToRgba(bgColor, 0.85);
        document.documentElement.style.setProperty('--kb-nav-bg', bg);
        document.documentElement.style.setProperty('--kb-nav-accent', accentColor);

        // Also apply to Shadow Host if it exists
        const host = document.getElementById('kb-nav-host');
        if (host) {
            host.style.setProperty('--kb-nav-bg', bg);
            host.style.setProperty('--kb-nav-accent', accentColor);
        }
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
    let shadowRoot = null;
    function tryInit() {
        if (initialized || !document.body) return;

        const host = document.createElement('div');
        host.id = 'kb-nav-host';
        // Explicitly style the host to be document-relative and ignore site CSS
        host.style.cssText = 'position: absolute !important; top: 0 !important; left: 0 !important; width: 100% !important; height: 100% !important; pointer-events: none !important; z-index: 2147483647 !important; display: block !important; border: none !important; padding: 0 !important; margin: 0 !important;';
        document.body.appendChild(host);

        // Use closed shadow root for isolation
        shadowRoot = host.attachShadow({ mode: 'closed' });

        // Inject CSS via fetch + style tag (more reliable for CSP on many sites)
        if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.getURL) {
            fetch(chrome.runtime.getURL('content.css'))
                .then(res => res.text())
                .then(css => {
                    const sheet = document.createElement('style');
                    sheet.id = 'kb-nav-styles';
                    sheet.textContent = css;
                    shadowRoot.appendChild(sheet);

                    // Re-apply colors to sync with manifest defaults
                    chrome.storage.local.get(['bgColor', 'accentColor'], (result) => {
                        applyColors(result.bgColor || '#fff176', result.accentColor || '#ffd700');
                    });
                })
                .catch(err => {
                    console.warn("Keyboard Navigator: fetch failed, using fallback", err);
                    injectFallbackStyles();
                });
        } else {
            injectFallbackStyles();
        }

        function injectFallbackStyles() {
            // Check if already injected
            if (shadowRoot.querySelector('#kb-nav-styles-fallback')) return;

            const style = document.createElement('style');
            style.id = 'kb-nav-styles-fallback';
            const styleTags = Array.from(document.querySelectorAll('style, link[rel="stylesheet"]'));
            let cssText = '';

            // Try to find the content.css content if it was injected by manifest
            styleTags.forEach(st => {
                try {
                    if (st.tagName === 'STYLE' && st.textContent.includes('kb-nav-hint')) {
                        cssText += st.textContent;
                    } else if (st.tagName === 'LINK' && st.sheet) {
                        // For links, we might be able to read the rules if same-origin or CORS
                        Array.from(st.sheet.cssRules).forEach(rule => {
                            if (rule.cssText.includes('kb-nav-hint')) {
                                cssText += rule.cssText;
                            }
                        });
                    }
                } catch (e) {
                    // Ignore CORS restricted sheets
                }
            });

            if (cssText) {
                style.textContent = cssText;
                shadowRoot.appendChild(style);
            }
        }

        shadowRoot.appendChild(hintContainer);

        mutationObserver.observe(document.body, { childList: true, subtree: true });
        updateTargets();
        initialized = true;
    }

    // Optimized state tracking
    const targets = new Set();
    const visibleTargets = new Set();

    let typingBuffer = "";
    let currentLabelLength = 1;

    const charData = {
        'A': { h: 'L', r: 1 }, 'S': { h: 'L', r: 1 }, 'D': { h: 'L', r: 1 }, 'F': { h: 'L', r: 1 }, 'G': { h: 'L', r: 1 },
        'J': { h: 'R', r: 1 }, 'K': { h: 'R', r: 1 }, 'L': { h: 'R', r: 1 }, 'H': { h: 'R', r: 1 },
        'Q': { h: 'L', r: 0 }, 'W': { h: 'L', r: 0 }, 'E': { h: 'L', r: 0 }, 'R': { h: 'L', r: 0 }, 'T': { h: 'L', r: 0 },
        'Y': { h: 'R', r: 0 }, 'U': { h: 'R', r: 0 }, 'I': { h: 'R', r: 0 }, 'O': { h: 'R', r: 0 }, 'P': { h: 'R', r: 0 },
        'Z': { h: 'L', r: 2 }, 'X': { h: 'L', r: 2 }, 'C': { h: 'L', r: 2 }, 'V': { h: 'L', r: 2 }, 'B': { h: 'L', r: 2 },
        'N': { h: 'R', r: 2 }, 'M': { h: 'R', r: 2 }
    };

    const alphabet = Object.keys(charData);

    const calculateCost = (label) => {
        let cost = 0;
        for (let i = 0; i < label.length; i++) {
            const char = label[i];
            const data = charData[char];
            // Lower row index (home=1) is better
            cost += (data.r === 1 ? 0 : data.r === 0 ? 1 : 2);

            if (i > 0) {
                const prev = charData[label[i-1]];
                // Hand alternation is great
                if (data.h === prev.h) cost += 1.5;
                else cost -= 0.5;

                // Penalty for same key repetition
                if (char === label[i-1]) cost += 3.0;

                // Penalty for large row jumps
                cost += Math.abs(data.r - prev.r) * 0.5;
            }
        }
        return cost;
    };

    const sortedLabels = {};
    const getSortedLabels = (length) => {
        if (sortedLabels[length]) return sortedLabels[length];

        let results = [];
        const chars = alphabet;

        if (length === 1) {
            results = chars;
        } else if (length === 2) {
            for (const a1 of chars) {
                for (const a2 of chars) {
                    results.push(a1 + a2);
                }
            }
        } else {
            // Fallback for long rare labels: just sequential
            for (let i = 0; i < Math.pow(26, length); i++) {
                let l = "";
                let t = i;
                for (let j = 0; j < length; j++) {
                    l = String.fromCharCode((t % 26) + 65) + l;
                    t = Math.floor(t / 26);
                }
                results.push(l);
            }
            return results;
        }

        results.sort((a, b) => calculateCost(a) - calculateCost(b));
        sortedLabels[length] = results;
        return results;
    };

    const getLabel = (i, length) => {
        const labels = getSortedLabels(length);
        if (i < labels.length) return labels[i];

        // Final fallback
        return labels[i % labels.length] + Math.floor(i / labels.length);
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
        span.style.animation = ''; // Allow CSS animation to trigger
        return span;
    }
    function releaseSpanToPool(span) {
        span.style.display = 'none';
        span.style.animation = 'none'; // Reset animation
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

    const selectors = 'a, button, input, textarea, select, label, summary, [role="button"], [role="link"], [role="checkbox"], [role="menuitem"], [onclick], [tabindex="0"], [contenteditable="true"], [role="textbox"], [hx-get], [hx-post], [hx-put], [hx-delete], [hx-patch]';
    const updateTargets = () => {
        if (!document.body) return;
        const shadowRoots = new Set();
        const scanShadowDOMs = (root) => {
            if (shadowRoots.has(root)) return [];
            shadowRoots.add(root);

            let elements = Array.from(root.querySelectorAll(selectors));

            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
                acceptNode: (node) => node.shadowRoot ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP
            });

            let host;
            while (host = walker.nextNode()) {
                elements = elements.concat(scanShadowDOMs(host.shadowRoot));
            }
            return elements;
        };

        let found = scanShadowDOMs(document);

        // FASTER FILTERING: Use native .closest()
        const wrapperSelector = 'a, button, label, [role="button"], [role="link"]';
        found = found.filter(el => {
            // Check if this element is already inside another valid target
            if (el.parentElement && el.parentElement.closest(wrapperSelector)) return false;

            // Handle Shadow DOM boundary
            const root = el.getRootNode();
            if (root !== document && root.host && root.host.closest(wrapperSelector)) return false;

            return true;
        });

        found.forEach(el => {
            if (!targets.has(el)) {
                targets.add(el);
                observer.observe(el);
            }
        });

        // REMOVED manual visibility check loop. Relying on IntersectionObserver.

        ensureLabelCapacity();

        // Cleanup elements no longer in DOM - FAST connected check
        for (const el of targets) {
            if (!el.isConnected) {
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

        // Use visible count for length, but with a small buffer
        const totalCount = Math.max(visibleTargets.size, 1);
        let length = 1;
        while (Math.pow(26, length) < totalCount) length++;

        // Cap at 3 for stability, but prioritize 1 and 2
        if (length !== currentLabelLength) {
            currentLabelLength = length;
            labelMap = new WeakMap();
            hintMap = {};
            nextLabelIndex = 0;
            elementToHintMap.forEach((span) => releaseSpanToPool(span));
            elementToHintMap.clear();
        }
    }

    let updateIdleHandle;
    const debouncedUpdate = () => {
        if (hintsActive) {
            clearTimeout(updateTimeout);
            updateTimeout = setTimeout(updateTargets, 50);
        } else {
            // Non-active: Use idle callback to avoid blocking
            if (window.requestIdleCallback) {
                if (updateIdleHandle) cancelIdleCallback(updateIdleHandle);
                updateIdleHandle = requestIdleCallback(() => updateTargets(), { timeout: 1000 });
            } else {
                clearTimeout(updateTimeout);
                updateTimeout = setTimeout(updateTargets, 500);
            }
        }
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
        // FAST PATH: Check basic visibility first
        if (el.offsetWidth === 0 || el.offsetHeight === 0) return false;

        // Modern API check (Chrome 105+) - very fast
        if (typeof el.checkVisibility === 'function') {
            return el.checkVisibility();
        }

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

    let lastPrepareTime = 0;
    let hintPreparationHandle;
    const runPrepare = () => {
        hintPreparationHandle = null;
        labelMap = new WeakMap();
        elementToHintMap.clear();
        motionState = new WeakMap();
        nextLabelIndex = 0;
        hintMap = {};

        hintContainer.style.display = 'none';
        updateTargets();
        ensureLabelCapacity();
        refreshVisibleHints(true);
    };

    function prepareHints() {
        if (hintsActive) return;

        const now = Date.now();
        if (now - lastPrepareTime < 500) return;
        lastPrepareTime = now;

        if (hintPreparationHandle) cancelAnimationFrame(hintPreparationHandle);
        hintPreparationHandle = requestAnimationFrame(runPrepare);
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

    let lastRefreshTime = 0;
    function refreshVisibleHints(force = false, isScrolling = false) {
        if (!hintsActive && !force) return;

        const now = Date.now();
        // If we recently refreshed (within 16ms), skip unless forced
        if (!force && !isScrolling && now - lastRefreshTime < 16) return;
        lastRefreshTime = now;

        const scrollX = window.scrollX;
        const scrollY = window.scrollY;

        // --- READ PHASE ---
        const measurements = [];
        const targetsToProcess = Array.from(visibleTargets);

        targetsToProcess.forEach(el => {
            let state = motionState.get(el);
            if (isScrolling && state && state.mode === 'static') return;

            const rect = el.getBoundingClientRect();
            // Stricter viewport check: must be in bounding client rect + some margin
            const isVisible = (state && typeof state.isVisible !== 'undefined') ? state.isVisible : isElementVisible(el);

            if (rect.width > 0 && rect.height > 0 && isVisible) {
                measurements.push({ el, rect, isVisible: true });
            } else {
                measurements.push({ el, isVisible: false });
            }
        });

        // SPATIAL SORT: Prioritize top-to-bottom, then left-to-right
        measurements.sort((a, b) => {
            if (!a.isVisible && !b.isVisible) return 0;
            if (!a.isVisible) return 1;
            if (!b.isVisible) return -1;

            // Allow 5px jitter in vertical alignment to keep rows together
            const verticalDiff = a.rect.top - b.rect.top;
            if (Math.abs(verticalDiff) > 5) return verticalDiff;
            return a.rect.left - b.rect.left;
        });

        // --- WRITE PHASE ---
        const seenUrls = new Map();
        const fragment = document.createDocumentFragment();

        measurements.forEach(({ el, rect, isVisible }) => {
            let span = elementToHintMap.get(el);
            let state = motionState.get(el);

            if (!isVisible) {
                if (span) {
                    releaseSpanToPool(span);
                    elementToHintMap.delete(el);
                }
                return;
            }

            // Link de-duplication - MUST happen before labeling
            const href = el.href || el.getAttribute('href');
            if ((el.tagName === 'A' || el.getAttribute('role') === 'link') && href) {
                const normalized = normalizeUrl(href);
                const existing = seenUrls.get(normalized);
                if (existing) {
                    const eRect = existing.getBoundingClientRect();
                    // If extremely close, skip this element entirely
                    if (Math.abs(rect.left - eRect.left) < 300 && Math.abs(rect.top - eRect.top) < 20) {
                        if (span) {
                            releaseSpanToPool(span);
                            elementToHintMap.delete(el);
                        }
                        return;
                    }
                }
                seenUrls.set(normalized, el);
            }

            // ASSIGN LABEL: Dynamic priority based on sorted order
            if (!labelMap.has(el)) {
                const code = getLabel(nextLabelIndex++, currentLabelLength);
                labelMap.set(el, code);
                hintMap[code] = el;
            }
            const code = labelMap.get(el);

            const docTop = rect.top + scrollY;
            const docLeft = rect.left + scrollX;

            if (!span) {
                span = getSpanFromPool();
                span.dataset.code = code;
                span.innerText = code;
                state = { docTop, docLeft, lastTop: rect.top, lastScrollY: scrollY, mode: 'static' };
                motionState.set(el, state);

                // Position Clamp: Ensure hints don't bleed off screen edges
                // Use fixed estimates for hint size to avoid O(N) offsetWidth reflows
                const vRight = window.innerWidth + scrollX;
                const vBottom = window.innerHeight + scrollY;
                const safeTop = Math.max(scrollY + 2, Math.min(docTop, vBottom - 25));
                const safeLeft = Math.max(scrollX + 2, Math.min(docLeft, vRight - 40));

                span.style.translate = `${Math.round(safeLeft)}px ${Math.round(safeTop)}px`;
                span.style.position = 'absolute';
                span.style.left = '0';
                span.style.top = '0';

                elementToHintMap.set(el, span);
                fragment.appendChild(span);
            } else {
                // ... update logic
                const deltaScroll = scrollY - state.lastScrollY;
                if (Math.abs(deltaScroll) > 1) {
                    const deltaTop = rect.top - state.lastTop;
                    let currentBehavior = 'unknown';

                    if (Math.abs(deltaTop) < 0.1) {
                        currentBehavior = 'fixed';
                    } else if (Math.abs(deltaTop + deltaScroll) < 1) {
                        currentBehavior = 'static';
                    }

                    if (currentBehavior !== 'unknown') {
                        state.mode = currentBehavior;
                    }

                    if (state.mode !== 'static') {
                        const vRight = window.innerWidth + scrollX;
                        const vBottom = window.innerHeight + scrollY;
                        const safeTop = Math.max(scrollY + 2, Math.min(docTop, vBottom - 25));
                        const safeLeft = Math.max(scrollX + 2, Math.min(docLeft, vRight - 40));
                        span.style.translate = `${Math.round(safeLeft)}px ${Math.round(safeTop)}px`;
                    }

                    state.lastTop = rect.top;
                    state.lastScrollY = scrollY;
                }
            }

            if (typingBuffer && code.startsWith(typingBuffer)) {
                const matched = code.slice(0, typingBuffer.length);
                const remaining = code.slice(typingBuffer.length);
                span.innerHTML = `<span class="kb-nav-hint-match">${matched}</span>${remaining}`;
                span.classList.remove('kb-nav-hint-filtered');
                if (typingBuffer) {
                    span.classList.add('kb-nav-hint-active'); // For scaling
                } else {
                    span.classList.remove('kb-nav-hint-active');
                }
                el.classList.add('kb-nav-target-highlight');
            } else {
                span.innerText = code;
                span.classList.remove('kb-nav-hint-active');
                if (typingBuffer) {
                    span.classList.add('kb-nav-hint-filtered');
                } else {
                    span.classList.remove('kb-nav-hint-filtered');
                }
                el.classList.remove('kb-nav-target-highlight');
            }
        });

        if (fragment.childNodes.length > 0) {
            hintContainer.appendChild(fragment);
        }

        // Cleanup
        elementToHintMap.forEach((span, el) => {
            if (!visibleTargets.has(el)) {
                releaseSpanToPool(span);
                elementToHintMap.delete(el);
                motionState.delete(el);
            }
        });
    }

    function finalizeSelection(targetEl, span) {
        // Inject checkmark SVG
        const checkmark = document.createElement('span');
        checkmark.className = 'kb-nav-checkmark';
        checkmark.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
        span.innerHTML = '';
        span.appendChild(checkmark);
        span.classList.add('kb-nav-hint-finalized');

        targetEl.classList.add('kb-nav-clicked');
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
        otherKeyPressed = false;

        // If preparation is still deferred, force it now
        if (hintPreparationHandle) {
            cancelAnimationFrame(hintPreparationHandle);
            runPrepare();
        }

        // Show prepared hints
        hintContainer.style.display = 'block';
        refreshVisibleHints();

        // Add a temporary fast poll to catch elements that load just after activation
        let ticks = 0;
        const interval = setInterval(() => {
            updateTargets();
            debouncedRefresh(); // Refresh UI for any new elements
            ticks++;
            if (!hintsActive || ticks > 10) {
                clearInterval(interval);
                // Switch to a slower background poll if still active
                if (hintsActive) {
                    const slowInterval = setInterval(() => {
                        if (!hintsActive) {
                            clearInterval(slowInterval);
                            return;
                        }
                        updateTargets();
                        debouncedRefresh();
                    }, 2000);
                }
            }
        }, 300);
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
                    const span = elementToHintMap.get(targetEl);
                    if (targetEl && span) {
                        finalizeSelection(targetEl, span);

                        // Trigger action instantly
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

                        // Just wait a moment for the user to see the "success" state
                        // while the browser processes the action.
                        setTimeout(() => {
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
                if (typingBuffer) {
                    hint.classList.add('kb-nav-hint-active'); // Only scale if actually filtering
                } else {
                    hint.classList.remove('kb-nav-hint-active');
                }
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
                hint.classList.remove('kb-nav-hint-active');
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
