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
