/* Scroll-triggered content entrance, with no external animation dependency */
(function initContentReveal() {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduceMotion || !('IntersectionObserver' in window)) return;

    const selectors = [
        '#hero .hero-terminal-wrap',
        '#hero .hero-desc',
        '#features > h2',
        '#features .feature-card',
        '#commands > h2',
        '#commands .cmd-window',
        '#commands .cmd-table-wrap',
        '#monitor > h2',
        '#monitor > p',
        '#monitor .monitor-card',
        '#download > h2',
        '#download .download-box',
        '#technologies > h2',
        '#technologies > p',
        '#technologies .technology-loop',
        '#about .about-inner',
        '.legal-document > h1',
        '.legal-index',
        '.legal-section'
    ].join(',');

    document.documentElement.classList.add('content-reveal-enabled');

    const observer = new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            entry.target.classList.add('content-revealed');
            observer.unobserve(entry.target);
        });
    }, { threshold: 0.1 });

    function observe(root) {
        const elements = [];
        if (root instanceof Element && root.matches(selectors)) elements.push(root);
        if (root.querySelectorAll) elements.push(...root.querySelectorAll(selectors));
        elements.forEach(element => {
            if (element.classList.contains('content-reveal-ready')) return;
            element.classList.add('content-reveal-ready');
            const siblings = [...element.parentElement.children].filter(sibling => sibling.matches(selectors));
            element.style.setProperty('--content-reveal-delay', `${Math.min(siblings.indexOf(element), 5) * 70}ms`);
            observer.observe(element);
        });
    }

    observe(document);
    const mutations = new MutationObserver(records => {
        records.forEach(record => record.addedNodes.forEach(node => {
            if (node instanceof Element) observe(node);
        }));
    });
    mutations.observe(document.body, { childList: true, subtree: true });
})();
