/* Cursor-reactive ASCII wave background */
(function initAsciiWaves() {
    const canvas = document.getElementById('site-background');
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;

    let width = 0;
    let height = 0;
    let animationId = 0;
    let pointerX = -10000;
    let pointerY = -10000;
    let targetX = -10000;
    let targetY = -10000;
    let lastFrame = 0;
    let time = 0;
    let pageVisible = !document.hidden;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const characters = ' .:-+*=%@#';
    const cellSize = 20;
    const fontSize = 13;
    const frameInterval = 1000 / 30;
    const gridFont = `${fontSize}px "Courier New", monospace`;
    let buckets = [];
    let columns = 0;
    let rows = 0;

    function resize() {
        const ratio = Math.min(window.devicePixelRatio || 1, 1);
        width = window.innerWidth;
        height = window.innerHeight;
        canvas.width = Math.max(1, Math.floor(width * ratio));
        canvas.height = Math.max(1, Math.floor(height * ratio));
        canvas.style.width = width + 'px';
        canvas.style.height = height + 'px';
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.font = gridFont;
        if ('letterSpacing' in ctx) {
            ctx.letterSpacing = `${Math.max(0, cellSize - ctx.measureText('M').width)}px`;
        }
        columns = Math.ceil(width / cellSize);
        rows = Math.ceil(height / cellSize);
        buckets = [new Array(columns), new Array(columns), new Array(columns)];
        if (reducedMotion) drawFrame(performance.now());
    }

    function drawFrame(now) {
        animationId = 0;
        if (!pageVisible) return;
        if (!reducedMotion && now - lastFrame < frameInterval) {
            animationId = requestAnimationFrame(drawFrame);
            return;
        }
        const dt = lastFrame ? Math.min((now - lastFrame) / 1000, 0.05) : 0;
        lastFrame = now;
        if (!reducedMotion) time += dt;
        const pointerEase = 1 - Math.exp(-14 * Math.max(dt, 1 / 60));
        pointerX += (targetX - pointerX) * pointerEase;
        pointerY += (targetY - pointerY) * pointerEase;
        const t = time;
        ctx.clearRect(0, 0, width, height);

        const cursorRadius = 240;
        const cursorRadiusSquared = cursorRadius * cursorRadius;
        const colors = ['rgba(255,255,255,0.10)', 'rgba(95,154,230,0.31)', 'rgba(182,215,255,0.60)'];
        const thresholds = [0.20, 0.43, 0.70];

        ctx.font = gridFont;
        if ('letterSpacing' in ctx) {
            ctx.letterSpacing = `${Math.max(0, cellSize - ctx.measureText('M').width)}px`;
        }
        ctx.textBaseline = 'top';
        for (let row = 0; row < rows; row++) {
            buckets.forEach(bucket => bucket.fill(' ', 0, columns));
            const y = row * cellSize;
            const ny = y / Math.max(height, 1);
            const rowWarp = Math.sin(ny * 7.5 + t * 0.42) * 0.08;

            for (let col = 0; col < columns; col++) {
                const x = col * cellSize;
                const nx = x / Math.max(width, 1);
                const warpedX = nx + rowWarp;
                const warpedY = ny + Math.sin(nx * 6.2 - t * 0.3) * 0.06;
                const waveA = Math.sin((warpedX * 5.2 + warpedY * 2.8) * Math.PI * 2 + t * 0.7);
                const waveB = Math.sin((warpedX * 3.1 - warpedY * 4.5) * Math.PI * 2 - t * 0.46);
                let brightness = (waveA * 0.52 + waveB * 0.34 + 0.35) * 0.7;

                const dx = x + cellSize / 2 - pointerX;
                const dy = y + cellSize / 2 - pointerY;
                const distanceSquared = dx * dx + dy * dy;
                if (distanceSquared < cursorRadiusSquared) {
                    const distance = Math.sqrt(distanceSquared);
                    const influence = 1 - distance / cursorRadius;
                    brightness += influence * (0.45 + Math.sin(distance * 0.035 - t * 2.8) * 0.28);
                }
                brightness = Math.max(0, Math.min(1, brightness));

                if (brightness < thresholds[0]) continue;
                const charIndex = Math.min(characters.length - 1, Math.floor(brightness * (characters.length - 1)));
                const bucketIndex = brightness < thresholds[1] ? 0 : brightness < thresholds[2] ? 1 : 2;
                buckets[bucketIndex][col] = characters[charIndex];
            }

            for (let bucket = 0; bucket < buckets.length; bucket++) {
                ctx.fillStyle = colors[bucket];
                ctx.fillText(buckets[bucket].join(''), 0, y);
            }
        }

        if (!reducedMotion && pageVisible) animationId = requestAnimationFrame(drawFrame);
    }

    resize();
    if (!reducedMotion) animationId = requestAnimationFrame(drawFrame);
    else drawFrame(performance.now());

    window.addEventListener('resize', resize);
    window.addEventListener('pointermove', event => {
        targetX = event.clientX;
        targetY = event.clientY;
        if (!animationId && !reducedMotion && pageVisible) animationId = requestAnimationFrame(drawFrame);
        else if (reducedMotion) {
            pointerX = targetX;
            pointerY = targetY;
            drawFrame(performance.now());
        }
    }, { passive: true });
    window.addEventListener('pointerout', event => {
        if (event.relatedTarget) return;
        targetX = -10000;
        targetY = -10000;
    }, { passive: true });
    document.addEventListener('visibilitychange', () => {
        pageVisible = !document.hidden;
        if (!pageVisible) {
            cancelAnimationFrame(animationId);
            animationId = 0;
        } else if (!reducedMotion) {
            lastFrame = 0;
            animationId = requestAnimationFrame(drawFrame);
        }
    });
})();
