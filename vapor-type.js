/* Canvas vapor effect for the static site wordmark. */
(function initVaporWordmark() {
    const container = document.querySelector('.site-banner-title');
    const canvas = document.getElementById('site-wordmark');
    const context = canvas?.getContext('2d');
    if (!container || !canvas || !context) return;

    const text = 'ST-SoftwareTool';
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const particleGap = 2.1;
    const cycleDuration = 8.2;
    const dissolveStart = 3.2;
    const dissolveDuration = 1.7;
    const condenseStart = 6.2;
    const condenseDuration = 1.7;
    const particles = [];
    let width = 1;
    let height = 1;
    let dpr = 1;
    let font = '';
    let baseline = 0;
    let frameId = 0;
    let startTime = 0;
    let visible = true;
    let pointer = null;
    let pointerStrength = 0;

    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const smooth = value => {
        const t = clamp(value, 0, 1);
        return t * t * (3 - 2 * t);
    };

    function resize() {
        const bounds = container.getBoundingClientRect();
        width = Math.max(1, bounds.width);
        height = Math.max(1, bounds.height);
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;
        context.setTransform(dpr, 0, 0, dpr, 0, 0);

        const measureContext = document.createElement('canvas').getContext('2d');
        if (!measureContext) return;
        const styles = getComputedStyle(container);
        const family = styles.fontFamily || 'Arial, sans-serif';
        const weight = '900';
        const spacing = Math.max(1, Math.min(7, width * 0.006));
        let fontSize = Math.min(83, Math.max(40, window.innerWidth * 0.05));
        const makeFont = size => `${weight} ${size}px ${family}`;
        measureContext.font = makeFont(fontSize);
        const fullWidth = measureContext.measureText(text).width + spacing * (text.length - 1);
        if (fullWidth > width - 12) fontSize *= (width - 12) / fullWidth;
        fontSize = Math.max(18, fontSize);
        font = makeFont(fontSize);
        measureContext.font = font;

        const textWidth = measureContext.measureText(text).width + spacing * (text.length - 1);
        let x = (width - textWidth) / 2;
        baseline = (height + fontSize * 0.68) / 2;
        const mask = document.createElement('canvas');
        mask.width = Math.ceil(width);
        mask.height = Math.ceil(height);
        const maskContext = mask.getContext('2d', { willReadFrequently: true });
        if (!maskContext) return;
        maskContext.font = font;
        maskContext.textBaseline = 'alphabetic';
        maskContext.fillStyle = '#fff';
        Array.from(text).forEach(character => {
            maskContext.fillText(character, x, baseline);
            x += maskContext.measureText(character).width + spacing;
        });

        const image = maskContext.getImageData(0, 0, mask.width, mask.height);
        particles.length = 0;
        const stride = Math.max(1, Math.round(particleGap));
        for (let y = 0; y < mask.height; y += stride) {
            for (let px = 0; px < mask.width; px += stride) {
                if (image.data[(y * mask.width + px) * 4 + 3] < 96) continue;
                particles.push({
                    x: px,
                    y,
                    order: px / Math.max(1, width) + Math.random() * 0.18,
                    seed: Math.random() * Math.PI * 2,
                    drift: 0.6 + Math.random() * 0.8,
                    size: 0.8 + Math.random() * 0.7
                });
            }
        }

        startTime = performance.now();
        requestDraw();
    }

    function requestDraw() {
        if (!frameId && visible) frameId = requestAnimationFrame(draw);
    }

    function draw(now) {
        frameId = 0;
        if (!visible) return;
        context.setTransform(dpr, 0, 0, dpr, 0, 0);
        context.clearRect(0, 0, width, height);

        if (reducedMotion) {
            context.font = font;
            context.textBaseline = 'alphabetic';
            context.textAlign = 'center';
            context.fillStyle = '#ffffff';
            context.fillText(text, width / 2, baseline);
            return;
        }

        const elapsed = (now - startTime) / 1000;
        const phase = elapsed % cycleDuration;
        let dissolve = 0;
        let condense = 0;
        if (phase >= dissolveStart && phase < dissolveStart + dissolveDuration) {
            dissolve = smooth((phase - dissolveStart) / dissolveDuration);
        } else if (phase >= dissolveStart + dissolveDuration && phase < condenseStart) {
            dissolve = 1;
        } else if (phase >= condenseStart) {
            dissolve = 1 - smooth((phase - condenseStart) / condenseDuration);
        }

        pointerStrength *= 0.92;
        const time = elapsed * 1.8;
        context.textAlign = 'left';
        for (const particle of particles) {
            const sweep = smooth((dissolve - particle.order * 0.62) / 0.38);
            let x = particle.x;
            let y = particle.y;
            let alpha = 1;

            if (sweep > 0) {
                const travel = sweep * particle.drift;
                const swirl = Math.sin(time + particle.seed + travel * 2.4) * 12 * travel;
                x += Math.cos(particle.seed + time * 0.4) * 9 * travel + swirl;
                y -= (18 + 34 * particle.drift) * travel + Math.abs(swirl) * 0.4;
                alpha = 1 - sweep * 0.94;
            }

            if (pointer && pointerStrength > 0.02) {
                const dx = x - pointer.x;
                const dy = y - pointer.y;
                const distance = Math.hypot(dx, dy);
                const reach = 95;
                if (distance < reach && distance > 0) {
                    const force = (1 - distance / reach) * pointerStrength;
                    x += (dx / distance) * force * 24;
                    y += (dy / distance) * force * 24 - force * 13;
                    alpha *= 1 - force * 0.45;
                }
            }

            if (alpha <= 0.025 || y < -12) continue;
            context.globalAlpha = alpha;
            context.fillStyle = '#ffffff';
            context.fillRect(x, y, particle.size, particle.size);
        }
        context.globalAlpha = 1;
        requestDraw();
    }

    function onPointerMove(event) {
        const bounds = canvas.getBoundingClientRect();
        pointer = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
        pointerStrength = 1;
        requestDraw();
    }

    function onPointerLeave() {
        pointer = null;
    }

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(container);
    canvas.addEventListener('pointermove', onPointerMove, { passive: true });
    canvas.addEventListener('pointerleave', onPointerLeave);
    document.addEventListener('visibilitychange', () => {
        visible = !document.hidden;
        if (visible) requestDraw();
        else {
            cancelAnimationFrame(frameId);
            frameId = 0;
        }
    });
    document.fonts?.ready.then(resize);
    resize();
})();
