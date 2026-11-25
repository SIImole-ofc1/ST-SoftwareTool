/* RubberSegment — segmented control with a rubbery thumb
 * (vanilla port of React Bits' <RubberSegment />; Motion's springs replaced by a tiny
 * built-in spring/tween engine, so no dependencies).
 *
 *   const seg = new RubberSegment(container, {
 *       items: ['Windows', 'macOS'],          // or { value, label, icon }
 *       value: 'Windows',
 *       onChange: (value, index) => {}
 *   });
 *   seg.setValue('macOS');   // jumps without animation (like a controlled prop change)
 *   seg.value;               // current value
 *
 * Options match the React props: trackColor, thumbColor, textColor, activeTextColor,
 * size ('sm' | 'md' | 'lg'), radius, inset, equalSlots, stretch, squash, speed, glide,
 * draggable, disabled, className, ariaLabel.
 */
(function () {
    'use strict';

    const EASE_OUT = [0.23, 1, 0.32, 1];
    const SPRING_UI = { bounce: 0 };
    const SPRING_MOMENTUM = { bounce: 0.2 };
    const DILATE = 0.19;
    const HANDOFF = 0.15;
    const FLICK = 110;
    const MAX_VELOCITY = 2000;
    const DEADZONE = 4;
    const SLOP = 10;
    const RUBBER = 0.55;
    const SIZES = {
        sm: { height: 28, font: 12, pad: 10, min: 36 },
        md: { height: 36, font: 13, pad: 14, min: 44 },
        lg: { height: 44, font: 14, pad: 18, min: 48 }
    };

    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const rubber = (over, dim) => (over * dim * RUBBER) / (dim + RUBBER * Math.abs(over));
    const project = (v, glide) => {
        const d = 1 - 0.1 * Math.pow(0.05, glide / 100);
        return ((v / 1000) * d) / (1 - d);
    };
    const velocityOf = (hist, now) => {
        const recent = hist.filter(([t]) => now - t <= 100);
        if (recent.length < 2) return 0;
        const [t0, x0] = recent[0];
        const [t1, x1] = recent[recent.length - 1];
        return t1 - t0 >= 8 ? ((x1 - x0) / (t1 - t0)) * 1000 : 0;
    };
    const nearestSlot = (slots, x) => {
        let best = 0;
        for (let i = 1; i < slots.length; i++) {
            if (Math.abs((slots[i].l + slots[i].r) / 2 - x) < Math.abs((slots[best].l + slots[best].r) / 2 - x)) best = i;
        }
        return best;
    };

    // cubic-bezier easing (same maths as CSS)
    function bezier(x1, y1, x2, y2) {
        const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
        const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
        const sx = t => ((ax * t + bx) * t + cx) * t;
        const sy = t => ((ay * t + by) * t + cy) * t;
        const dx = t => (3 * ax * t + 2 * bx) * t + cx;
        return p => {
            let t = p;
            for (let i = 0; i < 8; i++) {
                const e = sx(t) - p;
                const d = dx(t);
                if (Math.abs(e) < 1e-5 || Math.abs(d) < 1e-6) break;
                t -= e / d;
            }
            return sy(clamp(t, 0, 1));
        };
    }
    const easeOut = bezier(...EASE_OUT);

    /** An animatable number (like Motion's MotionValue): spring or tween, with velocity. */
    class Value {
        constructor(v, onChange) {
            this.v = v;
            this.vel = 0;
            this.onChange = onChange;
            this.hist = [];
            this.anim = null;
        }
        get() { return this.v; }
        set(v) {
            const now = performance.now();
            this.hist.push([now, v]);
            if (this.hist.length > 8) this.hist.shift();
            this.v = v;
            this.onChange();
        }
        jump(v) {
            this.stop();
            this.v = v;
            this.vel = 0;
            this.hist = [];
            this.onChange();
        }
        stop() {
            if (this.anim) {
                cancelAnimationFrame(this.anim.raf);
                this.anim.resolve(false);
                this.anim = null;
            }
        }
        getVelocity() {
            if (this.anim) return this.vel;
            return velocityOf(this.hist, performance.now());
        }
        /** Spring: { duration, bounce, velocity }. Tween: { duration, ease }. Resolves when settled. */
        animate(to, opts) {
            this.stop();
            this.hist = [];
            return new Promise(resolve => {
                const anim = this.anim = { resolve, raf: 0 };
                const from = this.v;
                const start = performance.now();
                let last = start;
                let x = this.v;
                let v = opts.velocity !== undefined ? opts.velocity : this.vel;
                const duration = Math.max(0.01, opts.duration);
                const tween = !!opts.ease;
                // Spring that settles in about `duration` seconds; bounce 0 = critically damped
                const zeta = clamp(1 - (opts.bounce || 0), 0.05, 1);
                const omega = 7.4 / (duration * Math.max(zeta, 0.3));
                const step = now => {
                    if (this.anim !== anim) return;
                    if (tween) {
                        const p = clamp((now - start) / 1000 / duration, 0, 1);
                        const nx = from + (to - from) * opts.ease(p);
                        this.vel = (nx - this.v) / Math.max(1e-3, (now - last) / 1000);
                        this.v = nx;
                        last = now;
                        this.onChange();
                        if (p >= 1) { this.anim = null; this.vel = 0; resolve(true); return; }
                    } else {
                        // up to 250 ms per frame: keeps real time on slow devices (1 ms sub-steps stay stable)
                        let dt = clamp((now - last) / 1000, 0, 0.25);
                        last = now;
                        while (dt > 0) {                     // 1 ms sub-steps, stable at any fps
                            const h = Math.min(dt, 0.001);
                            const a = -omega * omega * (x - to) - 2 * zeta * omega * v;
                            v += a * h;
                            x += v * h;
                            dt -= h;
                        }
                        this.v = x;
                        this.vel = v;
                        this.onChange();
                        if (Math.abs(x - to) < 0.05 && Math.abs(v) < 2) {
                            this.v = to; this.vel = 0; this.anim = null;
                            this.onChange();
                            resolve(true);
                            return;
                        }
                    }
                    anim.raf = requestAnimationFrame(step);
                };
                anim.raf = requestAnimationFrame(step);
            });
        }
    }

    class RubberSegment {
        constructor(container, options = {}) {
            this.o = Object.assign({
                items: [], value: undefined, onChange: null,
                trackColor: '#27272a', thumbColor: '#fafafa', textColor: '#fafafa',
                activeTextColor: '#18181b', size: 'md', radius: 10, inset: 3,
                equalSlots: true, stretch: 100, squash: 3, speed: 1, glide: 75,
                draggable: true, disabled: false, className: '', ariaLabel: 'Segmented control'
            }, options);
            const o = this.o;
            this.list = o.items.map(item => (typeof item === 'string' ? { value: item, label: item } : item));
            const startIndex = this.list.findIndex(item => item.value === o.value);
            this.index = Math.max(0, startIndex);
            this.reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
            this.slots = [];
            this.box = null;
            this.handoff = 0;
            this.drag = null;
            this.gen = 0;
            this.thumbRadius = Math.max(0, o.radius - o.inset);

            const update = () => this._paint();
            this.edgeL = new Value(0, update);
            this.edgeR = new Value(0, update);
            this.innerW = 0;
            this._build(container);
            this._measure = this._measure.bind(this);
            this._measure();
            this.resizeObserver = new ResizeObserver(this._measure);
            this.resizeObserver.observe(this.track);
            document.fonts?.ready.then(this._measure);
        }

        get value() { return this.list[this.index]?.value; }

        /** Set from outside: jumps without animation, no onChange. */
        setValue(value) {
            const i = this.list.findIndex(item => item.value === value);
            if (i < 0 || i === this.index || this.drag) return;
            this.index = i;
            this._syncAria();
            this._jumpTo(i);
        }

        destroy() {
            clearTimeout(this.handoff);
            this.edgeL.stop();
            this.edgeR.stop();
            this.resizeObserver.disconnect();
            this.track.remove();
        }

        // ── DOM ───────────────────────────────────────────────────────────────
        _build(container) {
            const o = this.o;
            const preset = SIZES[o.size] || SIZES.md;
            const track = this.track = document.createElement('div');
            track.className = `rubber-segment${o.className ? ` ${o.className}` : ''}`;
            track.setAttribute('role', 'radiogroup');
            track.setAttribute('aria-label', o.ariaLabel);
            if (o.disabled) track.setAttribute('aria-disabled', 'true');
            if (o.equalSlots) track.dataset.equal = '';
            if (o.draggable && !o.disabled) track.dataset.draggable = '';
            const vars = {
                '--rs-track': o.trackColor, '--rs-thumb': o.thumbColor, '--rs-ink': o.textColor,
                '--rs-ink-active': o.activeTextColor, '--rs-radius': `${o.radius}px`,
                '--rs-inset': `${o.inset}px`, '--rs-thumb-radius': `${this.thumbRadius}px`,
                '--rs-h': `${preset.height}px`, '--rs-font': `${preset.font}px`,
                '--rs-pad': `${preset.pad}px`, '--rs-min': `${preset.min}px`
            };
            Object.entries(vars).forEach(([k, v]) => track.style.setProperty(k, v));

            const fill = (el, item) => {
                if (item.icon) el.insertAdjacentHTML('beforeend', item.icon);   // trusted markup from page code
                el.append(document.createTextNode(item.label));
            };
            this.buttons = this.list.map((item, i) => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'rubber-segment__item';
                b.setAttribute('role', 'radio');
                b.disabled = !!o.disabled;
                fill(b, item);
                b.addEventListener('pointerdown', e => this._onDown(e, i));
                b.addEventListener('keydown', e => this._onKey(e));
                track.appendChild(b);
                return b;
            });
            const thumb = this.thumb = document.createElement('div');
            thumb.className = 'rubber-segment__thumb';
            thumb.setAttribute('aria-hidden', 'true');
            this.list.forEach(item => {
                const s = document.createElement('span');
                s.className = 'rubber-segment__item rubber-segment__copy';
                fill(s, item);
                thumb.appendChild(s);
            });
            track.appendChild(thumb);
            track.addEventListener('pointermove', e => this._onMove(e));
            track.addEventListener('pointerup', e => this._onUp(e));
            track.addEventListener('pointercancel', e => this._onCancel(e));
            track.addEventListener('lostpointercapture', e => this._onCancel(e));
            container.appendChild(track);
            this._syncAria();
        }

        _syncAria() {
            this.buttons.forEach((b, i) => {
                b.setAttribute('aria-checked', String(i === this.index));
                b.tabIndex = i === this.index ? 0 : -1;
            });
        }

        _paint() {
            const right = Math.max(0, this.innerW - this.edgeR.get());
            const left = Math.max(0, this.edgeL.get());
            this.thumb.style.clipPath = `inset(0 ${right}px 0 ${left}px round ${this.thumbRadius}px)`;
        }

        _t(seconds) { return seconds / this.o.speed; }

        _jumpTo(i) {
            const s = this.slots[i];
            if (!s) return;
            clearTimeout(this.handoff);
            this.gen += 1;
            this.edgeL.jump(s.l);
            this.edgeR.jump(s.r);
        }

        _measure() {
            const inset = this.o.inset;
            const rect = this.track.getBoundingClientRect();
            this.box = rect;
            this.slots = this.buttons.map(el => {
                const r = el.getBoundingClientRect();
                return { l: r.left - rect.left - inset, r: r.right - rect.left - inset };
            });
            this.innerW = rect.width - inset * 2;
            this._jumpTo(this.index);
        }

        _commit(i) {
            const prev = this.index;
            this.index = i;
            this._syncAria();
            if (i !== prev) this.o.onChange?.(this.list[i].value, i);
        }

        // ── motion ────────────────────────────────────────────────────────────
        _land(to, v, flick, withSquash) {
            const b = this.slots[to];
            if (!b) return;
            const g = ++this.gen;
            const { edgeL, edgeR } = this;
            const dir = Math.sign((b.l + b.r) / 2 - (edgeL.get() + edgeR.get()) / 2) || 1;
            const [lead, leadTo, trail, trailTo] = dir > 0 ? [edgeR, b.r, edgeL, b.l] : [edgeL, b.l, edgeR, b.r];
            const velocityFor = mv => clamp(v === null ? mv.getVelocity() : v, -MAX_VELOCITY, MAX_VELOCITY);
            const leadVelocity = velocityFor(lead);
            const trailVelocity = velocityFor(trail);
            lead.animate(leadTo, {
                ...(flick ? SPRING_MOMENTUM : SPRING_UI),
                duration: this._t(flick ? 0.4 : 0.3),
                velocity: leadVelocity
            });
            const squash = this.o.squash;
            if (!withSquash || squash <= 0) {
                trail.animate(trailTo, { ...SPRING_UI, duration: this._t(0.3), velocity: trailVelocity });
                return;
            }
            trail.animate(trailTo + dir * squash, { ...SPRING_UI, duration: this._t(0.3), velocity: trailVelocity })
                .then(done => {
                    if (done && this.gen === g) trail.animate(trailTo, { bounce: 0, duration: this._t(0.16) });
                });
        }

        _travel(from, to) {
            const a = this.slots[from];
            const b = this.slots[to];
            if (!a || !b) return;
            clearTimeout(this.handoff);
            this.gen += 1;
            if (this.reduce) {
                this.edgeL.jump(b.l);
                this.edgeR.jump(b.r);
                return;
            }
            const u = this.o.stretch / 100;
            const tween = { duration: this._t(DILATE), ease: easeOut };
            this.edgeL.animate(b.l + (Math.min(a.l, b.l) - b.l) * u, tween);
            this.edgeR.animate(b.r + (Math.max(a.r, b.r) - b.r) * u, tween);
            this.handoff = setTimeout(() => this._land(to, null, false, true), this._t(HANDOFF) * 1000);
        }

        // ── input ─────────────────────────────────────────────────────────────
        _localX(e) { return e.clientX - (this.box ? this.box.left : 0) - this.o.inset; }

        _onDown(e, i) {
            if (this.o.disabled || this.drag || e.button !== 0) return;
            this.box = this.track.getBoundingClientRect();
            try { e.currentTarget.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
            const x = this._localX(e);
            const onThumb = this.o.draggable && x >= this.edgeL.get() && x <= this.edgeR.get();
            this.drag = { id: e.pointerId, x0: x, slot: i, onThumb, live: false, offset: 0, w: 0, hist: [[e.timeStamp, x]] };
            if (onThumb) {
                clearTimeout(this.handoff);
                this.gen += 1;
                this.edgeL.stop();
                this.edgeR.stop();
            } else if (!this.reduce) {
                e.currentTarget.dataset.pressed = '';
            }
        }

        _onMove(e) {
            const d = this.drag;
            if (!d || e.pointerId !== d.id || !d.onThumb) return;
            const x = this._localX(e);
            d.hist.push([e.timeStamp, x]);
            if (d.hist.length > 8) d.hist.shift();
            const { edgeL, edgeR } = this;
            if (!d.live) {
                if (Math.abs(x - d.x0) < DEADZONE) return;
                d.live = true;
                d.offset = x - edgeL.get();
                d.w = edgeR.get() - edgeL.get();
                this.track.dataset.held = '';
            }
            const width = this.innerW;
            const l = x - d.offset;
            const maxL = width - d.w;
            if (this.reduce) {
                const c = clamp(l, 0, maxL);
                edgeL.set(c);
                edgeR.set(c + d.w);
            } else if (l < 0) {
                edgeL.set(0);
                edgeR.set(d.w - rubber(-l, d.w));
            } else if (l > maxL) {
                edgeR.set(width);
                edgeL.set(maxL + rubber(l - maxL, d.w));
            } else {
                edgeL.set(l);
                edgeR.set(l + d.w);
            }
        }

        _release() {
            const d = this.drag;
            this.drag = null;
            delete this.track.dataset.held;
            const el = this.buttons[d.slot];
            if (el) delete el.dataset.pressed;
            return d;
        }

        _onUp(e) {
            const d = this.drag;
            if (!d || e.pointerId !== d.id) return;
            this._release();
            const x = this._localX(e);
            if (!d.live) {
                if (Math.abs(x - d.x0) <= SLOP && d.slot !== this.index) {
                    const from = this.index;
                    this._commit(d.slot);
                    this._travel(from, d.slot);
                }
                return;
            }
            const v = velocityOf(d.hist, e.timeStamp);
            const flick = Math.abs(v) > FLICK;
            let to = nearestSlot(this.slots, (this.edgeL.get() + this.edgeR.get()) / 2 + project(v, this.o.glide));
            if (flick && to === this.index) to = clamp(to + Math.sign(v), 0, this.list.length - 1);
            this._commit(to);
            if (this.reduce) this._jumpTo(to);
            else this._land(to, v, flick, flick);
        }

        _onCancel(e) {
            const d = this.drag;
            if (!d || e.pointerId !== d.id) return;
            this._release();
            if (!d.live) return;
            if (this.reduce) this._jumpTo(this.index);
            else this._land(this.index, null, false, false);
        }

        _onKey(e) {
            if (this.o.disabled) return;
            const last = this.list.length - 1;
            let next = null;
            if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = Math.min(last, this.index + 1);
            else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = Math.max(0, this.index - 1);
            else if (e.key === 'Home') next = 0;
            else if (e.key === 'End') next = last;
            if (next === null) return;
            e.preventDefault();
            if (next === this.index) return;
            this._commit(next);
            this._jumpTo(next);
            this.buttons[next]?.focus();
        }
    }

    window.RubberSegment = RubberSegment;
})();
