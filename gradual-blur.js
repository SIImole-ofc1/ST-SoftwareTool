/* GradualBlur — progressive edge blur overlay.
   Vanilla port of the React Bits <GradualBlur /> component (https://reactbits.dev, by Ansh - github.com/ansh-dhanani).
   Markup: <div class="gradual-blur" data-position="bottom" data-target="page" data-auto-hide="true"></div>
   Data attributes (same meaning as the React props): position, target, height, width, strength, layers, curve,
   exponential, opacity, saturation, tint, auto-hide, duration, easing, z-index. */
(function initGradualBlur() {
    const CURVES = {
        linear: t => t,
        bezier: t => t * t * (3 - 2 * t),
        'ease-in': t => t * t,
        'ease-out': t => 1 - (1 - t) * (1 - t),
        'ease-in-out': t => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2)
    };
    const DIRECTIONS = { top: 'to top', bottom: 'to bottom', left: 'to left', right: 'to right' };
    const clamp01 = value => Math.min(Math.max(value, 0), 1);
    const toLength = value => (/^\d+(\.\d+)?$/.test(value) ? `${value}px` : value);
    const toTime = value => (/^\d+(\.\d+)?$/.test(value) ? `${value}s` : value);
    const num = (value, fallback) => (value == null || value === '' || Number.isNaN(Number(value)) ? fallback : Number(value));

    function setup(root) {
        const d = root.dataset;
        const edge = DIRECTIONS[d.position] ? d.position : 'bottom';
        const vertical = edge === 'top' || edge === 'bottom';
        const target = d.target === 'page' ? 'page' : 'parent';
        const count = Math.min(Math.max(Math.round(num(d.layers, 8)) || 1, 1), 16);
        const shape = CURVES[d.curve] || CURVES.linear;
        const maxBlur = Math.max(num(d.strength, 2), 0) * 12;
        const exponential = d.exponential !== 'false';
        const opacity = num(d.opacity, 1);
        const boost = Math.pow(Math.max(num(d.saturation, 1), 0), 1 / count);
        const tint = d.tint || 'transparent';
        const fade = `${toTime(d.duration || '0.3')} ${d.easing || 'ease-out'}`;
        const direction = DIRECTIONS[edge];
        const level = u => {
            const t = shape(clamp01(u));
            return maxBlur * (exponential ? (Math.pow(2, 4 * t) - 1) / 15 : t);
        };

        // Each layer blurs a slice of the ramp; radii combine so the total blur follows the curve.
        for (let i = 0; i < count; i++) {
            const from = level(i / count);
            const to = level((i + 1) / count);
            const radius = Math.sqrt(Math.max(to * to - from * from, 0));
            const filter = [
                `blur(calc(${radius.toFixed(3)}px * var(--gradual-blur-reveal, 1)))`,
                boost !== 1 ? `saturate(calc(1 + ${(boost - 1).toFixed(4)} * var(--gradual-blur-reveal, 1)))` : ''
            ].filter(Boolean).join(' ');
            const mask = `linear-gradient(${direction}, transparent ${((i / count) * 100).toFixed(2)}%, #000 ${(((i + 1) / count) * 100).toFixed(2)}%)`;
            const layer = document.createElement('div');
            Object.assign(layer.style, {
                position: 'absolute',
                inset: '0',
                borderRadius: 'inherit',
                backdropFilter: filter,
                webkitBackdropFilter: filter,
                maskImage: mask,
                webkitMaskImage: mask,
                opacity: String(opacity),
                transition: `backdrop-filter ${fade}, -webkit-backdrop-filter ${fade}`
            });
            root.appendChild(layer);
        }

        if (tint !== 'transparent') {
            const stops = Array.from({ length: 9 }, (_, k) => {
                const u = k / 8;
                const t = shape(u);
                const alpha = t * t * t * (t * (t * 6 - 15) + 10);
                return `rgba(0, 0, 0, ${alpha.toFixed(3)}) ${(u * 100).toFixed(1)}%`;
            }).join(', ');
            const tintMask = `linear-gradient(${direction}, ${stops})`;
            const wash = document.createElement('div');
            Object.assign(wash.style, {
                position: 'absolute',
                inset: '0',
                borderRadius: 'inherit',
                background: tint,
                maskImage: tintMask,
                webkitMaskImage: tintMask,
                opacity: 'var(--gradual-blur-reveal, 1)',
                transition: `opacity ${fade}`
            });
            root.appendChild(wash);
        }

        const depth = toLength(vertical ? (d.height || '6rem') : (d.width || d.height || '6rem'));
        const span = vertical && d.width ? toLength(d.width) : null;
        Object.assign(root.style, {
            position: target === 'page' ? 'fixed' : 'absolute',
            zIndex: String(num(d.zIndex, 1000)),
            pointerEvents: 'none',
            top: edge === 'bottom' ? '' : '0',
            bottom: edge === 'top' ? '' : '0',
            left: edge === 'right' ? '' : '0',
            right: edge === 'left' ? '' : '0',
            height: vertical ? depth : '',
            width: vertical ? (span || '') : depth,
            marginInline: span ? 'auto' : ''
        });
        root.setAttribute('aria-hidden', 'true');

        if (d.autoHide !== 'true') return;

        // Fade the blur out as the scroll container reaches this edge.
        const element = target === 'page' ? null : root.parentElement;
        const start = edge === 'top' || edge === 'left';
        const update = () => {
            const page = document.documentElement;
            const offset = element
                ? (vertical ? element.scrollTop : element.scrollLeft)
                : (vertical ? window.scrollY : window.scrollX);
            const limit = element
                ? (vertical ? element.scrollHeight - element.clientHeight : element.scrollWidth - element.clientWidth)
                : (vertical ? page.scrollHeight - window.innerHeight : page.scrollWidth - window.innerWidth);
            const box = root.getBoundingClientRect();
            const reach = Math.max(vertical ? box.height : box.width, 1);
            const room = start ? offset : limit - offset;
            root.style.setProperty('--gradual-blur-reveal', clamp01(room / reach).toFixed(3));
        };
        (element || window).addEventListener('scroll', update, { passive: true });
        window.addEventListener('resize', update);
        if (typeof ResizeObserver !== 'undefined') new ResizeObserver(update).observe(element || document.body);
        update();
    }

    document.querySelectorAll('.gradual-blur').forEach(setup);
})();
