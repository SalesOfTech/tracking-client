'use strict';
if (typeof importScripts === 'function') importScripts('privacy.js', 'outbox.js');
const HOST = 'com.soft.tracking';
const action = chrome.action || chrome.browserAction;
let syncing = null;
let state = {policy: {}, error: 'Desktop application is not connected'};
const outbox = new TrackingOutbox();
function saveStatus() {
  return new Promise((resolve, reject) => chrome.storage.local.set({status: state}, () => {
    const error = chrome.runtime.lastError;
    if (error) reject(new Error(error.message)); else resolve();
  }));
}
function native(message) {
  return new Promise((resolve, reject) => {
    const timer=setTimeout(()=>reject(new Error('Desktop connection timed out')),20000);
    chrome.runtime.sendNativeMessage(HOST, message, response => {
      clearTimeout(timer);
      const error = chrome.runtime.lastError;
      if (error || !response || response.ok !== true) reject(new Error(error ? error.message : 'Desktop application rejected request'));
      else resolve(response);
    });
  });
}
function currentPolicy() {
  return state.policy && state.policy.policy_expires_at * 1000 > Date.now() ? state.policy : {};
}
function badge() {
  const active = currentPolicy().tracking || currentPolicy().interactions;
  action.setBadgeText({text: state.error ? '!' : active ? 'ON' : 'OFF'});
  action.setBadgeBackgroundColor({color: state.error ? '#c74d45' : active ? '#11866c' : '#6c7471'});
}
function sync() {
  if (!syncing) syncing = syncOnce().finally(() => { syncing = null; });
  return syncing;
}
async function browserReceipt() {
  const stored = await new Promise(resolve => chrome.storage.local.get('browser_profile', resolve));
  let profile = stored.browser_profile;
  if (!/^[a-f0-9]{32}$/.test(profile || '')) {
    profile = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
    await new Promise(resolve => chrome.storage.local.set({browser_profile:profile}, resolve));
  }
  const ua = navigator.userAgent;
  let error='';
  try { await outbox.counts(); } catch (_) { error='storage_error'; }
  return {profile, error, version:chrome.runtime.getManifest().version, family:/Edg\//.test(ua)?'Edge':/OPR\//.test(ua)?'Opera':/Chrome\//.test(ua)?'Chrome':'Chromium'};
}
async function syncOnce() {
  try {
    const status = await native({action: 'status', browser:await browserReceipt()});
    state = status.status;
    const previous=await new Promise(resolve=>chrome.storage.local.get('retry_generation',resolve));
    if (state.retry_generation && previous.retry_generation!==state.retry_generation) {
      await outbox.retryRejected();
      await new Promise(resolve=>chrome.storage.local.set({retry_generation:state.retry_generation},resolve));
    }
    const events = await outbox.batch();
    if (events.length) {
      const result = await native({action: 'store', events});
      const ids = result.confirmed_event_ids;
      const rejected = result.rejected || {};
      const sent = new Set(events.map(event => event.event_id));
      if (!Array.isArray(ids) || ids.some(id => !sent.has(id)) || new Set(ids).size !== ids.length) throw new Error('Invalid database confirmation');
      if (typeof rejected !== 'object' || Array.isArray(rejected) || Object.keys(rejected).some(id => !sent.has(id) || ids.includes(id))) throw new Error('Invalid rejection list');
      await outbox.acknowledge(ids, rejected);
    }
    state.browser_queue = await outbox.counts();
    await saveStatus();
    if (state.extension_version && state.extension_version !== chrome.runtime.getManifest().version) {
      const last = await new Promise(resolve => chrome.storage.local.get('reload_version',resolve));
      if (last.reload_version !== state.extension_version) {
        await new Promise(resolve => chrome.storage.local.set({reload_version:state.extension_version},resolve));
        chrome.runtime.reload();
      }
    }
  } catch (error) {
    state = {policy: {}, error: error.message};
    await saveStatus().catch(() => {});
  } finally {
    badge();
  }
}
action.onClicked.addListener(() => {
  native({action:'open'}).catch(() => {
    chrome.tabs.create({url:chrome.runtime.getURL('setup.html')+'#connection'});
  });
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  (async () => {
    if (message.action === 'status') {
      await sync();
      return {ok: true, status: state};
    }
    if (message.action !== 'record' || !sender.tab || sender.id !== chrome.runtime.id) return {ok: false};
    const source = new URL(sender.url);
    const page = new URL(message.event.url);
    if (source.origin !== page.origin) return {ok: false};
    const event = TrackingPrivacy.sanitize(message.event, currentPolicy());
    if (!event) return {ok: false, error: 'Collection is disabled for this page'};
    await outbox.put(event);
    return {ok: true, stored: true};
  })().then(respond).catch(() => {
    state = {policy: {}, error: 'Cannot save activity locally. Collection paused.'};
    badge();
    saveStatus().catch(() => {});
    respond({ok: false, error: state.error});
  });
  return true;
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'tracking-sync') sync(); });
function injectOpenTabs() {
  chrome.tabs.query({}, tabs => {
    for (const tab of tabs) {
      if (!tab.id || !TrackingPrivacy.hostAllowed(tab.url || '', currentPolicy().domains)) continue;
      if (chrome.scripting) chrome.scripting.executeScript({target:{tabId:tab.id},files:['privacy.js','content.js']}).catch(()=>{});
      else chrome.tabs.executeScript(tab.id,{file:'privacy.js'},()=>{
        if (!chrome.runtime.lastError) chrome.tabs.executeScript(tab.id,{file:'content.js'},()=>{void chrome.runtime.lastError;});
      });
    }
  });
}
chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create('tracking-sync', {periodInMinutes: 1}); sync().then(injectOpenTabs); });
chrome.runtime.onStartup.addListener(() => { chrome.alarms.create('tracking-sync', {periodInMinutes: 1}); sync(); });
sync();
