(function () {
  'use strict';
  if (window.__softTrackingV3) {
    try { window.__softTrackingV3.stop(); } catch (_) { /* Previous extension context may already be invalidated. */ }
  }
  let policy = {};
  let employeeEpoch = null;
  let sessionGeneration = null;
  let activeAt = Date.now();
  let session = null;
  let currentUrl = location.href;
  let stopped = false;
  const listeners = [];
  const pending = [];
  let sending = false;
  let deliveryBlocked = false;
  const now = () => Math.floor(Date.now() / 1000);
  function id() {
    return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
  }
  function enabled() { return !stopped && !deliveryBlocked && /^[a-f0-9]{32}$/.test(employeeEpoch || '') && policy.policy_expires_at * 1000 > Date.now() && TrackingPrivacy.hostAllowed(location.href, policy.domains); }
  function drain() {
    if (sending || !pending.length) return;
    sending = true;
    let finished = false;
    const timer = setTimeout(() => complete(false), 5000);
    function complete(ok) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      sending = false;
      if (!ok) { deliveryBlocked = true; return; }
      pending.shift();
      deliveryBlocked = false;
      drain();
    }
    try {
      chrome.runtime.sendMessage(pending[0], response => {
        complete(!chrome.runtime.lastError && response && response.ok === true);
      });
    } catch (_) { complete(false); }
  }
  function queue(message) {
    pending.push(message);
    if (pending.length >= 128) deliveryBlocked = true;
    drain();
  }
  function send(type, target, timing) {
    if (!enabled()) return;
    const event = TrackingPrivacy.sanitize(Object.assign({event_id: id(), type, timestamp: now(), url: currentUrl, target}, timing), policy);
    if (!event) return;
    queue({action: 'record', employee_epoch: employeeEpoch, event});
  }
  function describe(element, includeValue) {
    const tag = element.tagName.toLowerCase();
    const label = element.labels && element.labels.length ? Array.from(element.labels).map(item => item.textContent).join(' ') : '';
    const target = {
      tag, role: element.getAttribute('role') || '', name: element.getAttribute('name') || element.id || '',
      label: (label || element.getAttribute('aria-label') || element.getAttribute('title') || (['a', 'button'].includes(tag) ? element.textContent : '')).trim().slice(0, 160),
      input_type: ['select', 'textarea'].includes(tag) ? tag : element.getAttribute('type') || 'text',
      autocomplete: element.getAttribute('autocomplete') || '', href: tag === 'a' ? element.href : ''
    };
    if (includeValue && policy.field_values) {
      const candidate = ['checkbox', 'radio'].includes(target.input_type) ? String(element.checked) : element.value;
      const value = TrackingPrivacy.safeValue(candidate, target);
      if (value !== undefined) target.value = value;
    }
    return target;
  }
  function on(target, name, fn, options) {
    target.addEventListener(name, fn, options);
    listeners.push(() => target.removeEventListener(name, fn, options));
  }
  function finishSession() {
    if (session !== null) {
      if (!deliveryBlocked) {
        const end = Math.min(now(), Math.floor(activeAt / 1000) + 30, policy.policy_expires_at || now());
        const event = TrackingPrivacy.sanitize({event_id: session.event_id, type: 'web_session',
          timestamp: session.timestamp, end_timestamp: end, url: session.url}, policy);
        if (event) queue({action: 'session', employee_epoch: session.employee_epoch, event, close: false});
      }
      // The background closes the last durable checkpoint, independent of the new policy.
      queue({action: 'session-close', employee_epoch: session.employee_epoch, event_id: session.event_id});
      session = null;
    }
  }
  function tick() {
    drain();
    if (currentUrl !== location.href) {
      finishSession();
      currentUrl = location.href;
      send('navigation', {label: document.title.slice(0, 160)});
    }
    if (!enabled() || document.visibilityState !== 'visible' || !document.hasFocus() || Date.now() - activeAt > 30000 || !policy.tracking) { finishSession(); return; }
    if (session !== null && now() - session.timestamp >= 15) finishSession();
    if (session === null) session = {event_id: id(), timestamp: now(), url: currentUrl, employee_epoch: employeeEpoch};
    const event = TrackingPrivacy.sanitize({event_id: session.event_id, type: 'web_session', timestamp: session.timestamp,
      end_timestamp: Math.min(now(), Math.floor(activeAt / 1000) + 30), url: session.url}, policy);
    if (event) queue({action: 'session', employee_epoch: session.employee_epoch, event, close: false});
  }
  on(document, 'click', event => {
    if (!event.isTrusted || !enabled()) return;
    const path = event.composedPath ? event.composedPath() : [event.target];
    const element = path.find(item => item instanceof Element && item.matches('a[href],button,[role="button"],[role="tab"],input[type="submit"]'));
    if (element) send(element.tagName.toLowerCase() === 'a' ? 'link_click' : 'button_click', describe(element, false));
  }, true);
  on(document, 'change', event => {
    if (event.isTrusted && enabled() && event.target instanceof Element && event.target.matches('input,textarea,select')) send('field_change', describe(event.target, true));
  }, true);
  on(document, 'submit', event => { if (event.isTrusted && event.target instanceof Element) send('form_submit', describe(event.target, false)); }, true);
  for (const name of ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll']) on(document, name, event => { if (event.isTrusted) activeAt = Date.now(); }, {passive: true, capture: true});
  on(document, 'visibilitychange', tick);
  on(window, 'blur', finishSession);
  on(window, 'pagehide', finishSession);
  function applyStatus(status) {
    const next = status || {};
    const nextPolicy = next.policy || {};
    if (employeeEpoch !== next.employee_epoch || sessionGeneration !== next.browser_session_generation || JSON.stringify(policy) !== JSON.stringify(nextPolicy)) finishSession();
    employeeEpoch = next.employee_epoch || null;
    sessionGeneration = next.browser_session_generation;
    policy = nextPolicy;
    drain();
  }
  const storageChange = (changes, area) => { if (area === 'local' && changes.status) applyStatus(changes.status.newValue); };
  chrome.storage.onChanged.addListener(storageChange);
  chrome.runtime.sendMessage({action: 'status'}, response => { if (!chrome.runtime.lastError && response && response.ok) applyStatus(response.status); });
  const timer = setInterval(tick, 1000);
  window.__softTrackingV3 = {stop() { finishSession(); stopped = true; clearInterval(timer); listeners.forEach(remove => remove()); chrome.storage.onChanged.removeListener(storageChange); }};
})();
