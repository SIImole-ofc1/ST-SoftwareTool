/* GradientText — animated gradient fill for text.
   Vanilla port of the React Bits <GradientText /> component (https://reactbits.dev).
   Markup:
     <span class="gradient-text" data-colors="#5227FF,#76a9ff,#1fb5ff" data-variant="flow"
           data-speed="2.5" data-glow="0.4">
       <span class="gradient-text__inner"><span class="gradient-text__content">Text</span></span>
     </span>
   Optional data attributes: data-angle (deg, default 90), data-scale (1–6, default 3), data-yoyo ("false" to loop).
   A glow layer is added automatically when data-glow > 0. */
(function initGradientText() {
    const VARIANTS = ['linear', 'flow', 'conic'];
    const TAU = Math.PI * 2;
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

    function setup(root) {
        const data = root.dataset;
        let colors = (data.colors || '#5227FF,#FF9FFC,#B497CF').split(',').map(c => c.trim()).filter(Boolean);
        if (colors.length < 2) colors = [colors[0] || '#ffffff', colors[0] || '#ffffff'];
        const s = {
            colors,
            speed: Math.max(0.2, Number(data.speed) || 8),
            variant: VARIANTS.includes(data.variant) ? data.variant : 'linear',
            angle: Number(data.angle ?? 90) || 0,
            scale: clamp(Number(data.scale) || 3, 1, 6),
            yoyo: data.yoyo !== 'false'
        };
        const glow = clamp(Number(data.glow) || 0, 0, 1);

        root.style.setProperty('--gt-gradient', `linear-gradient(${s.angle}deg, ${[...colors, colors[0]].join(', ')})`);
        root.style.setProperty('--gt-glow', String(glow));

        const inner = root.querySelector('.gradient-text__inner');
        const content = root.querySelector('.gradient-text__content');
        if (glow > 0 && inner && content && !inner.querySelector('.gradient-text__glow')) {
            const layer = document.createElement('span');
            layer.className = 'gradient-text__glow';
            layer.setAttribute('aria-hidden', 'true');
            layer.textContent = content.textContent;
            inner.appendChild(layer);
        }

        let time = 0;
        let raf = 0;
        let last = 0;
        let visible = true;

        function paint() {
            const list = s.colors;
            const cycle = time / s.speed;
            let image;
            if (s.variant === 'flow') {
                const blobs = list.map((color, i) => {
                    const spin = cycle * TAU;
                    const rate = 0.55 + ((i * 0.618) % 1) * 0.5;
                    const x = 50 + 44 * Math.sin(spin * rate + i * 2.1);
                    const y = 50 + 40 * Math.sin(spin * (rate * 0.83 + 0.21) + i * 1.3);
                    return `radial-gradient(ellipse ${(22 + 12 * s.scale).toFixed(1)}% ${(60 + 30 * s.scale).toFixed(1)}% at ${x.toFixed(2)}% ${y.toFixed(2)}%, ${color} 0%, transparent 100%)`;
                });
                image = `${blobs.join(', ')}, linear-gradient(${s.angle}deg, ${list.join(', ')})`;
            } else if (s.variant === 'conic') {
                const sway = s.yoyo ? Math.sin(cycle * Math.PI) * 50 : cycle * 360;
                image = `repeating-conic-gradient(from ${(sway - 30).toFixed(2)}deg at 50% 260%, ${[...list, list[0]].map((color, i) => `${color} ${((i / list.length) * 60 * (3 / s.scale)).toFixed(2)}deg`).join(', ')})`;
            } else {
                const loop = [...list, list[0]];
                const period = 100 * s.scale;
                let offset;
                if (s.yoyo) {
                    const swing = cycle % 2;
                    const t = swing < 1 ? swing : 2 - swing;
                    offset = (0.5 - Math.cos(Math.PI * t) / 2) * (period - 100);
                } else {
                    offset = (cycle % 1) * period;
                }
                const stops = loop.map((color, i) => `${color} ${((i / (loop.length - 1)) * period - offset).toFixed(2)}%`);
                image = `repeating-linear-gradient(${s.angle}deg, ${stops.join(', ')})`;
            }
            root.style.setProperty('--gt-gradient', image);
        }

        function tick(now) {
            raf = 0;
            time += Math.min(0.05, Math.max(0, (now - last) / 1000));
            last = now;
            paint();
            if (visible && !document.hidden) raf = requestAnimationFrame(tick);
        }

        function wake() {
            if (raf || reduce || !visible || document.hidden) return;
            last = performance.now();
            raf = requestAnimationFrame(tick);
        }

        new IntersectionObserver(entries => {
            visible = entries.some(entry => entry.isIntersecting);
            wake();
        }).observe(root);
        document.addEventListener('visibilitychange', wake);
        paint();
        wake();
    }

    document.querySelectorAll('.gradient-text').forEach(setup);
})();
