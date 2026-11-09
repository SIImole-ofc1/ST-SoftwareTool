/* Lightweight, cursor-independent wave field */
(function initWaveDots() {
    const canvas = document.getElementById('site-background');
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;

    const spacing = 28;
    const radius = 2.2;
    const frameInterval = 1000 / 30;
    const colors = ['#3a3446', '#566d91', '#9cbce5'];
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let dots = [];
    let width = 0;
    let height = 0;
    let raf = 0;
    let lastFrame = 0;
    let elapsed = 0;
    let visible = !document.hidden;

    function random(index) {
        const value = Math.sin(index * 127.1 + 311.7) * 43758.5453;
        return value - Math.floor(value);
    }

    function resize() {
        const ratio = Math.min(window.devicePixelRatio || 1, 1);
        width = window.innerWidth;
        height = window.innerHeight;
        canvas.width = Math.max(1, Math.round(width * ratio));
        canvas.height = Math.max(1, Math.round(height * ratio));
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

        const columns = Math.ceil(width / spacing) + 1;
        const rows = Math.ceil(height / spacing) + 1;
        dots = new Array(columns * rows);
        for (let row = 0; row < rows; row++) {
            for (let column = 0; column < columns; column++) {
                const index = row * columns + column;
                dots[index] = {
                    x: column * spacing,
                    y: row * spacing,
                    phase: random(index) * Math.PI * 2,
                    amplitude: 2 + random(index + 91) * 3,
                    brightness: random(index + 211)
                };
            }
        }
        draw();
        if (!reducedMotion) wake();
    }

    function draw() {
        ctx.clearRect(0, 0, width, height);
        const groups = [[], [], []];
        const waveTime = elapsed * 0.72;

        for (const dot of dots) {
            const rowPhase = dot.y * 0.012;
            const phase = dot.phase + waveTime;
            const x = dot.x + Math.sin(rowPhase + waveTime * 0.8 + dot.phase * 0.2) * dot.amplitude;
            const y = dot.y + Math.sin(dot.x * 0.009 - waveTime + rowPhase + dot.phase) * dot.amplitude;
            const wave = (Math.sin(dot.x * 0.006 - waveTime * 1.3 + rowPhase + dot.phase) + 1) * 0.5;
            const flicker = (Math.sin(phase * 0.65 + dot.brightness * 5) + 1) * 0.5;
            const intensity = wave * 0.72 + flicker * 0.28;
            const group = intensity > 0.76 ? 2 : intensity > 0.43 ? 1 : 0;
            groups[group].push(x, y);
        }

        groups.forEach((points, group) => {
            ctx.fillStyle = colors[group];
            ctx.beginPath();
            for (let index = 0; index < points.length; index += 2) {
                const x = points[index];
                const y = points[index + 1];
                ctx.moveTo(x + radius, y);
                ctx.arc(x, y, radius, 0, Math.PI * 2);
            }
            ctx.fill();
        });
    }

    function animate(now) {
        raf = 0;
        if (!visible || reducedMotion) return;
        if (now - lastFrame < frameInterval) {
            raf = requestAnimationFrame(animate);
            return;
        }
        const dt = lastFrame ? Math.min((now - lastFrame) / 1000, 0.05) : 1 / 30;
        lastFrame = now;
        elapsed += dt;
        draw();
        raf = requestAnimationFrame(animate);
    }

    function wake() {
        if (visible && !reducedMotion && !raf) raf = requestAnimationFrame(animate);
    }

    window.addEventListener('resize', resize, { passive: true });
    document.addEventListener('visibilitychange', () => {
        visible = !document.hidden;
        if (!visible) {
            cancelAnimationFrame(raf);
            raf = 0;
            lastFrame = 0;
        } else {
            wake();
        }
    });

    resize();
})();
