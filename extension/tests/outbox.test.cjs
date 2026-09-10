require('fake-indexeddb/auto');
const assert = require('node:assert/strict');
const test = require('node:test');
const BrowserOutbox = require('../outbox.js');
const event = n => ({event_id:n.toString(16).padStart(32,'0'),type:'navigation',timestamp:1700000000,url:'https://canva.com/'});

test('browser outbox survives reopening and only removes explicit confirmations',async()=>{
  let queue=new BrowserOutbox('restart');
  await queue.put(event(1)); await queue.put(event(2));
  await queue.close();
  queue=new BrowserOutbox('restart');
  assert.equal((await queue.batch()).length,2);
  await queue.acknowledge([event(1).event_id]);
  assert.deepEqual(await queue.batch(),[event(2)]);
  await queue.close();
});
test('same event ID cannot overwrite a queued payload',async()=>{
  const queue=new BrowserOutbox('immutable');
  await queue.put(event(1)); await queue.put(event(1));
  await assert.rejects(queue.put({...event(1),url:'https://sejda.com/'}));
  assert.deepEqual(await queue.batch(),[event(1)]);
  await queue.close();
});
test('rejections remain stored and do not block later events',async()=>{
  const queue=new BrowserOutbox('rejections');
  for(let n=1;n<=105;n++) await queue.put(event(n));
  assert.equal((await queue.batch()).length,100);
  await queue.acknowledge([],{[event(1).event_id]:'policy_disabled'});
  assert.equal((await queue.batch())[0].event_id,event(2).event_id);
  assert.deepEqual(await queue.counts(),{pending:104,rejected:1});
  await queue.close();
});
