/* Lightweight canvas StarBorder effect for the primary download link. */
(function initStarBorder() {
    const root = document.querySelector('.star-border');
    if (!root) return;

    const canvas = document.createElement('canvas');
    canvas.className = 'star-border__light';
    canvas.setAttribute('aria-hidden', 'true');
    root.prepend(canvas);

    const context = canvas.getContext('2d');
    if (!context) return;

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const bleed = 48;
    const thickness = 1;
    const stars = 4;
    const duration = 12;
    const trailLength = 0.5;
    const radius = 12;
    let width = 0;
    let height = 0;
    let dpr = 1;
    let phase = 0;
    let lapStarted = -1;
    let lastTime = 0;
    let frameId = 0;
    let visible = false;
    const pulses = [];

    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const wrap = (value, total) => ((value % total) + total) % total;
    const ease = value => value < 0.5 ? 4 * value ** 3 : 1 - ((-2 * value + 2) ** 3) / 2;

    function makeShape() {
        const r = Math.min(radius, width / 2, height / 2);
        const arc = Math.PI * r / 2;
        const across = Math.max(0, width - 2 * r);
        const down = Math.max(0, height - 2 * r);
        const segments = [
            { length: across, x: r, y: 0, dx: 1, dy: 0 },
            { length: arc, x: width - r, y: r, angle: -Math.PI / 2 },
            { length: down, x: width, y: r, dx: 0, dy: 1 },
            { length: arc, x: width - r, y: height - r, angle: 0 },
            { length: across, x: width - r, y: height, dx: -1, dy: 0 },
            { length: arc, x: r, y: height - r, angle: Math.PI / 2 },
            { length: down, x: 0, y: height - r, dx: 0, dy: -1 },
            { length: arc, x: r, y: r, angle: Math.PI }
        ];
        return { r, segments, total: segments.reduce((sum, segment) => sum + segment.length, 0) };
    }

    function pointAt(shape, distance) {
        let remaining = wrap(distance, shape.total);
        for (const segment of shape.segments) {
            if (remaining <= segment.length) {
                if (segment.angle === undefined) {
                    return {
                        x: segment.x + segment.dx * remaining,
                        y: segment.y + segment.dy * remaining
                    };
                }
                const angle = segment.angle + remaining / shape.r;
                return {
                    x: segment.x + Math.cos(angle) * shape.r,
                    y: segment.y + Math.sin(angle) * shape.r
                };
            }
            remaining -= segment.length;
        }
        return { x: 0, y: 0 };
    }

    function drawTrail(shape, head, length, intensity) {
        if (length <= 0 || intensity <= 0) return;
        const steps = clamp(Math.ceil(length / 3), 2, 90);
        let previous = pointAt(shape, head - length);
        for (let index = 1; index <= steps; index++) {
            const progress = index / steps;
            const current = pointAt(shape, head - length * (1 - progress));
            const alpha = intensity * progress * progress;
            context.lineCap = index === steps ? 'round' : 'butt';
            context.lineWidth = 2;
            context.strokeStyle = `rgba(255, 255, 255, ${alpha * 0.22})`;
            context.beginPath();
            context.moveTo(previous.x, previous.y);
            context.lineTo(current.x, current.y);
            context.stroke();
            context.lineWidth = thickness;
            context.strokeStyle = `rgba(255, 255, 255, ${alpha})`;
            context.beginPath();
            context.moveTo(previous.x, previous.y);
            context.lineTo(current.x, current.y);
            context.stroke();
            previous = current;
        }
    }

    function drawHead(point, intensity, time) {
        const reach = 20;
        const bloom = context.createRadialGradient(point.x, point.y, 0, point.x, point.y, reach);
        bloom.addColorStop(0, `rgba(255, 255, 255, ${0.42 * intensity})`);
        bloom.addColorStop(0.25, `rgba(255, 255, 255, ${0.16 * intensity})`);
        bloom.addColorStop(1, 'rgba(255, 255, 255, 0)');
        context.fillStyle = bloom;
        context.fillRect(point.x - reach, point.y - reach, reach * 2, reach * 2);

        const sparkle = 3 + 2 * (0.5 + 0.5 * Math.sin(time * 6));
        context.strokeStyle = `rgba(255, 255, 255, ${0.9 * intensity})`;
        context.lineWidth = 0.8;
        context.beginPath();
        context.moveTo(point.x - sparkle, point.y);
        context.lineTo(point.x + sparkle, point.y);
        context.moveTo(point.x, point.y - sparkle);
        context.lineTo(point.x, point.y + sparkle);
        context.stroke();
    }

    function draw(now) {
        frameId = 0;
        if (!visible) return;

        const time = now / 1000;
        const dt = lastTime ? Math.min(0.05, (now - lastTime) / 1000) : 0;
        lastTime = now;
        const shape = makeShape();
        if (shape.total <= 0) return;

        let distance = shape.total / duration * dt;
        if (lapStarted >= 0) {
            const before = ease(Math.min(1, lapStarted / 0.9));
            lapStarted += dt;
            distance += shape.total * (ease(Math.min(1, lapStarted / 0.9)) - before);
            if (lapStarted >= 0.9) lapStarted = -1;
        }
        phase = wrap(phase + distance, shape.total);

        context.setTransform(1, 0, 0, 1, 0, 0);
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.setTransform(dpr, 0, 0, dpr, bleed * dpr, bleed * dpr);
        context.lineWidth = thickness;
        context.strokeStyle = 'rgba(255, 255, 255, 0.1)';
        context.beginPath();
        context.roundRect(0.5, 0.5, width - 1, height - 1, radius);
        context.stroke();

        const spacing = shape.total / stars;
        const trail = Math.min(shape.total * trailLength, spacing * 0.92);
        for (let index = 0; index < stars; index++) {
            const head = phase + spacing * index;
            drawTrail(shape, head, trail, 1);
            drawHead(pointAt(shape, head), 1, time + index);
        }

        for (let index = pulses.length - 1; index >= 0; index--) {
            const pulse = pulses[index];
            pulse.age += dt;
            if (pulse.age >= 0.65) {
                pulses.splice(index, 1);
                continue;
            }
            const progress = pulse.age / 0.65;
            const travel = shape.total / 2 * (1 - (1 - progress) ** 2);
            const trailLengthPx = Math.min(travel, shape.total * 0.16);
            for (const direction of [-1, 1]) {
                const head = pulse.origin + direction * travel;
                drawTrail(shape, head, trailLengthPx, (1 - progress) ** 2);
                drawHead(pointAt(shape, head), (1 - progress) ** 2, time);
            }
        }

        if (!reduceMotion || pulses.length) frameId = requestAnimationFrame(draw);
    }

    function wake() {
        if (visible && !frameId) frameId = requestAnimationFrame(draw);
    }

    function resize() {
        width = root.clientWidth;
        height = root.clientHeight;
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.max(1, Math.round((width + bleed * 2) * dpr));
        canvas.height = Math.max(1, Math.round((height + bleed * 2) * dpr));
        wake();
    }

    function distanceFromPointer(event, shape) {
        const rect = root.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const y = event.clientY - rect.top;
        let nearest = 0;
        let nearestDistance = Infinity;
        for (let distance = 0; distance < shape.total; distance += 2) {
            const point = pointAt(shape, distance);
            const candidate = (point.x - x) ** 2 + (point.y - y) ** 2;
            if (candidate < nearestDistance) {
                nearestDistance = candidate;
                nearest = distance;
            }
        }
        return nearest;
    }

    root.addEventListener('pointerenter', () => {
        if (!reduceMotion) {
            lapStarted = 0;
            wake();
        }
    });
    root.addEventListener('pointerdown', event => {
        if (event.button !== 0 && event.pointerType === 'mouse') return;
        pulses.push({ origin: distanceFromPointer(event, makeShape()), age: 0 });
        if (pulses.length > 4) pulses.shift();
        wake();
    });
    root.addEventListener('focus', () => {
        if (!reduceMotion) {
            lapStarted = 0;
            wake();
        }
    });

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(root);
    const visibilityObserver = new IntersectionObserver(entries => {
        visible = entries.some(entry => entry.isIntersecting);
        if (visible) wake();
        else {
            cancelAnimationFrame(frameId);
            frameId = 0;
            lastTime = 0;
        }
    });
    visibilityObserver.observe(root);
})();
