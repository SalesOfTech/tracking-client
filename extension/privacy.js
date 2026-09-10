(function (root) {
  'use strict';
  const secretClues = /pass(?:word|wd)?|pwd|secret|token|api.?key|authorization|bearer|otp|one.?time|verification|2fa|cvv|cvc|credit.?card|card.?number|cc-|iban|heslo|parol|\u043f\u0430\u0440\u043e\u043b|\u0442\u043e\u043a\u0435\u043d|\u043a\u043e\u0434.*\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436|\u043d\u043e\u043c\u0435\u0440.*\u043a\u0430\u0440\u0442/i;
  const plain = value => typeof value === 'string' ? value.slice(0, 160) : '';
  function cleanUrl(raw) {
    try {
      const url = new URL(raw);
      if (!['http:', 'https:'].includes(url.protocol)) return '';
      return url.protocol + '//' + url.hostname.toLowerCase() + url.pathname.replace(/[A-Za-z0-9_=-]{32,}/g, '[redacted]').slice(0, 1024);
    } catch (_) { return ''; }
  }
  function hostAllowed(raw, domains) {
    try {
      const url = new URL(raw);
      if (!['http:', 'https:'].includes(url.protocol)) return false;
      return Array.isArray(domains) && domains.some(rawDomain => {
        try {
          const domain = new URL(rawDomain.includes('://') ? rawDomain : 'https://' + rawDomain).hostname.toLowerCase().replace(/^www\./, '');
          return url.hostname === domain || url.hostname.endsWith('.' + domain);
        } catch (_) { return false; }
      });
    } catch (_) { return false; }
  }
  function safeValue(value, target) {
    if (typeof value !== 'string' || value.length > 512) return undefined;
    if (!['text', 'search', 'email', 'tel', 'number', 'date', 'time', 'checkbox', 'radio', 'select', 'textarea'].includes(target.input_type)) return undefined;
    if (secretClues.test(Object.values(target).join(' ')) || secretClues.test(value)) return undefined;
    if (/^\s*[\d -]{4,25}\s*$/.test(value) || /(?:\d[ -]?){13,19}/.test(value) || /[A-Za-z0-9_=-]{32,}/.test(value)) return undefined;
    return value;
  }
  function sanitize(event, policy) {
    if (!event || !policy || !hostAllowed(event.url, policy.domains) || !Number.isInteger(event.timestamp)) return null;
    if (!/^[a-f0-9]{32}$/.test(event.event_id || '')) return null;
    const result = {event_id: event.event_id, type: event.type, timestamp: event.timestamp, url: cleanUrl(event.url)};
    if (event.type === 'web_session') {
      if (!policy.tracking || !Number.isInteger(event.end_timestamp) || event.end_timestamp < event.timestamp || event.end_timestamp - event.timestamp > 3600) return null;
      result.end_timestamp = event.end_timestamp;
      return result;
    }
    if (!policy.interactions || !['link_click', 'button_click', 'field_change', 'form_submit', 'navigation'].includes(event.type)) return null;
    const target = event.target || {};
    result.target = {};
    for (const key of ['tag', 'role', 'name', 'label', 'input_type', 'autocomplete']) result.target[key] = plain(target[key]);
    result.target.href = cleanUrl(target.href);
    if (policy.field_values && event.type === 'field_change') {
      const value = safeValue(target.value, result.target);
      if (value !== undefined) result.target.value = value;
    }
    return result;
  }
  const api = Object.freeze({cleanUrl, hostAllowed, safeValue, sanitize});
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.TrackingPrivacy = api;
})(globalThis);
