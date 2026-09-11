const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {waitFor, rows, workPage} = require('./durable.browser.cjs');

(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tracking-native-test-'));
  const repo = path.resolve(__dirname, '../..');
  const command = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const lookup = spawnSync(command, ['-c', 'import sys; print(sys.executable)'], {encoding: 'utf8', windowsHide: true});
  assert.equal(lookup.status, 0, lookup.stderr || 'Python is required for the source native fixture');
  const python = lookup.stdout.trim();
  const host = path.join(root, 'host.py');
  const env = {...process.env, HOME: root, XDG_CONFIG_HOME: path.join(root, '.config'),
    XDG_DATA_HOME: path.join(root, 'data'), SOFT_TRACKING_FIXTURE_ROOT: path.join(root, 'client')};
  delete env.SOFT_TRACKING_INSTALL;
  let context;
  try {
    const script = '#!' + python + '\n' + [
      'import json, os, sys, time',
      'from pathlib import Path',
      'from types import SimpleNamespace',
      'sys.path.insert(0, ' + JSON.stringify(path.join(repo, 'agent')) + ')',
      'from agent_tracker.core.client import Client',
      'from agent_tracker.native_host import main, ALLOWED_ORIGIN',
      'class FixtureHttp:',
      '    def __init__(self): self.session = SimpleNamespace(headers={})',
      '    def post_json(self, path, payload):',
      '        if path != "/client/v3/enroll" or payload["employee_key"] != "b" * 64:',
      '            raise AssertionError("Real network and nonfixture enrollment are forbidden")',
      '        return dict(ok=True, device_id=payload["device_id"], company_id=7, user_id=1, company_name="Synthetic company", user_name="Synthetic employee")',
      'client = Client(Path(os.environ["SOFT_TRACKING_FIXTURE_ROOT"]), http=FixtureHttp())',
      'mode = sys.argv[1] if len(sys.argv) > 1 else "host"',
      'try:',
      '    if mode == "initialize":',
      '        client.enroll("a" * 32, "b" * 64)',
      '        client.state.set("policy", dict(tracking=True, interactions=True, field_values=True, domains=["work.example.test"], policy_expires_at=int(time.time()) + 3600))',
      '        print(json.dumps(client.status()))',
      '    elif mode == "confirm":',
      '        client.outbox_for_epoch(sys.argv[2]).acknowledge(sys.argv[3:])',
      '    else:',
      '        raise SystemExit(main(ALLOWED_ORIGIN if mode == "host" else mode, client=client))',
      'finally:',
      '    client.close()',
      '',
    ].join('\n');
    fs.writeFileSync(host, script, {mode: 0o700});
    function invoke(args, input) {
      const result = spawnSync(python, [host, ...args], {env, input, windowsHide: true, timeout: 15000});
      assert.equal(result.status, 0, result.stderr && result.stderr.toString() || String(result.error || 'Source native fixture failed'));
      return result.stdout;
    }
    function exchange(messages) {
      const input = Buffer.concat(messages.map(message => {
        const data = Buffer.from(JSON.stringify(message));
        const header = Buffer.alloc(4);
        header.writeUInt32LE(data.length);
        return Buffer.concat([header, data]);
      }));
      const output = invoke(['host'], input);
      const replies = [];
      for (let offset = 0; offset < output.length;) {
        const size = output.readUInt32LE(offset);
        offset += 4;
        assert(size > 0 && offset + size <= output.length, 'Complete framed native reply required');
        replies.push(JSON.parse(output.subarray(offset, offset + size)));
        offset += size;
      }
      assert.equal(replies.length, messages.length);
      return replies;
    }
    const initial = JSON.parse(invoke(['initialize']).toString());
    assert.equal(initial.identity.company_name, 'Synthetic company');
    assert.match(initial.employee_epoch, /^[a-f0-9]{32}$/);
    assert.equal(initial.employee_epoch, initial.legacy_employee_epoch);
    const event = {event_id: '1'.repeat(32), type: 'web_session', timestamp: Math.floor(Date.now()/1000)-5,
      end_timestamp: Math.floor(Date.now()/1000)-1, url: 'https://work.example.test/editor'};
    const message = {action: 'store', employee_epoch: initial.employee_epoch, events: [event]};
    const replies = exchange([{action: 'status'}, message, message]);
    assert.equal(replies[0].status.employee_epoch, initial.employee_epoch);
    assert.equal(replies[0].status.version, require('../../package.json').version);
    for (const reply of replies.slice(1)) {
      assert.equal(reply.ok, true);
      assert.equal(reply.employee_epoch, initial.employee_epoch);
      assert.deepEqual(reply.stored_event_ids, [event.event_id]);
      assert.deepEqual(reply.confirmed_event_ids, []);
    }
    invoke(['confirm', initial.employee_epoch, event.event_id]);
    assert.deepEqual(exchange([message])[0].confirmed_event_ids, [event.event_id]);
    console.log('PASS: source native framing, mock-API profile enrollment, durable duplicate receipt and explicit final acknowledgement');

    if (process.platform !== 'linux') {
      console.log('SKIP: OS native messaging registration is isolated on Linux CI only; portable source native checks passed');
      return;
    }
    const profile = path.join(root, 'profile');
    const manifestFolder = path.join(profile, 'NativeMessagingHosts');
    fs.mkdirSync(manifestFolder, {recursive: true});
    fs.writeFileSync(path.join(manifestFolder, 'com.soft.tracking.json'), JSON.stringify({
      name: 'com.soft.tracking', description: 'Synthetic native fixture only', path: host, type: 'stdio',
      allowed_origins: ['chrome-extension://bjjdlmnghnhfnjlgacoijncoggpnfnjh/'],
    }));
    const extension = path.join(repo, 'extension');
    context = await chromium.launchPersistentContext(profile, {
      channel: process.env.PLAYWRIGHT_CHANNEL || 'chromium', headless: process.env.PLAYWRIGHT_HEADED !== '1', env,
      args: ['--disable-extensions-except=' + extension, '--load-extension=' + extension, '--disable-background-networking'],
    });
    await context.route(/^https?:/, route => new URL(route.request().url()).hostname === 'work.example.test'
      ? route.fulfill({contentType: 'text/html', body: '<!doctype html><h1>Synthetic work page</h1><button>Save changes</button>'}) : route.abort());
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker', {timeout: 15000});
    await worker.evaluate(() => sync());
    const status = await worker.evaluate(() => state);
    assert.equal(status.error, '');
    assert.equal(status.identity.company_name, 'Synthetic company');
    assert.equal(status.employee_epoch, initial.employee_epoch);
    assert.equal(status.legacy_employee_epoch, initial.legacy_employee_epoch);
    assert.match(status.browser_session_generation, /^[a-f0-9]{32}$/);
    const page = await workPage({context, worker});
    await page.getByRole('button', {name: 'Save changes'}).click();
    const pending = await waitFor(() => rows(worker), values => values.some(row => row.event.type === 'button_click'), 'Real content click must reach browser IndexedDB');
    const click = pending.find(row => row.event.type === 'button_click');
    await worker.evaluate(() => sync());
    assert((await rows(worker)).some(row => row.event_id === click.event_id), 'Native durable storage alone must not delete browser custody');
    invoke(['confirm', initial.employee_epoch, click.event_id]);
    await worker.evaluate(() => sync());
    assert(!(await rows(worker)).some(row => row.event_id === click.event_id), 'Explicit final native acknowledgement must dispose of the browser row');
    console.log('PASS: real Chromium content -> background IndexedDB -> native messaging -> enrolled Client profile; no external network');
  } finally {
    if (context) await context.close();
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    assert(path.basename(root).startsWith('tracking-native-test-'));
    fs.rmSync(root, {recursive: true, force: true});
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
