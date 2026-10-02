// Browser contract test. CHAT_DEMO_URL must point to a simulated, isolated server.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
    if (!process.env.CHAT_DEMO_URL) throw new Error('CHAT_DEMO_URL is required (simulated PO/build).');
    const browser = await chromium.launch({ headless: true,
        ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
    try {
        const page = await browser.newPage({ viewport: { width: 1280, height: 850 } });
        const errors = []; page.on('pageerror', error => errors.push(error.message));
        await page.goto(process.env.CHAT_DEMO_URL);
        await page.waitForFunction(() => !document.getElementById('buildButton').disabled);
        await page.locator('#instruction').fill('Bitte Kundenverwaltung erstellen');
        await page.locator('#buildButton').click();
        await page.locator('.chat-po[data-status="running"]').waitFor();
        await page.reload();
        await page.locator('.chat-po .chat-markdown strong').last().waitFor();
        await page.getByRole('button', { name: 'Bauen', exact: true }).waitFor();
        assert.equal(await page.locator('.chat-po .chat-markdown img').count(), 0);
        assert.equal(await page.evaluate(() => window.hacked), undefined);
        await page.setViewportSize({ width: 390, height: 844 });
        await page.screenshot({ path: '/tmp/vibelove-chat-mobile.png' });
        const button = await page.getByRole('button', { name: 'Bauen', exact: true }).boundingBox();
        const input = await page.locator('#instruction').boundingBox();
        assert(button.y + button.height <= input.y, 'Build action must stay above composer');
        assert(button.y >= 0 && input.y + input.height <= 844);
        await page.getByRole('button', { name: 'Bauen', exact: true }).click();
        await page.locator('.chat-build[data-status="running"]').waitFor();
        await page.route('**/chat/events?*', route => route.abort());
        await page.locator('.chat-connection').filter({ hasText: 'Verbindung unterbrochen' }).waitFor();
        await page.waitForTimeout(4000);
        await page.unroute('**/chat/events?*');
        await page.locator('.chat-build[data-status="complete"]').last().waitFor();
        assert.match(await page.locator('.chat-build .chat-markdown').last().innerText(), /Build bestanden/);
        await page.locator('#instruction').fill('Bitte jetzt eine Suche ergaenzen');
        await page.locator('#buildButton').click();
        await page.locator('.chat-po .chat-markdown strong').filter({ hasText: 'Bestehendes Projekt weiterbearbeiten' }).last().waitFor();
        await page.reload();
        await page.getByRole('button', { name: 'Bauen', exact: true }).waitFor();
        await page.setViewportSize({ width: 1280, height: 850 });
        await page.screenshot({ path: '/tmp/vibelove-chat-desktop.png' });
        const count = await page.locator('.chat-message').count();
        await page.locator('#instruction').fill('stop-test');
        await page.locator('#buildButton').click();
        await page.getByRole('button', { name: 'Stoppen', exact: true }).click();
        await page.locator('.chat-po[data-status="stopped"]').last().waitFor();
        await page.getByRole('button', { name: 'Erneut versuchen' }).waitFor();
        assert.equal(await page.locator('.chat-message').count(), count + 2);
        assert.deepEqual(errors, []);
        console.log('Chat: live output, reload, reconnect, Markdown/XSS, follow-up, mobile actions and stop OK');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
