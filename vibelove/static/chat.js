/* Project chat: durable jobs, incremental events, safe Markdown. */
(() => {
    const active = status => ['queued', 'running', 'stopping'].includes(status);
    const clean = text => String(text || '').replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '').replace(/\r(?!\n)/g, '\n');
    const element = (tag, className, text) => {
        const node = document.createElement(tag);
        node.className = className || '';
        if (text !== undefined) node.textContent = text;
        return node;
    };
    function markdown(node, text) {
        node.innerHTML = DOMPurify.sanitize(marked.parse(clean(text), { breaks: true }), {
            USE_PROFILES: { html: true }, FORBID_TAGS: ['img', 'style', 'form', 'input', 'button', 'iframe'],
            FORBID_ATTR: ['style', 'id', 'name']
        });
        node.querySelectorAll('a').forEach(link => { link.target = '_blank'; link.rel = 'noopener noreferrer'; });
    }
    function poText(raw) {
        return clean(raw).replace(/```decision[\s\S]*?(?:```|$)/g, '')
            .replace(/```(?:summary|question)\s*\n?/g, '')
            .replace(/```instruction\s*\n?/g, '\n### Auftrag\n')
            .replace(/```acceptance\s*\n?/g, '\n### Akzeptanzkriterien\n')
            .replace(/```step-(\d+)\s*\n?/g, '\n### Schritt $1\n')
            .replace(/^```\s*$/gm, '');
    }
    class VibeloveChat {
        constructor({ feed, form, input, send, busy, onBuildDone, rollback }) {
            Object.assign(this, { feed, form, input, send, busy, onBuildDone, rollback });
            this.messages = new Map(); this.nodes = new Map(); this.seq = 0; this.generation = 0;
            this.follow = true; this.submitting = false; this.polling = false;
            this.connection = element('div', 'chat-connection');
            this.actions = element('div', 'chat-actions');
            feed.after(this.connection, this.actions);
            this.jump = element('button', 'chat-jump', 'Neue Ausgabe ↓');
            this.jump.type = 'button'; this.jump.hidden = true;
            this.connection.before(this.jump);
            this.jump.onclick = () => { this.follow = true; this.scroll(); };
            feed.addEventListener('scroll', () => {
                this.follow = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
                if (this.follow) this.jump.hidden = true;
            });
            window.addEventListener('resize', () => { if (this.follow) requestAnimationFrame(() => this.scroll()); });
            input.addEventListener('input', () => { this.resizeInput(); this.saveDraft(); });
            input.addEventListener('keydown', event => {
                if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
                    event.preventDefault(); form.requestSubmit();
                }
            });
            form.onsubmit = event => { event.preventDefault(); this.submit(); };
            this.timer = setInterval(() => this.poll(), 700);
            this.clock = setInterval(() => this.updateClock(), 1000);
        }
        resizeInput() {
            this.input.style.height = 'auto';
            this.input.style.height = Math.min(this.input.scrollHeight, 128) + 'px';
        }
        saveDraft() {
            if (this.project) {
                try { sessionStorage.setItem('vibelove:draft:' + this.project, this.input.value); } catch (_) {}
            }
        }
        async api(url, options = {}) {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 15000);
            try {
                const response = await fetch(url, { ...options, signal: controller.signal });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
                return data;
            } finally { clearTimeout(timer); }
        }
        async load() {
            const generation = ++this.generation;
            this.saveDraft();
            this.loaded = false;
            this.busy(true); this.send.disabled = true;
            this.connection.textContent = 'Chat wird geladen…';
            try {
                const state = await this.api('/chat/state');
                if (generation !== this.generation) return;
                const changedProject = this.project !== state.project;
                this.project = state.project; this.seq = state.seq;
                if (changedProject) {
                    try { this.input.value = sessionStorage.getItem('vibelove:draft:' + this.project) || ''; }
                    catch (_) { this.input.value = ''; }
                    this.resizeInput();
                }
                this.messages = new Map(state.messages.map(m => [m.id, m]));
                this.nodes.clear(); this.feed.replaceChildren(); this.follow = true;
                this.render(); this.loaded = true; this.connection.textContent = '';
            } catch (error) {
                if (generation === this.generation) this.connection.textContent = 'Chat nicht erreichbar. Erneuter Verbindungsversuch…';
            }
        }
        async poll() {
            if (this.polling) return;
            this.polling = true;
            const generation = this.generation;
            try {
                if (!this.loaded) { await this.load(); return; }
                const data = await this.api('/chat/events?after=' + this.seq);
                if (generation !== this.generation) return;
                if (data.project !== this.project) { await this.load(); return; }
                if (data.reset) {
                    this.messages = new Map(data.reset.messages.map(m => [m.id, m]));
                    this.seq = data.reset.seq;
                } else {
                    for (const event of data.events) {
                        if (event.type === 'message') this.messages.set(event.message.id, event.message);
                        else {
                            const msg = this.messages.get(event.id);
                            if (!msg) continue;
                            if (event.type === 'delta') {
                                msg.raw = (msg.raw + event.text).slice(-2000000); msg.updated = event.updated;
                            } else Object.assign(msg, event.fields);
                        }
                    }
                    this.seq = data.seq;
                }
                this.connection.textContent = '';
                this.render();
            } catch (error) {
                this.connection.textContent = 'Verbindung unterbrochen. Auftrag laeuft auf dem Server weiter; verbinde erneut…';
            } finally { this.polling = false; }
        }
        scroll() {
            if (this.follow) { this.feed.scrollTop = this.feed.scrollHeight; this.jump.hidden = true; }
            else this.jump.hidden = false;
        }
        render() {
            let changed = false;
            for (const message of this.messages.values()) {
                let ui = this.nodes.get(message.id);
                if (!ui) {
                    const article = element('article', 'chat-message chat-' + message.role);
                    const header = element('header', 'chat-message-header');
                    const label = element('strong', '', ({ user: 'Du', po: 'Product Owner', build: 'Bauen', system: 'System' })[message.role]);
                    const status = element('span', 'chat-message-status');
                    header.append(label, status);
                    const model = element('small', 'chat-model', message.model || '');
                    const body = element('div', 'chat-markdown');
                    const details = element('details', 'chat-raw');
                    const summary = element('summary', '', 'Protokoll');
                    const pre = element('pre'); details.append(summary, pre);
                    article.append(header, model, body, details);
                    this.feed.append(article);
                    ui = { article, status, body, details, pre, version: null };
                    this.nodes.set(message.id, ui);
                }
                if (ui.version === message.updated) continue;
                const wasActive = active(ui.statusValue);
                ui.statusValue = message.status;
                if (wasActive && !active(message.status) && message.role === 'build') this.onBuildDone();
                ui.version = message.updated;
                ui.article.dataset.status = message.status;
                const decision = message.decision;
                let text = message.text;
                if (message.role === 'po' && decision && decision.type !== 'error') {
                    text = decision.question || decision.summary || '';
                    if (decision.instruction) text += '\n\n### Auftrag\n' + decision.instruction;
                    if (decision.acceptance) text += '\n\n### Akzeptanzkriterien\n' + decision.acceptance;
                    if (decision.steps) text += decision.steps.map(s => `\n\n### Schritt ${s.num}\n${s.instruction}`).join('');
                } else if (message.role === 'po' && message.raw) {
                    text = (message.text ? message.text + '\n\n' : '') + poText(message.raw);
                } else if (message.role === 'build') {
                    const raw = clean(message.raw).replace(/^\[Request-Diagnose\][^\n]*(?:\n|$)/gm, '');
                    text = (active(message.status) ? '' : message.text + '\n\n') + raw.slice(-80000);
                }
                if (message.role === 'user') ui.body.textContent = text;
                else markdown(ui.body, text || (active(message.status) ? ' ' : 'Keine Ausgabe.'));
                ui.pre.textContent = clean(message.raw);
                ui.details.hidden = !message.raw;
                if (message.rollback_to && !ui.rollback) {
                    ui.rollback = this.button('Vorherigen Stand wiederherstellen', () => this.rollback(message.rollback_to));
                    ui.article.append(ui.rollback);
                }
                changed = true;
            }
            this.updateActions(); this.updateClock();
            if (changed) requestAnimationFrame(() => this.scroll());
        }
        updateClock() {
            for (const m of this.messages.values()) {
                const ui = this.nodes.get(m.id); if (!ui) continue;
                const labels = { complete: 'Fertig', error: 'Fehler', incomplete: 'Unvollstaendig', stopped: 'Gestoppt', interrupted: 'Unterbrochen' };
                ui.status.textContent = active(m.status)
                    ? `${m.phase || 'Wird gestartet'} · ${Math.floor((Date.now() / 1000 - m.created))} s`
                    : labels[m.status] || m.status;
                if (active(m.status) && Date.now() / 1000 - m.updated > 30) {
                    ui.status.textContent += ` · seit ${Math.floor(Date.now() / 1000 - m.updated)} s keine neuen Daten`;
                }
                if (active(m.status) && m.attempt > 1) ui.status.textContent += ` · Versuch ${m.attempt}`;
                if (ui.rollback) ui.rollback.disabled = this.messagesArray().some(m => active(m.status));
            }
        }
        messagesArray() { return [...this.messages.values()]; }
        button(label, action, primary = false) {
            const button = element('button', primary ? 'primary' : '', label);
            button.type = 'button'; button.onclick = action; return button;
        }
        updateActions() {
            const messages = this.messagesArray();
            const job = messages.find(m => active(m.status));
            const decision = [...messages].reverse().find(m => m.role === 'po' && m.actionable && ['spec', 'plan'].includes(m.decision?.type));
            const last = messages.at(-1);
            const retryable = last?.role === 'po' && ['error', 'stopped', 'interrupted'].includes(last.status) && last.request_text;
            const key = [job?.id, job?.status, decision?.id, retryable && last.id, this.submitting].join(':');
            this.send.disabled = Boolean(job || this.submitting);
            this.busy(Boolean(job || this.submitting));
            if (key === this.actionKey) return;
            this.actionKey = key; this.actions.replaceChildren();
            if (job) {
                this.actions.append(this.button('Stoppen', async () => {
                    try { await this.api('/chat/stop', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id: job.id }) }); await this.poll(); }
                    catch (error) { this.notice(error.message); }
                }));
                this.actions.firstChild.disabled = job.status === 'stopping';
            } else if (decision) {
                this.actions.append(this.button('Bauen', () => this.start({ kind: 'build', source_id: decision.id }), true));
                this.actions.append(this.button('Anpassen', () => { this.input.focus(); this.input.placeholder = 'Was soll am Auftrag geaendert werden?'; }));
            } else if (retryable) {
                this.actions.append(this.button('Erneut versuchen', () => this.start({ kind: 'po', text: last.request_text, mit_verlauf: true })));
            }
            this.input.placeholder = job ? 'Naechste Nachricht…' : 'Nachricht an den PO…';
        }
        async start(payload) {
            if (this.submitting || this.messagesArray().some(m => active(m.status))) return;
            this.submitting = true; this.updateActions(); this.follow = true;
            const request_id = this.retry?.key === JSON.stringify(payload) ? this.retry.id : crypto.randomUUID();
            this.retry = { key: JSON.stringify(payload), id: request_id };
            try {
                await this.api('/chat/start', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ ...payload, request_id }) });
                this.retry = null;
                if (payload.kind === 'po' && this.input.value.trim() === payload.text) { this.input.value = ''; this.resizeInput(); this.saveDraft(); }
                await this.poll();
            } catch (error) {
                this.notice('Anfrage nicht bestaetigt: ' + error.message);
                await this.poll();
            } finally { this.submitting = false; this.updateActions(); }
        }
        submit() {
            const text = this.input.value.trim();
            if (text) this.start({ kind: 'po', text, mit_verlauf: document.getElementById('poMitVerlauf').checked });
        }
        notice(text) {
            const node = element('div', 'chat-notice', text); this.feed.append(node); this.scroll();
        }
    }
    window.VibeloveChat = VibeloveChat;
})();
