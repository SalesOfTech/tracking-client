const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
  try {
    const page = await browser.newPage({viewport:{width:960,height:700}});
    await page.route('https://work.example.test/**', route => route.fulfill({contentType:'text/html',body:`<!doctype html><html><body>
      <label for="project">Project name</label><input id="project" name="project">
      <label for="password">Password</label><input id="password" type="password">
      <label for="otp">Verification code</label><input id="otp" autocomplete="one-time-code">
      <a href="/next?token=secret">Open project</a><button>Save changes</button>
      </body></html>`}));
    await page.goto('https://work.example.test/editor');
    await page.evaluate(() => {
      window.collected=[];
      const policy={tracking:true,interactions:true,field_values:true,domains:['work.example.test'],policy_expires_at:Math.floor(Date.now()/1000)+3600};
      window.chrome={runtime:{sendMessage(message,reply){if(message.action==='record')window.collected.push(message.event);reply(message.action==='status'?{ok:true,status:{policy}}:{ok:true});}},storage:{onChanged:{addListener(){},removeListener(){}}}};
    });
    await page.addScriptTag({path:path.join(__dirname,'../privacy.js')});
    await page.addScriptTag({path:path.join(__dirname,'../content.js')});
    await page.locator('#project').fill('Spring campaign');
    await page.locator('#password').fill('Do-not-store-this');
    await page.locator('#otp').fill('123456');
    await page.getByRole('button',{name:'Save changes'}).click();
    await page.locator('a').evaluate(anchor => anchor.addEventListener('click',event=>event.preventDefault()));
    await page.getByRole('link',{name:'Open project'}).click();
    const events=await page.evaluate(()=>window.collected);
    assert.equal(events.find(e=>e.type==='field_change'&&e.target.name==='project').target.value,'Spring campaign');
    assert.equal(events.find(e=>e.type==='field_change'&&e.target.name==='password').target.value,undefined);
    assert.equal(events.find(e=>e.type==='field_change'&&e.target.name==='otp').target.value,undefined);
    assert(events.some(e=>e.type==='button_click'&&e.target.label==='Save changes'));
    assert.equal(events.find(e=>e.type==='link_click').target.href,'https://work.example.test/next');
    assert(!JSON.stringify(events).includes('Do-not-store-this'));
    assert(!JSON.stringify(events).includes('123456'));
    await page.evaluate(()=>{document.querySelector('button').click();});
    assert.equal((await page.evaluate(()=>window.collected)).length,events.length,'Synthetic clicks must be ignored');
    await page.evaluate(()=>window.__softTrackingV3.stop());
    console.log('PASS: real click/change events, label capture, secret exclusion, sanitized links, synthetic event exclusion');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
