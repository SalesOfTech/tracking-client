(function (root) {
  'use strict';
  const languages = ['ru', 'en', 'cs', 'uz'];
  const russianRegions = new Set(['RU','BY','KZ','KG','TJ','AM','AZ','MD','TM','UZ']);
  function selectLanguage(tags, override) {
    if (languages.includes(override)) return override;
    const parts = String((tags || [])[0] || '').split(/[.@]/)[0].replace(/_/g, '-').split('-');
    const primary = parts[0].toLowerCase();
    if (primary === 'ru' || primary === 'cs' || primary === 'uz') return primary;
    const country = parts.slice(1).find(part => /^[a-z]{2}$/i.test(part));
    if (country) return russianRegions.has(country.toUpperCase()) ? 'ru' : 'en';
    return ['ru','be','kk','ky','tg','hy','az','tk'].includes(primary) ? 'ru' : 'en';
  }
  const api = {languages, selectLanguage};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.TrackingLanguage = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
