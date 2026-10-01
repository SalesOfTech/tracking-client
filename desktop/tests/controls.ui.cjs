const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const {nativeFixture} = require('./actions.ui.cjs');
const locale = {exports: {}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/locale.ts'), 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText, locale);
const {text} = locale.exports;

async function assertControlsLayout(page) {
  const result = await page.evaluate(() => {
    const dialog = document.querySelector('.stop-dialog');
    const nodes = [...document.querySelectorAll('.agent-control button, .stop-dialog p, .stop-dialog h2, .stop-dialog button')];
    const rect = dialog?.getBoundingClientRect();
    let opacity = 1;
    for (let node = dialog; node; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity);
    return {
      overflow: document.documentElement.scrollWidth > innerWidth,
      clipped: nodes.filter(node => node.scrollWidth > node.clientWidth + 1).map(node => node.textContent),
      outside: rect && (rect.left < 0 || rect.right > innerWidth || rect.top < 0 || rect.bottom > innerHeight),
      opacity,
    };
  });
  assert.equal(result.overflow, false);
  assert.deepEqual(result.clipped, []);
  assert.ok(!result.outside, JSON.stringify(result));
  assert.ok(result.opacity >= 0.99, `Dialog is still faded: ${JSON.stringify(result)}`);
}

async function settleDialog(page) {
  await page.locator('.stop-dialog').waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('[data-slot="modal-backdrop"], [data-slot="modal-container"], .stop-dialog')]
    .every(node => !node.hasAttribute('data-entering') && !node.getAnimations().some(animation => animation.playState === 'running')));
}

async function verifyControls(browser, origin, output) {
  let combinations = 0;
  for (const language of ['en', 'ru', 'cs', 'uz']) {
    for (const theme of ['light', 'dark']) {
      const page = await browser.newPage({viewport: {width: 1024, height: 760}, colorScheme: theme});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      try {
        await nativeFixture(page, language, 'desktop', false);
        await page.goto(origin);
        await page.getByRole('button', {name: text(language, 'settings'), exact: true}).click();
        const stop = page.getByRole('button', {name: text(language, 'stopAgent'), exact: true});
        const remove = page.getByRole('button', {name: text(language, 'uninstallAgent'), exact: true});
        assert.equal(await stop.count(), 1);
        assert.equal(await remove.count(), 1);
        assert.equal(await page.locator('.agent-control .lucide-shield-check').count(), 0);
        for (const width of [1024, 390, 320]) {
          await page.setViewportSize({width, height: width === 1024 ? 760 : 640});
          await assertControlsLayout(page);
          await page.screenshot({path: path.join(output, `controls-settings-${language}-${theme}-${width}.png`)});
          await stop.click();
          await settleDialog(page);
          const dialog = page.getByRole('dialog', {name: text(language, 'stopTitle'), exact: true});
          await dialog.waitFor();
          assert.equal(await dialog.getByText(text(language, 'stopNotice'), {exact: true}).count(), 1);
          const cancel = dialog.getByRole('button', {name: text(language, 'cancel'), exact: true});
          await page.waitForFunction(label => document.activeElement?.textContent === label, text(language, 'cancel'));
          assert.equal(await page.locator('.agent-control button').first().evaluate(button => Boolean(button.closest('[inert], [aria-hidden="true"]'))), true, 'Background stop is inert while the confirmation is open');
          await assertControlsLayout(page);
          await page.screenshot({path: path.join(output, `controls-stop-${language}-${theme}-${width}.png`)});
          await cancel.click();
          await page.locator('.stop-dialog').waitFor({state: 'detached'});
          await dialog.waitFor({state: 'hidden'});
          assert.equal(await page.evaluate(() => window.__commands.filter(row => row.action === 'stop-agent').length), 0);
          combinations++;
        }
        await stop.click();
        await settleDialog(page);
        await page.getByRole('dialog').waitFor();
        await page.keyboard.press('Escape');
        await page.locator('.stop-dialog').waitFor({state: 'detached'});
        await page.getByRole('dialog').waitFor({state: 'hidden'});
        assert.equal(await page.evaluate(() => window.__commands.filter(row => row.action === 'stop-agent').length), 0);
        await stop.click();
        await settleDialog(page);
        await page.getByRole('dialog').getByRole('button', {name: text(language, 'stopConfirm'), exact: true}).click();
        await page.locator('.stop-dialog').waitFor({state: 'detached'});
        await page.getByRole('dialog').waitFor({state: 'hidden'});
        assert.equal(await stop.isDisabled(), true);
        assert.equal(await remove.isDisabled(), true);
        await page.waitForTimeout(1600);
        assert.equal(await stop.isDisabled(), true, 'Polling cannot clear local stop pending');
        assert.deepEqual(await page.evaluate(() => window.__commands.filter(row => row.action === 'stop-agent')), [{action: 'stop-agent', input: {}}]);
        await page.evaluate(() => window.__finishControl({message: 'stop_pending_activity'}));
        await page.getByRole('status').filter({hasText: text(language, 'stopPending')}).waitFor();
        assert.equal(await stop.isEnabled(), true);

        await remove.click();
        assert.equal(await page.getByRole('dialog').count(), 0, 'Native uninstall must not have a frontend confirmation');
        assert.equal(await remove.isDisabled(), true);
        await page.waitForTimeout(1600);
        assert.equal(await remove.isDisabled(), true, 'Polling cannot clear local uninstall pending');
        assert.deepEqual(await page.evaluate(() => window.__commands.filter(row => row.action === 'uninstall-agent')), [{action: 'uninstall-agent', input: {}}]);
        await page.evaluate(() => window.__finishControl({message: '', uninstalling: true, busy: true}));
        await page.getByRole('status').filter({hasText: text(language, 'uninstallStarted')}).waitFor();
        assert.equal(await remove.isDisabled(), true);
        assert.equal(await stop.isDisabled(), true);
        assert.equal(await page.getByRole('button', {name: text(language, 'checkUpdate'), exact: true}).isDisabled(), true);
        assert.equal(await page.locator('.language-select button').isDisabled(), true);
        await page.screenshot({path: path.join(output, `controls-uninstall-pending-${language}-${theme}.png`)});
        await page.evaluate(() => Object.assign(window.__state, {uninstalling: false, busy: false, message: 'uninstall_cancelled'}));
        await page.waitForTimeout(1600);
        await page.getByRole('status').filter({hasText: text(language, 'uninstallCancelled')}).waitFor();
        assert.equal(await remove.isEnabled(), true);
        await remove.click();
        await page.evaluate(() => window.__rejectControl());
        await page.getByRole('alert').filter({hasText: text(language, 'uninstallFailed')}).waitFor();
        assert.equal(await page.getByText(/PRIVATE_CONTROL_ERROR|C:\\Users\\private/).count(), 0);
        assert.equal(await remove.isEnabled(), true);
        await remove.click();
        await page.evaluate(() => window.__finishControl({message: 'uninstall_failed'}));
        await page.getByRole('status').filter({hasText: text(language, 'uninstallFailed')}).waitFor();
        await remove.click();
        await page.evaluate(() => window.__finishControl({message: 'uninstall_requested'}));
        await page.getByRole('status').filter({hasText: text(language, 'uninstallRequested')}).waitFor();
        for (const canUninstall of [false, undefined, 'true']) {
          await page.evaluate(value => {window.__state.canUninstall = value;}, canUninstall);
          await page.waitForTimeout(1600);
          assert.equal(await remove.count(), 0);
        }
        for (const guard of [{busy: true}, {busy: false, phase: 'installing'}, {phase: 'waiting', canManage: false}]) {
          await page.evaluate(patch => Object.assign(window.__state, patch, {canUninstall: true}), guard);
          await page.waitForTimeout(1600);
          assert.equal(await stop.isDisabled(), true);
          assert.equal(await remove.isDisabled(), true);
        }
        assert.deepEqual(errors, []);
      } finally {await page.close();}
    }
  }
  const preview = await browser.newPage();
  try {
    await preview.goto(`${origin}/?preview=1&lang=en`);
    await preview.getByRole('button', {name: 'Settings', exact: true}).click();
    assert.equal(await preview.getByRole('button', {name: text('en', 'stopAgent'), exact: true}).isDisabled(), true);
    assert.equal(await preview.locator('.remove-app').count(), 0);
    await preview.goto(`${origin}/?preview=1&mode=installer&lang=en&noCode=1`);
    await preview.locator('.installer-main').waitFor();
    assert.equal(await preview.locator('.agent-control').count(), 0);
  } finally {await preview.close();}
  console.log(`Lifecycle UI: ${combinations} language/theme/viewport combinations; one stop confirmation, cancellation, native uninstall routing, pending/error/retry and guards passed`);
}

module.exports = {verifyControls};
