/* Swipe toast: a small card that rises from the bottom-right corner, with a "fuse" line that
 * burns down until it closes itself. Swipe it down (or press Escape) to dismiss early.
 * Vanilla-JS port of the <SwipeToast /> component from React Bits (https://reactbits.dev),
 * without the motion / hugeicons dependencies, and limited to one toast at a time.
 *
 *   window.SwipeToast.show({ title, description, actionLabel, onAction, duration, ... })
 *
 * Showing it again while it is open re-arms the fuse instead of stacking another card.
 * Nothing runs while no toast is shown; the fuse is a compositor-only transform animation. */
(function () {
    'use strict';

    const EASE_OUT = 'cubic-bezier(0.23, 1, 0.32, 1)';
    const FLICK = 0.11;          // px/ms: a downward flick faster than this always dismisses
    const DEAD_ZONE = 3;         // px of movement before a press becomes a drag
    const RESIST_PX = 24;        // rubber-band strength when dragged upward
    const EXIT = 0.7;            // the drop takes 70% of slideMs
    const BURN = [{ transform: 'scaleX(1)' }, { transform: 'scaleX(0)' }];

    const DEFAULTS = {
        title: 'File archived',
        description: '',
        actionLabel: '',
        onAction: null,
        onClose: null,
        background: '#27272a',
        color: '#f5f5f5',
        fuseColor: '#f5a524',
        width: 356,
        radius: 12,
        slideMs: 400,
        settleBounce: 0.2,
        swipeDistance: 40,
        duration: 4000,
        fuse: 'bottom',
        pauseOnHover: true,
        dismissible: true
    };

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let current = null;

    const rubberband = (over, dim, c = 0.55) => (over * dim * c) / (dim + c * Math.abs(over));
    const velocityOf = hist => {
        if (hist.length < 2) return 0;
        const [t0, y0] = hist[0];
        const [t1, y1] = hist[hist.length - 1];
        return performance.now() - t1 > 100 ? 0 : (y1 - y0) / Math.max(1, t1 - t0);
    };
    const translateOf = el => {
        const m = getComputedStyle(el).transform;
        return m && m !== 'none' ? new DOMMatrixReadOnly(m).m42 : 0;
    };

    function create(o) {
        const root = document.createElement('div');
        root.className = 'swipe-toast';
        root.dataset.phase = 'open';
        root.dataset.mounted = 'false';
        root.dataset.fuse = o.duration > 0 ? o.fuse : 'none';
        root.dataset.dismissible = String(!!o.dismissible);
        root.style.setProperty('--st-bg', o.background);
        root.style.setProperty('--st-ink', o.color);
        root.style.setProperty('--st-fuse', o.fuseColor);
        root.style.setProperty('--st-w', o.width + 'px');
        root.style.setProperty('--st-radius', o.radius + 'px');
        root.style.setProperty('--st-slide', o.slideMs + 'ms');

        const lift = document.createElement('div');
        lift.className = 'swipe-toast__lift';
        const card = document.createElement('div');
        card.className = 'swipe-toast__card';
        card.setAttribute('role', 'status');
        card.setAttribute('aria-live', 'polite');
        card.setAttribute('aria-atomic', 'true');
        card.tabIndex = 0;

        const body = document.createElement('span');
        body.className = 'swipe-toast__body';
        const titleEl = document.createElement('span');
        titleEl.className = 'swipe-toast__title';
        const descEl = document.createElement('span');
        descEl.className = 'swipe-toast__desc';
        body.append(titleEl, descEl);
        card.appendChild(body);

        const action = document.createElement('button');
        action.type = 'button';
        action.className = 'swipe-toast__action';
        card.appendChild(action);

        const fuseEl = document.createElement('i');
        fuseEl.className = 'swipe-toast__fuse';
        fuseEl.setAttribute('aria-hidden', 'true');
        card.appendChild(fuseEl);

        lift.appendChild(card);
        root.appendChild(lift);

        let phase = 'open';
        let leaving = false;
        let burn = null;
        let settle = null;
        let drag = null;
        let pendingClose = null;
        let closeTimer = 0;
        let lastInput = 'pointer';
        let y = 0;
        const flags = { hover: false, interacting: false, focus: false, hidden: document.hidden };

        const setY = value => {
            y = value;
            card.style.transform = value ? 'translateY(' + value + 'px)' : '';
        };
        const setPhase = value => {
            phase = value;
            root.dataset.phase = value;
        };
        const syncFuse = () => {
            if (!burn) return;
            if (flags.hover || flags.interacting || flags.focus || flags.hidden) burn.pause();
            else if (burn.playState === 'paused') burn.play();
        };
        const arm = () => {
            if (burn) {
                burn.onfinish = null;
                burn.cancel();
                burn = null;
            }
            if (o.duration <= 0) return;
            burn = fuseEl.animate(BURN, { duration: o.duration, easing: 'linear', fill: 'forwards' });
            burn.onfinish = () => close('timeout');
            syncFuse();
        };
        const fill = next => {
            o = next;
            titleEl.textContent = o.title;
            descEl.textContent = o.description;
            descEl.hidden = !o.description;
            action.textContent = o.actionLabel;
            action.hidden = !o.actionLabel;
        };

        const finish = why => {
            setPhase('gone');
            leaving = false;
            if (burn) burn.cancel();
            document.removeEventListener('visibilitychange', onVisibility);
            root.remove();
            if (current === api) current = null;
            if (o.onClose) o.onClose(why);
        };
        const close = why => {
            if (phase !== 'open' || leaving) return;
            if (drag) {
                pendingClose = why;
                return;
            }
            if (burn) burn.pause();
            const now = why === 'escape' || (why === 'action' && lastInput === 'keyboard');
            if (now) root.dataset.instant = '';
            setPhase('closing');
            clearTimeout(closeTimer);
            closeTimer = setTimeout(() => finish(why), now ? 0 : o.slideMs * EXIT + 60);
        };
        const rescue = () => {
            clearTimeout(closeTimer);
            delete root.dataset.instant;
            card.style.opacity = '';
            setY(0);
            setPhase('open');
        };

        const swipeOut = (dy, v) => {
            if (burn) burn.pause();
            leaving = true;
            pendingClose = null;
            const to = dy + card.offsetHeight;
            if (!reduce) {
                card.animate(
                    [{ transform: 'translateY(' + dy + 'px)' }, { transform: 'translateY(' + to + 'px)' }],
                    { duration: Math.max(120, 300 - v * 400), easing: EASE_OUT, fill: 'forwards' }
                );
            }
            card.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, easing: EASE_OUT, fill: 'forwards' })
                .onfinish = () => finish('swipe');
        };
        const settleBack = () => {
            const from = y;
            setY(0);
            // A touch of overshoot on the way back, scaled by settleBounce (0 stops dead).
            const easing = reduce || o.settleBounce <= 0
                ? EASE_OUT
                : 'cubic-bezier(0.34, ' + (1 + o.settleBounce * 2).toFixed(2) + ', 0.64, 1)';
            settle = card.animate(
                [{ transform: 'translateY(' + from + 'px)' }, { transform: 'translateY(0px)' }],
                { duration: reduce ? 200 : 500, easing: easing }
            );
            settle.onfinish = () => { settle = null; };
        };

        card.addEventListener('pointerdown', e => {
            lastInput = 'pointer';
            if (e.button !== 0 || !o.dismissible || drag || leaving || e.target.closest('button')) return;
            if (phase === 'closing') rescue();
            if (settle) {
                const at = translateOf(card);
                settle.cancel();
                settle = null;
                setY(at);
            }
            try { card.setPointerCapture(e.pointerId); } catch (err) { /* already released */ }
            drag = { id: e.pointerId, startY: e.clientY, grab: null, moved: false, hist: [[performance.now(), y]] };
            flags.interacting = true;
            syncFuse();
        });
        card.addEventListener('pointermove', e => {
            if (!drag || drag.id !== e.pointerId) return;
            if (drag.grab === null) {
                if (Math.abs(e.clientY - drag.startY) < DEAD_ZONE) return;
                drag.grab = e.clientY - y;
                card.dataset.swiping = '';
            }
            const raw = e.clientY - drag.grab;
            setY(raw >= 0 ? raw : rubberband(raw, RESIST_PX));
            drag.moved = true;
            drag.hist.push([performance.now(), y]);
            if (drag.hist.length > 4) drag.hist.shift();
        });
        const onPointerUp = e => {
            if (!drag || drag.id !== e.pointerId) return;
            const d = drag;
            drag = null;
            delete card.dataset.swiping;
            try { card.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
            flags.interacting = false;
            const v = velocityOf(d.hist);
            if (y > 0 && (v > FLICK || (y >= o.swipeDistance && v >= 0))) {
                swipeOut(y, v);
                return;
            }
            if (d.moved) settleBack();
            const queued = pendingClose;
            pendingClose = null;
            if (queued) close(queued);
            else syncFuse();
        };
        card.addEventListener('pointerup', onPointerUp);
        card.addEventListener('pointercancel', onPointerUp);
        card.addEventListener('pointerenter', e => {
            if (o.pauseOnHover && e.pointerType === 'mouse') {
                flags.hover = true;
                syncFuse();
            }
        });
        card.addEventListener('pointerleave', e => {
            if (e.pointerType === 'mouse') {
                flags.hover = false;
                syncFuse();
            }
        });
        card.addEventListener('focusin', () => {
            flags.focus = true;
            syncFuse();
        });
        card.addEventListener('focusout', e => {
            if (!card.contains(e.relatedTarget)) {
                flags.focus = false;
                syncFuse();
            }
        });
        card.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') lastInput = 'keyboard';
            if (e.key === 'Escape' && o.dismissible) {
                e.stopPropagation();
                close('escape');
            }
        });
        action.addEventListener('click', () => {
            if (o.onAction) o.onAction();
            close('action');
        });
        function onVisibility() {
            flags.hidden = document.hidden;
            syncFuse();
        }
        document.addEventListener('visibilitychange', onVisibility);

        fill(o);
        document.body.appendChild(root);
        void root.offsetWidth;            // commit the hidden start state so the rise animates
        root.dataset.mounted = 'true';
        arm();

        const api = {
            update(next) {
                if (leaving) return;
                fill(next);
                root.dataset.fuse = o.duration > 0 ? o.fuse : 'none';
                if (phase !== 'open') rescue();
                arm();
            },
            close: () => close('programmatic')
        };
        return api;
    }

    window.SwipeToast = {
        show(options) {
            const o = Object.assign({}, DEFAULTS, options);
            if (current) current.update(o);
            else current = create(o);
            return current;
        }
    };
})();
