/* Download section: Windows / macOS switch (RubberSegment), typed thank-you (TextType),
 * and a check that the macOS files are actually published in the latest release. */
(function () {
    'use strict';

    const MAC_FILES = ['ST-SoftwareTool-macOS-arm64.dmg', 'ST-SoftwareTool-macOS-x86_64.dmg'];

    function init() {
        const section = document.getElementById('download');
        const host = document.getElementById('platform-switch');
        if (!section || !host || !window.RubberSegment) return;
        const panes = Array.from(section.querySelectorAll('.download-pane'));
        const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        // ── Windows / macOS switch ────────────────────────────────────────────
        const isMac = /Mac|iPhone|iPad/i.test(navigator.userAgentData?.platform || navigator.platform || '')
            || /Macintosh/i.test(navigator.userAgent);
        const show = (platform, animate) => {
            panes.forEach(pane => {
                const on = pane.dataset.platform === platform;
                pane.hidden = !on;
                if (on && animate && !reduce) {
                    pane.classList.remove('is-entering');
                    void pane.offsetWidth;          // restart the entrance animation
                    pane.classList.add('is-entering');
                }
            });
            section.dataset.activePlatform = platform;   // not "platform": panes use that
        };

        new window.RubberSegment(host, {
            items: [
                { value: 'windows', label: 'Windows' },
                { value: 'macos', label: 'macOS' }
            ],
            value: isMac ? 'macos' : 'windows',
            onChange: value => show(value, true),
            trackColor: '#0b1320',
            thumbColor: '#d8e2f0',
            textColor: '#d8e2f0',
            activeTextColor: '#0b1320',
            size: 'md',
            radius: 10,
            inset: 3,
            equalSlots: true,
            stretch: 100,
            squash: 3,
            speed: 1,
            glide: 75,
            draggable: true,
            ariaLabel: 'Choose your operating system'
        });
        show(isMac ? 'macos' : 'windows', false);

        // ── typed thank-you after a download button is pressed ────────────────
        section.addEventListener('click', event => {
            const link = event.target.closest('a[data-download]');
            if (!link || link.getAttribute('aria-disabled') === 'true') return;
            const box = link.closest('.download-box');
            const thanks = box && box.querySelector('.download-thanks');
            if (!thanks || !window.TextType) return;
            thanks.hidden = false;
            thanks._typer?.destroy();
            thanks._typer = new window.TextType(thanks, {
                text: [
                    'Thank you for downloading ST-SoftwareTool!',
                    'Your download has started.',
                    'Enjoy, and thanks for the support!'
                ],
                typingSpeed: 75,
                pauseDuration: 1500,
                deletingSpeed: 50,
                deleteMode: 'backspace',
                typeEffect: 'fade',
                mistakes: 0.2,
                cursorStyle: 'bar',
                cursorGlide: true,
                showCursor: true
            });
        });

        // ── macOS files: only offer them once a release actually contains them ─
        const checkMacFiles = release => {
            const names = new Set((release.assets || []).map(a => a.name));
            const ready = MAC_FILES.every(name => names.has(name));
            section.querySelectorAll('.download-pane[data-platform="macos"] a[data-download]').forEach(a => {
                a.classList.toggle('is-unavailable', !ready);
                if (ready) {
                    a.removeAttribute('aria-disabled');
                    a.removeAttribute('tabindex');
                } else {
                    a.setAttribute('aria-disabled', 'true');
                    a.setAttribute('tabindex', '-1');
                }
            });
            const note = section.querySelector('.mac-coming-soon');
            if (note) note.hidden = ready;
        };
        section.addEventListener('click', event => {
            const a = event.target.closest('a[data-download][aria-disabled="true"]');
            if (a) event.preventDefault();
        });
        if (window.stLatestRelease) checkMacFiles(window.stLatestRelease);
        window.addEventListener('st:release', event => checkMacFiles(event.detail));
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
