'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {fixture} = require('./main-lifecycle-fixture.cjs');

const hidden = {SOFT_TRACKING_UI_HIDDEN: '1'};

for (const platform of ['win32', 'darwin', 'linux']) {
  test(`${platform}: explicit launch opens once ready, restoring/focusing on later show`, () => {
    const f = fixture({platform});
    const window = f.start();
    assert.equal(window.options.show, false);
    assert.deepEqual(f.visibility(), []);
    f.ready();
    assert.deepEqual(f.visibility(), ['show', 'focus']);
    window.minimize();
    f.message({event: 'show'});
    assert.deepEqual(f.visibility().slice(-3), ['restore', 'show', 'focus']);
  });

  test(`${platform}: startup stays in background with usable tray`, () => {
    const f = fixture({platform, env: hidden});
    f.start();
    if (platform === 'linux') f.probes[0].callback(null, '   variant       boolean true\n');
    f.ready();
    assert.deepEqual(f.visibility(), ['hide']);
    assert.equal(f.trays.length, 1);
    assert.deepEqual(f.writes, []);
  });

  test(`${platform}: failed tray provides minimized fallback, never show or focus`, () => {
    const f = fixture({platform, env: hidden, trayFailure: 'create'});
    f.start();
    f.ready();
    assert.deepEqual(f.visibility(), ['minimize']);
    assert.equal(f.app.listenerCount('activate'), 1);
    assert.equal(f.nativeClose().prevented, true);
    assert.deepEqual(f.visibility(), ['minimize', 'minimize']);
  });

  test(`${platform}: early explicit show is queued and overrides hidden startup`, () => {
    const f = fixture({platform, env: hidden});
    f.message({event: 'show'});
    f.start();
    f.message({event: 'show'});
    assert.deepEqual(f.visibility(), []);
    f.ready();
    assert.deepEqual(f.visibility(), ['show', 'focus']);
    if (platform === 'linux') f.probes[0].callback(null, 'variant boolean true');
    assert.deepEqual(f.visibility(), ['show', 'focus']);
  });

  test(`${platform}: tray click and menu both restore the existing window`, () => {
    const f = fixture({platform, env: hidden});
    const window = f.start();
    f.ready();
    window.minimize();
    f.trays[0].emit('click');
    assert.deepEqual(f.visibility().slice(-3), ['restore', 'show', 'focus']);
    f.nativeClose();
    assert.equal(f.visibility().at(-1), 'hide');
    f.trays[0].menu[0].click();
    assert.deepEqual(f.visibility().slice(-2), ['show', 'focus']);
    assert.equal(f.windows.length, 1);
  });

  test(`${platform}: destroyed tray is not used as a hide-only escape route`, async () => {
    const f = fixture({platform, env: hidden});
    f.start();
    f.ready();
    f.trays[0].destroy();
    await f.windowCommand('close');
    assert.equal(f.visibility().at(-1), 'minimize');
  });

  for (const mode of ['desktop', 'installer']) {
    for (const autostart of ['0', '1']) {
      test(`${platform}/${mode}/hidden=${autostart}: health cannot display, minimize or bypass acknowledged ready`, async () => {
        const f = fixture({platform, env: {SOFT_TRACKING_UI_HEALTH: '1', SOFT_TRACKING_UI_MODE: mode,
          SOFT_TRACKING_UI_HIDDEN: autostart, SOFT_TRACKING_HEALTH: '/fixture/health'}});
        f.app.emit('activate');
        f.message({event: 'show'});
        f.start();
        f.app.emit('activate');
        f.ready();
        f.app.emit('activate');
        f.message({event: 'show'});
        f.nativeClose();
        await f.windowCommand('minimize');
        await f.windowCommand('close');
        assert.deepEqual(f.visibility(), []);
        assert.deepEqual(f.probes, []);
        assert.deepEqual(f.trays, []);
        assert.ok(!f.events.includes('quit'));
        const ready = f.command('ready');
        assert.equal(f.writes.at(-1).action, 'ready');
        assert.ok(!f.events.includes('quit'));
        f.message({id: f.writes.at(-1).id, ok: true, data: {}});
        await ready;
        f.flushImmediate();
        assert.equal(f.events.at(-1), 'quit');
        assert.deepEqual(f.visibility(), []);
        assert.deepEqual(f.phases, ['main', 'app-ready', 'page-ready', 'request-ready', 'response-ready-ok']);
      });
    }
  }
}

test('macOS ignores startup activate, but later Dock activation opens hidden/minimized window', () => {
  for (const trayFailure of ['', 'create']) {
    const f = fixture({platform: 'darwin', env: hidden, trayFailure});
    f.app.emit('activate');
    f.start();
    f.app.emit('activate');
    assert.deepEqual(f.visibility(), []);
    f.ready();
    assert.deepEqual(f.visibility(), [trayFailure ? 'minimize' : 'hide']);
    f.app.emit('activate');
    assert.deepEqual(f.visibility().slice(-2), ['show', 'focus']);
    if (trayFailure) assert.equal(f.visibility().at(-3), 'restore');
  }
});

test('Linux with a Tray object but no registered host stays reachable and minimized', async () => {
  for (const [error, output] of [[null, 'variant boolean false'], [null, 'unexpected true output'],
    [null, 'variant boolean true\nextra'], [Error('timeout'), ''], [Error('ENOENT'), '']]) {
    const f = fixture({platform: 'linux', env: hidden});
    f.start();
    assert.equal(f.trays.length, 1);
    f.probes[0].callback(error, output);
    f.ready();
    assert.deepEqual(f.visibility(), ['minimize']);
    await f.windowCommand('close');
    assert.deepEqual(f.visibility(), ['minimize', 'minimize']);
  }
});

test('Linux host probe is bounded, session-local, fixed-argument and uses no shell', () => {
  const f = fixture({platform: 'linux', env: hidden});
  f.start();
  assert.equal(f.probes.length, 1);
  const probe = f.probes[0];
  assert.equal(probe.file, '/usr/bin/dbus-send');
  assert.deepEqual(Array.from(probe.args), ['--session', '--print-reply=literal', '--reply-timeout=1000',
    '--dest=org.kde.StatusNotifierWatcher', '/StatusNotifierWatcher', 'org.freedesktop.DBus.Properties.Get',
    'string:org.kde.StatusNotifierWatcher', 'string:IsStatusNotifierHostRegistered']);
  assert.equal(probe.options.timeout, 1500);
  assert.equal(probe.options.maxBuffer, 1024);
  assert.equal(probe.options.shell, undefined);
});

test('Linux delayed positive probe can hide fallback but must not hide an explicitly restored window', () => {
  for (const explicit of [false, true]) {
    const f = fixture({platform: 'linux', env: hidden});
    const window = f.start();
    f.ready();
    assert.deepEqual(f.visibility(), ['minimize']);
    if (explicit) {window.restore(); window.show();}
    f.probes[0].callback(null, 'variant boolean true');
    assert.equal(f.visibility().at(-1), explicit ? 'show' : 'hide');
  }
});

test('tray setup failure disposes its partial icon and keeps minimized fallback', () => {
  const f = fixture({env: hidden, trayFailure: 'menu'});
  f.start();
  f.ready();
  assert.equal(f.trays[0].destroyed, true);
  assert.deepEqual(f.visibility(), ['minimize']);
});

test('missing tray image cannot strand hidden startup behind an empty icon', () => {
  const f = fixture({env: hidden, emptyIcon: true});
  f.start();
  f.ready();
  assert.deepEqual(f.trays, []);
  assert.deepEqual(f.visibility(), ['minimize']);
});

test('shutdown, parent disconnect and SIGTERM prevent queued/late reveals', () => {
  for (const end of [f => f.message({event: 'shutdown'}), f => f.input.emit('end'),
    f => f.process.emit('SIGTERM')]) {
    const f = fixture({platform: 'linux', env: hidden});
    f.start();
    f.message({event: 'show'});
    end(f);
    f.ready();
    f.message({event: 'show'});
    f.app.emit('activate');
    f.probes[0].callback(null, 'variant boolean true');
    assert.deepEqual(f.visibility(), []);
    assert.equal(f.nativeClose().prevented, undefined);
  }
});

test('destroyed window is never restored or displayed by stale show/tray/activate events', () => {
  const f = fixture({platform: 'linux', env: hidden});
  const window = f.start();
  f.ready();
  window.destroyed = true;
  const before = f.visibility();
  f.message({event: 'show'});
  f.app.emit('activate');
  f.trays[0].emit('click');
  f.probes[0].callback(null, 'variant boolean true');
  assert.deepEqual(f.visibility(), before);
});

test('installer still consults busy status before allowing close', async () => {
  for (const busy of [true, false]) {
    const f = fixture({env: {SOFT_TRACKING_UI_MODE: 'installer'}});
    f.start();
    f.ready();
    const close = f.windowCommand('close');
    assert.equal(f.writes.at(-1).action, 'status');
    assert.ok(!f.events.includes('quit'));
    f.message({id: f.writes.at(-1).id, ok: true, data: {busy}});
    await close;
    assert.equal(f.events.includes('quit'), !busy);
    assert.deepEqual(f.trays, []);
  }
});

test('health ready rejection and timeout remain failures, never success or visibility', async () => {
  for (const failure of ['reply', 'timeout']) {
    const f = fixture({env: {SOFT_TRACKING_UI_HEALTH: '1'}});
    f.start();
    f.ready();
    const ready = f.command('ready');
    if (failure === 'reply') f.message({id: f.writes.at(-1).id, ok: false, error: 'connect_failed'});
    else {
      const timer = [...f.timers.values()][0];
      assert.equal(timer.delay, 35000);
      timer.callback();
    }
    await assert.rejects(ready, failure === 'reply' ? /connect_failed/ : /server_unavailable/);
    f.flushImmediate();
    assert.ok(!f.events.includes('quit'));
    assert.deepEqual(f.visibility(), []);
  }
});

test('ordinary ready ACK does not terminate the long-lived runtime', async () => {
  const f = fixture({env: hidden});
  f.start();
  f.ready();
  const ready = f.command('ready');
  f.message({id: f.writes.at(-1).id, ok: true, data: {}});
  await ready;
  f.flushImmediate();
  assert.ok(!f.events.includes('quit'));
  assert.deepEqual(f.visibility(), ['hide']);
});

test('backgrounding never weakens the webContents security boundary', async () => {
  const f = fixture({env: hidden});
  const window = f.start();
  f.ready();
  assert.equal(window.options.webPreferences.sandbox, true);
  assert.equal(window.options.webPreferences.contextIsolation, true);
  assert.equal(window.options.webPreferences.nodeIntegration, false);
  assert.equal(window.options.webPreferences.webSecurity, true);
  assert.equal(window.popup().action, 'deny');
  assert.equal(window.permissionCheck(), false);
  window.permissionRequest(null, 'camera', allowed => assert.equal(allowed, false));
  await assert.rejects(f.windowCommand('close', {sender: {}, senderFrame: {}}), /invalid_request/);
  await assert.rejects(f.command('ready', {}, {sender: {}, senderFrame: {}}), /unauthorized/);
  assert.deepEqual(f.writes, []);
});
