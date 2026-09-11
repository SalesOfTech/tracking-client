const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const {IDBFactory, IDBKeyRange} = require('fake-indexeddb');

const OLD = 'a'.repeat(32), NEW = 'b'.repeat(32), UNKNOWN = 'c'.repeat(32);
const NOW = 1700000100;
const plain = value => JSON.parse(JSON.stringify(value));
const policy = (host = 'old.example.test', expires = NOW + 600) => ({tracking:true,interactions:true,field_values:true,domains:[host],policy_expires_at:expires});
const desktop = (epoch = OLD, rules = policy()) => ({identity:{device_id:epoch,company_name:'Fixture',user_name:'Test'},
  employee_epoch:epoch,legacy_employee_epoch:OLD,policy:rules,error:'',queue:{pending:0,rejected:0}});
const event = (id, host = 'old.example.test', timestamp = NOW - 10) => ({event_id:id.toString(16).padStart(32,'0'),
  type:'button_click',timestamp,url:`https://${host}/page`,target:{tag:'button',label:'Save'}});
const session = (id, end = NOW - 1) => ({event_id:id.toString(16).padStart(32,'0'),type:'web_session',timestamp:NOW - 20,
  end_timestamp:end,url:'https://old.example.test/page'});
const sender = url => ({tab:{id:1},id:'fixture',url});

async function harness(t, options = {}) {
  const h = {calls:[],badges:[],opened:[],storage:options.storage || {},factory:options.factory || new IDBFactory(),
    desktop:options.desktop || desktop(),now:NOW,storageError:false,reply:options.reply};
  const timers = new Set();
  let listener, clicked;
  const chrome = {
    action:{onClicked:{addListener:fn => { clicked = fn; }},setBadgeText:value => h.badges.push(value.text),setBadgeBackgroundColor:() => {}},
    runtime:{id:'fixture',getManifest:() => ({version:'3.0.4.60000'}),getURL:name => 'chrome-extension://fixture/' + name,
      sendNativeMessage:(host, message, callback) => {
        assert.equal(host, 'com.soft.tracking');
        h.calls.push(plain(message));
        Promise.resolve().then(async () => {
          let result = h.reply ? await h.reply(plain(message), h) : undefined;
          if (result === undefined) result = message.action === 'status' ? {ok:true,status:plain(h.desktop)} :
            {ok:true,employee_epoch:message.employee_epoch,stored_event_ids:(message.events || []).map(row => row.event_id),confirmed_event_ids:[],rejected:{}};
          callback(plain(result));
        }).catch(error => callback({ok:false,error:error.message}));
      },
      onMessage:{addListener:fn => { listener = fn; }},onInstalled:{addListener:() => {}},onStartup:{addListener:() => {}},reload:() => {}},
    storage:{local:{get:(key, callback) => {
      const keys = typeof key === 'string' ? [key] : Array.isArray(key) ? key : Object.keys(key || h.storage);
      callback(plain(Object.fromEntries(keys.filter(name => name in h.storage).map(name => [name,h.storage[name]]))));
    },set:(value, callback) => {
      if (h.storageError) chrome.runtime.lastError = {message:'Synthetic local storage failure'};
      else Object.assign(h.storage, plain(value));
      callback();
      delete chrome.runtime.lastError;
    }}},
    alarms:{onAlarm:{addListener:() => {}},create:() => {}},
    tabs:{create:value => h.opened.push(value),query:(options, callback) => callback([])}
  };
  class ClockDate extends Date { static now() { return h.now * 1000; } }
  const context = vm.createContext({chrome,navigator:{userAgent:'Chrome/140',...(options.navigator || {})},
    crypto:webcrypto,URL,Promise,Date:ClockDate,indexedDB:h.factory,IDBKeyRange,structuredClone,
    setTimeout:(fn, ms) => { const timer = setTimeout(fn, ms); timers.add(timer); return timer; },
    clearTimeout:timer => { clearTimeout(timer); timers.delete(timer); }});
  for (const file of ['privacy.js','outbox.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'), context);
  if (options.seed) {
    const queue = vm.runInContext('new TrackingOutbox()', context);
    try { await options.seed(queue); } finally { await queue.close(); }
  }
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../background.js'),'utf8'), context);
  h.queue = vm.runInContext('outbox', context);
  h.sync = () => vm.runInContext('sync()', context);
  h.ready = h.sync();
  h.status = () => plain(vm.runInContext('state', context));
  h.request = (message, source = sender(message.event?.url || 'https://old.example.test/page')) =>
    new Promise(resolve => listener(message, source, result => resolve(plain(result))));
  h.click = () => clicked();
  h.batch = async () => plain(await h.queue.epochBatches());
  t.after(async () => {
    await vm.runInContext('Promise.all([syncing,checkpointing])', context);
    for (const timer of timers) clearTimeout(timer);
    await h.queue.close();
  });
  return h;
}

test('concurrent initial requests share recovery and capability handshake; toolbar opens desktop', async t => {
  const h = await harness(t, {desktop:desktop(OLD,{})});
  const first = h.request({action:'status'}, {}), second = h.request({action:'status'}, {});
  assert.equal((await first).status.identity.company_name, 'Fixture');
  assert.equal((await second).status.identity.company_name, 'Fixture');
  assert.deepEqual(h.calls.filter(row => row.action === 'status').map(row => row.browser.epoch_protocol), [0,1]);
  assert.match(h.calls[0].browser.profile, /^[a-f0-9]{32}$/);
  assert.match(h.status().browser_session_generation, /^[a-f0-9]{32}$/);
  assert.equal(h.status().employee_epoch, OLD);
  assert.equal(h.badges.at(-1), 'OFF');
  h.click();
  await new Promise(setImmediate);
  assert.equal(h.calls.at(-1).action, 'open');
  assert.deepEqual(h.opened, []);
});

const families = [
  ['Yandex', {userAgent:'Mozilla Chrome/140 Safari/537 YaBrowser/25.1'}],
  ['Chrome', {userAgent:'Mozilla Chrome/140 Safari/537'}],
  ['Edge', {userAgent:'Mozilla Chrome/140 Safari/537 Edg/140'}],
  ['Opera', {userAgent:'Mozilla Chrome/140 Safari/537 OPR/120'}],
  ['Brave', {userAgent:'Mozilla Chrome/140 Brave/140'}],
  ['Vivaldi', {userAgent:'Mozilla Chrome/140 Vivaldi/7.0'}],
  ['Chromium', {userAgent:'Mozilla Chromium/140 Chrome/140'}],
  ['Firefox', {userAgent:'Mozilla Gecko/20100101 Firefox/140'}],
  ['Chromium', {userAgent:'UnknownBrowser/1'}],
  ['Yandex', {userAgent:'Chrome/140',userAgentData:{brands:[{brand:'Yandex'},{brand:'Chromium'}]}}],
  ['Brave', {userAgent:'Chrome/140',brave:{isBrave:async () => true}}],
  ['Chrome', {userAgent:'Chrome/140',userAgentData:{brands:[null,{}, {brand:12}]},brave:{isBrave:async () => { throw new Error('unavailable'); }}}]
];
for (const [family, navigator] of families) test(`native receipt detects ${family}: ${navigator.userAgent}`, async t => {
  const h = await harness(t, {navigator});
  await h.ready;
  assert.ok(h.calls.filter(row => row.action === 'status').every(row => row.browser.family === family));
});

test('mixed queues send unchanged payloads in homogeneous epoch envelopes', async t => {
  const old = event(1), current = event(2,'new.example.test');
  const h = await harness(t, {desktop:desktop(NEW,policy('new.example.test')),seed:async queue => {
    await queue.put(old, OLD); await queue.put(current, NEW);
  }});
  await h.ready;
  const stores = h.calls.filter(row => row.action === 'store');
  assert.deepEqual(stores.map(row => ({employee_epoch:row.employee_epoch,events:row.events})),
    [{employee_epoch:OLD,events:[old]},{employee_epoch:NEW,events:[current]}]);
  assert.deepEqual(plain(await h.queue.counts()), {pending:2,rejected:0});
  h.reply = message => message.action === 'store' ? {ok:true,employee_epoch:message.employee_epoch,
    confirmed_event_ids:message.events.map(row => row.event_id),rejected:{}} : undefined;
  await h.sync();
  assert.deepEqual(plain(await h.queue.counts()), {pending:0,rejected:0});
});

for (const [name, modify] of [
  ['wrong epoch', ack => ({...ack,employee_epoch:NEW})],
  ['missing epoch', ack => ({...ack,employee_epoch:undefined})],
  ['unsent id', ack => ({...ack,confirmed_event_ids:['f'.repeat(32)]})],
  ['duplicate id', ack => ({...ack,confirmed_event_ids:[event(1).event_id,event(1).event_id]})],
  ['ack and rejection overlap', ack => ({...ack,rejected:{[event(1).event_id]:'denied'}})],
  ['unsent rejection', ack => ({...ack,rejected:{['f'.repeat(32)]:'denied'}})]
]) test(`invalid native ${name} cannot delete old event`, async t => {
  const h = await harness(t, {seed:queue => queue.put(event(1), OLD),reply:message => message.action === 'store' ?
    modify({ok:true,employee_epoch:OLD,confirmed_event_ids:[event(1).event_id],rejected:{}}) : undefined});
  await h.ready;
  assert.deepEqual(await h.batch(), [{employee_epoch:OLD,events:[event(1)]}]);
  assert.ok(h.status().error);
});

test('switch closes old sessions without retagging and repeated status does not close current sessions', async t => {
  const h = await harness(t);
  await h.ready;
  assert.equal((await h.request({action:'session',employee_epoch:OLD,event:session(1),close:false})).ok, true);
  await h.sync();
  assert.deepEqual(await h.batch(), []);
  h.desktop = desktop(NEW,policy('new.example.test'));
  await h.sync();
  assert.deepEqual(await h.batch(), [{employee_epoch:OLD,events:[session(1)]}]);
  const delayed = await h.request({action:'session',employee_epoch:OLD,event:session(1,NOW + 20),close:true});
  assert.deepEqual(delayed, {ok:true,stored:true,employee_epoch:OLD});
  assert.deepEqual(await h.batch(), [{employee_epoch:OLD,events:[session(1)]}]);
  assert.equal((await h.request({action:'session',employee_epoch:OLD,event:session(2),close:false})).ok, false);
});

test('late old clicks use persisted old policy and cannot claim new post-retirement activity', async t => {
  const h = await harness(t);
  await h.ready;
  h.desktop = desktop(NEW,policy('new.example.test'));
  await h.sync();
  const delayed = {...event(1),password:'must-not-persist',target:{tag:'input',input_type:'password',value:'must-not-persist'}};
  assert.equal((await h.request({action:'record',employee_epoch:OLD,event:delayed})).ok, true);
  const batches = await h.batch();
  assert.equal(batches[0].employee_epoch, OLD);
  assert.equal(batches[0].events[0].url, delayed.url);
  assert.doesNotMatch(JSON.stringify(batches), /must-not-persist|employee_epoch.*employee_epoch/);
  assert.equal((await h.request({action:'record',employee_epoch:OLD,event:event(2,'new.example.test')})).ok, false);
  assert.equal((await h.request({action:'record',employee_epoch:OLD,event:event(3,'old.example.test',NOW + 1)})).ok, false);
  assert.equal((await h.request({action:'record',employee_epoch:UNKNOWN,event:event(4)})).ok, false);
  assert.equal((await h.batch())[0].events.length, 1);
  assert.equal(h.storage.employee_epoch_policies[OLD].retired_at, NOW);
});

test('lost custody reply retries exact old payload after policy expiry without new delivery claim', async t => {
  const h = await harness(t);
  await h.ready;
  const payload = require('../privacy.js').sanitize(event(1), policy());
  const message = {action:'record',employee_epoch:OLD,event:payload};
  assert.equal((await h.request(message)).stored, true);
  h.now = NOW + 700;
  h.desktop = desktop(NEW,policy('new.example.test',NOW + 2000));
  await h.sync();
  const before = await h.batch();
  const reply = await h.request(message);
  assert.deepEqual(reply, {ok:true,stored:true,employee_epoch:OLD});
  assert.equal(reply.confirmed_event_ids, undefined);
  assert.deepEqual(await h.batch(), before);
  assert.equal((await h.request({...message,event:{...payload,url:'https://old.example.test/changed'}})).ok, false);
  assert.deepEqual(await h.batch(), before);
});

test('retired session checkpoint sanitizes extra and password fields before durable write', async t => {
  const h = await harness(t);
  await h.ready;
  h.desktop = desktop(NEW,policy('new.example.test'));
  await h.sync();
  await h.queue.checkpointSession(session(1), OLD);
  const raw = {...session(1,NOW + 30),url:'https://old.example.test/page?token=private',
    password:'do-not-store',target:{input_type:'password',value:'do-not-store'},extra:'do-not-store'};
  const reply = await h.request({action:'session',employee_epoch:OLD,event:raw,close:true});
  assert.equal(reply.ok, true);
  assert.deepEqual(await h.batch(), [{employee_epoch:OLD,events:[session(1,NOW + 30)]}]);
  assert.doesNotMatch(JSON.stringify(await h.batch()), /do-not-store|private|password/);
});

test('expired old policy close persists only existing snapshot and ignores incoming private fields', async t => {
  const h = await harness(t);
  await h.ready;
  h.now = NOW + 700;
  h.desktop = desktop(NEW,policy('new.example.test',NOW + 2000));
  await h.sync();
  await h.queue.checkpointSession(session(1), OLD);
  const raw = {...session(1,NOW + 660),timestamp:NOW + 650,password:'do-not-store',extra:'do-not-store'};
  const reply = await h.request({action:'session',employee_epoch:OLD,event:raw,close:true});
  assert.equal(reply.ok, true);
  assert.deepEqual(await h.batch(), [{employee_epoch:OLD,events:[session(1)]}]);
  assert.equal((await h.request({action:'session',employee_epoch:OLD,event:{...raw,event_id:event(2).event_id},close:true})).ok, false);
});

test('closed old session acknowledges late close-false checkpoint without writes after policy expiry and DB ACK', async t => {
  const h = await harness(t);
  await h.ready;
  assert.equal((await h.request({action:'session',employee_epoch:OLD,event:session(1),close:false})).ok, true);
  h.desktop = desktop(OLD,{...policy(),tracking:false,policy_expires_at:NOW - 1});
  await h.sync();
  h.now = NOW + 700;
  h.desktop = desktop(NEW,policy('new.example.test',NOW + 2000));
  await h.sync();
  const frozen = [{employee_epoch:OLD,events:[session(1)]}];
  assert.deepEqual(await h.batch(), frozen);
  const raw = {...session(1,NOW + 660),timestamp:NOW + 650,password:'do-not-store',
    extra:'do-not-store',target:{input_type:'password',value:'do-not-store'}};
  const message = {action:'session',employee_epoch:OLD,event:raw,close:false};
  const transaction = h.queue.transaction.bind(h.queue);
  for (const confirmed of [false,true]) {
    if (confirmed) {
      h.reply = request => request.action === 'store' ? {ok:true,employee_epoch:OLD,
        confirmed_event_ids:request.events.map(row => row.event_id),rejected:{}} : undefined;
      await h.sync();
    }
    const modes = [];
    h.queue.transaction = (mode, fn) => { modes.push(mode); return transaction(mode, fn); };
    try {
      assert.deepEqual(await h.request(message), {ok:true,stored:true,employee_epoch:OLD});
      assert.equal((await h.request({...message,event:{...raw,event_id:event(2).event_id}})).ok, false);
      assert.ok(modes.length > 0 && modes.every(mode => mode === 'readonly'));
    } finally { h.queue.transaction = transaction; }
    assert.deepEqual(await h.batch(), confirmed ? [] : frozen);
    assert.equal(await h.queue.closedSession(event(1).event_id, OLD), true);
    assert.equal(await h.queue.closedSession(event(1).event_id, NEW), false);
  }
});

test('background restart changes generation and recovers committed session without resurrection', async t => {
  const first = await harness(t);
  await first.ready;
  await first.request({action:'session',employee_epoch:OLD,event:session(1),close:false});
  const generation = first.status().browser_session_generation;
  await first.queue.close();
  const second = await harness(t, {factory:first.factory,storage:first.storage});
  await second.ready;
  assert.notEqual(second.status().browser_session_generation, generation);
  assert.deepEqual(await second.batch(), [{employee_epoch:OLD,events:[session(1)]}]);
  second.reply = message => message.action === 'store' ? {ok:true,employee_epoch:OLD,
    confirmed_event_ids:message.events.map(row => row.event_id),rejected:{}} : undefined;
  await second.sync();
  const delayed = await second.request({action:'session',employee_epoch:OLD,event:session(1,NOW + 50),close:true});
  assert.equal(delayed.ok, true);
  assert.equal((await second.request({action:'session-close',employee_epoch:OLD,event_id:event(1).event_id})).ok, true);
  assert.deepEqual(await second.batch(), []);
});

test('readiness reply epoch change is applied before any queued event is dispatched', async t => {
  let statuses = 0;
  const h = await harness(t, {seed:queue => queue.checkpointSession(session(1), OLD),reply:message => {
    if (message.action === 'status' && ++statuses === 2) return {ok:true,status:desktop(NEW,policy('new.example.test'))};
  }});
  await h.ready;
  assert.equal(h.status().employee_epoch, NEW);
  assert.deepEqual(h.calls.filter(row => row.action === 'store').map(row => [row.employee_epoch,row.events]), [[OLD,[session(1)]]]);
});

test('storage failure preserves existing rows and does not publish new employee before checkpoint', async t => {
  const h = await harness(t);
  await h.ready;
  await h.queue.put(event(1), OLD);
  await h.queue.checkpointSession(session(2), OLD);
  const transaction = h.queue.transaction.bind(h.queue);
  h.queue.transaction = (mode, fn) => transaction(mode, (store, result, tx) => {
    fn(store, result, tx);
    if (mode === 'readwrite') tx.abort();
  });
  h.desktop = desktop(NEW,policy('new.example.test'));
  await h.sync();
  assert.equal(h.status().employee_epoch, OLD);
  assert.deepEqual(await h.batch(), [{employee_epoch:OLD,events:[event(1)]}]);
  h.queue.transaction = transaction;
  await h.sync();
  assert.equal(h.status().employee_epoch, NEW);
  assert.deepEqual(await h.batch(), [{employee_epoch:OLD,events:[event(1),session(2)]}]);
});
