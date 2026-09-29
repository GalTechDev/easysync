// ==============================================
// SYNC_CLUSTER — démo live intégrée au schéma de la page d'accueil
// Un client modifie son état -> paquet vers le SyncServer -> broadcast aux autres
// ==============================================
document.addEventListener('DOMContentLoaded', () => {
    const svg = document.getElementById('cluster-svg');
    if (!svg) return;

    const NODES = ['A', 'B', 'C'];
    const COLORS = { A: '#0056c6', B: '#0056c6', C: '#006e0b' }; // C passe par UDP
    const SELECTED_STROKE = { A: '#0056c6', B: '#0056c6', C: '#006e0b' };
    const IDLE_STROKE = { A: '#c2c6d8', B: '#c2c6d8', C: '#a1e8af' };
    const IDLE_FILL = { A: '#ffffff', B: '#ffffff', C: '#eafdf0' };
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const packetsLayer = document.getElementById('cluster-packets');
    const logEl = document.getElementById('cluster-log');
    const nodeEls = {};
    NODES.forEach(id => (nodeEls[id] = svg.querySelector(`.cluster-node[data-node="${id}"]`)));

    let clients, server, relayed, source = 'A', generation = 0;

    function reset() {
        generation++; // invalide les paquets encore en vol
        packetsLayer.innerHTML = '';
        clients = {};
        NODES.forEach(id => (clients[id] = { score: 0, players: [] }));
        server = { score: 0, players: [] };
        relayed = 0;
        NODES.forEach(renderClient);
        renderServer();
    }

    // --- Rendu ---
    function renderClient(id) {
        const c = clients[id];
        nodeEls[id].querySelector('.node-val').textContent = `score ${c.score} · players ${c.players.length}`;
    }

    function formatPlayers(list) {
        const shown = list.slice(-4).join(', ');
        return `players = [${list.length > 4 ? '…, ' : ''}${shown}]`;
    }

    function renderServer() {
        document.getElementById('srv-score').textContent = `score = ${server.score}`;
        document.getElementById('srv-players').textContent = formatPlayers(server.players);
        document.getElementById('srv-ops').textContent = `relayed = ${relayed}`;
    }

    function flash(groupEl) {
        const box = groupEl.querySelector('.node-box');
        const prev = box.getAttribute('fill');
        box.setAttribute('fill', '#d9f7e0');
        setTimeout(() => box.setAttribute('fill', prev), 350);
    }

    function selectSource(id) {
        source = id;
        NODES.forEach(n => {
            const box = nodeEls[n].querySelector('.node-box');
            box.setAttribute('stroke', n === id ? SELECTED_STROKE[n] : IDLE_STROKE[n]);
            box.setAttribute('stroke-width', n === id ? '3' : '1');
            box.setAttribute('fill', IDLE_FILL[n]);
        });
        document.querySelectorAll('.src-btn').forEach(btn => {
            const on = btn.dataset.src === id;
            const onBg = id === 'C' ? 'bg-secondary' : 'bg-primary';
            btn.classList.remove('bg-primary', 'bg-secondary', 'text-on-primary', 'text-on-surface-variant', 'bg-surface-container-lowest', 'hover:bg-surface-container');
            if (on) btn.classList.add(onBg, 'text-on-primary');
            else btn.classList.add('bg-surface-container-lowest', 'text-on-surface-variant', 'hover:bg-surface-container');
        });
    }

    // --- Paquets animés le long des liens ---
    function travel(id, toServer) {
        const path = document.getElementById('link-' + id);
        const len = path.getTotalLength();
        const gen = generation;
        const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        dot.setAttribute('r', '5');
        dot.setAttribute('fill', COLORS[id]);
        packetsLayer.appendChild(dot);

        const duration = reducedMotion ? 0 : 450;
        return new Promise(resolve => {
            const start = performance.now();
            function step(now) {
                if (gen !== generation) { dot.remove(); return; }
                const t = duration ? Math.min(1, (now - start) / duration) : 1;
                const eased = 1 - Math.pow(1 - t, 3);
                const p = path.getPointAtLength(len * (toServer ? eased : 1 - eased));
                dot.setAttribute('cx', p.x);
                dot.setAttribute('cy', p.y);
                if (t < 1) requestAnimationFrame(step);
                else { dot.remove(); resolve(); }
            }
            requestAnimationFrame(step);
        });
    }

    // --- Opérations ---
    const OPS = {
        score: { code: 'state.score += 10', apply: s => { s.score += 10; } },
        append: { code: null, apply: null }, // construit dynamiquement (nom du client)
    };

    function makeOp(kind, src) {
        if (kind === 'score') return OPS.score;
        const name = `'${src}${clients[src].players.length + 1}'`;
        return { code: `state.players.append(${name})`, apply: s => { s.players.push(name.replace(/'/g, '')); } };
    }

    function log(text) {
        logEl.removeAttribute('data-en'); // le journal n'est plus traduit une fois la démo lancée
        logEl.removeAttribute('data-fr');
        logEl.textContent = text;
    }

    function run(kind, src) {
        const op = makeOp(kind, src);
        const gen = generation;
        op.apply(clients[src]);
        renderClient(src);
        flash(nodeEls[src]);
        const others = NODES.filter(n => n !== src);
        const transport = src === 'C' ? 'UDP' : 'TCP';
        log(`> CLIENT_${src} · ${op.code}  —  ${transport} → SyncServer → broadcast ×${others.length}`);

        travel(src, true).then(() => {
            if (gen !== generation) return;
            op.apply(server);
            relayed++;
            renderServer();
            flash(document.getElementById('cluster-server'));
            others.forEach(id => travel(id, false).then(() => {
                if (gen !== generation) return;
                op.apply(clients[id]);
                renderClient(id);
                flash(nodeEls[id]);
            }));
        });
    }

    // --- Interactions ---
    let autoplay = null;
    function stopAutoplay() {
        if (autoplay) { clearInterval(autoplay); autoplay = null; }
    }

    NODES.forEach(id => {
        nodeEls[id].style.cursor = 'pointer';
        nodeEls[id].addEventListener('click', () => { stopAutoplay(); selectSource(id); });
    });
    document.querySelectorAll('.src-btn').forEach(btn =>
        btn.addEventListener('click', () => { stopAutoplay(); selectSource(btn.dataset.src); }));
    document.querySelectorAll('.op-btn').forEach(btn =>
        btn.addEventListener('click', () => {
            stopAutoplay();
            if (btn.dataset.op === 'reset') reset();
            else run(btn.dataset.op, source);
        }));

    reset();
    selectSource('A');

    // Démo automatique tant que le visiteur n'a pas interagi
    if (!reducedMotion) {
        let i = 0;
        autoplay = setInterval(() => {
            const src = NODES[i % 3];
            selectSource(src);
            run(i % 2 ? 'append' : 'score', src);
            i++;
        }, 2600);
    }
});
