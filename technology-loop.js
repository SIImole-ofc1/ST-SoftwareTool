/* Accessible, continuously scrolling technology logo strip */
(function initTechnologyLoop() {
    const track = document.querySelector('[data-technology-track]');
    if (!track) return;

    const technologies = [
        ['CSS', 'css'],
        ['HTML', 'html'],
        ['Python', 'python'],
        ['Java', 'java'],
        ['JavaScript', 'javascript'],
        ['C', 'c'],
        ['C++', 'cpp'],
        ['C#', 'csharp'],
        ['X', 'x'],
        ['VSCode', 'vscode'],
        ['VS', 'visualstudio'],
        ['Google', 'google'],
        ['Git', 'git'],
        ['GitHub', 'github'],
        ['Linux', 'linux'],
        ['Windows', 'windows'],
        ['macOS', 'macos']
    ];

    function createList(hidden = false) {
        const list = document.createElement('ul');
        list.className = 'technology-list';
        list.setAttribute('role', 'list');
        if (hidden) list.setAttribute('aria-hidden', 'true');

        technologies.forEach(([name, logo]) => {
            const item = document.createElement('li');
            item.className = 'technology-logo';
            item.dataset.tech = name;
            item.title = name;
            const icon = document.createElement('img');
            icon.className = 'technology-mark';
            icon.src = `assets/technology-logos/${logo}.svg`;
            icon.alt = '';
            icon.setAttribute('aria-hidden', 'true');
            icon.width = 32;
            icon.height = 32;
            icon.decoding = 'async';
            const label = document.createElement('span');
            label.textContent = name;
            item.append(icon, label);
            list.appendChild(item);
        });

        return list;
    }

    track.append(createList(), createList(true));
})();
