const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const extension = path.resolve(process.argv[2] || path.join(__dirname, '..'));
  const manifest = JSON.parse(fs.readFileSync(path.join(extension, 'manifest.json'), 'utf8'));
  assert(manifest.default_locale, 'Localized extensions require default_locale');
  const messages = JSON.parse(fs.readFileSync(path.join(extension, '_locales', manifest.default_locale, 'messages.json'), 'utf8'));
  for (const match of JSON.stringify(manifest).matchAll(/__MSG_(\w+)__/g)) assert(messages[match[1]], `Missing message: ${match[1]}`);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'tracking-extension-test-'));
  let context;
  try {
    context = await chromium.launchPersistentContext(profile, {channel:'chromium', headless:true,
      args:[`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]});
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker', {timeout:15000});
    const actual = await worker.evaluate(() => ({manifest:chrome.runtime.getManifest(), name:chrome.i18n.getMessage('extensionName')}));
    assert.equal(actual.manifest.default_locale, 'en');
    assert.equal(actual.manifest.name, actual.name);
    assert.equal(actual.manifest.version, manifest.version);
    const page = await context.newPage();
    assert.equal(manifest.action.default_popup, undefined, 'Settings belong in the desktop application');
    await page.goto(new URL('setup.html', worker.url()).href);
    assert((await page.locator('body').innerText()).includes('SOFT Tracking'));
    console.log('PASS: unpacked extension, service worker, localization and guide; no settings popup');
  } finally {
    if (context) await context.close();
    fs.rmSync(profile, {recursive:true, force:true});
  }
})().catch(error => {console.error(error); process.exitCode=1;});
