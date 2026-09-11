const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const OLD_EPOCH = 'a'.repeat(32);
const NEW_EPOCH = 'b'.repeat(32);
const FIXTURE_URL = 'https://work.example.test/editor';
const HTML = '<!doctype html><html><head><title>Synthetic work page</title></head><body style="font:18px sans-serif;padding:32px;background:white;color:#222"><h1>Synthetic work page</h1><label for="project">Project name</label><input id="project" name="project"><button>Save changes</button><a href="/next">Next page</a></body></html>';

// Prepended only to a temporary extension copy. No real native host can be called.
const NATIVE_FIXTURE = `
globalThis.__fixture = {attempts: [], dropRecordAck: false, dropped: null};
const fixtureAddMessage = chrome.runtime.onMessage.addListener.bind(chrome.runtime.onMessage);
chrome.runtime.onMessage.addListener = listener => fixtureAddMessage((message, sender, respond) =>
  listener(message, sender, result => {
    if (__fixture.dropRecordAck && message.action === 'record' && result && result.ok === true) {
      __fixture.dropRecordAck = false;
      __fixture.dropped = structuredClone(message);
      return;
    }
    respond(result);
  }));
chrome.runtime.sendNativeMessage = (host, message, reply) => {
  if (host !== 'com.soft.tracking') throw new Error('Unexpected fixture native host');
  __fixture.attempts.push(structuredClone(message));
  chrome.storage.local.get('fixtureConfig', ({fixtureConfig = {}}) => {
    const epoch = fixtureConfig.epoch || '${OLD_EPOCH}';
    const status = {error: '', version: 'fixture', identity: {company_id: 7, user_id: epoch === '${OLD_EPOCH}' ? 1 : 2, company_name: 'Synthetic company', user_name: 'Synthetic employee'},
      employee_epoch: epoch, legacy_employee_epoch: '${OLD_EPOCH}',
      policy: {tracking: true, interactions: true, field_values: true, domains: ['work.example.test'], policy_expires_at: Math.floor(Date.now()/1000) + 3600},
      queue: {pending: 0, rejected: 0}, collection_reason: 'recording'};
    if (message.action === 'status') return reply({ok: true, status});
    if (message.action === 'open') return reply({ok: true});
    if (message.action !== 'store') throw new Error('Unexpected fixture native action');
    const ids = message.events.map(event => event.event_id);
    reply({ok: true, employee_epoch: fixtureConfig.ack === 'wrong_epoch' ? '${NEW_EPOCH}' : message.employee_epoch,
      stored_event_ids: ids, confirmed_event_ids: fixtureConfig.ack === 'confirmed' ? ids : [], rejected: {}});
  });
};
`;

async function waitFor(read, predicate, label, timeout = 12000) {
  const deadline = Date.now() + timeout;
  let value;
  while (Date.now() < deadline) {
    value = await read();
    if (predicate(value)) return value;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(label + ': ' + JSON.stringify(value));
}

async function launchFixture(source = path.resolve(__dirname, '..')) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tracking-browser-fixture-'));
  const extension = path.join(root, 'extension');
  fs.mkdirSync(extension);
  for (const name of ['manifest.json', 'background.js', 'content.js', 'privacy.js', 'outbox.js', 'setup.html', 'setup.js', 'setup.css', 'locale.js', '_locales', 'icons']) {
    fs.cpSync(path.join(source, name), path.join(extension, name), {recursive: true});
  }
  fs.writeFileSync(path.join(extension, 'background.js'), NATIVE_FIXTURE + fs.readFileSync(path.join(source, 'background.js'), 'utf8'));
  let context;
  const close = async () => {
    if (context) await context.close();
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    assert(path.basename(root).startsWith('tracking-browser-fixture-'));
    fs.rmSync(root, {recursive: true, force: true});
  };
  try {
    context = await chromium.launchPersistentContext(path.join(root, 'profile'), {
      channel: process.env.PLAYWRIGHT_CHANNEL || 'chromium', headless: process.env.PLAYWRIGHT_HEADED !== '1',
      viewport: {width: 960, height: 700},
      args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`, '--disable-background-networking', '--no-first-run'],
    });
    await context.route(/^https?:/, route => new URL(route.request().url()).hostname === 'work.example.test'
      ? route.fulfill({contentType: 'text/html', body: HTML}) : route.abort());
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker', {timeout: 15000});
    await worker.evaluate(() => sync());
    const status = await worker.evaluate(() => state);
    assert.equal(status.employee_epoch, OLD_EPOCH);
    assert.match(status.browser_session_generation, /^[a-f0-9]{32}$/);
    return {root, extension, context, worker, close};
  } catch (error) { await close(); throw error; }
}

async function rows(worker, store = 'events') {
  return worker.evaluate(async name => {
    const db = await outbox.database;
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(name, 'readonly');
      const request = transaction.objectStore(name).getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }, store);
}

async function configure(worker, change) {
  await worker.evaluate(async update => {
    const {fixtureConfig = {}} = await chrome.storage.local.get('fixtureConfig');
    await chrome.storage.local.set({fixtureConfig: {...fixtureConfig, ...update}});
    await sync();
  }, change);
}

async function workPage(fixture) {
  const page = await fixture.context.newPage();
  await page.goto(FIXTURE_URL);
  await page.bringToFront();
  await page.getByRole('heading', {name: 'Synthetic work page'}).waitFor();
  await page.mouse.move(250, 150);
  await waitFor(() => rows(fixture.worker, 'sessions'), values => values.some(row => !row.closed), 'Content script must persist a session checkpoint');
  return page;
}

async function retainedUntilServerConfirmation() {
  const fixture = await launchFixture();
  try {
    const page = await workPage(fixture);
    await page.getByRole('button', {name: 'Save changes'}).click();
    const pending = await waitFor(() => rows(fixture.worker), values => values.some(row => row.event.type === 'button_click'), 'Click must reach real IndexedDB');
    const id = pending.find(row => row.event.type === 'button_click').event_id;
    await configure(fixture.worker, {ack: 'stored_only'});
    assert((await rows(fixture.worker)).some(row => row.event_id === id), 'Native storage receipt is not a server acknowledgement');
    await configure(fixture.worker, {ack: 'wrong_epoch'});
    assert((await rows(fixture.worker)).some(row => row.event_id === id), 'Wrong-employee acknowledgement must not dispose of the row');
    await configure(fixture.worker, {ack: 'confirmed'});
    assert(!(await rows(fixture.worker)).some(row => row.event_id === id), 'Matching server acknowledgement disposes of exactly the acknowledged row');
  } finally { await fixture.close(); }
}

async function employeeSwitchWithLostContentReply() {
  const fixture = await launchFixture();
  try {
    const page = await workPage(fixture);
    await fixture.worker.evaluate(() => { __fixture.dropRecordAck = true; });
    await page.getByRole('button', {name: 'Save changes'}).click();
    const old = await waitFor(() => fixture.worker.evaluate(() => __fixture.dropped), value => !!value, 'Persisted old-employee click reply must be withheld');
    assert.equal(old.employee_epoch, OLD_EPOCH);
    await configure(fixture.worker, {epoch: NEW_EPOCH});
    assert((await rows(fixture.worker)).some(row => row.event_id === old.event.event_id && row.employee_epoch === OLD_EPOCH));
    // Exercise the real five-second content RPC deadline, not a fixture timeout.
    await page.waitForTimeout(6200);
    await page.getByRole('button', {name: 'Save changes'}).click();
    await waitFor(() => rows(fixture.worker), values => values.some(row => row.event.type === 'button_click' && row.employee_epoch === NEW_EPOCH),
      'Page must continue after replaying an already-durable old-employee click');
    const all = await rows(fixture.worker);
    assert.equal(all.filter(row => row.event_id === old.event.event_id).length, 1);
    assert.equal(all.find(row => row.event_id === old.event.event_id).employee_epoch, OLD_EPOCH);
    const next = await fixture.context.newPage();
    await next.goto('https://work.example.test/next');
    await next.getByRole('button', {name: 'Save changes'}).click();
    await waitFor(() => rows(fixture.worker), values => values.some(row => row.event.url === 'https://work.example.test/next'
      && row.event.type === 'button_click' && row.employee_epoch === NEW_EPOCH), 'Fresh page must collect for the new employee');
  } finally { await fixture.close(); }
}

async function restartRecoversCheckpoint() {
  const fixture = await launchFixture();
  try {
    const page = await workPage(fixture);
    const checkpoints = await waitFor(() => rows(fixture.worker, 'sessions'), values => values.some(row => !row.closed && row.event.end_timestamp > row.event.timestamp), 'A nonzero durable session checkpoint is required');
    const checkpoint = checkpoints.find(row => !row.closed && row.event.end_timestamp > row.event.timestamp);
    const generation = await fixture.worker.evaluate(() => state.browser_session_generation);
    const cdp = await fixture.context.newCDPSession(page);
    const versions = new Map();
    cdp.on('ServiceWorker.workerVersionUpdated', ({versions: updates}) => updates.forEach(version => versions.set(version.versionId, version)));
    await cdp.send('ServiceWorker.enable');
    const version = await waitFor(async () => [...versions.values()], values => values.some(value => value.scriptURL === fixture.worker.url()), 'Discover extension service worker version');
    const target = version.find(value => value.scriptURL === fixture.worker.url());
    await cdp.send('ServiceWorker.stopWorker', {versionId: target.versionId});
    await cdp.send('ServiceWorker.startWorker', {scopeURL: new URL('./', target.scriptURL).href});
    await page.getByRole('button', {name: 'Save changes'}).click();
    // Modern Chromium restarts the execution context on the same Playwright Worker.
    // Observe the actual generation change instead of requiring a new Worker event.
    let worker = fixture.worker;
    await waitFor(async () => {
      worker = fixture.context.serviceWorkers().find(candidate => candidate.url() === fixture.worker.url()) || worker;
      return worker.evaluate(() => state.browser_session_generation).catch(() => null);
    }, value => !!value && value !== generation, 'Worker execution context must restart', 15000);
    await worker.evaluate(() => sync());
    assert.notEqual(await worker.evaluate(() => state.browser_session_generation), generation);
    const recovered = await waitFor(() => rows(worker), values => values.some(row => row.event_id === checkpoint.event_id), 'Restart must recover the last durable checkpoint');
    const session = recovered.find(row => row.event_id === checkpoint.event_id);
    assert.equal(session.employee_epoch, OLD_EPOCH);
    assert(session.event.end_timestamp >= checkpoint.event.end_timestamp);
    assert.equal(recovered.filter(row => row.event_id === checkpoint.event_id).length, 1);
    const beforeClicks = recovered.filter(row => row.event.type === 'button_click').length;
    await page.getByRole('button', {name: 'Save changes'}).click();
    await waitFor(() => rows(worker), values => values.filter(row => row.event.type === 'button_click').length > beforeClicks, 'Page continues after service worker restart');
    await waitFor(() => rows(worker, 'sessions'), values => values.some(row => !row.closed && row.event_id !== checkpoint.event_id), 'New durable session starts after recovery');
    await cdp.detach();
  } finally { await fixture.close(); }
}

async function realPageFocusBoundary() {
  const fixture = await launchFixture();
  try {
    const page = await workPage(fixture);
    const active = await waitFor(() => rows(fixture.worker, 'sessions'), values => values.some(row => !row.closed && row.event.end_timestamp > row.event.timestamp), 'Focused page checkpoint');
    const original = active.find(row => !row.closed);
    const pageCdp = await fixture.context.newCDPSession(page);
    await pageCdp.send('Emulation.setFocusEmulationEnabled', {enabled: false});
    const other = await fixture.context.newPage();
    const otherCdp = await fixture.context.newCDPSession(other);
    await otherCdp.send('Emulation.setFocusEmulationEnabled', {enabled: false});
    await other.goto('https://work.example.test/other');
    await other.bringToFront();
    await waitFor(() => page.evaluate(() => document.hasFocus()), value => value === false, 'Original tab loses real browser focus');
    await waitFor(() => rows(fixture.worker, 'sessions'), values => values.some(row => row.event_id === original.event_id && row.closed), 'Blur closes the durable checkpoint');
    const closed = (await rows(fixture.worker)).find(row => row.event_id === original.event_id);
    assert(closed && closed.event.end_timestamp >= original.event.end_timestamp);
    await page.bringToFront();
    await page.mouse.move(360, 180);
    await waitFor(() => rows(fixture.worker, 'sessions'), values => values.some(row => !row.closed && row.event.url === FIXTURE_URL && row.event_id !== original.event_id), 'Refocused page starts a new durable session');
    await pageCdp.detach();
    await otherCdp.detach();
  } finally { await fixture.close(); }
}

async function main() {
  let failed = false;
  for (const [name, test] of [['server acknowledgement retention', retainedUntilServerConfirmation],
    ['employee switch with a lost content reply', employeeSwitchWithLostContentReply],
    ['service worker checkpoint recovery', restartRecoversCheckpoint],
    ['real browser page focus boundary', realPageFocusBoundary]]) {
    try { await test(); console.log('PASS: ' + name); }
    catch (error) { failed = true; console.error('FAIL: ' + name, error); }
  }
  if (failed) process.exitCode = 1;
}

module.exports = {launchFixture, waitFor, rows, configure, workPage, OLD_EPOCH, NEW_EPOCH, restartRecoversCheckpoint, realPageFocusBoundary};
if (require.main === module) main().catch(error => {console.error(error); process.exitCode = 1;});
