// Run only against an isolated Vibelove instance; creates a test project.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const base = process.env.VIBELOVE_TEST_URL;
  if (!base) throw new Error('Set VIBELOVE_TEST_URL to an isolated test instance.');
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1360, height: 850 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    await page.locator('#projektAktionenBtn').click();
    await page.locator('#neuesProjektBtn').click();
    await page.locator('#newProjectTemplate option[value="vite-business"]').waitFor({ state: 'attached' });
    await page.locator('#newProjectName').fill('template-test-' + Date.now());
    await page.locator('#newProjectTemplate').selectOption('vite-business');
    await page.screenshot({ path: '/tmp/vibelove-template-dialog-desktop.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: '/tmp/vibelove-template-dialog-mobile.png' });
    const creation = page.waitForResponse(r => r.url() === base + '/projects' && r.request().method() === 'POST', { timeout: 300000 });
    await page.locator('#newProjectSubmit').click();
    assert.equal(await page.locator('#newProjectSubmit').isDisabled(), true);
    const response = await creation;
    assert.equal(response.status(), 200, await response.text());
    const project = await response.json();
    await page.locator('#newProjectDialog').waitFor({ state: 'hidden' });

    const app = await browser.newPage({ viewport: { width: 1280, height: 850 } });
    app.on('pageerror', error => errors.push(error.message));
    const url = new URL(base); url.port = String(project.frontend_port);
    for (let retry = 0; ; retry++) {
      try { await app.goto(url.toString()); await app.getByRole('heading', { name: 'Kunden', exact: true }).waitFor({ timeout: 3000 }); break; }
      catch (error) { if (retry === 15) throw error; await new Promise(r => setTimeout(r, 1000)); }
    }
    assert.equal(await app.locator('.brand img').evaluate(img => img.complete && img.naturalWidth > 0), true);
    await app.screenshot({ path: '/tmp/vibelove-template-app-desktop.png' });
    await app.getByRole('button', { name: 'Neuer Kunde' }).click();
    await app.getByLabel('Name', { exact: true }).fill('Test Person');
    await app.getByLabel('Unternehmen', { exact: true }).fill('Test Firma');
    await app.getByLabel('E-Mail', { exact: true }).fill('test@example.com');
    await app.getByRole('button', { name: 'Speichern', exact: true }).click();
    await app.getByLabel('Kunden suchen').fill('Test Person');
    assert.equal(await app.locator('tbody tr').count(), 1);
    await app.reload();
    await app.getByLabel('Kunden suchen').fill('Test Person');
    assert.equal(await app.locator('tbody tr').count(), 1);
    await app.getByRole('button', { name: 'Test Person bearbeiten' }).click();
    await app.getByLabel('Name', { exact: true }).fill('Changed Person');
    await app.getByRole('button', { name: 'Speichern', exact: true }).click();
    await app.getByLabel('Kunden suchen').fill('Changed');
    await app.getByRole('button', { name: 'Changed Person loeschen' }).click();
    await app.getByRole('button', { name: 'Abbrechen', exact: true }).click();
    assert.equal(await app.locator('tbody tr').count(), 1);
    await app.getByRole('button', { name: 'Changed Person loeschen' }).click();
    await app.getByRole('button', { name: 'Loeschen', exact: true }).click();
    await app.getByText('Keine Eintraege gefunden.').waitFor();
    await app.getByLabel('Kunden suchen').fill('');
    await app.setViewportSize({ width: 390, height: 844 });
    assert.equal(await app.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await app.screenshot({ path: '/tmp/vibelove-template-app-mobile.png' });
    await app.getByRole('button', { name: 'Neuer Kunde' }).click();
    await app.screenshot({ path: '/tmp/vibelove-template-sheet-mobile.png' });
    await app.keyboard.press('Escape');
    assert.equal(await app.locator('dialog').count(), 0);
    assert.deepEqual(errors, []);
    console.log('Template creation, CRUD persistence, cancellation, assets and responsive layouts OK');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
