const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {pathToFileURL} = require('node:url');
const {guides} = require('../setup.js');

(async () => {
  const browser = await chromium.launch({headless: process.env.PLAYWRIGHT_HEADED !== '1', ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {})});
  try {
    const errors = [];
    const context = await browser.newContext({locale:'en-US'});
    await context.route(/^https?:/, route => route.abort());
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    const url = pathToFileURL(path.resolve(__dirname, '../setup.html')).href;
    fs.mkdirSync('artifacts/verification', {recursive:true});
    for (const language of Object.keys(guides)) {
      for (const width of [1000, 360]) {
        await page.setViewportSize({width, height:850});
        await page.goto(url + '#lang=' + language);
        assert.equal(await page.locator('html').getAttribute('lang'), language);
        assert.equal(await page.locator('h1').textContent(), guides[language].title);
        assert.equal(await page.locator('section').count(), 5);
        assert.equal(await page.locator('select').inputValue(), language);
        assert.ok(await page.locator('header img').evaluate(img => img.complete && img.naturalWidth > 0));
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
        await page.locator('nav a').last().click();
        const lastSection=guides[language].sections.at(-1)[0];
        await page.waitForFunction(id=>document.getElementById(id).getBoundingClientRect().top<innerHeight,lastSection);
        assert.ok(await page.locator('#'+lastSection).evaluate(el=>el.getBoundingClientRect().top<innerHeight));
        await page.screenshot({path:`artifacts/verification/guide-${language}-${width}.png`, fullPage:true});
      }
    }
    await page.goto(url + '#lang=ru');
    await page.locator('select').selectOption('cs');
    await page.waitForFunction(() => document.documentElement.lang === 'cs');
    await page.reload();
    assert.equal(await page.locator('html').getAttribute('lang'), 'cs');
    await page.goto(url + '#lang=uz');
    assert.equal(await page.locator('html').getAttribute('lang'), 'uz');
    await context.addInitScript(() => { Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }}); });
    await page.goto(url + '#lang=ru');
    assert.equal(await page.locator('html').getAttribute('lang'), 'ru');
    assert.deepEqual(errors, []);
    const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../manifest.json')));
    assert.equal(manifest.action.default_popup, undefined);
    assert.equal(fs.existsSync(path.resolve(__dirname, '../popup.html')), false);
    console.log('PASS: four offline guides, desktop/mobile, navigation, explicit language and blocked storage');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode = 1;});
