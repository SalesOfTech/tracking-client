const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {launchFixture} = require('./durable.browser.cjs');

(async () => {
  const extension = path.resolve(process.argv[2] || path.join(__dirname, '..'));
  const manifest = JSON.parse(fs.readFileSync(path.join(extension, 'manifest.json'), 'utf8'));
  assert(manifest.default_locale, 'Localized extensions require default_locale');
  const messages = JSON.parse(fs.readFileSync(path.join(extension, '_locales', manifest.default_locale, 'messages.json'), 'utf8'));
  for (const match of JSON.stringify(manifest).matchAll(/__MSG_(\w+)__/g)) assert(messages[match[1]], `Missing message: ${match[1]}`);
  let fixture;
  try {
    fixture = await launchFixture(extension);
    const {context, worker} = fixture;
    const actual = await worker.evaluate(() => ({manifest:chrome.runtime.getManifest(), name:chrome.i18n.getMessage('extensionName')}));
    assert.equal(actual.manifest.default_locale, 'en');
    assert.equal(actual.manifest.name, actual.name);
    assert.equal(actual.manifest.version, manifest.version);
    const page = await context.newPage();
    assert.equal(manifest.action.default_popup, undefined, 'Settings belong in the desktop application');
    await page.goto(new URL('setup.html', worker.url()).href);
    assert((await page.locator('body').innerText()).includes('SOFT Tracking'));
    assert.equal(await page.locator('section').count(),5);
    assert(await page.locator('header img').evaluate(image=>image.complete&&image.naturalWidth>0));
    assert((await page.screenshot()).length>5000,'Extension guide must render nonblank pixels');
    const database=await worker.evaluate(async()=>{const db=await outbox.database;return{name:db.name,stores:[...db.objectStoreNames]};});
    assert.equal(database.name,'soft-tracking-v3');
    assert(database.stores.includes('events')&&database.stores.includes('sessions'));
    console.log('PASS: real unpacked extension, service worker, IndexedDB, localized nonblank guide; isolated native fixture');
  } finally {
    if(fixture)await fixture.close();
  }
})().catch(error => {console.error(error); process.exitCode=1;});
