const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '../vibelove/templates/index.html'), 'utf8');
for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) {
    new vm.Script(match[1]);
}
const elements = new Map();
const storage = new Map();
const classes = new Set();
function element(id) {
    if (!elements.has(id)) elements.set(id, {
        style: {}, attributes: {}, hidden: false,
        setAttribute(key, value) { this.attributes[key] = String(value); },
        getBoundingClientRect() { return { width: parseFloat(this.style.width) }; },
    });
    return elements.get(id);
}
const context = vm.createContext({
    console: { warn() {} },
    window: { innerWidth: 1400, addEventListener() {} },
    document: {
        getElementById: element,
        body: { classList: {
            toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
            add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
        } },
    },
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
});
const start = html.indexOf("        const chatPanel =");
const end = html.indexOf('        let gitCommits', start);
vm.runInContext(html.slice(start, end), context);
vm.runInContext("loadChatLayout('alpha'); chatLayout.width = 360; applyChatLayout(); saveChatLayout(); toggleChatBtn.onclick();", context);
assert.equal(element('chatPanel').hidden, true);
vm.runInContext("loadChatLayout('beta')", context);
assert.equal(element('chatPanel').hidden, false);
assert.equal(element('chatPanel').style.width, '672px');
vm.runInContext("loadChatLayout('alpha')", context);
assert.equal(element('chatPanel').hidden, true);
assert.equal(element('chatPanel').style.width, '360px');
vm.runInContext("toggleChatBtn.onclick(); chatResizer.onkeydown({key: 'ArrowLeft', preventDefault() {}}); loadChatLayout('alpha');", context);
assert.equal(element('chatPanel').style.width, '340px');
vm.runInContext('window.innerWidth = 390; applyChatLayout()', context);
assert.ok(parseFloat(element('chatPanel').style.width) <= 312);
assert.equal(element('toggleChatBtn').attributes['aria-expanded'], 'true');
storage.set('vibelove:chat:broken', '{invalid');
vm.runInContext("loadChatLayout('broken')", context);
assert.equal(element('chatPanel').hidden, false);
console.log('JavaScript syntax and project layout persistence OK');
