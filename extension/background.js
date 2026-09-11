'use strict';
if (typeof importScripts === 'function') importScripts('privacy.js', 'outbox.js');
const HOST = 'com.soft.tracking';
const action = chrome.action || chrome.browserAction;
let syncing = null;
let checkpointing = Promise.resolve();
let recovered = false;
let epochPolicies = null;
const sessionGeneration = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
let state = {policy: {}, error: 'Desktop application is not connected'};
const outbox = new TrackingOutbox();
function checkpoint(fn) {
  const operation = checkpointing.then(fn);
  checkpointing = operation.catch(() => {});
  return operation;
}
function validEpoch(value) { return typeof value === 'string' && /^[a-f0-9]{32}$/.test(value); }
function plainObject(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function storageGet(key) {
  return new Promise((resolve, reject) => chrome.storage.local.get(key, value => {
    const error = chrome.runtime.lastError;
    if (error) reject(new Error(error.message)); else resolve(value);
  }));
}
function storageSet(value) {
  return new Promise((resolve, reject) => chrome.storage.local.set(value, () => {
    const error = chrome.runtime.lastError;
    if (error) reject(new Error(error.message)); else resolve();
  }));
}
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
  const stored = await storageGet('browser_profile');
  let profile = stored.browser_profile;
  if (!validEpoch(profile)) {
    profile = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
    await storageSet({browser_profile:profile});
  }
  let error='';
  try { await outbox.counts(); } catch (_) { error='storage_error'; }
  return {profile, error, epoch_protocol:recovered ? 1 : 0, version:chrome.runtime.getManifest().version, family:await browserFamily()};
}
async function browserFamily() {
  const ua = typeof navigator.userAgent === 'string' ? navigator.userAgent : '';
  const brandData = navigator.userAgentData && navigator.userAgentData.brands;
  const brands = Array.isArray(brandData) ? brandData.filter(item => item && typeof item.brand === 'string').map(item => item.brand) : [];
  if (/YaBrowser\//i.test(ua) || brands.some(name => /Yandex/i.test(name))) return 'Yandex';
  if (/(?:OPR|Opera)\//i.test(ua) || brands.some(name => /Opera/i.test(name))) return 'Opera';
  if (/Edg(?:e|A|iOS)?\//i.test(ua) || brands.includes('Microsoft Edge')) return 'Edge';
  if (/Vivaldi\//i.test(ua) || brands.includes('Vivaldi')) return 'Vivaldi';
  if (/Brave\//i.test(ua) || brands.includes('Brave')) return 'Brave';
  try { if (navigator.brave && await navigator.brave.isBrave()) return 'Brave'; } catch (_) { /* Brand API may be unavailable. */ }
  if (/Firefox\//i.test(ua)) return 'Firefox';
  if (/Chromium\//i.test(ua)) return 'Chromium';
  return /Chrome\//i.test(ua) ? 'Chrome' : 'Chromium';
}
function applyDesktopStatus(next) {
  return checkpoint(async () => {
      if (!plainObject(next) || !plainObject(next.policy)) throw new Error('Invalid desktop status');
      if (validEpoch(next.employee_epoch) && validEpoch(next.legacy_employee_epoch)) {
        if (epochPolicies === null) {
          const saved = (await storageGet('employee_epoch_policies')).employee_epoch_policies;
          epochPolicies = plainObject(saved) ? saved : {};
        }
        const policies = {};
        for (const [epoch, entry] of Object.entries(epochPolicies)) {
          if (validEpoch(epoch) && plainObject(entry) && plainObject(entry.policy)) {
            policies[epoch] = {policy: entry.policy, retired_at: entry.retired_at};
            if (epoch !== next.employee_epoch && !Number.isInteger(entry.retired_at)) policies[epoch].retired_at = Math.floor(Date.now() / 1000);
          }
        }
        policies[next.employee_epoch] = {policy: next.policy, retired_at: null};
        await storageSet({employee_epoch_policies: policies});
        if (!recovered || state.employee_epoch !== next.employee_epoch) {
          await outbox.transitionEpoch(next.legacy_employee_epoch, next.employee_epoch, !recovered);
        }
        epochPolicies = policies;
        recovered = true;
      } else if (next.identity) throw new Error('Desktop employee epoch upgrade required');
      state = next;
      state.browser_session_generation = sessionGeneration;
      await saveStatus();
  });
}
async function syncOnce() {
  try {
    const wasReady = recovered;
    const status = await native({action: 'status', browser:await browserReceipt()});
    await applyDesktopStatus(status.status);
    // Advertise readiness only after durable migration/recovery has committed.
    if (!wasReady && recovered) {
      const ready = await native({action: 'status', browser:await browserReceipt()});
      await applyDesktopStatus(ready.status);
    }
    const previous=await new Promise(resolve=>chrome.storage.local.get('retry_generation',resolve));
    if (state.retry_generation && previous.retry_generation!==state.retry_generation) {
      await outbox.retryRejected();
      await new Promise(resolve=>chrome.storage.local.set({retry_generation:state.retry_generation},resolve));
    }
    const batches = validEpoch(state.employee_epoch) ? await outbox.epochBatches() : [];
    for (const {employee_epoch, events} of batches) {
      const result = await native({action: 'store', employee_epoch, events});
      if (result.employee_epoch !== employee_epoch) throw new Error('Invalid employee epoch confirmation');
      const ids = result.confirmed_event_ids;
      const rejected = result.rejected || {};
      const sent = new Set(events.map(event => event.event_id));
      if (!Array.isArray(ids) || ids.some(id => !sent.has(id)) || new Set(ids).size !== ids.length) throw new Error('Invalid database confirmation');
      if (typeof rejected !== 'object' || Array.isArray(rejected) || Object.keys(rejected).some(id => !sent.has(id) || ids.includes(id))) throw new Error('Invalid rejection list');
      await outbox.acknowledge(ids, rejected, employee_epoch);
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
    state = Object.assign({}, state, {policy: {}, error: error.message});
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
    if (!plainObject(message) || !plainObject(sender)) return {ok: false};
    if (message.action === 'status') {
      await sync();
      return {ok: true, status: state};
    }
    if (!['record', 'session', 'session-close'].includes(message.action) || !sender.tab || sender.id !== chrome.runtime.id) return {ok: false};
    if (message.action === 'session-close') {
      if (!validEpoch(message.employee_epoch) || !validEpoch(message.event_id)) return {ok: false};
      return checkpoint(async () => {
        const stored = await outbox.closeSession(message.event_id, message.employee_epoch);
        return {ok: stored, stored, employee_epoch: message.employee_epoch};
      });
    }
    if (!plainObject(message.event) || typeof message.event.url !== 'string' || typeof sender.url !== 'string') return {ok: false};
    if (message.action === 'session' && (message.event.type !== 'web_session' || typeof message.close !== 'boolean')) return {ok: false};
    const source = new URL(sender.url);
    const page = new URL(message.event.url);
    if (source.origin !== page.origin) return {ok: false};
    return checkpoint(async () => {
      const epoch = message.employee_epoch === undefined ? state.legacy_employee_epoch : message.employee_epoch;
      if (!validEpoch(epoch)) return {ok: false, error: 'Employee epoch required'};
      if (message.action === 'record' && await outbox.storedEvent(message.event, epoch)) {
        return {ok: true, stored: true, employee_epoch: epoch};
      }
      if (message.action === 'session' && await outbox.closedSession(message.event.event_id, epoch)) {
        return {ok: true, stored: true, employee_epoch: epoch};
      }
      const retired = epoch !== state.employee_epoch;
      let policy = currentPolicy();
      if (retired) {
        const saved = epochPolicies && epochPolicies[epoch];
        if (!saved || !Number.isInteger(saved.retired_at) || !Number.isInteger(saved.policy.policy_expires_at) || !Number.isInteger(message.event.timestamp)
            || message.event.timestamp > saved.retired_at || message.event.timestamp >= saved.policy.policy_expires_at) {
          // A known closed session can be acknowledged even after its policy expires.
          if (message.action === 'session' && message.close) {
            const stored = await outbox.closeSession(message.event.event_id, epoch);
            return {ok: stored, stored, employee_epoch: epoch};
          }
          return {ok: false, error: 'Employee changed; refresh this page'};
        }
        policy = saved.policy;
      }
      const event = TrackingPrivacy.sanitize(message.event, policy);
      if (!event && message.action === 'session' && message.close) {
        const stored = await outbox.closeSession(message.event.event_id, epoch);
        return {ok: stored, stored, employee_epoch: epoch};
      }
      if (!event) return {ok: false, error: 'Collection is disabled for this page'};
      if (message.action === 'session') {
        const stored = await outbox.checkpointSession(event, epoch, message.close, retired);
        return {ok: stored, stored, employee_epoch: epoch};
      } else await outbox.put(event, epoch);
      return {ok: true, stored: true, employee_epoch: epoch};
    });
  })().then(respond).catch(() => {
    state = Object.assign({}, state, {policy: {}, error: 'Cannot save activity locally. Collection paused.'});
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
