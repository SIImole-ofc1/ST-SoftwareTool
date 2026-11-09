/* Rotating target brackets that align to interactive elements */
(function initTargetCursor() {
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;

    const root = document.createElement('div');
    root.className = 'target-cursor';
    root.setAttribute('aria-hidden', 'true');
    root.innerHTML = '<span></span><span></span><span></span><span></span><i></i><b></b>';
    document.body.appendChild(root);

    const corners = [...root.querySelectorAll('span')];
    const dot = root.querySelector('i');
    const label = root.querySelector('b');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const pointer = { x: 0, y: 0, inside: false, pressed: false, exempt: false, seen: false };
    const frameBox = { x: 0, y: 0, halfWidth: 18, halfHeight: 18 };
    const cursor = { angle: 0, opacity: 0 };
    let target = null;
    let animationFrame = 0;
    let lastTime = 0;
    let targetRadius = 0;

    function setNativeCursorHidden(hidden) {
        document.documentElement.classList.toggle('target-cursor-enabled', hidden);
    }

    function setTarget(element) {
        const next = element instanceof Element
            ? element.closest('a, button, [data-cursor-target]')
            : null;
        if (next === target) return;
        target = next;
        if (target) targetRadius = parseFloat(getComputedStyle(target).borderTopLeftRadius) || 0;
    }

    function wake() {
        if (!animationFrame && !document.hidden) {
            animationFrame = requestAnimationFrame(animate);
        }
    }

    function animate(now) {
        animationFrame = 0;
        const dt = lastTime ? Math.min(0.05, (now - lastTime) / 1000) : 1 / 60;
        lastTime = now;
        if (target && !target.isConnected) target = null;

        const locked = Boolean(target && pointer.inside);
        const rect = locked ? target.getBoundingClientRect() : null;
        const goalX = rect ? rect.left + rect.width / 2 : pointer.x;
        const goalY = rect ? rect.top + rect.height / 2 : pointer.y;
        const goalHalfWidth = rect ? rect.width / 2 + 6 : 18;
        const goalHalfHeight = rect ? rect.height / 2 + 6 : 18;
        const ease = reducedMotion ? 1 : 1 - Math.exp(-dt / (locked ? 0.075 : 0.045));

        frameBox.x += (goalX - frameBox.x) * ease;
        frameBox.y += (goalY - frameBox.y) * ease;
        frameBox.halfWidth += (goalHalfWidth - frameBox.halfWidth) * ease;
        frameBox.halfHeight += (goalHalfHeight - frameBox.halfHeight) * ease;
        if (Math.abs(goalX - frameBox.x) < 0.03) frameBox.x = goalX;
        if (Math.abs(goalY - frameBox.y) < 0.03) frameBox.y = goalY;
        if (Math.abs(goalHalfWidth - frameBox.halfWidth) < 0.03) frameBox.halfWidth = goalHalfWidth;
        if (Math.abs(goalHalfHeight - frameBox.halfHeight) < 0.03) frameBox.halfHeight = goalHalfHeight;

        if (locked || reducedMotion) {
            cursor.angle += (0 - cursor.angle) * (reducedMotion ? 1 : 1 - Math.exp(-dt / 0.08));
        } else {
            cursor.angle = (cursor.angle + Math.PI * 2 * dt / 2) % (Math.PI * 2);
        }

        const opacityGoal = pointer.inside && !pointer.exempt ? 1 : 0;
        cursor.opacity = pointer.exempt
            ? 0
            : cursor.opacity + (opacityGoal - cursor.opacity) * (reducedMotion ? 1 : 1 - Math.exp(-dt / 0.08));
        if (Math.abs(opacityGoal - cursor.opacity) < 0.002) cursor.opacity = opacityGoal;

        const pressScale = pointer.pressed ? 0.9 : 1;
        const halfWidth = frameBox.halfWidth * pressScale;
        const halfHeight = frameBox.halfHeight * pressScale;
        const angle = locked ? 0 : cursor.angle;
        const cornerOffsets = [
            [-halfWidth, -halfHeight],
            [halfWidth, -halfHeight],
            [halfWidth, halfHeight],
            [-halfWidth, halfHeight]
        ];
        root.style.opacity = String(cursor.opacity);
        root.style.setProperty('--cursor-color', '#ffffff');
        root.style.setProperty('--cursor-radius', `${locked ? Math.min(targetRadius + 6, 12) : 2}px`);

        corners.forEach((corner, index) => {
            const [offsetX, offsetY] = cornerOffsets[index];
            const cos = Math.cos(angle);
            const sin = Math.sin(angle);
            const x = frameBox.x + offsetX * cos - offsetY * sin;
            const y = frameBox.y + offsetX * sin + offsetY * cos;
            const rotation = (index * 90 + angle * 180 / Math.PI) % 360;
            corner.style.transform = `translate3d(${x}px, ${y}px, 0) rotate(${rotation}deg)`;
        });

        dot.style.transform = `translate3d(${pointer.x}px, ${pointer.y}px, 0) translate(-50%, -50%) scale(${pointer.pressed ? 0.65 : 1})`;

        const text = locked ? target.getAttribute('data-cursor-label') || '' : '';
        label.textContent = text;
        label.style.left = `${frameBox.x - halfWidth}px`;
        label.style.top = `${frameBox.y - halfHeight - 8}px`;
        label.style.opacity = text ? String(cursor.opacity) : '0';

        const spinning = pointer.inside && !locked && !reducedMotion;
        const settling = spinning ||
            Math.abs(goalX - frameBox.x) + Math.abs(goalY - frameBox.y) > 0.05 ||
            Math.abs(goalHalfWidth - frameBox.halfWidth) + Math.abs(goalHalfHeight - frameBox.halfHeight) > 0.05 ||
            Math.abs(opacityGoal - cursor.opacity) > 0.002;
        if (settling) animationFrame = requestAnimationFrame(animate);
        else lastTime = 0;
    }

    window.addEventListener('pointermove', event => {
        if (event.pointerType && event.pointerType !== 'mouse') return;
        pointer.x = event.clientX;
        pointer.y = event.clientY;
        pointer.seen = true;
        const overWordmark = event.target instanceof Element && event.target.closest('.site-banner-title');
        pointer.exempt = Boolean(overWordmark);
        pointer.inside = !overWordmark;
        setNativeCursorHidden(pointer.inside);
        setTarget(pointer.inside ? event.target : null);
        wake();
    }, { passive: true });

    window.addEventListener('pointerdown', event => {
        if (event.pointerType && event.pointerType !== 'mouse') return;
        pointer.pressed = true;
        wake();
    }, { passive: true });

    window.addEventListener('pointerup', () => {
        pointer.pressed = false;
        wake();
    }, { passive: true });

    window.addEventListener('pointerout', event => {
        if (!event.relatedTarget) {
            pointer.inside = false;
            pointer.pressed = false;
            pointer.exempt = false;
            setNativeCursorHidden(false);
            setTarget(null);
            wake();
        }
    }, { passive: true });

    window.addEventListener('scroll', () => {
        if (pointer.inside && pointer.seen) {
            setTarget(document.elementFromPoint(pointer.x, pointer.y));
            wake();
        }
    }, { capture: true, passive: true });

    window.addEventListener('blur', () => {
        pointer.inside = false;
        pointer.pressed = false;
        pointer.exempt = false;
        setNativeCursorHidden(false);
        setTarget(null);
        wake();
    });

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            cancelAnimationFrame(animationFrame);
            animationFrame = 0;
            root.style.opacity = '0';
            setNativeCursorHidden(false);
        } else if (pointer.inside) {
            setNativeCursorHidden(true);
            wake();
        }
    });
})();
