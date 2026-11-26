/* Gyro cursor — an arrow cursor that turns to face the direction it moves.
   Original implementation for this site (inspired by the idea of React Bits Pro's "Gyroscope Cursor").
   The arrow's tip is the click point, so it rotates around the tip. When the pointer rests,
   the arrow eases back to the classic up-left pointer angle.
   Only active on devices with a precise hovering pointer (mouse/trackpad). */
(function initGyroCursor() {
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const REST_ANGLE = -135;          // degrees; the arrow points right at 0, so -135 is the usual up-left pointer
    const TURN_RATE = 14;             // how quickly the arrow turns toward its heading (higher = snappier)
    const MIN_SPEED = 60;             // px/s; below this the heading is not updated, so small jitters don't spin it
    const REST_AFTER = 0.55;          // seconds without movement before the arrow returns to the rest angle
    const INTERACTIVE = 'a, button, [role="button"], summary, label, select, input[type="checkbox"], input[type="radio"], input[type="submit"], input[type="button"], [data-cursor-target]';
    const TEXT_FIELDS = 'input:not([type="checkbox"]):not([type="radio"]):not([type="submit"]):not([type="button"]), textarea, [contenteditable="true"]';

    const root = document.createElement('div');
    root.className = 'gyro-cursor';
    root.setAttribute('aria-hidden', 'true');
    root.innerHTML =
        '<svg class="gyro-cursor-arrow" viewBox="-21 -11 23 22" width="23" height="22">' +
        '<path d="M0 0 L-19 -9 L-13.5 0 L-19 9 Z" />' +
        '</svg>';
    document.body.appendChild(root);

    const pointer = { x: 0, y: 0, vx: 0, vy: 0, t: 0, inside: false };
    let angle = REST_ANGLE;
    let heading = REST_ANGLE;
    let still = 0;
    let raf = 0;
    let last = 0;

    const shortest = delta => ((delta + 540) % 360) - 180;

    function draw() {
        root.style.transform = `translate3d(${pointer.x}px, ${pointer.y}px, 0) rotate(${angle.toFixed(2)}deg)`;
    }

    function frame(now) {
        raf = 0;
        const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60;
        last = now;
        still += dt;

        const speed = Math.hypot(pointer.vx, pointer.vy);
        if (!reduce && speed > MIN_SPEED && still < 0.08) {
            heading = (Math.atan2(pointer.vy, pointer.vx) * 180) / Math.PI;
        } else if (still > REST_AFTER || reduce) {
            heading = REST_ANGLE;
        }
        const delta = shortest(heading - angle);
        angle += reduce ? delta : delta * (1 - Math.exp(-dt * TURN_RATE));
        angle = ((angle + 540) % 360) - 180; // stay within one turn (rendering is identical)
        draw();

        // Keep animating while turning or while waiting to settle back to rest.
        if (Math.abs(delta) > 0.1 || (heading !== REST_ANGLE && still <= REST_AFTER)) {
            raf = requestAnimationFrame(frame);
        } else {
            last = 0;
        }
    }

    function wake() {
        if (!raf) raf = requestAnimationFrame(frame);
    }

    window.addEventListener('pointermove', event => {
        if (event.pointerType && event.pointerType !== 'mouse') return;
        const now = event.timeStamp / 1000;
        const dt = pointer.t ? Math.max(0.001, now - pointer.t) : 0;
        if (dt > 0 && dt < 0.2) {
            const blend = 1 - Math.exp(-dt * 30);
            pointer.vx += ((event.clientX - pointer.x) / dt - pointer.vx) * blend;
            pointer.vy += ((event.clientY - pointer.y) / dt - pointer.vy) * blend;
        } else {
            pointer.vx = 0;
            pointer.vy = 0;
        }
        pointer.x = event.clientX;
        pointer.y = event.clientY;
        pointer.t = now;
        still = 0;

        if (!pointer.inside) {
            pointer.inside = true;
            document.documentElement.classList.add('gyro-cursor-enabled');
        }
        const target = event.target instanceof Element ? event.target : null;
        const inText = Boolean(target?.closest(TEXT_FIELDS));
        root.classList.toggle('is-visible', !inText);
        root.classList.toggle('is-hovering', Boolean(target?.closest(INTERACTIVE)));
        draw();
        wake();
    }, { passive: true });

    window.addEventListener('pointerdown', () => root.classList.add('is-pressed'));
    window.addEventListener('pointerup', () => root.classList.remove('is-pressed'));
    window.addEventListener('pointerout', event => {
        if (!event.relatedTarget) {
            pointer.inside = false;
            pointer.t = 0;
            root.classList.remove('is-visible', 'is-pressed');
        }
    });
    window.addEventListener('blur', () => root.classList.remove('is-visible', 'is-pressed'));
})();
