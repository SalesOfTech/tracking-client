'use strict';
const ACTIONS = new Set(['status', 'enroll', 'switch-employee', 'stop-agent', 'browser-page', 'copy-browser-page', 'check', 'check-update', 'repair', 'retry', 'resume', 'open', 'preferences', 'install', 'launch', 'ready']);
const BROWSER_PAGES = Object.freeze({
  Chrome: 'chrome://extensions', Edge: 'edge://extensions', Yandex: 'browser://extensions',
  Opera: 'opera://extensions', Brave: 'brave://extensions', Vivaldi: 'vivaldi://extensions',
  Chromium: 'chrome://extensions', Firefox: 'about:addons',
});
const LANGUAGES = new Set(['en', 'ru', 'cs', 'uz']);
function validate(action, input = {}) {
  if (!ACTIONS.has(action) || !input || typeof input !== 'object' || Array.isArray(input)) throw Error('invalid_request');
  if (Buffer.byteLength(JSON.stringify(input)) > 4096) throw Error('invalid_request');
  const allowed = {
    enroll: ['code', 'key'], 'switch-employee': ['key'], 'browser-page': ['browser'], 'copy-browser-page': ['browser'], preferences: ['language'], open: ['target'], install: ['code'],
  }[action] || [];
  if (Object.keys(input).some(key => !allowed.includes(key))) throw Error('invalid_request');
  if (action === 'enroll' && (!/^[a-f0-9]{64}$/.test(input.key) || (input.code && !/^[a-f0-9]{32}$/.test(input.code)))) throw Error('invalid_employee_key');
  if (action === 'install' && !/^(?:[a-f0-9]{32}|[a-f0-9]{64})$/.test(input.code)) throw Error('setup_code_required');
  if (action === 'switch-employee' && !/^[a-f0-9]{64}$/.test(input.key)) throw Error('invalid_employee_key');
  if (action === 'browser-page' && !['Chrome', 'Edge', 'Yandex', 'Opera', 'Brave', 'Vivaldi', 'Chromium', 'Firefox'].includes(input.browser)) throw Error('invalid_request');
  if (action === 'copy-browser-page' && (typeof input.browser !== 'string' || !Object.hasOwn(BROWSER_PAGES, input.browser))) throw Error('invalid_request');
  if (action === 'preferences' && !LANGUAGES.has(input.language)) throw Error('invalid_request');
  if (action === 'open' && !['guide', 'extension', 'dashboard'].includes(input.target)) throw Error('invalid_request');
  return {action, input};
}
module.exports = {validate, BROWSER_PAGES};
