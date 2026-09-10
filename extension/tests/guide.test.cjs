const {test} = require('node:test');
const assert = require('node:assert/strict');
const {selectLanguage, guides} = require('../setup.js');
const cases = require('./locale-cases.json');

test('guide and native language selection use the same cases', () => {
  for (const item of cases) assert.equal(selectLanguage(item.tags, item.override), item.expected, JSON.stringify(item));
});
test('all four guides have matching sections, steps and content', () => {
  assert.deepEqual(Object.keys(guides).sort(), ['cs','en','ru','uz']);
  for (const [language, guide] of Object.entries(guides)) {
    assert.deepEqual(Object.keys(guide), Object.keys(guides.en));
    assert.deepEqual(guide.sections.map(([id,,steps]) => [id, steps.length]), guides.en.sections.map(([id,,steps]) => [id, steps.length]));
    for (const [,title,steps] of guide.sections) {
      assert.ok(title.length > 3, language);
      assert.ok(steps.every(step => step.length > 40), language);
    }
    assert.match(JSON.stringify(guide), /chrome:\/\/extensions/);
    assert.match(JSON.stringify(guide), /edge:\/\/extensions/);
  }
});
