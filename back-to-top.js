/* Sling-style back-to-top control shared by the homepage and legal page. */
(function initBackToTop() {
    const root = document.createElement('div');
    root.className = 'back-to-top';
    root.innerHTML = `
        <svg class="back-to-top__fx" viewBox="0 0 140 140" aria-hidden="true">
            <path class="back-to-top__band"></path>
            <circle class="back-to-top__power" cx="70" cy="70" r="37" pathLength="1" stroke-dasharray="0 1"></circle>
        </svg>
        <button class="back-to-top__face" type="button" aria-label="Back to top" title="Back to top">
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
            </svg>
        </button>
        <span class="back-to-top__sr" aria-live="polite"></span>`;
    document.body.append(root);

    const button = root.querySelector('button');
    const band = root.querySelector('.back-to-top__band');
    const power = root.querySelector('.back-to-top__power');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const armAt = 42;
    const maxPull = 105;
    let grip = null;
    let wasDragged = false;
    let scrollFrame = 0;

    function updateVisibility() {
        scrollFrame = 0;
        root.classList.toggle('is-visible', window.scrollY > 240);
    }

    function requestVisibilityUpdate() {
        if (!scrollFrame) scrollFrame = requestAnimationFrame(updateVisibility);
    }

    function paintBand(x, y) {
        const distance = Math.hypot(x, y);
        const progress = Math.min(1, distance / armAt);
        power.setAttribute('stroke-dasharray', `${progress} 1`);
        band.classList.toggle('is-armed', distance >= armAt);

        if (distance < 1) {
            band.setAttribute('d', '');
            return;
        }

        const nx = -y / distance;
        const ny = x / distance;
        const padRadius = 23;
        const wellRadius = 31;
        const x1 = 70 + nx * wellRadius;
        const y1 = 70 + ny * wellRadius;
        const x2 = 70 - nx * wellRadius;
        const y2 = 70 - ny * wellRadius;
        const x3 = 70 + x + nx * padRadius;
        const y3 = 70 + y + ny * padRadius;
        const x4 = 70 + x - nx * padRadius;
        const y4 = 70 + y - ny * padRadius;
        band.setAttribute('d', `M${x1} ${y1}L${x3} ${y3}M${x2} ${y2}L${x4} ${y4}`);
    }

    function scrollToTop() {
        window.scrollTo({ top: 0, behavior: reducedMotion ? 'instant' : 'smooth' });
    }

    function fireParticles(x, y) {
        const baseAngle = Math.atan2(-y, -x);
        root.querySelectorAll('.back-to-top__particle').forEach((particle, index) => {
            const angle = baseAngle + (Math.random() - 0.5) * 1.05;
            const distance = index === 0 ? 110 : 35 + Math.random() * 75;
            particle.animate(
                [
                    { opacity: 0.95, transform: 'translate(-50%, -50%) scale(1)' },
                    {
                        opacity: 0,
                        transform: `translate(calc(-50% + ${Math.cos(angle) * distance}px), calc(-50% + ${Math.sin(angle) * distance}px)) scale(.25)`
                    }
                ],
                { duration: 420, delay: index * 8, easing: 'cubic-bezier(.23,1,.32,1)' }
            );
        });
    }

    function finish(pointerId, cancelled) {
        if (!grip || grip.id !== pointerId) return;
        const { x, y } = grip;
        grip = null;
        button.removeAttribute('data-held');
        button.releasePointerCapture?.(pointerId);

        if (!cancelled && Math.hypot(x, y) >= armAt) {
            wasDragged = true;
            scrollToTop();
            fireParticles(x, y);
        }

        button.style.transition = reducedMotion ? 'none' : 'transform 360ms cubic-bezier(.23,1,.32,1)';
        button.style.transform = 'translate(0, 0)';
        band.setAttribute('d', '');
        power.setAttribute('stroke-dasharray', '0 1');
        band.classList.remove('is-armed');
        window.setTimeout(() => {
            button.style.transition = '';
            wasDragged = false;
        }, 380);
    }

    button.addEventListener('pointerdown', event => {
        if (event.button !== 0 || grip) return;
        event.preventDefault();
        button.setPointerCapture(event.pointerId);
        button.setAttribute('data-held', '');
        button.style.transition = 'none';
        grip = { id: event.pointerId, startX: event.clientX, startY: event.clientY, x: 0, y: 0 };
    });

    button.addEventListener('pointermove', event => {
        if (!grip || grip.id !== event.pointerId) return;
        const dx = event.clientX - grip.startX;
        const dy = event.clientY - grip.startY;
        const distance = Math.hypot(dx, dy);
        const limited = Math.min(distance, maxPull);
        const scale = distance ? limited / distance : 0;
        grip.x = dx * scale;
        grip.y = dy * scale;
        button.style.transform = `translate(${grip.x}px, ${grip.y}px)`;
        paintBand(grip.x, grip.y);
    });

    button.addEventListener('pointerup', event => finish(event.pointerId, false));
    button.addEventListener('pointercancel', event => finish(event.pointerId, true));
    button.addEventListener('lostpointercapture', event => finish(event.pointerId, true));
    button.addEventListener('click', event => {
        if (wasDragged) {
            event.preventDefault();
            return;
        }
        scrollToTop();
        fireParticles(0, -1);
    });

    for (let index = 0; index < 12; index++) {
        const particle = document.createElement('span');
        particle.className = 'back-to-top__particle';
        particle.setAttribute('aria-hidden', 'true');
        root.append(particle);
    }

    window.addEventListener('scroll', requestVisibilityUpdate, { passive: true });
    updateVisibility();
})();
