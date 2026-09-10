'use strict';
const ACTIONS = new Set(['status', 'enroll', 'check', 'repair', 'retry', 'resume', 'open', 'preferences', 'install', 'launch', 'ready']);
const LANGUAGES = new Set(['en', 'ru', 'cs', 'uz']);
function validate(action, input = {}) {
  if (!ACTIONS.has(action) || !input || typeof input !== 'object' || Array.isArray(input)) throw Error('invalid_request');
  if (Buffer.byteLength(JSON.stringify(input)) > 4096) throw Error('invalid_request');
  const allowed = {
    enroll: ['code', 'key'], preferences: ['language', 'theme'], open: ['target'], install: ['code'],
  }[action] || [];
  if (Object.keys(input).some(key => !allowed.includes(key))) throw Error('invalid_request');
  if (action === 'enroll' && (!/^[a-f0-9]{64}$/.test(input.key) || !/^[a-f0-9]{32}$/.test(input.code))) throw Error('invalid_employee_key');
  if (action === 'install' && !/^[a-f0-9]{32}$/.test(input.code)) throw Error('setup_code_required');
  if (action === 'preferences' && ((!LANGUAGES.has(input.language) && input.language !== undefined) || (!['system', 'light', 'dark'].includes(input.theme) && input.theme !== undefined))) throw Error('invalid_request');
  if (action === 'open' && !['guide', 'extension', 'dashboard'].includes(input.target)) throw Error('invalid_request');
  return {action, input};
}
module.exports = {validate};
