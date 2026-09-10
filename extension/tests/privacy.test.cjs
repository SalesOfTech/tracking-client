const assert = require('node:assert/strict');
const test = require('node:test');
const privacy = require('../privacy.js');
const policy = {tracking:true, interactions:true, field_values:true, domains:['canva.com','sejda.com']};
const event = (target = {}) => ({event_id:'a'.repeat(32),type:'field_change',timestamp:1700000000,url:'https://www.canva.com/design?token=secret',target});

test('domains include www and subdomains without suffix confusion', () => {
  assert.equal(privacy.hostAllowed('https://www.canva.com/', policy.domains), true);
  assert.equal(privacy.hostAllowed('https://editor.sejda.com/', policy.domains), true);
  for (const url of ['https://canva.com.evil.test','https://evilcanva.com','javascript:alert(1)']) assert.equal(privacy.hostAllowed(url, policy.domains), false);
});
test('URLs drop credentials, query and fragment', () => {
  assert.equal(privacy.cleanUrl('https://user:pass@www.canva.com/editor?token=secret#secret'),'https://www.canva.com/editor');
  assert.equal(privacy.cleanUrl('data:text/plain,private'),'');
});
test('ordinary values are opt-in and metadata survives redaction', () => {
  const input=event({input_type:'text',label:'Project name',value:'Spring campaign'});
  assert.equal(privacy.sanitize(input,policy).target.value,'Spring campaign');
  assert.equal(privacy.sanitize(input,{...policy,field_values:false}).target.value,undefined);
  assert.equal(privacy.sanitize(input,{...policy,interactions:false}),null);
});
test('sensitive fields and values never enter the payload', () => {
  for (const name of ['password','api-key','token','one-time-code','cc-number','cvv','heslo']) {
    const result=privacy.sanitize(event({input_type:'text',name,value:'private content'}),policy);
    assert.equal(result.target.value,undefined,name);
  }
  for (const value of ['123456','4111 1111 1111 1111','Card: 4111111111111111','Bearer test','a'.repeat(64)]) {
    assert.equal(privacy.sanitize(event({input_type:'text',label:'Unknown',value}),policy).target.value,undefined,value);
  }
  assert.equal(privacy.sanitize(event({input_type:'file',value:'C:/private/file'}),policy).target.value,undefined);
});
test('unknown fields are not copied', () => {
  const input={...event({input_type:'text',label:'Name'}),html:'private',cookies:'private',employee_key:'private'};
  const clean=privacy.sanitize(input,policy);
  for(const key of ['html','cookies','employee_key']) assert.equal(clean[key],undefined);
});
