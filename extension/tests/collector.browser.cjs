const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({headless:process.env.PLAYWRIGHT_HEADED !== '1',...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
  try {
    const page = await browser.newPage({viewport:{width:960,height:700}});
    await page.route(/^https?:/, route => route.abort());
    await page.route('https://work.example.test/**', route => route.fulfill({contentType:'text/html',body:`<!doctype html><html><body>
      <label for="project">Project name</label><input id="project" name="project">
      <label for="password">Password</label><input id="password" type="password">
      <label for="otp">Verification code</label><input id="otp" autocomplete="one-time-code">
      <a href="/next?token=secret">Open project</a><button>Save changes</button>
      </body></html>`}));
    await page.goto('https://work.example.test/editor');
    const start = Math.floor(Date.now()/1000)*1000;
    await page.clock.install({time:new Date(start)});
    await page.clock.pauseAt(new Date(start+1000));
    await page.addScriptTag({path:path.join(__dirname,'../outbox.js')});
    await page.evaluate(() => {
      window.collected=[];
      window.protocol=[];
      const policy={tracking:true,interactions:true,field_values:true,domains:['work.example.test'],policy_expires_at:Math.floor(Date.now()/1000)+3600};
      const status={policy,employee_epoch:'a'.repeat(32),legacy_employee_epoch:'a'.repeat(32),browser_session_generation:'1'.repeat(32)};
      const db=new TrackingOutbox('collector-fixture');
      const listeners=new Set();
      let tail=Promise.resolve();
      let focused=true;
      // Deterministic content-only focus harness; durable.browser.cjs uses real extension contexts.
      Object.defineProperty(document,'hasFocus',{value:()=>focused,configurable:true});
      window.fixture={
        focus(value){focused=value;window.dispatchEvent(new Event(value?'focus':'blur'));},
        status(update){Object.assign(status,update);for(const listener of listeners)listener({status:{newValue:structuredClone(status)}},'local');},
        async idle(){let before;do{before=tail;await before;await Promise.resolve();}while(before!==tail);},
        async events(){await this.idle();return(await db.epochBatches()).flatMap(batch=>batch.events.map(event=>({...event,employee_epoch:batch.employee_epoch})));},
      };
      window.chrome={runtime:{sendMessage(message,reply){
        protocol.push(structuredClone(message));
        if(message.action==='status'){reply({ok:true,status:structuredClone(status)});return;}
        tail=tail.then(async()=>{
          if(message.action==='record'){await db.put(message.event,message.employee_epoch);collected.push(message.event);return true;}
          if(message.action==='session')return db.checkpointSession(message.event,message.employee_epoch,message.close);
          if(message.action==='session-close')return db.closeSession(message.event_id,message.employee_epoch);
          throw new Error('Unexpected content protocol action: '+message.action);
        }).then(ok=>reply({ok:ok!==false}));
      }},storage:{onChanged:{addListener(listener){listeners.add(listener);},removeListener(listener){listeners.delete(listener);}}}};
    });
    await page.addScriptTag({path:path.join(__dirname,'../privacy.js')});
    await page.addScriptTag({path:path.join(__dirname,'../content.js')});
    await page.locator('#project').fill('Spring campaign');
    await page.locator('#password').fill('Do-not-store-this');
    await page.locator('#otp').fill('123456');
    await page.getByRole('button',{name:'Save changes'}).click();
    await page.locator('a').evaluate(anchor => anchor.addEventListener('click',event=>event.preventDefault()));
    await page.getByRole('link',{name:'Open project'}).click();
    await page.evaluate(()=>fixture.idle());
    const events=await page.evaluate(()=>window.collected);
    assert.equal(events.find(e=>e.type==='field_change'&&e.target.name==='project').target.value,'Spring campaign');
    assert.equal(events.find(e=>e.type==='field_change'&&e.target.name==='password').target.value,undefined);
    assert.equal(events.find(e=>e.type==='field_change'&&e.target.name==='otp').target.value,undefined);
    assert(events.some(e=>e.type==='button_click'&&e.target.label==='Save changes'));
    assert.equal(events.find(e=>e.type==='link_click').target.href,'https://work.example.test/next');
    assert(!JSON.stringify(events).includes('Do-not-store-this'));
    assert(!JSON.stringify(events).includes('123456'));
    await page.evaluate(()=>{document.querySelector('button').click();});
    await page.evaluate(()=>fixture.idle());
    assert.equal((await page.evaluate(()=>window.collected)).length,events.length,'Synthetic clicks must be ignored');
    console.log('PASS: real click/change events, label capture, secret exclusion, sanitized links, synthetic event exclusion');
    for(let second=1;second<=48;second++){
      if(second%10===0)await page.mouse.move(100+second,150);
      await page.clock.runFor(1000);
      await page.evaluate(()=>fixture.idle());
    }
    await page.evaluate(()=>fixture.focus(false));
    const sessions=(await page.evaluate(()=>fixture.events())).filter(event=>event.type==='web_session').sort((a,b)=>a.timestamp-b.timestamp);
    assert(sessions.length>=4,'Long activity must produce bounded durable sessions');
    for(let index=1;index<sessions.length;index++)assert.equal(sessions[index].timestamp,sessions[index-1].end_timestamp,'No one-second gap at a 15-second session rollover');
    assert.equal(sessions.reduce((total,event)=>total+event.end_timestamp-event.timestamp,0),sessions.at(-1).end_timestamp-sessions[0].timestamp);
    await page.clock.runFor(5000);
    assert.equal((await page.evaluate(()=>fixture.events())).filter(event=>event.type==='web_session').length,sessions.length,'Unfocused page must not collect time');
    console.log('PASS: durable session checkpoints, continuous 15-second segments, focus closure');
    await page.evaluate(()=>fixture.focus(true));
    await page.mouse.move(350,150);
    const lastInput=await page.evaluate(()=>Math.floor(Date.now()/1000));
    for(let second=1;second<=35;second++){
      await page.clock.runFor(1000);
      await page.evaluate(()=>fixture.idle());
    }
    assert((await page.evaluate(()=>fixture.events())).filter(event=>event.type==='web_session').every(event=>event.end_timestamp<=lastInput+30),'Idle time must be capped at the last input plus 30 seconds');
    await page.mouse.move(400,150);
    await page.clock.runFor(2000);
    await page.evaluate(()=>fixture.status({employee_epoch:'b'.repeat(32),browser_session_generation:'2'.repeat(32)}));
    await page.getByRole('button',{name:'Save changes'}).click();
    assert((await page.evaluate(()=>fixture.events())).some(event=>event.type==='button_click'&&event.employee_epoch==='b'.repeat(32)));
    await page.evaluate(()=>window.__softTrackingV3.stop());
    await page.evaluate(()=>fixture.idle());
    const protocol=await page.evaluate(()=>window.protocol);
    assert(protocol.some(message=>message.action==='session'&&message.close===false));
    assert(protocol.some(message=>message.action==='session-close'));
    console.log('PASS: idle boundary, employee epoch/generation handoff, session-close protocol');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
