/* TextType — typewriter text with typos, effects and a gliding cursor
 * (vanilla port of React Bits' <TextType />; GSAP replaced by CSS / Web Animations).
 *
 *   const tt = new TextType(element, {
 *       text: ['Thank you for downloading!', 'Enjoy!'],
 *       typingSpeed: 75, pauseDuration: 1500, deletingSpeed: 50,
 *       deleteMode: 'backspace', typeEffect: 'fade', mistakes: 0.2,
 *       cursorStyle: 'bar', cursorGlide: true, showCursor: true
 *   });
 *   tt.destroy();
 *
 * Options match the React props: text, prefix, prefixColor, typingSpeed, initialDelay,
 * pauseDuration, deletingSpeed, deleteMode ('backspace' | 'select'), typeEffect
 * ('none' | 'fade' | 'blur' | 'rise'), mistakes, variableSpeed {min, max}, loop,
 * showCursor, cursorStyle ('character' | 'bar' | 'block' | 'underscore'), cursorCharacter,
 * cursorColor, cursorBlinkDuration, cursorGlide, hideCursorWhileTyping, cursorClassName,
 * className, textColors, onSentenceComplete, startOnVisible.
 * (reverseMode is not supported in this port.)
 */
(function () {
    'use strict';

    const NEIGHBORS = {
        a: 'qwsz', b: 'vghn', c: 'xdfv', d: 'serfcx', e: 'wsdr', f: 'drtgvc', g: 'ftyhbv',
        h: 'gyujnb', i: 'ujko', j: 'huikmn', k: 'jiolm', l: 'kop', m: 'njk', n: 'bhjm',
        o: 'iklp', p: 'ol', q: 'wa', r: 'edft', s: 'awedxz', t: 'rfgy', u: 'yhji', v: 'cfgb',
        w: 'qase', x: 'zsdc', y: 'tghu', z: 'asx', 0: '9p', 1: '2q', 2: '13w', 3: '24e',
        4: '35r', 5: '46t', 6: '57y', 7: '68u', 8: '79i', 9: '80o'
    };

    const EFFECTS = {
        fade: {
            keyframes: [{ opacity: 0 }, { opacity: 1 }],
            options: { duration: 360, easing: 'cubic-bezier(0.25, 1, 0.5, 1)' }
        },
        blur: {
            keyframes: [{ opacity: 0, filter: 'blur(0.3em)' }, { opacity: 1, filter: 'blur(0em)' }],
            options: { duration: 520, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' }
        },
        rise: {
            keyframes: [{ opacity: 0, top: '0.6em' }, { opacity: 1, top: '0em' }],
            options: { duration: 480, easing: 'cubic-bezier(0.34, 1.4, 0.64, 1)' }
        }
    };

    const graphemes = value => {
        if (typeof Intl !== 'undefined' && Intl.Segmenter) {
            return Array.from(new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(value), p => p.segment);
        }
        return Array.from(value);
    };

    const typoFor = char => {
        const keys = NEIGHBORS[char.toLowerCase()];
        if (!keys) return null;
        const pick = keys[Math.floor(Math.random() * keys.length)];
        return char === char.toLowerCase() ? pick : pick.toUpperCase();
    };

    const el = (tag, cls, text) => {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined) node.textContent = text;
        return node;
    };

    class TextType {
        constructor(root, options = {}) {
            this.o = Object.assign({
                text: '', prefix: '', prefixColor: 'currentColor', typingSpeed: 50,
                initialDelay: 0, pauseDuration: 2000, deletingSpeed: 30, deleteMode: 'backspace',
                typeEffect: 'none', mistakes: 0, variableSpeed: undefined, loop: true,
                showCursor: true, cursorStyle: 'character', cursorCharacter: '|',
                cursorColor: 'currentColor', cursorBlinkDuration: 0.5, cursorGlide: true,
                hideCursorWhileTyping: false, cursorClassName: '', className: '',
                textColors: [], onSentenceComplete: null, startOnVisible: false
            }, options);
            const o = this.o;
            this.root = root;
            this.sentences = (Array.isArray(o.text) ? o.text : [o.text]).map(String);
            this.reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
            this.minSpeed = o.variableSpeed?.min ?? o.typingSpeed;
            this.maxSpeed = o.variableSpeed?.max ?? o.typingSpeed;
            const pace = (this.minSpeed + this.maxSpeed) / 2;
            this.glideTime = Math.min(0.18, Math.max(0.06, pace * 0.0016));

            // state (mirrors the React component)
            this.textIndex = 0;
            this.count = 0;
            this.selected = 0;
            this.typo = null;
            this.deleting = false;
            this.lastTypo = -1;
            this.timer = 0;
            this.rendered = [];        // [{ key, el }]
            this.lastCaret = null;

            this._build();
            this._setSentence();
            this._render();
            const begin = () => this._tick();
            if (o.startOnVisible) {
                this.io = new IntersectionObserver(([entry]) => {
                    if (!entry.isIntersecting) return;
                    this.io.disconnect();
                    begin();
                }, { threshold: 0.1 });
                this.io.observe(root);
            } else {
                begin();
            }
        }

        destroy() {
            clearTimeout(this.timer);
            this.io?.disconnect();
            this.ro?.disconnect();
            this.root.replaceChildren();
            this.root.classList.remove('text-type');
        }

        // ── DOM ───────────────────────────────────────────────────────────────
        _build() {
            const o = this.o;
            const root = this.root;
            root.classList.add('text-type');
            if (o.className) root.classList.add(...o.className.split(/\s+/).filter(Boolean));
            root.replaceChildren();
            root.appendChild(el('span', 'text-type__sr', o.prefix + this.sentences.join(' ')));
            const content = el('span', 'text-type__content');
            content.setAttribute('aria-hidden', 'true');
            if (o.prefix) {
                const p = el('span', 'text-type__prefix', o.prefix);
                p.style.color = o.prefixColor;
                content.appendChild(p);
            }
            this.textEl = el('span');
            content.appendChild(this.textEl);
            this.caret = el('span', 'text-type__caret', '​');
            content.appendChild(this.caret);
            root.appendChild(content);

            if (o.showCursor) {
                const cursor = this.cursor = el('span',
                    `text-type__cursor text-type__cursor--${o.cursorStyle} ${o.cursorClassName}`.trim());
                cursor.setAttribute('aria-hidden', 'true');
                if (o.cursorColor && o.cursorColor !== 'currentColor') cursor.style.color = o.cursorColor;
                const blink = this.blink = el('span', 'text-type__blink');
                if (o.cursorStyle === 'character') blink.textContent = o.cursorCharacter;
                else blink.appendChild(el('span', 'text-type__shape'));
                if (!this.reduce && o.cursorBlinkDuration > 0) {
                    blink.style.setProperty('--tt-blink', `${o.cursorBlinkDuration * 2.2}s`);
                    blink.classList.add('text-type__blink--on');
                }
                cursor.appendChild(blink);
                root.appendChild(cursor);
                this.ro = new ResizeObserver(() => this._placeCursor(true));
                this.ro.observe(root);
            }
        }

        _setSentence() {
            const i = this.textIndex % this.sentences.length;
            this.index = i;
            this.chars = graphemes(this.sentences[i]);
            const colors = this.o.textColors;
            this.textEl.style.color = colors.length ? colors[i % colors.length] : 'inherit';
        }

        _render() {
            const shown = Math.min(this.count, this.chars.length);
            const marked = Math.min(this.selected, shown);
            const want = [];
            for (let i = 0; i < shown; i++) {
                want.push({ key: `c${i}`, char: this.chars[i], selected: i >= shown - marked });
            }
            (this.typo?.tail ?? []).forEach((char, i) => want.push({ key: `t${i}`, char, selected: false }));

            // keep existing spans (so only new characters animate), drop the rest
            const keep = new Map(this.rendered.map(r => [r.key, r]));
            const next = [];
            want.forEach(w => {
                let r = keep.get(w.key);
                if (r && r.char === w.char) {
                    keep.delete(w.key);
                } else {
                    const span = el('span', 'text-type__char', w.char);
                    r = { key: w.key, char: w.char, el: span, fresh: true };
                }
                r.el.classList.toggle('text-type__char--selected', w.selected);
                next.push(r);
            });
            keep.forEach(r => r.el.remove());
            next.forEach((r, i) => {
                if (this.textEl.children[i] !== r.el) this.textEl.insertBefore(r.el, this.textEl.children[i] || null);
                if (r.fresh) {
                    r.fresh = false;
                    const fx = EFFECTS[this.o.typeEffect];
                    if (fx && !this.reduce && r.el.animate) r.el.animate(fx.keyframes, fx.options);
                }
            });
            this.rendered = next;

            if (this.cursor) {
                const typing = shown < this.chars.length || this.deleting || this.typo !== null;
                this.cursor.classList.toggle('text-type__cursor--hidden', this.o.hideCursorWhileTyping && typing);
                this._placeCursor(false);
            }
        }

        _placeCursor(instant) {
            const cursor = this.cursor;
            if (!cursor) return;
            const x = this.caret.offsetLeft;
            const y = this.caret.offsetTop;
            const last = this.lastCaret;
            if (last && Math.abs(last.x - x) < 0.5 && Math.abs(last.y - y) < 0.5) return;
            this.lastCaret = { x, y };
            const jump = instant || !last || !this.o.cursorGlide || this.reduce || Math.abs(last.y - y) > 1;
            cursor.style.transition = jump ? 'none'
                : `transform ${this.glideTime}s cubic-bezier(0.215, 0.61, 0.355, 1), opacity 0.2s ease`;
            cursor.style.transform = `translate(${x}px, ${y}px)`;
            if (instant || !this.blink.classList.contains('text-type__blink--on')) return;
            // solid while moving, then resume blinking
            this.blink.style.animation = 'none';
            void this.blink.offsetWidth;
            this.blink.style.animation = '';
        }

        // ── typing state machine (same rules as the React version) ────────────
        _wait(ms, next) {
            this.timer = setTimeout(() => { next(); this._render(); this._tick(); }, ms);
        }

        _speed() { return this.minSpeed + Math.random() * (this.maxSpeed - this.minSpeed); }

        _tick() {
            const o = this.o;
            const total = this.chars.length;
            const shown = Math.min(this.count, total);
            const typo = this.typo;

            if (!this.deleting) {
                if (typo) {
                    if (this.reduce) this._wait(0, () => { this.typo = null; });
                    else if (!typo.fixing) {
                        if (typo.tail.length < typo.plan.length) {
                            this._wait(this._speed(), () => {
                                this.typo = { ...typo, tail: typo.plan.slice(0, typo.tail.length + 1) };
                            });
                        } else {
                            this._wait(260 + Math.random() * 320, () => { this.typo = { ...typo, fixing: true }; });
                        }
                    } else if (typo.tail.length) {
                        this._wait(Math.max(45, o.deletingSpeed), () => {
                            this.typo = { ...typo, tail: typo.tail.slice(0, -1) };
                        });
                    } else {
                        this._wait(90 + Math.random() * 140, () => { this.typo = null; });
                    }
                } else if (shown < total) {
                    const lead = shown === 0 ? o.initialDelay : 0;
                    const next = shown;
                    const wrong = o.mistakes > 0 && shown > 0 && this.lastTypo !== next ? typoFor(this.chars[next]) : null;
                    if (this.reduce) {
                        this._wait(lead, () => { this.count = total; });
                    } else if (wrong && Math.random() < Math.min(1, o.mistakes) * 0.16) {
                        this.lastTypo = next;
                        const plan = [wrong];
                        const follow = this.chars[next + 1];
                        if (follow && follow.trim() && Math.random() < 0.4) plan.push(follow);
                        this._wait(lead + this._speed(), () => { this.typo = { plan, tail: [wrong], fixing: false }; });
                    } else {
                        this._wait(lead + this._speed(), () => { this.count = shown + 1; });
                    }
                } else if (o.loop || this.index < this.sentences.length - 1) {
                    this._wait(o.pauseDuration, () => { this.deleting = true; });
                }
            } else if (shown > 0) {
                if (this.reduce) {
                    this._wait(0, () => { this.count = 0; });
                } else if (o.deleteMode === 'select') {
                    if (this.selected < shown) {
                        this._wait(o.deletingSpeed, () => { this.selected += 1; });
                    } else {
                        this._wait(Math.max(160, o.deletingSpeed * 8), () => { this.selected = 0; this.count = 0; });
                    }
                } else {
                    this._wait(o.deletingSpeed, () => { this.count = shown - 1; });
                }
            } else {
                o.onSentenceComplete?.(this.sentences[this.index], this.index);
                this.lastTypo = -1;
                this.selected = 0;
                this.deleting = false;
                this.textIndex = (this.index + 1) % this.sentences.length;
                this._setSentence();
                this._render();
                this._tick();
            }
        }
    }

    window.TextType = TextType;
})();
