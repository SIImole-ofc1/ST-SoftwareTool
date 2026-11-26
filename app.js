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
        // Shared with download.js (e.g. to see whether the macOS files are published yet)
        window.stLatestRelease = release;
        window.dispatchEvent(new CustomEvent('st:release', { detail: release }));
    })
    .catch(error => console.error('Unable to load the latest version:', error));

/* ── Active nav on scroll ────────────────── */
const sections = document.querySelectorAll('section[id]');
const navLinks = document.querySelectorAll('.nav-menu a[href^="#"]');
// Section positions are cached (re-measured when the page size changes) and the menu is only
// touched when the active section actually changes, at most once per frame.
let sectionTops = [];
let activeNavId = null;
let navFrame = 0;
function measureSections() {
    sectionTops = Array.from(sections, s => ({ id: s.id, top: s.offsetTop }));
}
function updateActiveNav() {
    navFrame = 0;
    if (!sectionTops.length) measureSections();
    let cur = sectionTops[0]?.id || '';
    for (const s of sectionTops) {
        if (window.scrollY >= s.top - 60) cur = s.id;
    }
    if (cur === activeNavId) return;
    activeNavId = cur;
    navLinks.forEach(a => {
        const active = a.getAttribute('href') === '#' + cur;
        a.classList.toggle('active', active);
        if (active) a.setAttribute('aria-current', 'location');
        else a.removeAttribute('aria-current');
    });
}
window.addEventListener('scroll', () => {
    if (!navFrame) navFrame = requestAnimationFrame(updateActiveNav);
}, { passive: true });
if ('ResizeObserver' in window) {
    new ResizeObserver(() => {
        measureSections();
        activeNavId = null;
        updateActiveNav();
    }).observe(document.body);
}
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
const outEl     = document.getElementById('term-out');
const inputEl   = document.getElementById('term-typed');

// Steps are scheduled with later(): while the terminal is off-screen or the tab is hidden the
// next step waits, so the typing doesn't keep the page repainting when nobody can see it.
let terminalVisible = true;
let pendingStep = null;
function later(fn, ms) {
    setTimeout(() => {
        if (terminalVisible && !document.hidden) fn();
        else pendingStep = fn;
    }, ms);
}
function resumeTerminal() {
    if (!pendingStep || !terminalVisible || document.hidden) return;
    const fn = pendingStep;
    pendingStep = null;
    fn();
}
if (outEl) {
    new IntersectionObserver(entries => {
        terminalVisible = entries.some(e => e.isIntersecting);
        resumeTerminal();
    }).observe(outEl);
}
document.addEventListener('visibilitychange', resumeTerminal);

// Reserve the tallest session's height once, so typing never changes the page layout below.
function reserveTerminalHeight() {
    const probe = document.createElement('div');
    probe.textContent = 'X';
    outEl.appendChild(probe);
    const lineHeight = probe.getBoundingClientRect().height;
    probe.remove();
    const maxLines = Math.max(...SESSIONS.map(session => session.length));
    if (lineHeight > 0) outEl.style.height = `${Math.max(140, Math.ceil(maxLines * lineHeight))}px`;
}

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
        later(nextSession, 2500);
        return;
    }
    const entry = session[lineIdx];
    if (entry.delay > 0) {
        later(startTyping, entry.delay);
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
        later(typeLine, 80);
    }
}

function typeChar() {
    const session = SESSIONS[sessionIdx];
    const entry   = session[lineIdx];
    if (charIdx <= entry.text.length) {
        inputEl.textContent = entry.text.slice(0, charIdx);
        charIdx++;
        later(typeChar, 45 + Math.random() * 35);
    } else {
        // commit line to output
        appendLine(entry.text, entry.cls);
        inputEl.textContent = '';
        lineIdx++;
        later(typeLine, 200);
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
    if (outEl) reserveTerminalHeight();
    later(nextSession, 600);
});

/* ── Privacy / Terms consent popup ───────────── */
(function initPrivacyConsent() {
    // Shown on every page load — agreement is intentionally not remembered.
    const modal = document.getElementById('privacy-modal');
    if (!modal) return;

    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('privacy-locked');

    const agreeBtn = document.getElementById('privacy-agree');
    if (agreeBtn) {
        agreeBtn.addEventListener('click', () => {
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
