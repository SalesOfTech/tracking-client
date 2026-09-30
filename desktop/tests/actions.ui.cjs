const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const {BROWSER_PAGES} = require('../protocol.cjs');
const locale = {exports: {}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/locale.ts'), 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText, locale);
const {text} = locale.exports;

async function assertLayout(page) {
  const result = await page.evaluate(() => {
    const scroll = document.querySelector('.content-scroll');
    const footer = document.querySelector('.status-footer');
    const copy = document.querySelector('.update-copy').getBoundingClientRect();
    const button = document.querySelector('.update-action').getBoundingClientRect();
    const version = document.querySelector('.app-version').getBoundingClientRect();
    return {
      overflow: document.documentElement.scrollWidth > innerWidth || scroll.scrollWidth > scroll.clientWidth,
      clipped: [...footer.querySelectorAll('p, button'), ...document.querySelectorAll('.browser-row p, .copy-feedback, .browser-pages button')].filter(el => el.scrollWidth > el.clientWidth + 1).map(el => el.textContent),
      overlap: copy.right > button.left + 1 || button.right > version.left + 1,
      scrollbar: scroll.offsetWidth - scroll.clientWidth,
      overflowing: scroll.scrollHeight > scroll.clientHeight,
      shadow: scroll.classList.contains('scroll-shadow'),
      gutter: getComputedStyle(scroll).scrollbarGutter,
      viewport: innerWidth,
      language: document.documentElement.lang,
      wideElements: [...scroll.querySelectorAll('*')].filter(el => el.getBoundingClientRect().right > scroll.getBoundingClientRect().right + 1).map(el => `${el.tagName}.${el.className}`),
    };
  });
  assert.equal(result.overflow, false, JSON.stringify(result));
  assert.deepEqual(result.clipped, []);
  assert.equal(result.overlap, false);
  assert.equal(result.shadow, true);
  assert.equal(result.gutter, 'auto');
  assert.equal(result.scrollbar, result.overflowing ? 12 : 0);
}

async function nativeFixture(page, language = 'en', mode = 'desktop') {
  await page.clock.install();
  await page.addInitScript(({language, mode, pages}) => {
    const now = Date.now() / 1000;
    window.__commands = [];
    window.__clipboardReads = 0;
    Object.defineProperty(navigator, 'clipboard', {get() {window.__clipboardReads++; throw Error('browser_clipboard_forbidden');}});
    window.__state = {mode, language, theme: 'system', version: 'test', code: '', phase: 'waiting', busy: false, error: '', message: '', enrolled: true, company: 'Demo company', employee: 'Demo employee', collection: 'recording', pending: 0, rejected: 0,
      update: 'active', updateAvailable: true, updateChecking: false, updateCheckedAt: now - 60,
      receipt: {hostname: 'demo.kommo.com', timestamp: now - 90, end_timestamp: now - 10, confirmed_at: now - 5},
      browsers: Object.keys(pages).map(family => ({family, connected: true, version: 'test', last_seen: now}))};
    window.tracking = {nativeFrame: false, window: async () => {}, invoke: async (action, input = {}) => {
      window.__commands.push({action, input});
      if (action === 'ready') return {};
      if (action === 'check-update') {
        const snapshot = structuredClone(window.__state);
        return new Promise((resolve, reject) => {
          window.__finishUpdate = patch => {Object.assign(window.__state, patch); resolve({...snapshot, ...patch});};
          window.__rejectUpdate = () => reject(Error('PRIVATE_UPDATE_ERROR'));
        });
      }
      if (action === 'copy-browser-page') {
        if (window.__copyFail) throw Error('PRIVATE_CLIPBOARD_ERROR');
        window.__clipboard = pages[input.browser];
        return {copied: true};
      }
      return structuredClone(window.__state);
    }};
  }, {language, mode, pages: BROWSER_PAGES});
}

async function verifyUpdates(browser, origin, output) {
  const labels = {checking: 'checking', active: 'installed', installed: 'installed', downloading: 'downloading', rolled_back: 'rollback', error: 'updateError', registration: 'updateNotChecked'};
  let combinations = 0;
  for (const language of ['en', 'ru', 'cs', 'uz']) {
    for (const theme of ['light', 'dark']) {
      const page = await browser.newPage({colorScheme: theme});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      try {
        for (const width of [1600, 390]) {
          await page.setViewportSize({width, height: width === 1600 ? 900 : 640});
          for (const [state, label] of Object.entries(labels)) {
            await page.goto(`${origin}/?preview=1&lang=${language}&update=${state}`);
            await page.locator('.connection-status.state-success').waitFor();
            const button = page.getByRole('button', {name: text(language, 'checkUpdate'), exact: true});
            assert.equal(await button.isDisabled(), ['checking', 'downloading'].includes(state));
            assert.equal(await page.locator('.update-copy').getAttribute('aria-busy'), String(['checking', 'downloading'].includes(state)));
            assert.ok((await page.locator('.update-summary').innerText()).includes(text(language, label)));
            assert.equal(await page.locator('.update-checked time').count(), 1);
            await assertLayout(page);
            if (language === 'ru' && ['active', 'checking', 'error'].includes(state)) await page.screenshot({path: path.join(output, `updates-${state}-${theme}-${width}.png`)});
            combinations++;
          }
        }
        for (const query of ['enroll=1', 'updateAvailable=0']) {
          await page.goto(`${origin}/?preview=1&lang=${language}&${query}`);
          await page.locator('.status-footer').waitFor();
          assert.equal(await page.getByRole('button', {name: text(language, 'checkUpdate'), exact: true}).isDisabled(), true);
        }
        await page.goto(`${origin}/?preview=1&lang=${language}&updateChecking=1`);
        await page.locator('.connection-status.state-success').waitFor();
        assert.equal(await page.locator('.update-copy').getAttribute('aria-busy'), 'true');
        for (const query of ['update=registration', 'update=checking&updateChecking=0']) {
          await page.goto(`${origin}/?preview=1&lang=${language}&${query}`);
          const button = page.getByRole('button', {name: text(language, 'checkUpdate'), exact: true});
          await button.click();
          await page.locator('.update-summary').filter({hasText: text(language, 'installed')}).waitFor();
          assert.equal(await button.isEnabled(), true);
        }
        await page.goto(`${origin}/?preview=1&lang=${language}&state=busy&updateCheckedAt=none`);
        await page.locator('.connection-status.state-accent').waitFor();
        assert.equal(await page.getByRole('button', {name: text(language, 'checkUpdate'), exact: true}).isEnabled(), true);
        assert.equal(await page.locator('.update-checked').count(), 0);
        assert.deepEqual(errors, []);
      } finally {await page.close();}
    }
  }

  const page = await browser.newPage({viewport: {width: 1024, height: 760}});
  try {
    await nativeFixture(page);
    await page.goto(origin);
    await page.locator('.connection-status.state-success').waitFor();
    const button = page.getByRole('button', {name: text('en', 'checkUpdate'), exact: true});
    await page.mouse.move(10, 100);
    await button.hover();
    await page.getByRole('tooltip').filter({hasText: text('en', 'checkUpdate')}).waitFor();
    await button.click();
    assert.equal(await page.locator('.update-copy').getAttribute('aria-busy'), 'true');
    assert.equal(await button.isDisabled(), true);
    assert.equal(await page.locator('.connection-status.state-success').count(), 1);
    await page.clock.runFor(1600);
    assert.equal(await button.isDisabled(), true, 'A status poll cannot cancel a locally pending request');
    assert.deepEqual(await page.evaluate(() => window.__commands.filter(row => row.action === 'check-update')), [{action: 'check-update', input: {}}]);
    await page.evaluate(() => {window.__state.error = 'connection_failure';});
    await page.clock.runFor(1600);
    await page.locator('.connection-status.state-danger').waitFor();
    await page.evaluate(() => window.__finishUpdate({updateChecking: true}));
    assert.equal(await page.locator('.connection-status.state-danger').count(), 1, 'Stale connection data in updater response must be ignored');
    assert.equal(await button.isDisabled(), true, 'Backend updater busy must survive the request acknowledgement');
    await page.evaluate(() => Object.assign(window.__state, {error: '', updateChecking: false, update: 'active', updateCheckedAt: Date.now() / 1000}));
    await page.clock.runFor(1600);
    await page.locator('.connection-status.state-success').waitFor();
    await page.waitForFunction(() => !document.querySelector('.update-action button').disabled);
    await button.click();
    await page.evaluate(() => window.__rejectUpdate());
    await page.locator('.update-summary').filter({hasText: text('en', 'updateCheckFailed')}).waitFor();
    assert.equal(await page.locator('.connection-status.state-success').count(), 1);
    assert.equal(await page.getByText('PRIVATE_UPDATE_ERROR').count(), 0);
    assert.equal(await button.isEnabled(), true);
    await button.click();
    await page.evaluate(() => window.__finishUpdate({update: 'installed', updateChecking: false, updateCheckedAt: Date.now() / 1000}));
    await page.locator('.update-summary').filter({hasText: text('en', 'installed')}).waitFor();
    assert.equal(await button.isEnabled(), true);
    assert.equal(await page.evaluate(() => window.__commands.filter(row => row.action === 'check').length), 0);
  } finally {await page.close();}
  console.log(`Updater UI: ${combinations} language/theme/viewport/state combinations, pending/busy/error/retry and connection isolation passed`);
}

async function verifyBrowserCopy(browser, origin, output) {
  for (const language of ['en', 'ru', 'cs', 'uz']) {
    const page = await browser.newPage({viewport: {width: 1024, height: 760}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    try {
      await nativeFixture(page, language);
      await page.goto(origin);
      await page.locator('.sidebar').getByRole('button', {name: text(language, 'help'), exact: true}).click();
      await page.locator('.browser-pages').waitFor();
      assert.equal(await page.getByText(text(language, 'copyBrowserInstructions'), {exact: true}).count(), 1);
      for (const [family, address] of Object.entries(BROWSER_PAGES)) {
        const button = page.getByRole('button', {name: `${text(language, 'copyAddress')}: ${family}`, exact: true});
        await button.click();
        await button.locator('xpath=../..').getByRole('status').filter({hasText: text(language, 'addressCopied')}).waitFor();
        assert.equal(await page.evaluate(() => window.__clipboard), address);
        assert.equal(page.url(), `${origin}/`);
      }
      const copies = await page.evaluate(() => window.__commands.filter(row => row.action === 'copy-browser-page'));
      assert.deepEqual(copies, Object.keys(BROWSER_PAGES).map(browser => ({action: 'copy-browser-page', input: {browser}})));
      await page.clock.runFor(3100);
      assert.equal(await page.locator('.copy-feedback').filter({hasText: text(language, 'addressCopied')}).count(), 0);
      await page.getByRole('button', {name: text(language, 'browsers'), exact: true}).click();
      await page.locator('.browser-row').first().waitFor();
      assert.equal(await page.getByText(text(language, 'copyBrowserInstructions'), {exact: true}).count(), 1);
      const chrome = page.getByRole('button', {name: `${text(language, 'copyAddress')}: Chrome`, exact: true});
      await chrome.click();
      await page.locator('.browser-row').first().getByRole('status').filter({hasText: text(language, 'addressCopied')}).waitFor();
      await page.evaluate(() => {window.__copyFail = true;});
      await chrome.click();
      await page.locator('.browser-row').first().getByRole('status').filter({hasText: text(language, 'copyFailed')}).waitFor();
      assert.equal(await page.getByText('PRIVATE_CLIPBOARD_ERROR').count(), 0);
      await page.evaluate(() => {window.__copyFail = false;});
      await chrome.click();
      await page.locator('.browser-row').first().getByRole('status').filter({hasText: text(language, 'addressCopied')}).waitFor();
      await page.getByRole('button', {name: text(language, 'connection'), exact: true}).click();
      await page.locator('.connection-status.state-success').waitFor();
      assert.equal(await page.evaluate(() => window.__clipboardReads), 0);
      assert.equal(await page.evaluate(() => window.__commands.filter(row => row.action === 'browser-page').length), 0);
      assert.equal(page.context().pages().length, 1);
      for (const width of [1600, 390, 320]) {
        await page.setViewportSize({width, height: width === 1600 ? 900 : 640});
        await page.getByRole('button', {name: text(language, 'browsers'), exact: true}).click();
        await page.getByRole('button', {name: `${text(language, 'copyAddress')}: Chrome`, exact: true}).click();
        await assertLayout(page);
        assert.ok(await page.locator('.browser-row p').first().evaluate(el => el.clientHeight <= 80), 'Browser metadata must stay readable in compact layout');
        assert.ok(await page.locator('.browser-row > span').first().evaluate(el => el.clientHeight <= 22 && el.scrollWidth <= el.clientWidth + 1), 'Short browser status must remain on one line');
        await page.screenshot({path: path.join(output, `browser-copy-list-${language}-${width}.png`)});
        await page.locator('.sidebar').getByRole('button', {name: text(language, 'help'), exact: true}).click();
        await page.locator('.copy-instructions').scrollIntoViewIfNeeded();
        await assertLayout(page);
        await page.screenshot({path: path.join(output, `browser-copy-guide-${language}-${width}.png`)});
      }
      assert.deepEqual(errors, []);
    } finally {await page.close();}
  }
  const installer = await browser.newPage({viewport: {width: 560, height: 610}});
  try {
    await nativeFixture(installer, 'ru', 'installer');
    await installer.goto(origin);
    await installer.getByRole('button', {name: text('ru', 'help'), exact: true}).click();
    await installer.getByRole('button', {name: `${text('ru', 'copyAddress')}: Firefox`, exact: true}).click();
    assert.equal(await installer.evaluate(() => window.__clipboard), 'about:addons');
    assert.equal(await installer.evaluate(() => window.__commands.filter(row => row.action === 'browser-page').length), 0);
  } finally {await installer.close();}
  console.log('Browser copy UI: eight families, four languages, Guide/list/installer, confirmation timeout, failure/retry and no navigation passed');
}

module.exports = {verifyUpdates, verifyBrowserCopy};
