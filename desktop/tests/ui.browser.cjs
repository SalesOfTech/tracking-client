/* Runs in GitHub Actions against the actual built HeroUI interface. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('../../node_modules/playwright');
const {verifyConnection, verifyInstallerMigration} = require('./connection.ui.cjs');
const {verifyUpdates, verifyBrowserCopy} = require('./actions.ui.cjs');
const {verifyControls} = require('./controls.ui.cjs');
const root = path.resolve(__dirname, '../dist');

(async () => {
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const file = path.resolve(root, '.' + (url.pathname === '/' ? '/index.html' : url.pathname));
    try {
      if (!file.startsWith(root + path.sep)) throw Error('path');
      res.setHeader('Content-Type', {'.html':'text/html', '.js':'text/javascript', '.css':'text/css', '.png':'image/png'}[path.extname(file)] || 'application/octet-stream');
      res.end(await fs.readFile(file));
    } catch {res.writeHead(404); res.end();}
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  const output = process.env.TRACKING_UI_OUTPUT || path.join(require('node:os').tmpdir(), 'soft-tracking-ui');
  try {
    browser = await chromium.launch({headless: true, ignoreDefaultArgs: ['--hide-scrollbars']});
    await fs.mkdir(output, {recursive: true});
    await verifyControls(browser, origin, output);
    if (process.env.TRACKING_UI_FOCUS === 'lifecycle') return;
    await verifyConnection(browser, origin, output);
    await verifyUpdates(browser, origin, output);
    await verifyBrowserCopy(browser, origin, output);
    await verifyInstallerMigration(browser, origin, output);
    for (const language of ['ru','en','cs','uz']) {
      for (const theme of ['light','dark']) {
        const page = await browser.newPage({viewport: {width: 1024, height: 760}, colorScheme: theme});
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`${origin}/?preview=1&lang=${language}&theme=${theme === 'light' ? 'dark' : 'light'}`);
        await page.locator('.receipt-title strong').waitFor();
        assert.equal(await page.locator('.select__value').innerText(), {ru:'Русский', en:'English', cs:'Čeština', uz:"O'zbekcha"}[language]);
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
        assert.equal(await page.locator('html').getAttribute('lang'), language);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth || document.querySelector('.page-content').scrollWidth > document.querySelector('.content-scroll').clientWidth);
        assert.equal(overflow, false);
        const alignment = await page.locator('.language-select').evaluate(select => {
          const box = selector => select.querySelector(selector).getBoundingClientRect();
          const icon = box('.select__trigger > svg'), value = box('.select__value');
          return Math.abs(icon.top + icon.height / 2 - value.top - value.height / 2);
        });
        assert.ok(alignment < 1, `Language icon/value misaligned by ${alignment}px`);
        await page.screenshot({path: path.join(output, `electron-${language}-${theme}.png`)});
        await page.locator('.sidebar nav button').nth(2).click();
        assert.equal(await page.locator('.theme-control').count(), 0);
        await page.emulateMedia({colorScheme: theme === 'light' ? 'dark' : 'light'});
        await page.waitForFunction(expected => document.documentElement.dataset.theme === expected, theme === 'light' ? 'dark' : 'light');
        await page.locator('.sidebar .help-button').click();
        await page.locator('.guide section').first().waitFor();
        assert.equal(await page.locator('.guide section').count(), 5);
        await page.locator('.browser-pages button').first().click();
        await page.locator('.browser-pages .copy-feedback').first().filter({hasText: /\S/}).waitFor();
        assert.equal(page.url().startsWith(origin), true);
        await page.screenshot({path: path.join(output, `electron-guide-${language}.png`)});
        await page.locator('.language-select button').click();
        await page.getByRole('option', {name: 'Čeština', exact: true}).click();
        assert.equal(await page.locator('html').getAttribute('lang'), 'cs');
        assert.deepEqual(errors, []);
        await page.close();
      }
    }
    const page = await browser.newPage({viewport: {width: 700, height: 560}});
    await page.goto(`${origin}/?preview=1&enroll=1&lang=ru`);
    const field = page.getByLabel('Код активации сотрудника', {exact: true});
    await field.fill('b'.repeat(64));
    await page.getByRole('button', {name: 'Активировать', exact: true}).click();
    await page.locator('.receipt-title strong').waitFor();
    await page.screenshot({path: path.join(output, 'electron-compact.png')});
    await page.getByRole('button', {name: 'Сменить сотрудника', exact: true}).click();
    await page.getByLabel('Код активации сотрудника', {exact: true}).fill('c'.repeat(64));
    await page.locator('.employee-switch').getByRole('button', {name: 'Сменить сотрудника', exact: true}).click();
    await page.getByText('Demo colleague', {exact: true}).waitFor();
    assert.equal(await page.locator('.employee-switch').count(), 0);
    await page.locator('.sidebar nav button').nth(2).click();
    assert.equal(await page.getByRole('button', {name: 'Остановить агент', exact: true}).isDisabled(), true);
    assert.equal(await page.locator('.remove-app').count(), 0);
    await page.setViewportSize({width: 560, height: 610});
    for (const language of ['en','ru','cs','uz']) {
      await page.goto(`${origin}/?preview=1&mode=installer&noCode=1&lang=${language}`);
      const key = page.locator('.code-field input');
      await key.fill('short');
      assert.equal(await page.locator('.wide-button').isEnabled(), false);
      await key.fill('c'.repeat(64));
      assert.equal(await page.locator('.wide-button').isEnabled(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({path:path.join(output,`single-key-installer-${language}.png`)});
      await page.locator('.wide-button').click();
    }
    await page.goto(`${origin}/?preview=1&mode=installer&lang=ru&theme=dark`);
    await page.getByRole('heading', {name: 'Устанавливаем SOFT Tracking'}).waitFor();
    await page.screenshot({path: path.join(output, 'electron-installer-progress.png')});
    await page.getByRole('button', {name: 'Открыть SOFT Tracking'}).waitFor();
    await page.screenshot({path: path.join(output, 'electron-installer-complete.png')});
    await page.getByRole('button', {name: 'Инструкция по установке', exact: true}).click();
    await page.locator('.installer-guide .guide section').first().waitFor();
    assert.equal(await page.locator('.installer-guide .guide section').count(), 5);
    await page.screenshot({path: path.join(output, 'electron-installer-guide.png')});
    await page.getByRole('button', {name: 'Назад', exact: true}).click();
    await page.getByRole('button', {name: 'Открыть SOFT Tracking'}).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    for (const platform of ['darwin', 'linux']) {
      await page.goto(`${origin}/?preview=1&lang=en&platform=${platform}`);
      await page.locator('.receipt-title strong').waitFor();
      assert.equal(await page.locator('.titlebar').count(), 0);
    }
    await page.close();
    console.log('HeroUI: 4 languages, 2 themes, activation and installer passed');
    console.log(`UI screenshots: ${output}`);
  } finally {
    try {await browser?.close();}
    finally {await new Promise(resolve => server.close(resolve));}
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
