(() => {
    const panel = document.getElementById('projectTerminal');
    const body = document.getElementById('terminalBody');
    const toggle = document.getElementById('terminalToggle');
    const restart = document.getElementById('terminalRestart');
    const handle = document.getElementById('terminalResize');
    const status = document.getElementById('terminalStatus');
    const files = document.getElementById('filesPanel');
    const sessions = new Map();
    let project = null;
    let layout = { open: false, height: 240 };
    let fitting = false;

    function message(session, text) {
        session.message = text;
        if (session.project === project) { status.textContent = text; status.title = text; }
    }
    async function api(path, data) {
        const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        const value = await response.json();
        if (!response.ok) throw new Error(value.error || 'Terminal nicht erreichbar.');
        return value;
    }
    function saveLayout() {
        if (!project) return;
        try { localStorage.setItem('vibelove:terminal:' + project, JSON.stringify(layout)); } catch (error) {}
    }
    function visible() { return files.getBoundingClientRect().height > 0 && layout.open; }
    function applyLayout() {
        body.hidden = !layout.open;
        handle.hidden = !layout.open;
        toggle.setAttribute('aria-expanded', String(layout.open));
        toggle.title = layout.open ? 'Terminal einklappen' : 'Terminal öffnen';
        document.getElementById('terminalChevron').style.transform = layout.open ? 'rotate(180deg)' : '';
        restart.hidden = !layout.open;
        const max = Math.max(100, files.getBoundingClientRect().height - 80);
        const height = Math.max(100, Math.min(layout.height, max));
        panel.style.flexBasis = (layout.open ? height : 34) + 'px';
        handle.setAttribute('aria-valuemin', '100');
        handle.setAttribute('aria-valuemax', String(max));
        handle.setAttribute('aria-valuenow', String(Math.round(height)));
        if (!visible() || !project) return;
        let session = sessions.get(project);
        if (!session) session = createSession(project);
        if (!session) return;
        if (!session.id && !session.connecting) connect(session);
        scheduleFit();
    }
    function scheduleFit() {
        if (fitting) return;
        fitting = true;
        requestAnimationFrame(() => {
            fitting = false;
            const session = sessions.get(project);
            if (!visible() || !session) return;
            session.fit.fit();
        });
    }
    function queue(session, path, data) {
        session.queue = session.queue.then(() => api(path, { project: session.project, session: session.id, ...data }))
            .catch(error => message(session, error.message));
    }
    function createSession(name) {
        if (!window.Terminal || !window.FitAddon) { status.textContent = 'Terminal-Bibliothek konnte nicht geladen werden.'; return null; }
        const host = document.createElement('div');
        body.appendChild(host);
        const term = new Terminal({ cursorBlink: true, fontSize: 13, scrollback: 3000,
            theme: { background: '#16191c', foreground: '#e5e7eb' }, allowProposedApi: false });
        const fit = new FitAddon.FitAddon();
        term.loadAddon(fit);
        term.open(host);
        const session = { project: name, host, term, fit, id: null, cursor: 0, connecting: false,
            queue: Promise.resolve(), polling: false, exited: false, message: '' };
        sessions.set(name, session);
        term.onData(data => {
            if (!session.id || session.exited) return;
            // Keep pasted text within the endpoint's byte limit, preserving order.
            for (let i = 0; i < data.length;) {
                let end = Math.min(i + 2000, data.length);
                if (end < data.length && /[\uD800-\uDBFF]/.test(data[end - 1])) end--;
                queue(session, '/terminal/input', { data: data.slice(i, end) });
                i = end;
            }
        });
        term.onResize(({ cols, rows }) => {
            if (session.id && !session.exited) queue(session, '/terminal/resize', { cols, rows });
        });
        return session;
    }
    async function connect(session, reset = false) {
        if (session.connecting) return;
        session.connecting = true;
        message(session, 'Verbindet …');
        try {
            const data = await api('/terminal/open', { project: session.project, session: session.id,
                restart: reset, cols: session.term.cols, rows: session.term.rows });
            if (session.id !== data.session) {
                session.term.reset(); session.cursor = 0;
            }
            session.id = data.session;
            session.exited = false;
            message(session, session.project);
            await poll(session);
            if (project === session.project && visible()) { session.fit.fit(); session.term.focus(); }
        } catch (error) { message(session, error.message); }
        finally { session.connecting = false; }
    }
    async function poll(session) {
        if (session.polling || !session.id || session.exited) return;
        session.polling = true;
        const id = session.id;
        try {
            const params = new URLSearchParams({ project: session.project, session: id, cursor: session.cursor });
            const response = await fetch('/terminal/output?' + params);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Verbindung unterbrochen.');
            if (session.id !== id) return;
            if (data.data) await new Promise(resolve => session.term.write(Uint8Array.from(atob(data.data), char => char.charCodeAt(0)), resolve));
            session.cursor = data.cursor;
            session.exited = data.exited;
            message(session, data.exited ? 'Shell beendet' : session.project);
        } catch (error) { message(session, error.message); }
        finally { session.polling = false; }
    }
    toggle.onclick = () => { layout.open = !layout.open; saveLayout(); applyLayout(); };
    restart.onclick = async () => {
        const session = sessions.get(project);
        if (!session || session.connecting) return;
        if (session.id && !session.exited && !confirm('Terminalsitzung beenden und neu starten?')) return;
        await session.queue;
        await connect(session, !!session.id);
    };
    handle.onpointerdown = event => {
        if (event.button !== 0) return;
        handle.setPointerCapture(event.pointerId);
        document.body.classList.add('terminal-resizing');
    };
    handle.onpointermove = event => {
        if (!handle.hasPointerCapture(event.pointerId)) return;
        layout.height = Math.max(100, Math.min(files.getBoundingClientRect().height - 80, panel.getBoundingClientRect().bottom - event.clientY));
        applyLayout();
    };
    handle.onlostpointercapture = () => { document.body.classList.remove('terminal-resizing'); saveLayout(); };
    handle.onkeydown = event => {
        if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        layout.height = Math.max(100, Math.min(files.getBoundingClientRect().height - 80, layout.height + (event.key === 'ArrowUp' ? 20 : -20)));
        saveLayout(); applyLayout();
    };
    window.projectTerminal = { setProject(name) {
        if (project === name) return;
        project = name;
        layout = { open: false, height: 240 };
        try {
            const saved = JSON.parse(localStorage.getItem('vibelove:terminal:' + name));
            if (saved) layout = { open: saved.open === true, height: Number.isFinite(saved.height) ? saved.height : 240 };
        } catch (error) {}
        for (const session of sessions.values()) session.host.hidden = session.project !== name;
        status.textContent = sessions.get(name)?.message || '';
        applyLayout();
    } };
    new ResizeObserver(applyLayout).observe(files);
    new ResizeObserver(scheduleFit).observe(body);
    setInterval(() => { const session = sessions.get(project); if (visible() && session) poll(session); }, 200);
    if (typeof layoutProject === 'string') window.projectTerminal.setProject(layoutProject);
})();
