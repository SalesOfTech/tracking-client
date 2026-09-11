require('fake-indexeddb/auto');
const assert = require('node:assert/strict');
const test = require('node:test');
const BrowserOutbox = require('../outbox.js');
const OLD = 'a'.repeat(32);
const NEW = 'b'.repeat(32);
let sequence = 0;
const event = n => ({event_id:n.toString(16).padStart(32,'0'),type:'navigation',timestamp:1700000000,url:'https://example.test/'});
const session = (n, end = 1700000030) => ({...event(n),type:'web_session',end_timestamp:end});
function queueFor(t) {
  const name = `outbox-unit-${++sequence}`;
  const queue = new BrowserOutbox(name);
  t.after(() => queue.close());
  return {queue, name};
}
function rows(queue) {
  return queue.transaction('readonly', (store, result, tx) => {
    const events = store.getAll();
    events.onsuccess = () => {
      const sessions = tx.objectStore('sessions').getAll();
      sessions.onsuccess = () => result({events:events.result, sessions:sessions.result});
    };
  });
}
function legacyDatabase(name, version, events, sessions = []) {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(name, version);
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore('events', {keyPath:'event_id'});
      if (version > 1) store.createIndex('state', 'state');
      for (const event of events) store.add(event);
      if (version > 1) {
        const active = request.result.createObjectStore('sessions', {keyPath:'event_id'});
        for (const session of sessions) active.add(session);
      }
    };
    request.onsuccess = () => { request.result.close(); resolve(); };
    request.onerror = () => reject(request.error);
  });
}

test('browser outbox survives reopening and only removes explicit epoch confirmations', async t => {
  let {queue, name} = queueFor(t);
  await queue.put(event(1), OLD);
  await queue.put(event(2), OLD);
  await queue.close();
  queue = new BrowserOutbox(name);
  t.after(() => queue.close());
  assert.deepEqual(await queue.epochBatches(), [{employee_epoch:OLD,events:[event(1),event(2)]}]);
  await queue.acknowledge([event(1).event_id], {}, OLD);
  assert.deepEqual(await queue.batch(), [event(2)]);
});
test('same event ID cannot overwrite a payload or move to another employee', async t => {
  const {queue} = queueFor(t);
  await queue.put(event(1), OLD);
  await queue.put(event(1), OLD);
  await assert.rejects(queue.put({...event(1),url:'https://other.test/'}, OLD));
  await assert.rejects(queue.put(event(1), NEW));
  assert.deepEqual(await queue.epochBatches(), [{employee_epoch:OLD,events:[event(1)]}]);
});

test('stored custody retry is read-only, epoch-bound, and is not server confirmation', async t => {
  const {queue} = queueFor(t);
  assert.equal(await queue.storedEvent(event(1), OLD), false);
  await queue.put(event(1), OLD);
  const before = await rows(queue);
  assert.equal(await queue.storedEvent(event(1), OLD), true);
  await assert.rejects(queue.storedEvent(event(1), NEW));
  await assert.rejects(queue.storedEvent({...event(1),url:'https://other.test/'}, OLD));
  assert.deepEqual(await rows(queue), before);
  await queue.acknowledge([], {[event(1).event_id]:'denied'}, OLD);
  assert.equal(await queue.storedEvent(event(1), OLD), true);
  assert.deepEqual(await queue.counts(), {pending:0,rejected:1});
  await queue.acknowledge([event(1).event_id], {}, OLD);
  assert.equal(await queue.storedEvent(event(1), OLD), false);
});

test('mixed employee queues form homogeneous bounded batches', async t => {
  const {queue} = queueFor(t);
  for (let n = 1; n <= 105; n++) {
    await queue.put(event(n), OLD);
    await queue.put(event(n + 200), NEW);
  }
  const batches = await queue.epochBatches();
  assert.equal(batches.length, 2);
  for (const batch of batches) {
    assert.equal(batch.events.length, 100);
    assert.ok(batch.events.every(row => (parseInt(row.event_id,16) < 200) === (batch.employee_epoch === OLD)));
    assert.ok(batch.events.every(row => !('employee_epoch' in row)));
  }
  await queue.acknowledge(batches.find(row => row.employee_epoch === OLD).events.map(row => row.event_id), {}, OLD);
  assert.deepEqual((await queue.epochBatches()).map(row => [row.employee_epoch,row.events.length]), [[OLD,5],[NEW,100]]);
});

test('wrong employee ACK rolls back the whole transaction including earlier deletes', async t => {
  const {queue} = queueFor(t);
  await queue.put(event(1), OLD);
  await queue.put(event(2), NEW);
  const before = await rows(queue);
  await assert.rejects(queue.acknowledge([event(1).event_id,event(2).event_id], {}, OLD));
  await assert.rejects(queue.acknowledge([], {[event(1).event_id]:'denied'}, NEW));
  assert.deepEqual(await rows(queue), before);
});

test('rejections retain employee ownership and do not block later events', async t => {
  const {queue} = queueFor(t);
  for (let n = 1; n <= 105; n++) await queue.put(event(n), OLD);
  await queue.put(event(200), NEW);
  await queue.acknowledge([], {[event(1).event_id]:'policy_disabled'}, OLD);
  assert.equal((await queue.batch())[0].event_id, event(2).event_id);
  assert.deepEqual(await queue.counts(), {pending:105,rejected:1});
  await queue.retryRejected();
  const first = (await rows(queue)).events.find(row => row.event_id === event(1).event_id);
  assert.equal(first.employee_epoch, OLD);
  assert.deepEqual(first.event, event(1));
  assert.deepEqual(await queue.counts(), {pending:106,rejected:0});
});

test('v1 legacy rows migrate to original employee, never the new active pointer', async t => {
  const name = `legacy-v1-${++sequence}`;
  await legacyDatabase(name, 1, [event(1),event(2)]);
  const queue = new BrowserOutbox(name);
  t.after(() => queue.close());
  await assert.rejects(queue.epochBatches());
  await queue.transitionEpoch(OLD, NEW, true);
  assert.deepEqual(await queue.epochBatches(), [{employee_epoch:OLD,events:[event(1),event(2)]}]);
  assert.deepEqual(await queue.batch(), [event(1),event(2)]);
});

test('legacy rejected events and active sessions retain original owner during recovery', async t => {
  const name = `legacy-v2-${++sequence}`;
  await legacyDatabase(name, 2, [{event_id:event(1).event_id,event:event(1),state:'rejected',error:'denied'}],
    [{event_id:session(2).event_id,event:session(2),state:'pending'}]);
  const queue = new BrowserOutbox(name);
  t.after(() => queue.close());
  await queue.transitionEpoch(OLD, NEW, true);
  assert.deepEqual(await queue.epochBatches(), [{employee_epoch:OLD,events:[session(2)]}]);
  const stored = await rows(queue);
  assert.ok(stored.events.every(row => row.employee_epoch === OLD));
  assert.deepEqual(stored.sessions, [{event_id:session(2).event_id,employee_epoch:OLD,closed:true}]);
  assert.deepEqual(await queue.counts(), {pending:1,rejected:1});
});

test('employee transition closes old sessions and leaves new employee sessions active', async t => {
  const {queue} = queueFor(t);
  await queue.put(event(1), OLD);
  await queue.checkpointSession(session(2), OLD);
  await queue.checkpointSession(session(3), NEW);
  await queue.transitionEpoch(OLD, NEW);
  assert.deepEqual(await queue.epochBatches(), [{employee_epoch:OLD,events:[event(1),session(2)]}]);
  const active = (await rows(queue)).sessions.find(row => row.event_id === session(3).event_id);
  assert.equal(active.employee_epoch, NEW);
  assert.equal(active.closed, undefined);
});

test('restart recovers last committed session once and ignores delayed closed checkpoints', async t => {
  let {queue,name} = queueFor(t);
  await queue.checkpointSession(session(1), OLD);
  await queue.close();
  queue = new BrowserOutbox(name);
  t.after(() => queue.close());
  await queue.transitionEpoch(OLD, OLD, true);
  await queue.transitionEpoch(OLD, OLD, true);
  assert.equal(await queue.checkpointSession(session(1,1700000090), OLD, true, true), true);
  assert.deepEqual(await queue.batch(), [session(1)]);
  await queue.acknowledge([session(1).event_id], {}, OLD);
  assert.equal(await queue.closeSession(session(1).event_id, OLD), true);
  assert.equal(await queue.checkpointSession(session(1,1700000099), OLD, false, true), true);
  assert.deepEqual(await queue.batch(), []);
  assert.equal((await rows(queue)).sessions[0].closed, true);
});

test('session identity and monotonic checkpoints cannot be changed by delayed writers', async t => {
  const {queue} = queueFor(t);
  await queue.checkpointSession(session(1), OLD);
  for (const invalid of [session(1,1700000010), {...session(1),timestamp:1700000001}, {...session(1),url:'https://other.test/'}]) {
    await assert.rejects(queue.checkpointSession(invalid, OLD));
  }
  await assert.rejects(queue.checkpointSession(session(1), NEW));
  assert.equal(await queue.closeSession(session(1).event_id, NEW), false);
  assert.equal(await queue.checkpointSession(session(2), OLD, false, true), false);
  assert.deepEqual((await rows(queue)).sessions[0].event, session(1));
});

test('aborted writes preserve queued rows and active sessions for retry', async t => {
  const {queue} = queueFor(t);
  await queue.put(event(1), OLD);
  await queue.checkpointSession(session(2), OLD);
  const before = await rows(queue);
  const transaction = queue.transaction.bind(queue);
  queue.transaction = (mode, fn) => transaction(mode, (store, result, tx) => {
    fn(store, result, tx);
    if (mode === 'readwrite') tx.abort();
  });
  for (const operation of [() => queue.put(event(3), NEW), () => queue.acknowledge([event(1).event_id], {}, OLD),
    () => queue.transitionEpoch(OLD, NEW), () => queue.closeSession(session(2).event_id, OLD)]) {
    await assert.rejects(operation());
    assert.deepEqual(await rows(queue), before);
  }
  queue.transaction = transaction;
  await queue.transitionEpoch(OLD, NEW);
  assert.deepEqual(await queue.epochBatches(), [{employee_epoch:OLD,events:[event(1),session(2)]}]);
});
