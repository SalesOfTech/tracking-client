const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const locale = {exports: {}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/locale.ts'), 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText, locale);
const {text} = locale.exports;

async function layout(page) {
  const result = await page.evaluate(() => {
    const scroll = document.querySelector('.content-scroll');
    const content = document.querySelector('.connection-page');
    const style = getComputedStyle(scroll);
    const scrollbar = getComputedStyle(scroll, '::-webkit-scrollbar');
    const parts = ['.status-icon', '.status-copy', '.status-action'].map(selector => content.querySelector(selector).getBoundingClientRect());
    const overlap = parts.some((a, i) => parts.slice(i + 1).some(b => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1));
    const clipped = [...content.querySelectorAll('button, h2, h3, p, strong')].filter(node => node.clientWidth && node.scrollWidth > node.clientWidth + 1).map(node => node.textContent);
    return {
      overflow: document.documentElement.scrollWidth > innerWidth || content.scrollWidth > scroll.clientWidth,
      overlap, clipped, overflowY: style.overflowY, scrollbarWidth: scrollbar.width, scrollbarDisplay: scrollbar.display, scrollbarGutter: style.scrollbarGutter, standardWidth: style.scrollbarWidth, standardColor: style.scrollbarColor, scrollbarSpace: scroll.offsetWidth - scroll.clientWidth,
      brandLoaded: [...document.querySelectorAll('.brand img')].every(img => img.complete && img.naturalWidth > 0),
    };
  });
  assert.equal(result.overflow, false, JSON.stringify(result));
  assert.equal(result.overlap, false, JSON.stringify(result));
  assert.deepEqual(result.clipped, []);
  assert.equal(result.overflowY, 'scroll');
  assert.equal(result.scrollbarWidth, '12px');
  assert.notEqual(result.scrollbarDisplay, 'none');
  assert.equal(result.scrollbarGutter, 'stable');
  assert.equal(result.standardWidth, 'auto');
  assert.equal(result.standardColor, 'auto');
  assert.equal(result.scrollbarSpace, 12);
  assert.equal(result.brandLoaded, true);
}

async function verifyConnection(browser, origin, output) {
  const states = {healthy: ['success', 'connectionHealthy'], busy: ['accent', 'connectionChecking'], 'browser-missing': ['danger', 'browserDisconnected'], failed: ['danger', 'connectionProblem']};
  let checked = 0;
  for (const language of ['en', 'ru', 'cs', 'uz']) {
    for (const theme of ['light', 'dark']) {
      const page = await browser.newPage({colorScheme: theme});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      try {
        for (const viewport of [{width: 1024, height: 760}, {width: 700, height: 560}, {width: 390, height: 640}, {width: 320, height: 568}]) {
          await page.setViewportSize(viewport);
          for (const [state, [tone, title]] of Object.entries(states)) {
            await page.goto(`${origin}/?preview=1&lang=${language}&state=${state}&theme=${theme === 'light' ? 'dark' : 'light'}`);
            await page.locator(`.connection-status.state-${tone}`).waitFor();
            assert.equal(await page.locator('#connection-status-title').innerText(), text(language, title));
            assert.equal(await page.locator('.status-copy').getAttribute('aria-busy'), String(state === 'busy'));
            assert.equal(await page.locator('.connection-page [role="status"]').count(), 1);
            assert.equal(await page.locator('.connection-page .notice').count(), 0);
            assert.equal(await page.getByRole('button', {name: text(language, 'check'), exact: true}).count(), 0);
            assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
            assert.equal(await page.locator('html').getAttribute('lang'), language);
            const colors = await page.locator('.status-icon').evaluate((icon, tone) => {
              const probe = document.createElement('span');
              probe.style.color = `var(--${{success: 'success-text', danger: 'danger-text', accent: 'accent'}[tone]})`;
              document.body.append(probe);
              const result = [getComputedStyle(icon).color, getComputedStyle(probe).color];
              probe.remove();
              return result;
            }, tone);
            assert.equal(colors[0], colors[1]);
            if (state === 'busy') assert.equal(await page.locator('.status-action button').isDisabled(), true);
            if (state === 'failed') {
              assert.equal(await page.getByText(/ST-DEMO-001/).count(), 1);
              assert.equal(await page.locator('.status-copy').innerText().then(value => value.includes(text(language, 'connectionHealthy'))), false);
            }
            await layout(page);
            if (language === 'ru' && viewport.width !== 320) await page.screenshot({path: path.join(output, `connection-${state}-${theme}-${viewport.width}.png`)});
            checked++;
          }
        }
        assert.deepEqual(errors, []);
      } finally {await page.close();}
    }
  }

  console.log(`Connection layout: ${checked} state/language/theme/viewport combinations passed`);
  const page = await browser.newPage({viewport: {width: 700, height: 560}});
  try {
    await page.goto(`${origin}/?preview=1&lang=en&state=healthy`);
    const refresh = page.getByRole('button', {name: text('en', 'refreshStatus'), exact: true});
    await page.mouse.move(10, 100);
    await refresh.hover();
    await page.getByRole('tooltip').filter({hasText: text('en', 'refreshStatus')}).waitFor();
    await page.keyboard.press('Escape');
    await page.mouse.move(10, 100);
    await page.keyboard.press('Tab');
    await refresh.focus();
    await page.getByRole('tooltip').filter({hasText: text('en', 'refreshStatus')}).waitFor();
    await refresh.click();
    await page.locator('.connection-status.state-accent').waitFor();
    await page.locator('.connection-status.state-success').waitFor();
    assert.equal(await page.locator('.connection-page .notice').count(), 0);
    await page.goto(`${origin}/?preview=1&lang=en&state=browser-missing`);
    await page.locator('.status-action button').click();
    await page.getByRole('heading', {name: text('en', 'browsers'), exact: true}).waitFor();
    await page.getByRole('button', {name: text('en', 'connection'), exact: true}).click();
    await page.locator('.connection-status.state-danger').waitFor();
    await page.goto(`${origin}/?preview=1&lang=en&state=paused`);
    await page.getByRole('button', {name: text('en', 'resume'), exact: true}).click();
    await page.locator('.connection-status.state-success').waitFor();
    for (const state of ['awaiting-session', 'invalid-receipt', 'stale', 'delivery-error', 'rejected', 'browser-error']) {
      await page.goto(`${origin}/?preview=1&lang=en&state=${state}`);
      await page.locator('.connection-status.state-danger').waitFor();
      if (['awaiting-session', 'invalid-receipt'].includes(state)) {
        assert.equal(await page.locator('.delivery-line').count(), 0);
        const receipt = await page.locator('.receipt-section').boundingBox();
        assert.ok(receipt.height < 110, 'Missing receipt must stay compact');
      }
      if (state === 'stale') assert.equal(await page.locator('.receipt-section').getAttribute('data-receipt'), 'stale');
      if (state === 'delivery-error') assert.equal(await page.locator('.status-copy p').innerText(), text('en', 'deliveryFailed'));
      await layout(page);
    }
    await page.goto(`${origin}/?preview=1&lang=en&state=failed`);
    await page.getByRole('button', {name: text('en', 'refreshStatus')}).click();
    await page.locator('.connection-status.state-accent').waitFor();
    await page.locator('.connection-status.state-danger').waitFor();
    assert.equal(await page.getByText(/ST-DEMO-001/).count(), 1);
    await page.emulateMedia({colorScheme: 'dark'});
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    assert.equal(await page.locator('.connection-status.state-danger').count(), 1);

    await page.goto(`${origin}/?preview=1&lang=ru&state=healthy`);
    await page.getByRole('button', {name: text('ru', 'switchEmployee'), exact: true}).click();
    await page.locator('.employee-switch input').waitFor();
    const scrolled = await page.locator('.content-scroll').evaluate(scroll => {
      scroll.scrollTop = scroll.scrollHeight;
      return scroll.scrollHeight > scroll.clientHeight && scroll.scrollTop > 0;
    });
    assert.equal(scrolled, true);
  } finally {await page.close();}
  console.log(`Connection UI: ${checked} state/language/theme/viewport combinations, actions and scrolling passed`);
}

async function mockBridge(page, mode, code = 'a'.repeat(32)) {
  await page.clock.install();
  await page.addInitScript(({mode, code}) => {
    const now = Date.now() / 1000;
    window.__calls = [];
    window.__state = {mode, language: 'en', theme: 'system', version: 'test', code, busy: false, phase: 'waiting', error: '', message: '', enrolled: true, collection: 'recording', pending: 0, rejected: 0,
      receipt: {hostname: 'demo.kommo.com', timestamp: now - 90, end_timestamp: now - 15, confirmed_at: now - 5}, browsers: [{family: 'Chrome', connected: true, last_seen: now}], domains: ['demo.kommo.com']};
    window.tracking = {nativeFrame: false, window: async () => {}, invoke: async action => {
      window.__calls.push(action);
      if (action === 'status' && window.__failStatus) throw Error('offline');
      if (action === 'ready') {
        if (mode === 'installer') Object.assign(window.__state, {phase: 'detecting', busy: true});
        return {};
      }
      if (action === 'install') {
        if (window.__state.busy || ['detecting', 'migrating'].includes(window.__state.phase)) throw Error('premature_install');
        Object.assign(window.__state, {phase: 'installing', busy: true, error: '', errorText: ''});
      }
      return structuredClone(window.__state);
    }};
  }, {mode, code});
}

async function pollState(page, patch) {
  await page.evaluate(patch => Object.assign(window.__state, patch), patch);
  await page.clock.runFor(1600);
}

async function verifyInstallerMigration(browser, origin, output) {
  const page = await browser.newPage({viewport: {width: 560, height: 610}});
  try {
    for (const language of ['en', 'ru', 'cs', 'uz']) {
      for (const phase of ['detecting', 'migrating']) {
        await page.goto(`${origin}/?preview=1&mode=installer&phase=${phase}&noCode=1&lang=${language}`);
        const label = text(language, phase === 'detecting' ? 'setupDetecting' : 'setupMigrating');
        await page.getByRole('heading', {name: label, exact: true}).waitFor();
        assert.equal(await page.getByRole('progressbar', {name: label}).count(), 1);
        assert.equal(await page.locator('.install-progress-label .spinner').count(), 1);
        assert.equal(await page.locator('.wide-button, .code-field').count(), 0);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await page.screenshot({path: path.join(output, `installer-${phase}-${language}.png`)});
      }
    }
  } finally {await page.close();}

  for (const outcome of ['complete', 'waiting', 'failed']) {
    const page = await browser.newPage({viewport: {width: 560, height: 610}});
    try {
      await mockBridge(page, 'installer', outcome === 'waiting' ? 'a'.repeat(32) : '');
      await page.goto(origin);
      await page.getByRole('heading', {name: text('en', 'setupDetecting')}).waitFor();
      assert.equal(await page.evaluate(() => window.__calls.includes('install')), false, 'ready detection must win over pre-ready waiting/code');
      await pollState(page, {busy: false});
      assert.equal(await page.getByRole('progressbar').count(), 1, 'detecting implies busy even without the flag');
      assert.equal(await page.evaluate(() => window.__calls.includes('install')), false);
      if (outcome !== 'waiting') {
        await pollState(page, {phase: 'migrating', busy: false});
        await page.getByRole('heading', {name: text('en', 'setupMigrating')}).waitFor();
        assert.equal(await page.getByRole('progressbar').count(), 1);
        assert.equal(await page.locator('.wide-button').count(), 0);
      }
      await pollState(page, {phase: outcome, busy: false, error: outcome === 'failed' ? 'setup_migration_failed' : '', errorText: outcome === 'failed' ? 'INTERNAL_PRIVATE_DETAILS' : ''});
      if (outcome === 'complete') {
        await page.getByRole('button', {name: text('en', 'openApp')}).waitFor();
        assert.equal(await page.locator('.code-field, #install-key-help').count(), 0);
        assert.equal(await page.locator('.installed-symbol').count(), 1);
        assert.equal(await page.evaluate(() => window.__calls.includes('install')), false);
        await pollState(page, {error: 'setup_migration_failed'});
        await page.getByRole('heading', {name: text('en', 'installFailed')}).waitFor();
        assert.equal(await page.locator('.installed-symbol').count(), 0, 'An error must override cached completion');
      } else if (outcome === 'waiting') {
        await page.getByRole('heading', {name: text('en', 'installing')}).waitFor();
        await pollState(page, {phase: 'installing', busy: false});
        assert.equal(await page.evaluate(() => window.__calls.filter(action => action === 'install').length), 1);
      } else {
        await page.getByRole('alert').filter({hasText: text('en', 'setupMigrationFailed')}).waitFor();
        assert.equal(await page.getByText('INTERNAL_PRIVATE_DETAILS').count(), 0);
        assert.equal(await page.getByRole('progressbar').count(), 0);
        assert.equal(await page.evaluate(() => window.__calls.includes('install')), false);
        await page.locator('.code-field input').fill('b'.repeat(64));
        await page.getByRole('button', {name: text('en', 'install'), exact: true}).click();
        await page.getByRole('heading', {name: text('en', 'installing')}).waitFor();
        assert.equal(await page.evaluate(() => window.__calls.filter(action => action === 'install').length), 1);
      }
    } finally {await page.close();}
  }

  const cached = await browser.newPage();
  try {
    await mockBridge(cached, 'desktop');
    await cached.goto(origin);
    await cached.locator('.connection-status.state-success').waitFor();
    await cached.evaluate(() => {window.__failStatus = true;});
    await cached.clock.runFor(1600);
    await cached.locator('.connection-status.state-danger').waitFor();
    assert.equal(await cached.locator('.status-copy p').innerText(), text('en', 'genericError'));
    await cached.evaluate(() => {window.__failStatus = false;});
    await cached.clock.runFor(1600);
    await cached.locator('.connection-status.state-success').waitFor();
  } finally {await cached.close();}
  console.log('Installer UI: 4 languages, detecting/migrating guards, migration outcomes, manual fallback and cached-status failure passed');
}

module.exports = {verifyConnection, verifyInstallerMigration};
