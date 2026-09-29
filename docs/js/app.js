document.addEventListener('DOMContentLoaded', () => {
    // --- Menu mobile (identique au portfolio) ---
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('mobile-overlay');
    const openBtn = document.getElementById('open-menu-btn');
    const closeBtn = document.getElementById('close-menu-btn');

    function openMenu() {
        sidebar.classList.remove('-translate-x-full');
        sidebar.classList.add('translate-x-0');
        overlay.classList.remove('hidden');
        requestAnimationFrame(() => overlay.classList.remove('opacity-0'));
    }

    function closeMenu() {
        sidebar.classList.remove('translate-x-0');
        sidebar.classList.add('-translate-x-full');
        overlay.classList.add('opacity-0');
        setTimeout(() => overlay.classList.add('hidden'), 300);
    }

    if (openBtn) openBtn.addEventListener('click', openMenu);
    if (closeBtn) closeBtn.addEventListener('click', closeMenu);
    if (overlay) overlay.addEventListener('click', closeMenu);
    document.querySelectorAll('#sidebar a[href^="#"]').forEach(a => {
        a.addEventListener('click', () => {
            if (window.innerWidth < 768) closeMenu();
        });
    });

    // --- Langue FR / EN ---
    const langBtn = document.getElementById('lang-btn');
    let lang = 'fr';
    try {
        lang = localStorage.getItem('easysync-lang') || (navigator.language.startsWith('fr') ? 'fr' : 'en');
    } catch (e) { /* stockage indisponible */ }

    function applyLang() {
        document.documentElement.lang = lang;
        document.querySelectorAll('[data-en]').forEach(el => {
            const text = el.getAttribute('data-' + lang);
            if (text !== null) el.innerHTML = text;
        });
        if (langBtn) langBtn.querySelector('span').textContent = lang === 'fr' ? 'EN' : 'FR';
        if (typeof onScroll === 'function') onScroll();
    }

    if (langBtn) {
        langBtn.addEventListener('click', () => {
            lang = lang === 'fr' ? 'en' : 'fr';
            try { localStorage.setItem('easysync-lang', lang); } catch (e) { }
            applyLang();
        });
    }

    // --- Copier la commande d'installation ---
    document.querySelectorAll('[data-copy]').forEach(btn => {
        btn.addEventListener('click', () => {
            navigator.clipboard.writeText(btn.getAttribute('data-copy')).then(() => {
                const icon = btn.querySelector('.material-symbols-outlined');
                icon.textContent = 'check';
                setTimeout(() => (icon.textContent = 'content_copy'), 1500);
            });
        });
    });

    // --- Scrollspy (page wiki) ---
    const scroller = document.getElementById('main-scroll');
    const tocLinks = document.querySelectorAll('.toc-link');
    const title = document.getElementById('topbar-title');
    let onScroll = null;
    if (scroller && tocLinks.length) {
        const sections = [...tocLinks].map(l => document.querySelector(l.getAttribute('href'))).filter(Boolean);
        onScroll = () => {
            let current = sections[0];
            for (const s of sections) {
                if (s.getBoundingClientRect().top < 140) current = s;
            }
            // La dernière section peut être trop courte pour atteindre le haut
            if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) {
                current = sections[sections.length - 1];
            }
            tocLinks.forEach(l => l.classList.toggle('active', l.getAttribute('href') === '#' + current.id));
            const h2 = current.querySelector('h2');
            if (title && h2) title.textContent = h2.textContent.trim();
        };
        scroller.addEventListener('scroll', onScroll, { passive: true });
    }

    applyLang();
});

// ==============================================
// BACKGROUND CLOUD ANIMATION (repris du portfolio)
// ==============================================
const SimplexNoise = function () {
    this.grad3 = [[1, 1, 0], [-1, 1, 0], [1, -1, 0], [-1, -1, 0], [1, 0, 1], [-1, 0, 1], [1, 0, -1], [-1, 0, -1], [0, 1, 1], [0, -1, 1], [0, 1, -1], [0, -1, -1]];
    this.p = []; for (var i = 0; i < 256; i++) { this.p[i] = Math.floor(Math.random() * 256); }
    this.perm = []; for (var i = 0; i < 512; i++) { this.perm[i] = this.p[i & 255]; }
    this.dot = function (g, x, y) { return g[0] * x + g[1] * y; };
    this.noise = function (xin, yin) {
        var n0, n1, n2; var F2 = 0.5 * (Math.sqrt(3.0) - 1.0); var s = (xin + yin) * F2; var i = Math.floor(xin + s); var j = Math.floor(yin + s); var G2 = (3.0 - Math.sqrt(3.0)) / 6.0; var t = (i + j) * G2; var X0 = i - t; var Y0 = j - t; var x0 = xin - X0; var y0 = yin - Y0; var i1, j1; if (x0 > y0) { i1 = 1; j1 = 0; } else { i1 = 0; j1 = 1; } var x1 = x0 - i1 + G2; var y1 = y0 - j1 + G2; var x2 = x0 - 1.0 + 2.0 * G2; var y2 = y0 - 1.0 + 2.0 * G2; var ii = i & 255; var jj = j & 255; var gi0 = this.perm[ii + this.perm[jj]] % 12; var gi1 = this.perm[ii + i1 + this.perm[jj + j1]] % 12; var gi2 = this.perm[ii + 1 + this.perm[jj + 1]] % 12; var t0 = 0.5 - x0 * x0 - y0 * y0; if (t0 < 0) n0 = 0.0; else { t0 *= t0; n0 = t0 * t0 * this.dot(this.grad3[gi0], x0, y0); } var t1 = 0.5 - x1 * x1 - y1 * y1; if (t1 < 0) n1 = 0.0; else { t1 *= t1; n1 = t1 * t1 * this.dot(this.grad3[gi1], x1, y1); } var t2 = 0.5 - x2 * x2 - y2 * y2; if (t2 < 0) n2 = 0.0; else { t2 *= t2; n2 = t2 * t2 * this.dot(this.grad3[gi2], x2, y2); } return 70.0 * (n0 + n1 + n2);
    };
};

document.addEventListener('DOMContentLoaded', () => {
    const bgCanvas = document.getElementById('cloud-bounce-canvas');
    if (!bgCanvas) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const ctx = bgCanvas.getContext('2d');
    const noiseGen = new SimplexNoise();
    let width, height;
    let time = 0;

    const gridSize = 4;
    const flowSpeed = 0.005;
    const cloudZoom = 0.0018;
    // Teintes de gris très clairs se fondant dans le bg (#f7f9fb)
    const colors = ['#f7f9fb', '#eceef0', '#e0e3e5', '#c2c6d8'];

    function initCanvas() {
        width = window.innerWidth;
        height = window.innerHeight;
        bgCanvas.width = width;
        bgCanvas.height = height;
    }

    window.addEventListener('resize', initCanvas);
    initCanvas();

    function fbm(x, y) {
        let total = 0; let amp = 0.6; let freq = 1.0; let max = 0;
        for (let i = 0; i < 5; i++) {
            total += noiseGen.noise(x * cloudZoom * freq, y * cloudZoom * freq) * amp;
            max += amp; amp *= 0.5; freq *= 2.0;
        }
        return Math.pow((total / max) + 0.5, 2.2);
    }

    function drawBg() {
        ctx.clearRect(0, 0, width, height);

        for (let y = 0; y < height; y += gridSize) {
            for (let x = 0; x < width; x += gridSize) {
                let n = fbm(x + time * 50, y + time * 30);
                if (n < 0.12) continue;

                let intensity = (n - 0.12) / 0.88;
                let cIdx = intensity > 0.75 ? 3 : intensity > 0.5 ? 2 : intensity > 0.25 ? 1 : 0;

                if (cIdx > 0) {
                    ctx.fillStyle = colors[cIdx];
                    if (Math.random() < intensity * 2.5) ctx.fillRect(x, y, gridSize - 1, gridSize - 1);
                }
            }
        }
        time += flowSpeed;
        requestAnimationFrame(drawBg);
    }

    drawBg();
});
