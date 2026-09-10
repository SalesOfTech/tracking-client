(function () {
  'use strict';
  if (window.__softTrackingV3) {
    try { window.__softTrackingV3.stop(); } catch (_) { /* Previous extension context may already be invalidated. */ }
  }
  let policy = {};
  let activeAt = Date.now();
  let sessionStart = null;
  let currentUrl = location.href;
  let stopped = false;
  const listeners = [];
  const now = () => Math.floor(Date.now() / 1000);
  function id() {
    return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
  }
  function enabled() { return !stopped && policy.policy_expires_at * 1000 > Date.now() && TrackingPrivacy.hostAllowed(location.href, policy.domains); }
  function send(type, target, timing) {
    if (!enabled()) return;
    const event = TrackingPrivacy.sanitize(Object.assign({event_id: id(), type, timestamp: now(), url: currentUrl, target}, timing), policy);
    if (!event) return;
    try {
      chrome.runtime.sendMessage({action: 'record', event}, response => {
        if (chrome.runtime.lastError || !response || !response.ok) policy = {};
      });
    } catch (_) { policy = {}; }
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
    if (sessionStart !== null) {
      const end = Math.min(now(), Math.floor(activeAt / 1000) + 30);
      if (end > sessionStart) send('web_session', {}, {timestamp: sessionStart, end_timestamp: end});
      sessionStart = null;
    }
  }
  function tick() {
    if (currentUrl !== location.href) {
      finishSession();
      currentUrl = location.href;
      send('navigation', {label: document.title.slice(0, 160)});
    }
    if (!enabled() || document.visibilityState !== 'visible' || !document.hasFocus() || Date.now() - activeAt > 30000 || !policy.tracking) { finishSession(); return; }
    if (sessionStart !== null && now() - sessionStart >= 15) finishSession();
    if (sessionStart === null) sessionStart = now();
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
  const storageChange = (changes, area) => { if (area === 'local' && changes.status) policy = changes.status.newValue && changes.status.newValue.policy || {}; };
  chrome.storage.onChanged.addListener(storageChange);
  chrome.runtime.sendMessage({action: 'status'}, response => { if (!chrome.runtime.lastError && response && response.ok) policy = response.status.policy || {}; });
  const timer = setInterval(tick, 1000);
  window.__softTrackingV3 = {stop() { finishSession(); stopped = true; clearInterval(timer); listeners.forEach(remove => remove()); chrome.storage.onChanged.removeListener(storageChange); }};
})();
