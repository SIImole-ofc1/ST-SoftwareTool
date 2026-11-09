/* ═══════════════════════════════════════
   ST-SoftwareTool  —  Site JS
   ═══════════════════════════════════════ */

/* ── Footer date and public release version ─── */
const statusDate = document.getElementById('status-date');
if (statusDate) {
    statusDate.textContent = new Date().toLocaleDateString('en-GB', {
        day: '2-digit',
        month: 'short',
        year: 'numeric'
    });
}

fetch('https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases/latest', {
    headers: { Accept: 'application/vnd.github.v3+json' }
})
    .then(response => {
        if (!response.ok) throw new Error(`GitHub release request failed: ${response.status}`);
        return response.json();
    })
    .then(release => {
        if (!release.tag_name) return;
        document.querySelectorAll('.gh-version').forEach(element => {
            element.textContent = release.tag_name;
        });
    })
    .catch(error => console.error('Unable to load the latest version:', error));

/* ── Active nav on scroll ────────────────── */
const sections = document.querySelectorAll('section[id]');
const navLinks = document.querySelectorAll('.nav-menu a[href^="#"]');
function updateActiveNav() {
    let cur = sections[0]?.id || '';
    sections.forEach(s => {
        if (window.scrollY >= s.offsetTop - 60) cur = s.id;
    });
    navLinks.forEach(a => {
        const active = a.getAttribute('href') === '#' + cur;
        a.classList.toggle('active', active);
        if (active) a.setAttribute('aria-current', 'location');
        else a.removeAttribute('aria-current');
    });
}
window.addEventListener('scroll', updateActiveNav, { passive: true });
updateActiveNav();

/* ── Terminal animation ──────────────────── */
const SESSIONS = [
    [
        { text: '>_/help:show',                         cls: 'prompt',  delay: 60 },
        { text: 'ST-SoftwareTool - Command Reference',  cls: 'info',    delay: 0  },
        { text: '--------------------------------------',cls: 'dim',     delay: 0  },
        { text: '>_/app:list',                          cls: 'prompt',  delay: 40 },
        { text: 'All Apps  (7)',                        cls: 'success',  delay: 0  },
        { text: '--------------------------------------- ',cls:'dim',     delay: 0  },
        { text: 'Pinned: Discord             [General]',cls: '',        delay: 0  },
        { text: '  Counter-Strike 2           [Games]',  cls: '',        delay: 0  },
        { text: '  Visual Studio Code         [Dev]',    cls: '',        delay: 0  },
        { text: '  Spotify                    [Media]',  cls: '',        delay: 0  },
    ],
    [
        { text: '>_/from:open_app("Discord")',           cls: 'prompt',  delay: 60 },
        { text: "Launched 'Discord'.",                   cls: 'success', delay: 0  },
        { text: '>_/find:search("steam")',               cls: 'prompt',  delay: 50 },
        { text: "Results for 'steam'  (2)",             cls: 'info',    delay: 0  },
        { text: '  Steam                      [Games]',  cls: '',        delay: 0  },
        { text: '  SteamVR                    [Games]',  cls: '',        delay: 0  },
        { text: '>_/from:pin_app("Discord")',            cls: 'prompt',  delay: 50 },
        { text: "Pinned 'Discord'.",                     cls: 'success', delay: 0  },
    ],
    [
        { text: '>_/sys:scan',                          cls: 'prompt',  delay: 70 },
        { text: 'Scanning registry & Start Menu...',   cls: 'info',    delay: 0  },
        { text: 'Found 142 programs total.',            cls: 'info',    delay: 800},
        { text: 'Imported 138 new apps.',               cls: 'success', delay: 400},
        { text: '>_/app:list_cat("Games")',             cls: 'prompt',  delay: 50 },
        { text: 'Category: Games',                      cls: 'info',    delay: 0  },
        { text: 'Pinned: Counter-Strike 2    [Games]', cls: '',        delay: 0  },
        { text: '  Minecraft                  [Games]', cls: '',        delay: 0  },
        { text: '  Steam                      [Games]', cls: '',        delay: 0  },
    ],
];

let sessionIdx  = 0;
let lineIdx     = 0;
let charIdx     = 0;
let typingTimer = null;
const outEl     = document.getElementById('term-out');
const inputEl   = document.getElementById('term-typed');

function nextSession() {
    if (!outEl || !inputEl) return;
    outEl.innerHTML = '';
    inputEl.textContent = '';
    lineIdx = 0;
    charIdx = 0;
    typeLine();
}

function typeLine() {
    const session = SESSIONS[sessionIdx];
    if (lineIdx >= session.length) {
        sessionIdx = (sessionIdx + 1) % SESSIONS.length;
        setTimeout(nextSession, 2500);
        return;
    }
    const entry = session[lineIdx];
    if (entry.delay > 0) {
        setTimeout(startTyping, entry.delay);
    } else {
        startTyping();
    }
}

function startTyping() {
    const session = SESSIONS[sessionIdx];
    const entry   = session[lineIdx];
    charIdx = 0;
    inputEl.textContent = '';

    if (entry.cls === 'prompt') {
        // show typing in input row
        typeChar();
    } else {
        // instant output line
        appendLine(entry.text, entry.cls);
        lineIdx++;
        setTimeout(typeLine, 80);
    }
}

function typeChar() {
    const session = SESSIONS[sessionIdx];
    const entry   = session[lineIdx];
    if (charIdx <= entry.text.length) {
        inputEl.textContent = entry.text.slice(0, charIdx);
        charIdx++;
        typingTimer = setTimeout(typeChar, 45 + Math.random() * 35);
    } else {
        // commit line to output
        appendLine(entry.text, entry.cls);
        inputEl.textContent = '';
        lineIdx++;
        setTimeout(typeLine, 200);
    }
}

function appendLine(text, cls) {
    if (!outEl) return;
    const div = document.createElement('div');
    div.className = cls ? `t-${cls}` : '';
    div.textContent = text;
    outEl.appendChild(div);
    outEl.scrollTop = outEl.scrollHeight;
}

// Progress bar animation
function animateProgress() {
    document.querySelectorAll('.progress-fill[data-target]').forEach(bar => {
        const target = bar.dataset.target;
        bar.style.width = '0%';
        setTimeout(() => { bar.style.width = target + '%'; }, 300);
    });
}

// Intersection observer to trigger animations on scroll
const observer = new IntersectionObserver(entries => {
    entries.forEach(e => {
        if (e.isIntersecting) {
            animateProgress();
            observer.unobserve(e.target);
        }
    });
}, { threshold: 0.3 });
document.querySelectorAll('#monitor').forEach(el => observer.observe(el));

// Start terminal animation after page loads
window.addEventListener('load', () => {
    setTimeout(nextSession, 600);
});

/* ── Interactive TechText-style wordmark ───────── */
(function initSiteWordmark() {
    const container = document.querySelector('.site-banner-title');
    const canvas = document.getElementById('site-wordmark');
    const ctx = canvas?.getContext('2d');
    if (!container || !canvas || !ctx) return;
    if (canvas.dataset.effect === 'vapor') return;

    const text = 'ST-SoftwareTool';
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const glyphs = [];
    let width = 1;
    let height = 1;
    let dpr = 1;
    let activeIndex = -1;
    let draggingIndex = -1;
    let pointerX = 0;
    let pointerY = 0;
    let grabX = 0;
    let grabY = 0;
    let pressedIndex = -1;
    let pressX = 0;
    let pressY = 0;
    let frame = { x: 0, y: 0, width: 0, height: 0, alpha: 0 };
    let animationId = 0;
    let lastFrame = 0;
    let elapsed = 0;
    let visible = true;

    function measure() {
        const bounds = container.getBoundingClientRect();
        width = Math.max(1, bounds.width);
        height = Math.max(1, bounds.height);
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

        const styles = getComputedStyle(container);
        const family = styles.fontFamily || 'Arial, sans-serif';
        const weight = '900';
        const spacing = Math.max(1, Math.min(7, width * 0.006));
        const measureFont = size => `${weight} ${size}px ${family}`;
        let fontSize = Math.min(83, Math.max(40, window.innerWidth * 0.05));
        ctx.font = measureFont(fontSize);
        const fullWidth = ctx.measureText(text).width + spacing * (text.length - 1);
        if (fullWidth > width - 12) fontSize *= (width - 12) / fullWidth;
        fontSize = Math.max(18, fontSize);
        ctx.font = measureFont(fontSize);

        const advances = Array.from(text, char => ctx.measureText(char).width);
        const totalWidth = advances.reduce((sum, advance) => sum + advance, 0) + spacing * (text.length - 1);
        let x = (width - totalWidth) / 2;
        const baseline = (height + fontSize * 0.68) / 2;
        glyphs.length = 0;

        Array.from(text).forEach((char, index) => {
            const metrics = ctx.measureText(char);
            glyphs.push({
                char,
                x,
                box: {
                    left: x - (metrics.actualBoundingBoxLeft || 0),
                    right: x + (metrics.actualBoundingBoxRight || advances[index]),
                    top: baseline - (metrics.actualBoundingBoxAscent || fontSize * 0.75),
                    bottom: baseline + (metrics.actualBoundingBoxDescent || fontSize * 0.15)
                },
                offsetX: 0,
                offsetY: 0,
                velocityX: 0,
                velocityY: 0,
                kick: 0
            });
            x += advances[index] + spacing;
        });

        container.dataset.baseline = String(baseline);
        container.dataset.fontSize = String(fontSize);
        container.dataset.font = measureFont(fontSize);
        requestDraw();
    }

    function requestDraw() {
        if (!animationId && visible) animationId = requestAnimationFrame(draw);
    }

    function findGlyph(x, y) {
        let closest = -1;
        let closestDistance = Infinity;
        glyphs.forEach((glyph, index) => {
            const left = glyph.box.left + glyph.offsetX - 10;
            const right = glyph.box.right + glyph.offsetX + 10;
            const top = glyph.box.top + glyph.offsetY - 12;
            const bottom = glyph.box.bottom + glyph.offsetY + 12;
            const dx = x < left ? left - x : x > right ? x - right : 0;
            const dy = y < top ? top - y : y > bottom ? y - bottom : 0;
            const distance = Math.hypot(dx, dy);
            if (distance < closestDistance) {
                closest = index;
                closestDistance = distance;
            }
        });
        return closestDistance <= 22 ? closest : -1;
    }

    function kick(index) {
        glyphs.forEach((glyph, i) => {
            const distance = Math.abs(i - index);
            if (distance > 3) return;
            glyph.velocityY -= distance === 0 ? 390 : 110 / distance;
            glyph.kick = Math.max(glyph.kick, 0.8 - distance * 0.18);
        });
    }

    function draw(now) {
        animationId = 0;
        if (!visible) return;
        const dt = lastFrame ? Math.min((now - lastFrame) / 1000, 0.04) : 0;
        lastFrame = now;
        elapsed += dt;
        const baseline = Number(container.dataset.baseline || height * 0.68);
        const font = container.dataset.font || '900 64px Arial, sans-serif';
        ctx.clearRect(0, 0, width, height);
        ctx.font = font;
        ctx.textBaseline = 'alphabetic';
        ctx.lineJoin = 'round';

        let settling = false;
        glyphs.forEach((glyph, index) => {
            if (index === draggingIndex) {
                settling = true;
            } else {
                glyph.velocityX += -glyph.offsetX * 55 * dt;
                glyph.velocityY += -glyph.offsetY * 55 * dt;
                const damping = Math.exp(-13 * dt);
                glyph.velocityX *= damping;
                glyph.velocityY *= damping;
                glyph.offsetX += glyph.velocityX * dt;
                glyph.offsetY += glyph.velocityY * dt;
                if (Math.abs(glyph.offsetX) < 0.15 && Math.abs(glyph.velocityX) < 1) {
                    glyph.offsetX = 0;
                    glyph.velocityX = 0;
                }
                if (Math.abs(glyph.offsetY) < 0.15 && Math.abs(glyph.velocityY) < 1) {
                    glyph.offsetY = 0;
                    glyph.velocityY = 0;
                }
                if (glyph.offsetX || glyph.offsetY || glyph.velocityX || glyph.velocityY) settling = true;
            }
            glyph.kick = Math.max(0, glyph.kick - dt * 1.8);

            const x = glyph.x + glyph.offsetX;
            const y = baseline + glyph.offsetY;
            const hovered = index === activeIndex;
            const moving = glyph.kick > 0.03 || Math.abs(glyph.offsetY) > 2;
            ctx.globalAlpha = 1;
            ctx.fillStyle = '#ffffff';
            if (hovered) {
                ctx.shadowColor = '#ffffff';
                ctx.shadowBlur = 24 + Math.sin(elapsed * 6) * 8;
            } else {
                ctx.shadowBlur = 0;
            }
            ctx.fillText(glyph.char, x, y);
            ctx.shadowBlur = 0;

            if (hovered || moving) {
                ctx.globalAlpha = hovered ? 0.95 : Math.min(0.7, glyph.kick);
                ctx.strokeStyle = hovered ? '#ffffff' : '#9dd7ff';
                ctx.lineWidth = hovered ? 1.3 : 1;
                ctx.setLineDash([4, 2]);
                ctx.strokeText(glyph.char, x, y);
                ctx.setLineDash([]);
                ctx.globalAlpha = 1;
            }
        });

        const target = activeIndex >= 0 ? glyphs[activeIndex] : null;
        if (target) {
            const left = target.box.left + target.offsetX - 8;
            const right = target.box.right + target.offsetX + 8;
            const top = target.box.top + target.offsetY - 8;
            const bottom = target.box.bottom + target.offsetY + 8;
            const ease = 1 - Math.exp(-14 * Math.max(dt, 0.016));
            frame.x += (left - frame.x) * ease;
            frame.y += (top - frame.y) * ease;
            frame.width += (right - left - frame.width) * ease;
            frame.height += (bottom - top - frame.height) * ease;
            frame.alpha += (1 - frame.alpha) * ease;

            ctx.globalAlpha = frame.alpha;
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.86)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 3]);
            ctx.strokeRect(frame.x, frame.y, frame.width, frame.height);
            ctx.setLineDash([]);

            const label = `${target.char}  /  ${Math.round(Number(container.dataset.fontSize || 64))}px`;
            ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
            const labelWidth = ctx.measureText(label).width + 12;
            const labelX = Math.max(2, Math.min(width - labelWidth - 2, frame.x));
            const labelY = Math.max(14, frame.y - 7);
            ctx.fillStyle = 'rgba(5, 16, 32, 0.92)';
            ctx.fillRect(labelX, labelY - 11, labelWidth, 15);
            ctx.fillStyle = '#ffffff';
            ctx.fillText(label, labelX + 6, labelY);
            ctx.beginPath();
            ctx.moveTo(frame.x + frame.width / 2, frame.y + frame.height);
            ctx.lineTo(frame.x + frame.width / 2, frame.y + frame.height + 8);
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.75)';
            ctx.stroke();

            for (let i = 0; i < 10; i++) {
                const phase = elapsed * 2.5 + i * 2.399;
                const radius = 8 + ((i * 13 + Math.floor(elapsed * 5)) % 28);
                const sx = target.x + target.offsetX + Math.cos(phase) * radius;
                const sy = baseline + target.offsetY - 5 + Math.sin(phase) * radius * 0.62;
                const alpha = 0.25 + 0.7 * Math.abs(Math.sin(phase * 1.8));
                ctx.globalAlpha = alpha;
                ctx.fillStyle = '#ffffff';
                ctx.fillRect(Math.round(sx), Math.round(sy), 2, 2);
            }
            ctx.globalAlpha = 1;
            if (!reducedMotion) settling = true;
        } else {
            frame.alpha *= Math.exp(-12 * dt);
            if (frame.alpha > 0.01) settling = true;
        }

        if (settling || activeIndex >= 0) requestDraw();
    }

    function localPoint(event) {
        const bounds = canvas.getBoundingClientRect();
        return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
    }

    function onPointerMove(event) {
        const point = localPoint(event);
        pointerX = point.x;
        pointerY = point.y;
        if (draggingIndex >= 0) {
            const glyph = glyphs[draggingIndex];
            glyph.offsetX = pointerX - grabX - glyph.x;
            glyph.offsetY = pointerY - grabY - Number(container.dataset.baseline || height * 0.68);
            glyph.velocityX = 0;
            glyph.velocityY = 0;
        } else {
            activeIndex = findGlyph(pointerX, pointerY);
        }
        requestDraw();
    }

    function onPointerDown(event) {
        if (event.button !== undefined && event.button !== 0) return;
        const point = localPoint(event);
        pointerX = point.x;
        pointerY = point.y;
        activeIndex = findGlyph(pointerX, pointerY);
        pressedIndex = activeIndex;
        pressX = pointerX;
        pressY = pointerY;
        if (activeIndex >= 0) {
            draggingIndex = activeIndex;
            grabX = pointerX - glyphs[draggingIndex].x - glyphs[draggingIndex].offsetX;
            grabY = pointerY - Number(container.dataset.baseline || height * 0.68) - glyphs[draggingIndex].offsetY;
            canvas.setPointerCapture?.(event.pointerId);
        }
        requestDraw();
    }

    function onPointerUp(event) {
        const point = localPoint(event);
        pointerX = point.x;
        pointerY = point.y;
        if (draggingIndex >= 0) {
            const moved = Math.hypot(pointerX - pressX, pointerY - pressY);
            const index = draggingIndex;
            const glyph = glyphs[index];
            glyph.offsetX = pointerX - grabX - glyph.x;
            glyph.offsetY = pointerY - grabY - Number(container.dataset.baseline || height * 0.68);
            draggingIndex = -1;
            if (event.type === 'pointerup' && moved < 6 && pressedIndex >= 0 && !reducedMotion) kick(index);
            canvas.releasePointerCapture?.(event.pointerId);
        }
        activeIndex = point.x >= 0 && point.x <= width && point.y >= 0 && point.y <= height
            ? findGlyph(pointerX, pointerY)
            : -1;
        pressedIndex = -1;
        requestDraw();
    }

    function onPointerLeave() {
        if (draggingIndex >= 0) return;
        activeIndex = -1;
        requestDraw();
    }

    const resizeObserver = new ResizeObserver(measure);
    resizeObserver.observe(container);
    window.addEventListener('resize', measure, { passive: true });
    document.fonts?.ready.then(measure);
    canvas.addEventListener('pointermove', onPointerMove, { passive: true });
    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('pointercancel', onPointerUp);
    canvas.addEventListener('pointerleave', onPointerLeave);
    document.addEventListener('visibilitychange', () => {
        visible = !document.hidden;
        if (visible) {
            lastFrame = 0;
            requestDraw();
        } else {
            cancelAnimationFrame(animationId);
            animationId = 0;
        }
    });

    measure();
})();

/* ── Privacy / Terms consent popup ───────────── */
(function initPrivacyConsent() {
    const KEY = 'st_privacy_agreed';
    const modal = document.getElementById('privacy-modal');
    if (!modal) return;

    let agreed = false;
    try { agreed = localStorage.getItem(KEY) === '1'; } catch (e) {}

    if (agreed) {
        modal.hidden = true;
        modal.setAttribute('aria-hidden', 'true');
        return;
    }

    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('privacy-locked');

    const agreeBtn = document.getElementById('privacy-agree');
    if (agreeBtn) {
        agreeBtn.addEventListener('click', () => {
            try { localStorage.setItem(KEY, '1'); } catch (e) {}
            modal.hidden = true;
            modal.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('privacy-locked');
        });
    }

    // Focus primary action for keyboard / accessibility
    setTimeout(() => {
        if (agreeBtn) agreeBtn.focus();
    }, 50);
})();
