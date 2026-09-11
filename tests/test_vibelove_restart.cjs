const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../vibelove/templates/index.html'), 'utf8');
const start = html.indexOf('    let previewRestartRunning');
const end = html.indexOf('    // ── Projekt-Verwaltung', start);
const code = html.slice(start, end);

async function scenario(status, ok) {
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {
            disabled: false, textContent: '', open: false,
            classList: { remove() {}, toggle() {} },
        });
        return elements.get(id);
    };
    let reloads = 0;
    const requests = [];
    const context = vm.createContext({
        document: { getElementById: element },
        setInterval() { return 1; }, clearInterval() {},
        reloadPreview() { reloads++; },
        async fetch(url) {
            requests.push(url);
            if (url === '/preview-status') return { ok: true, json: async () => ({ log: 'process output' }) };
            assert.equal(element('restartViteBtn').disabled, true);
            return { ok, status, json: async () => ({ ok, error: 'Start failed' }) };
        },
    });
    vm.runInContext(code, context);
    await vm.runInContext('restartVite()', context);
    assert.equal(element('restartViteBtn').disabled, false);
    assert.equal(element('restartViteBtn').textContent, 'Vorschau neu starten');
    assert.equal(reloads, ok ? 1 : 0);
    assert.equal(element('previewRestartMessage').textContent, ok ? 'Bereit' : 'Start failed');
    if (status === 409) assert.deepEqual(requests, ['/restart-vite']);
    if (!ok) assert.equal(element('previewRestartDetails').open, true);
    await vm.runInContext('restartVite()', context);
    assert.equal(requests.filter(url => url === '/restart-vite').length, 2);
}

(async () => {
    await scenario(200, true);
    await scenario(500, false);
    await scenario(409, false);
    console.log('Preview restart: success, errors, rejection and retry OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
