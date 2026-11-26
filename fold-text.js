/* FoldText — text that unfolds piece by piece around a hinge (paper fold).
   Vanilla, GSAP-free port of the React Bits <FoldText /> component (https://reactbits.dev).
   Markup: <span class="fold-text" data-split="word" data-hinge="top" data-trigger="mount">Your text</span>
   Data attributes (same meaning as the React props): split (char|word), hinge (top|bottom|left|right),
   fold-angle, duration, stagger, stagger-from (start|center|end|edges|random), perspective,
   crease-shading, trigger (mount|scroll), delay (seconds before starting). */
(function initFoldText() {
    const HINGES = {
        top: { axis: 'rotateX', sign: -1 },
        bottom: { axis: 'rotateX', sign: 1 },
        left: { axis: 'rotateY', sign: 1 },
        right: { axis: 'rotateY', sign: -1 }
    };
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const num = (value, fallback) => (value == null || value === '' || Number.isNaN(Number(value)) ? fallback : Number(value));
    const power3Out = t => 1 - Math.pow(1 - t, 3);
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

    function order(count, from) {
        const index = [...Array(count).keys()];
        if (from === 'end') return index.map(i => count - 1 - i);
        if (from === 'center') return index.map(i => Math.abs(i - (count - 1) / 2));
        if (from === 'edges') return index.map(i => (count - 1) / 2 - Math.abs(i - (count - 1) / 2));
        if (from === 'random') return index.map(() => Math.random() * (count - 1));
        return index;
    }

    function setup(root) {
        const d = root.dataset;
        const text = root.textContent.replace(/\s+/g, ' ').trim();
        const hingeName = HINGES[d.hinge] ? d.hinge : 'top';
        const { axis, sign } = HINGES[hingeName];
        const byChar = d.split === 'char';
        const angle = reduce ? 0 : clamp(num(d.foldAngle, 92), 0, 180);
        const crease = reduce ? 0 : clamp(num(d.creaseShading, 0.55), 0, 1);
        const duration = Math.max(0.05, reduce ? 0.22 : num(d.duration, 0.65));
        const stagger = Math.max(0, reduce ? 0.02 : num(d.stagger, 0.045));
        const delay = Math.max(0, num(d.delay, 0));

        root.style.setProperty('--fold-perspective', `${Math.max(120, num(d.perspective, 700))}px`);
        root.textContent = '';
        const sr = document.createElement('span');
        sr.className = 'fold-text-sr-only';
        sr.textContent = text;
        const visual = document.createElement('span');
        visual.className = 'fold-text-visual';
        visual.setAttribute('aria-hidden', 'true');
        root.append(sr, visual);

        const pieces = [];
        const makePiece = content => {
            const segment = document.createElement('span');
            segment.className = 'fold-text-segment';
            const piece = document.createElement('span');
            piece.className = 'fold-text-piece';
            piece.dataset.foldHinge = hingeName;
            piece.dataset.foldText = content;
            piece.textContent = content;
            piece.style.opacity = '0';
            segment.appendChild(piece);
            pieces.push(piece);
            return segment;
        };
        text.split(' ').forEach((word, i) => {
            if (i > 0) visual.appendChild(document.createTextNode(' '));
            if (!byChar) {
                visual.appendChild(makePiece(word));
                return;
            }
            const wrap = document.createElement('span');
            wrap.className = 'fold-text-word';
            Array.from(word).forEach(char => wrap.appendChild(makePiece(char)));
            visual.appendChild(wrap);
        });

        const starts = order(pieces.length, d.staggerFrom).map(rank => rank * stagger);
        const total = Math.max(...starts) + duration;

        function render(elapsed) {
            pieces.forEach((piece, i) => {
                const p = power3Out(clamp((elapsed - starts[i]) / duration, 0, 1));
                piece.style.transform = `${axis}(${sign * (1 - p) * angle}deg)`;
                piece.style.opacity = String(p);
                piece.style.setProperty('--fold-crease', String(crease * (1 - p)));
            });
        }

        function play() {
            let t0 = 0;
            const frame = now => {
                if (!t0) t0 = now;
                const elapsed = (now - t0) / 1000 - delay;
                render(elapsed);
                if (elapsed < total) requestAnimationFrame(frame);
            };
            requestAnimationFrame(frame);
        }

        render(0);
        // Wait for the privacy popup (body.privacy-locked) to close so the unfold is actually seen.
        if (document.body.classList.contains('privacy-locked')) {
            const watcher = new MutationObserver(() => {
                if (document.body.classList.contains('privacy-locked')) return;
                watcher.disconnect();
                play();
            });
            watcher.observe(document.body, { attributes: true, attributeFilter: ['class'] });
        } else if (d.trigger === 'scroll') {
            const observer = new IntersectionObserver(entries => {
                if (!entries.some(entry => entry.isIntersecting)) return;
                observer.disconnect();
                play();
            }, { rootMargin: '0px 0px -18% 0px' });
            observer.observe(root);
        } else {
            play();
        }
    }

    document.querySelectorAll('.fold-text').forEach(setup);
})();
