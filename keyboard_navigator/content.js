(function() {
    'use strict';

    // --- UTILS ---
    const hexToRgba = (hex, opacity) => {
        let r = parseInt(hex.slice(1, 3), 16);
        let g = parseInt(hex.slice(3, 5), 16);
        let b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r}, ${g}, ${b}, ${opacity})`;
    };

    const normalizeUrl = (u) => {
        try {
            const url = new URL(u, window.location.href);
            return url.origin + url.pathname + url.search;
        } catch (e) { return u; }
    };

    // --- CONFIG & STATE ---
    const CONFIG = {
        selectors: 'a, button, input, textarea, select, label, summary, [role="button"], [role="link"], [role="checkbox"], [role="menuitem"], [role="tab"], [role="option"], [role="radio"], [role="switch"], [role="menuitemcheckbox"], [role="menuitemradio"], [onclick], [tabindex="0"], [contenteditable="true"], [role="textbox"], [hx-get], [hx-post], [hx-put], [hx-delete], [hx-patch]',
        focusTags: ['INPUT', 'TEXTAREA', 'SELECT'],
        charData: {
            'A': { h: 'L', r: 1 }, 'S': { h: 'L', r: 1 }, 'D': { h: 'L', r: 1 }, 'F': { h: 'L', r: 1 }, 'G': { h: 'L', r: 1 },
            'J': { h: 'R', r: 1 }, 'K': { h: 'R', r: 1 }, 'L': { h: 'R', r: 1 }, 'H': { h: 'R', r: 1 },
            'Q': { h: 'L', r: 0 }, 'W': { h: 'L', r: 0 }, 'E': { h: 'L', r: 0 }, 'R': { h: 'L', r: 0 }, 'T': { h: 'L', r: 0 },
            'Y': { h: 'R', r: 0 }, 'U': { h: 'R', r: 0 }, 'I': { h: 'R', r: 0 }, 'O': { h: 'R', r: 0 }, 'P': { h: 'R', r: 0 },
            'Z': { h: 'L', r: 2 }, 'X': { h: 'L', r: 2 }, 'C': { h: 'L', r: 2 }, 'V': { h: 'L', r: 2 }, 'B': { h: 'L', r: 2 },
            'N': { h: 'R', r: 2 }, 'M': { h: 'R', r: 2 }
        }
    };

    const state = {
        active: false,
        closing: false,
        mode: 'SAME_TAB',
        buffer: "",
        labelLen: 1,
        shiftDown: false,
        otherKeyPressed: false,
        lastMouse: { x: 0, y: 0 },
        initialized: false
    };

    // --- NAVIGATOR MODULES ---

    const Labeler = {
        alphabet: Object.keys(CONFIG.charData),
        cache: {},
        calculateCost(label) {
            let cost = 0;
            for (let i = 0; i < label.length; i++) {
                const char = label[i];
                const data = CONFIG.charData[char];
                cost += (data.r === 1 ? 0 : data.r === 0 ? 1 : 2);
                if (i > 0) {
                    const prev = CONFIG.charData[label[i-1]];
                    if (data.h === prev.h) cost += 1.5; else cost -= 0.5;
                    if (char === label[i-1]) cost += 3.0;
                    cost += Math.abs(data.r - prev.r) * 0.5;
                }
            }
            return cost;
        },
        getSorted(length) {
            if (this.cache[length]) return this.cache[length];
            let res = [];
            if (length === 1) res = this.alphabet;
            else if (length === 2) {
                for (const a1 of this.alphabet) for (const a2 of this.alphabet) res.push(a1 + a2);
            } else {
                // Generative fallback for 3+ characters: sequential letter-only combinations
                const count = Math.pow(26, length);
                // For memory/perf safety, cap at a reasonable number for high-density pages
                for (let i = 0; i < Math.min(count, 5000); i++) {
                    let l = ""; let t = i;
                    for (let j = 0; j < length; j++) { l = String.fromCharCode((t % 26) + 65) + l; t = Math.floor(t/26); }
                    res.push(l);
                }
                return res;
            }
            res.sort((a, b) => this.calculateCost(a) - this.calculateCost(b));
            return this.cache[length] = res;
        },
        get(i, len) {
            const list = this.getSorted(len);
            if (i < list.length) return list[i];
            // If we run out of labels of this length, increase length instead of using numbers
            return this.get(i - list.length, len + 1);
        }
    };

    const Renderer = {
        host: null,
        shadow: null,
        container: null,
        pools: { span: [], div: [] },

        init() {
            this.container = document.createElement('div');
            this.container.id = 'kb-nav-container';
            this.ensureHost();
            this.initColors();
        },

        ensureHost() {
            if (this.host && this.host.isConnected) return;
            if (this.host) this.host.remove();
            this.host = document.createElement('div');
            this.host.id = 'kb-nav-host';
            this.host.style.cssText = 'position: absolute !important; top: 0 !important; left: 0 !important; width: 100% !important; height: 100% !important; pointer-events: none !important; z-index: 2147483647 !important; display: block !important; border: none !important; padding: 0 !important; margin: 0 !important;';
            document.body.appendChild(this.host);
            this.shadow = this.host.attachShadow({ mode: 'closed' });
            this.shadow.appendChild(this.container);
            this.injectStyles();
            Scanner.observe();
        },

        injectStyles() {
            if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.getURL) {
                fetch(chrome.runtime.getURL('content.css'))
                    .then(r => r.text())
                    .then(css => {
                        const s = document.createElement('style'); s.textContent = css; this.shadow.appendChild(s);
                        this.syncColors();
                    }).catch(() => this.fallbackStyles());
            } else this.fallbackStyles();
        },

        fallbackStyles() {
            if (this.shadow.querySelector('#kb-fallback')) return;
            const s = document.createElement('style'); s.id = 'kb-fallback';
            s.textContent = `
                .kb-nav-hint { position: absolute; padding: 3px 6px; background: rgba(255, 241, 118, 0.9); color: #000; font-family: sans-serif; font-size: 14px; font-weight: 700; z-index: 2147483647; pointer-events: none; border-radius: 4px; border: 1px solid rgba(0,0,0,0.2); transition: opacity 0.15s ease-out, transform 0.15s cubic-bezier(0.2, 0, 0, 1); }
                .kb-nav-target-highlight { position: absolute; border: 2px solid #ffd700; pointer-events: none; z-index: 2147483646; transition: opacity 0.15s ease-out; }
                .kb-nav-hint-filtered { display: none !important; }
                .kb-nav-hint-active { transform: scale(1.1); }
                #kb-nav-container.kb-nav-closing .kb-nav-hint,
                #kb-nav-container.kb-nav-closing .kb-nav-target-highlight { opacity: 0 !important; }
            `;
            this.shadow.appendChild(s);
            this.syncColors();
        },

        initColors() {
            if (typeof chrome !== 'undefined' && chrome.storage) {
                chrome.storage.onChanged.addListener(() => this.syncColors());
                this.syncColors();
            }
        },

        syncColors() {
            if (typeof chrome === 'undefined' || !chrome.storage) return;
            chrome.storage.local.get(['bgColor', 'accentColor'], (res) => {
                const bg = hexToRgba(res.bgColor || '#fff176', 0.85);
                const accent = res.accentColor || '#ffd700';
                document.documentElement.style.setProperty('--kb-nav-bg', bg);
                document.documentElement.style.setProperty('--kb-nav-accent', accent);
                if (this.host) {
                    this.host.style.setProperty('--kb-nav-bg', bg);
                    this.host.style.setProperty('--kb-nav-accent', accent);
                }
            });
        },

        getSpan() {
            const s = this.pools.span.pop() || document.createElement('span');
            s.className = 'kb-nav-hint'; s.style.display = 'inline-block'; s.style.opacity = '1'; return s;
        },
        getDiv() {
            const d = this.pools.div.pop() || document.createElement('div');
            d.className = 'kb-nav-target-highlight'; d.style.display = 'block'; d.style.opacity = '0'; return d;
        },
        releaseSpan(s) {
            s.style.display = 'none'; s.textContent = ''; delete s.dataset.code;
            if (s.parentNode) s.parentNode.removeChild(s); this.pools.span.push(s);
        },
        releaseDiv(d) {
            d.style.display = 'none'; d.style.opacity = '0';
            if (d.parentNode) d.parentNode.removeChild(d); this.pools.div.push(d);
        }
    };

    const Scanner = {
        targets: new Set(),
        visible: new Set(),
        observer: null,
        mutationObserver: null,

        init() {
            this.observer = new IntersectionObserver((es) => {
                es.forEach(e => { if (e.isIntersecting) this.visible.add(e.target); else this.visible.delete(e.target); });
                if (state.active) Core.debouncedRefresh();
            }, { threshold: 0, rootMargin: '200px' });
            this.mutationObserver = new MutationObserver(() => { if (state.active) Core.update(); });
        },

        observe() {
            if (this.mutationObserver) this.mutationObserver.disconnect();
            document.querySelectorAll(CONFIG.selectors).forEach(el => this.observer.observe(el));
            this.mutationObserver.observe(document.body, { childList: true, subtree: true });
        },

        scan() {
            const roots = new Set();
            const find = (root) => {
                if (!root || roots.has(root)) return []; roots.add(root);
                let els = Array.from(root.querySelectorAll(CONFIG.selectors));
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
                    acceptNode: (n) => n.shadowRoot ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP
                });
                let h; while (h = walker.nextNode()) els = els.concat(find(h.shadowRoot));
                return els;
            };

            let found = find(document);
            const targetSet = new Set(found);
            found = found.filter(el => {
                let p = el.parentElement;
                while (p) {
                    if (targetSet.has(p)) {
                        // Keep distinct interactive elements even if nested in another target
                        const interactiveTags = ['A', 'BUTTON', 'INPUT', 'TEXTAREA', 'SELECT', 'SUMMARY', 'details'];
                        if (interactiveTags.includes(el.tagName) || el.hasAttribute('role') || el.tagName === 'YT-TAB-SHAPE') break;
                        return false;
                    }
                    p = p.parentElement;
                }
                const r = el.getRootNode();
                if (r !== document && r.host) {
                    let hp = r.host;
                    while (hp) { if (targetSet.has(hp)) return false; hp = hp.parentElement; }
                }
                return true;
            });

            this.targets.clear();
            found.forEach(el => { this.targets.add(el); this.observer.observe(el); });
            return found;
        }
    };

    const Core = {
        hintMap: {},
        elToHint: new Map(),
        elToHighlight: new Map(),
        labelMap: new WeakMap(),
        motion: new WeakMap(),
        refreshTimer: null,
        deactivateTimer: null,

        activate(mode = 'SAME_TAB') {
            if (state.closing) { state.closing = false; Renderer.container.classList.remove('kb-nav-closing'); clearTimeout(this.deactivateTimer); }
            Renderer.ensureHost();
            state.active = true; state.mode = mode; state.buffer = ""; state.otherKeyPressed = false;

            this.update();
            Renderer.container.style.display = 'block';
            this.refresh(true);
            this.startPolling();
        },

        deactivate(instant = false) {
            if (!state.active && !state.closing) return;
            state.active = false;
            if (instant) { this.finalizeDeactivation(); return; }
            state.closing = true;
            Renderer.container.classList.add('kb-nav-closing');
            this.deactivateTimer = setTimeout(() => { if (!state.active) this.finalizeDeactivation(); }, 150);
        },

        finalizeDeactivation() {
            state.closing = false;
            Renderer.container.style.display = 'none';
            Renderer.container.classList.remove('kb-nav-closing');
            this.elToHint.forEach((s, el) => { Renderer.releaseSpan(s); el.classList.remove('kb-nav-clicked'); });
            this.elToHighlight.forEach(d => Renderer.releaseDiv(d));
            this.elToHint.clear(); this.elToHighlight.clear(); this.hintMap = {};
            state.buffer = "";
        },

        update() { Renderer.ensureHost(); Scanner.scan(); },

        debouncedRefresh() {
            if (this.refreshTimer) return;
            this.refreshTimer = requestAnimationFrame(() => { this.refresh(); this.refreshTimer = null; });
        },

        refresh(force = false) {
            if (!state.active && !state.closing) return;
            const scrollX = window.scrollX, scrollY = window.scrollY;
            const frag = document.createDocumentFragment();
            const seenLocations = [];
            const seenUrls = new Map();

            // STABLE SORT: Always process elements from top-to-bottom, left-to-right
            // This prevents chaotic re-labeling when the DOM order is random or changing.
            const sortedVisible = Array.from(Scanner.visible)
                .filter(el => el.isConnected)
                .map(el => ({ el, rect: el.getBoundingClientRect() }))
                .filter(item => item.rect.width > 0 && item.rect.height > 0)
                .sort((a, b) => (a.rect.top + scrollY) - (b.rect.top + scrollY) || (a.rect.left + scrollX) - (b.rect.left + scrollX));

            // RESET Mappings for this pass to ensure strict uniqueness
            this.hintMap = {};
            const activeEls = new Set();
            let labelIdx = 0;

            // DYNAMIC LABEL LENGTH: Ensure prefix-free labels by using a consistent length for all visible items.
            // We pre-calculate the actual number of hints that will be shown to avoid "falling forward" conflicts.
            let finalCount = 0;
            const preFiltered = sortedVisible.filter(({ el, rect }) => {
                const isDup = seenLocations.some(p =>
                    Math.abs(rect.left - p.rect.left) < 5 && Math.abs(rect.top - p.rect.top) < 5 &&
                    Math.abs(rect.width - p.rect.width) < 5 && Math.abs(rect.height - p.rect.height) < 5
                );
                if (isDup) return false;
                const href = el.href || el.getAttribute('href');
                if ((el.tagName === 'A' || el.getAttribute('role') === 'link') && href) {
                    const norm = normalizeUrl(href);
                    const exist = seenUrls.get(norm);
                    if (exist && exist !== el && Math.abs(rect.left - exist.getBoundingClientRect().left) < 50 && Math.abs(rect.top - exist.getBoundingClientRect().top) < 15) return false;
                    seenUrls.set(norm, el);
                }
                seenLocations.push({ rect, el });
                return true;
            });

            // Re-reset helpers for the actual assignment loop
            seenLocations.length = 0;
            seenUrls.clear();

            let L = 1;
            let capacity = Labeler.alphabet.length;
            while (capacity < preFiltered.length && L < 5) { L++; capacity *= Labeler.alphabet.length; }
            state.labelLen = L;

            sortedVisible.forEach(({ el, rect }) => {
                // Spatial de-duplication
                const isDup = seenLocations.some(p =>
                    Math.abs(rect.left - p.rect.left) < 5 && Math.abs(rect.top - p.rect.top) < 5 &&
                    Math.abs(rect.width - p.rect.width) < 5 && Math.abs(rect.height - p.rect.height) < 5
                );
                if (isDup) return;

                const href = el.href || el.getAttribute('href');
                if ((el.tagName === 'A' || el.getAttribute('role') === 'link') && href) {
                    const norm = normalizeUrl(href);
                    const exist = seenUrls.get(norm);
                    if (exist && exist !== el && Math.abs(rect.left - exist.getBoundingClientRect().left) < 50 && Math.abs(rect.top - exist.getBoundingClientRect().top) < 15) return;
                    seenUrls.set(norm, el);
                }
                seenLocations.push({ rect, el });

                // ATOMIC ASSIGNMENT: Force unique labels for every unique visible target
                // We clear labelMap per-session or just use the index to guarantee uniqueness here.
                const code = Labeler.get(labelIdx++, state.labelLen);
                this.labelMap.set(el, code);
                this.hintMap[code] = el;
                activeEls.add(el);

                const docTop = rect.top + scrollY, docLeft = rect.left + scrollX;
                let span = this.elToHint.get(el);
                let highlight = this.elToHighlight.get(el);
                let m = this.motion.get(el);

                if (!span) {
                    span = Renderer.getSpan(); span.dataset.code = code; span.innerText = code;
                    m = { docTop, docLeft, lastTop: rect.top, lastScrollY: scrollY, mode: 'static' };
                    this.motion.set(el, m);
                    const safeTop = Math.max(scrollY + 2, Math.min(docTop, window.innerHeight + scrollY - 25));
                    const safeLeft = Math.max(scrollX + 2, Math.min(docLeft, window.innerWidth + scrollX - 40));
                    span.style.translate = `${Math.round(safeLeft)}px ${Math.round(safeTop)}px`;
                    this.elToHint.set(el, span); frag.appendChild(span);
                    highlight = Renderer.getDiv();
                    highlight.style.width = `${Math.round(rect.width)}px`; highlight.style.height = `${Math.round(rect.height)}px`;
                    highlight.style.translate = `${Math.round(docLeft)}px ${Math.round(docTop)}px`;
                    this.elToHighlight.set(el, highlight); frag.appendChild(highlight);
                } else {
                    if (span.dataset.code !== code) { span.dataset.code = code; span.textContent = code; }
                    const safeTop = Math.max(scrollY + 2, Math.min(docTop, window.innerHeight + scrollY - 25));
                    const safeLeft = Math.max(scrollX + 2, Math.min(docLeft, window.innerWidth + scrollX - 40));
                    span.style.translate = `${Math.round(safeLeft)}px ${Math.round(safeTop)}px`;
                    if (highlight) highlight.style.translate = `${Math.round(docLeft)}px ${Math.round(docTop)}px`;
                }

                if (state.buffer && code.startsWith(state.buffer)) {
                    if (span.textContent !== code) span.textContent = code;
                    if (!span.firstChild || span.firstChild.className !== 'kb-nav-hint-match' || span.firstChild.textContent !== code.slice(0, state.buffer.length)) {
                        span.textContent = '';
                        const m = document.createElement('span'); m.className = 'kb-nav-hint-match';
                        m.textContent = code.slice(0, state.buffer.length);
                        span.appendChild(m);
                        span.appendChild(document.createTextNode(code.slice(state.buffer.length)));
                    }
                    span.classList.remove('kb-nav-hint-filtered'); span.classList.add('kb-nav-hint-active');
                    if (highlight) highlight.style.opacity = '1';
                } else {
                    if (span.textContent !== code) span.textContent = code;
                    span.classList.remove('kb-nav-hint-active');
                    if (state.buffer) span.classList.add('kb-nav-hint-filtered'); else span.classList.remove('kb-nav-hint-filtered');
                    if (highlight) highlight.style.opacity = '0';
                }
            });

            if (frag.childNodes.length > 0) Renderer.container.appendChild(frag);
            this.elToHint.forEach((s, el) => {
                if (!activeEls.has(el)) {
                    Renderer.releaseSpan(s); this.elToHint.delete(el);
                    const h = this.elToHighlight.get(el); if (h) { Renderer.releaseDiv(h); this.elToHighlight.delete(el); }
                    this.motion.delete(el);
                }
            });
        },

        startPolling() {
            let ticks = 0;
            const itv = setInterval(() => {
                if (!state.active) { clearInterval(itv); return; }
                this.update(); this.debouncedRefresh();
                if (++ticks > 10) {
                    clearInterval(itv);
                    const slow = setInterval(() => { if (!state.active) clearInterval(slow); else { this.update(); this.debouncedRefresh(); } }, 2000);
                }
            }, 150);
        },

        select(el, span) {
            const isFocusable = CONFIG.focusTags.includes(el.tagName) || el.contentEditable === 'true' || el.getAttribute('role') === 'textbox' || el.getAttribute('role') === 'tab';
            span.textContent = '✓';
            span.classList.add('kb-nav-hint-finalized'); el.classList.add('kb-nav-clicked');
            if (state.mode === 'NEW_TAB' && el.tagName === 'A' && el.href) window.open(el.href, '_blank');
            else {
                el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                el.click(); if (isFocusable) el.focus();
            }
            this.deactivate(isFocusable);
        }
    };

    const Input = {
        init() {
            window.addEventListener('keydown', (e) => this.onKeyDown(e), true);
            window.addEventListener('keyup', (e) => this.onKeyUp(e), true);
            window.addEventListener('blur', () => this.onBlur());
            window.addEventListener('scroll', () => { if (state.active) Core.debouncedRefresh(); }, { passive: true });
            ['mousedown', 'wheel', 'touchstart', 'touchmove'].forEach(t => window.addEventListener(t, () => { if (state.shiftDown) state.otherKeyPressed = true; }, { passive: true }));
            window.addEventListener('mousemove', (e) => {
                if (state.shiftDown && !state.otherKeyPressed && (Math.abs(e.screenX - state.lastMouse.x) > 3 || Math.abs(e.screenY - state.lastMouse.y) > 3)) state.otherKeyPressed = true;
                state.lastMouse = { x: e.screenX, y: e.screenY };
            }, { passive: true });
            window.addEventListener('popstate', () => Core.update());
            window.addEventListener('hashchange', () => Core.update());
        },

        onKeyDown(e) {
            if (e.key === 'Shift') { if (!state.shiftDown) { state.shiftDown = true; if (state.active) { Core.deactivate(); state.otherKeyPressed = true; } else state.otherKeyPressed = false; } return; }
            if (state.active) {
                if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'PageUp', 'PageDown', 'Home', 'End'].includes(e.key)) return;
                if (e.key === 'Escape') Core.deactivate();
                else if (e.key === 'Backspace') { state.buffer = state.buffer.slice(0, -1); Core.debouncedRefresh(); }
                else if (e.key.length === 1 && /^[a-zA-Z]$/.test(e.key)) {
                    state.buffer += e.key.toUpperCase();
                    let match = false; for (const code in Core.hintMap) if (code.startsWith(state.buffer)) { match = true; break; }
                    if (!match) { Core.deactivate(); return; }
                    Core.debouncedRefresh();
                    const el = Core.hintMap[state.buffer];
                    if (el && state.buffer.length === state.labelLen) Core.select(el, Core.elToHint.get(el));
                } else { Core.deactivate(); return; }
                e.preventDefault(); e.stopPropagation();
            } else if (state.shiftDown && !['CapsLock', 'NumLock', 'ScrollLock'].includes(e.key)) state.otherKeyPressed = true;
        },

        onKeyUp(e) {
            if (e.key === 'Shift') {
                state.shiftDown = false;
                if (!state.otherKeyPressed) {
                    if (state.active) Core.deactivate();
                    else Core.activate(e.code === 'ShiftRight' ? 'NEW_TAB' : 'SAME_TAB');
                }
                state.otherKeyPressed = false;
            }
        },

        onBlur() { state.shiftDown = false; state.otherKeyPressed = false; if (state.active) Core.deactivate(); }
    };

    function tryInit() {
        if (state.initialized) return;
        if (!document.body) { setTimeout(tryInit, 50); return; }
        Scanner.init();
        Renderer.init();
        Input.init();
        state.initialized = true;
        console.info("Keyboard Navigator: Tap 'Shift' to steer.");
    }

    tryInit();
})();
