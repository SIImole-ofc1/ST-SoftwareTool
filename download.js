/* Download section: Windows / macOS switch (RubberSegment), typed thank-you (TextType),
 * a "Downloading ST" toast (SwipeToast), and download links that always point at a release which really has the file. */
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

        // ── special icon gift: the unlock code for Settings › App Icon › Special ──
        // Shown after the download toast and kept until "Collect" is pressed.
        const SPECIAL_CODE = 'SIImole';
        const showGift = () => window.SwipeToast?.show({
            title: 'Special Icon collected!',
            description: `Type this code inside ST settings to claim gift!  Code: ${SPECIAL_CODE}`,
            actionLabel: 'Collect',
            onAction: () => navigator.clipboard?.writeText(SPECIAL_CODE).catch(() => {}),
            onClose: why => {
                if (why !== 'action') return;
                window.SwipeToast?.show({       // once the gift card has gone
                    title: `Code ${SPECIAL_CODE} copied`,
                    description: 'In ST-SoftwareTool: Settings › App Icon › Special › Unlock…',
                    background: '#27272a',
                    color: '#f5f5f5',
                    fuseColor: '#3b82f6',
                    radius: 19,
                    duration: 5000,
                    fuse: 'top'
                });
            },
            background: '#27272a',
            color: '#f5f5f5',
            fuseColor: '#3b82f6',
            width: 356,
            radius: 19,
            slideMs: 700,
            settleBounce: 0.2,
            swipeDistance: 40,
            duration: 0,              // stays until Collect is pressed
            fuse: 'top',
            pauseOnHover: true,
            dismissible: false
        });

        // ── typed thank-you after a download button is pressed ────────────────
        section.addEventListener('click', event => {
            const link = event.target.closest('a[data-download]');
            if (!link || link.getAttribute('aria-disabled') === 'true') return;
            window.SwipeToast?.show({
                title: 'Downloading ST',
                description: 'Thanks for Downloading',
                actionLabel: 'Ok',
                background: '#27272a',
                color: '#f5f5f5',
                fuseColor: '#76a9ff',
                width: 356,
                radius: 12,
                slideMs: 400,
                settleBounce: 0.2,
                swipeDistance: 40,
                duration: 2000,
                fuse: 'bottom',
                pauseOnHover: true,
                onClose: showGift
            });
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

        // ── Each button downloads from the newest release that has its file ──
        // A release can be published before every file is attached (the macOS .dmg files
        // are built afterwards, and a release may ship for one OS first). Until then the
        // button uses the previous release's file instead of a broken /latest/ link.
        let olderReleases = null;
        const listReleases = () => olderReleases || (olderReleases =
            fetch('https://api.github.com/repos/SIImole-ofc1/ST-SoftwareTool/releases?per_page=15', {
                headers: { Accept: 'application/vnd.github.v3+json' }
            }).then(r => (r.ok ? r.json() : [])).catch(() => []));
        const assetIn = (release, name) => (release.assets || []).find(a => a.name === name);

        const setAvailable = (a, ok) => {
            a.classList.toggle('is-unavailable', !ok);
            if (ok) {
                a.removeAttribute('aria-disabled');
                a.removeAttribute('tabindex');
            } else {
                a.setAttribute('aria-disabled', 'true');
                a.setAttribute('tabindex', '-1');
            }
        };

        const resolveDownloads = async latest => {
            const links = Array.from(section.querySelectorAll('a[data-download]'));
            let macReady = true;
            for (const a of links) {
                const name = (a.getAttribute('href') || '').split('/').pop();
                if (!name) continue;
                let asset = assetIn(latest, name);
                if (!asset) {
                    const releases = await listReleases();
                    const older = releases.find(r => !r.draft && !r.prerelease && assetIn(r, name));
                    asset = older && assetIn(older, name);
                }
                if (asset) a.href = asset.browser_download_url;
                if (MAC_FILES.includes(name)) {
                    setAvailable(a, Boolean(asset));
                    macReady = macReady && Boolean(asset);
                }
            }
            const note = section.querySelector('.mac-coming-soon');
            if (note) note.hidden = macReady;
        };
        section.addEventListener('click', event => {
            const a = event.target.closest('a[data-download][aria-disabled="true"]');
            if (a) event.preventDefault();
        });
        if (window.stLatestRelease) resolveDownloads(window.stLatestRelease);
        window.addEventListener('st:release', event => resolveDownloads(event.detail));
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
