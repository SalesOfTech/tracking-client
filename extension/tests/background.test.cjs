const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');

test('concurrent status requests await one native connection; toolbar opens the app without a popup', async()=>{
  let listener,clicked;
  const calls=[],badges=[],storage={};
  const status={identity:{company_name:'Fixture',user_name:'Test'},policy:{},error:'',queue:{pending:0,rejected:0}};
  const chrome={
    action:{onClicked:{addListener:fn=>clicked=fn},setBadgeText:value=>badges.push(value.text),setBadgeBackgroundColor:()=>{}},
    runtime:{getManifest:()=>({version:'3.0.4.60000'}),getURL:name=>'chrome-extension://fixture/'+name,
      sendNativeMessage:(host,message,reply)=>{calls.push(message);setTimeout(()=>reply({ok:true,status}),20);},
      onMessage:{addListener:fn=>listener=fn},onInstalled:{addListener:()=>{}},onStartup:{addListener:()=>{}}},
    storage:{local:{get:(key,reply)=>reply({[key]:storage[key]}),set:(value,reply)=>{Object.assign(storage,value);reply();}}},
    alarms:{onAlarm:{addListener:()=>{}}},tabs:{create:()=>{throw new Error('Unexpected guide fallback');}}
  };
  class Outbox {async counts(){return{pending:0,rejected:0};}async batch(){return[];}}
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../background.js'),'utf8'),{chrome,TrackingOutbox:Outbox,TrackingPrivacy:{},navigator:{userAgent:'Chrome/140'},crypto:webcrypto,URL,Promise,setTimeout,clearTimeout});
  const request=()=>new Promise(resolve=>listener({action:'status'},{},resolve));
  const first=request(),second=request();
  assert.equal((await first).status.identity.company_name,'Fixture');
  assert.equal((await second).status.identity.company_name,'Fixture');
  assert.equal(calls.filter(call=>call.action==='status').length,1);
  assert.match(calls[0].browser.profile,/^[a-f0-9]{32}$/);
  assert.equal(badges.at(-1),'OFF');
  clicked();
  assert.equal(calls.at(-1).action,'open');
});
