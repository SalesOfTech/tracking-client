'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {fixture} = require('./main-lifecycle-fixture.cjs');
const {BROWSER_PAGES} = require('../protocol.cjs');

const expected = {Chrome: 'chrome://extensions', Edge: 'edge://extensions', Yandex: 'browser://extensions',
  Opera: 'opera://extensions', Brave: 'brave://extensions', Vivaldi: 'vivaldi://extensions',
  Chromium: 'chrome://extensions', Firefox: 'about:addons'};

test('the protocol exports exactly eight immutable approved browser addresses', () => {
  assert.deepEqual(BROWSER_PAGES, expected);
  assert.equal(Object.isFrozen(BROWSER_PAGES), true);
});

for (const [browser, address] of Object.entries(expected)) {
  test(`copy ${browser} fixed address locally without requesting Python status`, async () => {
    const f = fixture();
    f.start();
    const result = f.command('copy-browser-page', {browser});
    assert.deepEqual(f.clipboard, [address]);
    assert.deepEqual(f.events.slice(-1), [['clipboard', address]]);
    assert.deepEqual(f.writes, []);
    assert.deepEqual(JSON.parse(JSON.stringify(await result)), {copied: true});
  });
}

test('invalid browser, free text, URLs and unexpected properties cannot touch clipboard or parent', async () => {
  const f = fixture();
  f.start();
  for (const input of [{}, null, [], 'Chrome', {browser: 'Safari'}, {browser: 'chrome'},
    {browser: 'chrome://extensions'}, {browser: '__proto__'}, {browser: 'constructor'},
    {browser: {toString: () => 'Chrome'}}, {browser: ['Chrome']},
    {browser: 'Chrome', url: 'file:///etc/passwd'}, {browser: 'Chrome', text: 'arbitrary'},
    {browser: 'Chrome', command: 'open'}, {browser: 'Chrome', extra: true}]) {
    await assert.rejects(f.command('copy-browser-page', input), /invalid_request/);
  }
  assert.deepEqual(f.clipboard, []);
  assert.deepEqual(f.writes, []);
});

test('foreign sender, frame or navigation cannot copy even an approved address', async () => {
  const f = fixture();
  const window = f.start();
  const frame = window.webContents.mainFrame;
  for (const source of [{sender: {}, senderFrame: frame},
    {sender: window.webContents, senderFrame: {url: frame.url}},
    {sender: window.webContents, senderFrame: null}]) {
    await assert.rejects(f.command('copy-browser-page', {browser: 'Chrome'}, source), /unauthorized/);
  }
  frame.url = 'https://untrusted.example';
  await assert.rejects(f.command('copy-browser-page', {browser: 'Chrome'}), /unauthorized/);
  assert.deepEqual(f.clipboard, []);
  assert.deepEqual(f.writes, []);
});

test('authorization runs before input validation or clipboard access', async () => {
  const f = fixture();
  f.start();
  const input = {toJSON() {throw Error('validation should not run');}};
  await assert.rejects(f.command('copy-browser-page', input, {sender: {}, senderFrame: {}}), /unauthorized/);
  assert.deepEqual(f.clipboard, []);
  assert.deepEqual(f.writes, []);
});

test('clipboard failure prevents status request and rejects the operation', async () => {
  const f = fixture({clipboardFailure: true});
  f.start();
  await assert.rejects(f.command('copy-browser-page', {browser: 'Firefox'}), /clipboard unavailable/);
  assert.deepEqual(f.writes, []);
});

test('health mode never changes the user clipboard', async () => {
  const f = fixture({env: {SOFT_TRACKING_UI_HEALTH: '1'}});
  f.start();
  await assert.rejects(f.command('copy-browser-page', {browser: 'Chrome'}), /invalid_request/);
  assert.deepEqual(f.clipboard, []);
  assert.deepEqual(f.writes, []);
});

test('copy completes without a Python status response or browser launch', async () => {
  const f = fixture();
  f.start();
  const result = f.command('copy-browser-page', {browser: 'Edge'});
  assert.deepEqual(f.clipboard, ['edge://extensions']);
  assert.deepEqual(JSON.parse(JSON.stringify(await result)), {copied: true});
  assert.deepEqual(f.writes, []);
  assert.deepEqual(f.probes, []);
});
