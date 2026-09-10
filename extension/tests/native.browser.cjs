const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {chromium}=require('playwright');

(async()=>{
  if(process.platform!=='linux'){console.log('SKIP: isolated Linux native messaging test');return;}
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'tracking-native-test-'));
  const repo=path.resolve(__dirname,'../..');
  let context;
  try{
    const host=path.join(root,'host.py');
    fs.writeFileSync(host,'#!/usr/bin/env python3\nimport sys\nsys.path.insert(0,'+JSON.stringify(path.join(repo,'agent'))+')\nfrom agent_tracker.native_host import main\nraise SystemExit(main(sys.argv[1]))\n',{mode:0o700});
    // Chromium resolves user-level native hosts relative to --user-data-dir.
    const profile=path.join(root,'profile');
    for(const folder of [path.join(profile,'NativeMessagingHosts')]){
      fs.mkdirSync(folder,{recursive:true});
      fs.writeFileSync(path.join(folder,'com.soft.tracking.json'),JSON.stringify({name:'com.soft.tracking',description:'Test fixture only',path:host,type:'stdio',allowed_origins:['chrome-extension://bjjdlmnghnhfnjlgacoijncoggpnfnjh/']}));
    }
    const extension=path.join(repo,'extension');
    context=await chromium.launchPersistentContext(profile,{channel:'chromium',headless:true,env:{...process.env,HOME:root,XDG_CONFIG_HOME:path.join(root,'.config'),XDG_DATA_HOME:path.join(root,'data')},args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
    const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');
    await worker.evaluate(()=>sync());
    const status=await worker.evaluate(async()=> (await chrome.storage.local.get('status')).status);
    assert.equal(status.error,'');
    assert.equal(status.identity,null);
    assert.equal(status.version,require('../../package.json').version);
    assert.equal(status.collection_reason,'unregistered');
    console.log('PASS: real Chromium extension -> native host -> desktop state, with isolated home and collection disabled');
  }finally{if(context)await context.close();fs.rmSync(root,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
