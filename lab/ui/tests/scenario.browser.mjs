import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdir,readFile} from 'node:fs/promises'
import path from 'node:path'
const require=createRequire(import.meta.url)
const {chromium}=require(process.env.NETLAB_PLAYWRIGHT_MODULE || 'playwright')
const project=JSON.parse(await readFile(process.env.NETLAB_SCENARIO_FIXTURE || '/tmp/netlab-scenario-fixture.json','utf8'))
const artifacts=process.env.NETLAB_UI_ARTIFACTS || '/tmp/netlab-scenario-browser'
const url=process.env.NETLAB_SCENARIO_URL || 'http://127.0.0.1:8766'
await mkdir(artifacts,{recursive:true})
const browser=await chromium.launch({executablePath:process.env.NETLAB_BROWSER || undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']})
const context=await browser.newContext({viewport:{width:1440,height:960}}),page=await context.newPage(),errors=[]
page.on('pageerror',error=>errors.push(error.message))
page.on('console',message=>{if(message.type()==='error' && /React|Maximum update|unique.*key|not focusable/i.test(message.text())) errors.push(message.text())})
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms))
async function until(condition,message){for(let i=0;i<150;i++){if(await condition())return;await pause(100)}throw new Error(message)}
async function step(name,fn){await fn();console.log('PASS '+name)}
const draft=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('netlab.draft')))
const screenshot=name=>page.screenshot({path:path.join(artifacts,name+'.png')})
await page.addInitScript(project=>{localStorage.setItem('netlab.draft',JSON.stringify(project));localStorage.setItem('netlab.theme','"dark"')},project)
const dialog=page.getByRole('dialog',{name:'Ssenariy: Muharrir',exact:true})
const start=()=>dialog.getByRole('button',{name:'Ishga tushirish',exact:true})
const card=index=>dialog.locator(`[data-app-index="${index}"]`)
async function select(scope,name,label){await scope.getByRole('combobox',{name,exact:true}).click();await page.getByRole('option',{name:label,exact:true}).click()}
async function add(type){await dialog.getByRole('button',{name:'Ilova qo‘shish',exact:true}).click();const count=await dialog.locator('[data-app-index]').count(),current=card(count-1);if(type!=='UDP (CBR)') await select(current,'Trafik turi',type);return current}
async function closeResults(){const result=page.getByRole('dialog',{name:'Natijalar — scenario_review',exact:true});await result.waitFor();await result.getByRole('button',{name:'Yopish',exact:true}).click()}
async function backendState(kind,id){return (await context.request.get(`${url}/api/${kind}/scenario_review/${id}`)).json()}
let runPosts=0,studyPosts=0,lastRun,lastStudy
page.on('request',request=>{if(request.method()==='POST'){const pathname=new URL(request.url()).pathname;if(pathname==='/api/runs')runPosts++;if(pathname==='/api/studies')studyPosts++}})
try {
  await step('open responsive scenario editor with existing settings preserved',async()=>{
    await page.goto(url);await page.getByRole('button',{name:/^Muharrir/}).click();await dialog.waitFor()
    assert.equal(await dialog.getByRole('spinbutton',{name:'Davomiylik (s)',exact:true}).inputValue(),'10')
    const sample=dialog.getByRole('spinbutton',{name:'Namuna oralig‘i (s)',exact:true})
    await sample.fill('');await sample.pressSequentially('0.125')
    assert.equal((await draft()).scenarios.Muharrir.sample,.125)
    assert.equal((await draft()).scenarios.Muharrir.ospf_convergence_s,.25)
    await dialog.getByText('Takrorlanuvchanlik va ns-3',{exact:true}).click()
    await dialog.getByRole('spinbutton',{name:'Seed',exact:true}).fill('7')
    await dialog.getByRole('spinbutton',{name:'RNG run',exact:true}).fill('3')
  })
  await step('configure VoIP, HTTP, FTP and multicast using type-specific controls',async()=>{
    const voice=await add('VoIP');await select(voice,'Qabul qiluvchi','h2x0');await select(voice,'Ovoz kodeki','G.729 — 8 kbit/s')
    await voice.getByRole('spinbutton',{name:'DSCP (0–63)',exact:true}).fill('46')
    const web=await add('HTTP');await select(web,'Qabul qiluvchi','h2x0');await web.getByRole('textbox',{name:'Sahifa hajmi',exact:true}).fill('100KB');await web.getByRole('spinbutton',{name:'So‘rov oralig‘i (s)',exact:true}).fill('2')
    const file=await add('FTP');await select(file,'Qabul qiluvchi','h2x0');await file.getByRole('textbox',{name:'Fayl hajmi',exact:true}).fill('1MB');await select(file,'Uzatish yo‘nalishi','Mijoz → server')
    const tv=await add('Multicast');await tv.getByRole('checkbox',{name:'h2x0',exact:true}).check();await tv.getByRole('textbox',{name:'IPv4 multicast guruhi',exact:true}).fill('239.2.3.4')
    await select(tv,'Manba','h1x0');assert.ok(!(await draft()).scenarios.Muharrir.apps[3].dst.includes('h1x0'))
    await select(tv,'Manba','h0x0');await tv.getByRole('checkbox',{name:'h1x0',exact:true}).check()
    const apps=(await draft()).scenarios.Muharrir.apps
    assert.equal(apps[0].codec,'g729');assert.equal(apps[0].dscp,46);assert.equal(apps[0].rate,undefined)
    assert.equal(apps[1].page,'100KB');assert.equal(apps[1].think,2);assert.equal(apps[2].direction,'upload')
    assert.deepEqual([...apps[3].dst].sort(),['h1x0','h2x0']);assert.equal(apps[3].group,'239.2.3.4')
    await tv.evaluate(element=>element.scrollIntoView({block:'start'}));await screenshot('scenario-multicast-dark')
  })
  await step('keep UDP/TCP/Ping controls and reject fractional packet sizes',async()=>{
    const ping=await add('Ping');await select(ping,'Qabul qiluvchi','h2x0');await ping.getByRole('spinbutton',{name:'Ping oralig‘i (s)',exact:true}).fill('0.1')
    const tcp=await add('TCP (bulk)');await select(tcp,'Qabul qiluvchi','h2x0')
    const udp=await add('UDP (CBR)');await select(udp,'Qabul qiluvchi','h2x0')
    const size=udp.getByRole('spinbutton',{name:'Paket hajmi (B)',exact:true})
    await size.fill('1000.5');assert.equal(await size.evaluate(input=>input.checkValidity()),false)
    await dialog.getByRole('button',{name:'Tekshirish',exact:true}).click();assert.equal(runPosts,0)
    assert.equal(await size.evaluate(input=>input===document.activeElement),true)
    await size.fill('1000')
    const details=udp.locator('details');await details.locator('summary').click()
    const port=udp.getByRole('spinbutton',{name:'Qabul porti',exact:true});await port.fill('70000')
    await details.locator('summary').click();await dialog.getByRole('button',{name:'Tekshirish',exact:true}).click()
    assert.equal(await details.getAttribute('open'),'');assert.equal(await port.evaluate(input=>input===document.activeElement),true)
    await port.fill('12345')
    const apps=(await draft()).scenarios.Muharrir.apps
    assert.equal(apps.length,7);assert.equal(apps[4].interval,.1);assert.equal(apps[5].rate,undefined)
  })
  await step('configure deterministic link/device failures and timed link parameters',async()=>{
    await dialog.getByRole('checkbox',{name:'Modelni yoqish',exact:true}).check()
    await select(dialog,'Nosozlik taqsimoti','Qat’iy davriy')
    await dialog.getByRole('spinbutton',{name:'Nosozlik seed',exact:true}).fill('9')
    await dialog.getByRole('spinbutton',{name:'Kabellar MTBF (s)',exact:true}).fill('3')
    await dialog.getByRole('spinbutton',{name:'Kabellar MTTR (s)',exact:true}).fill('1')
    await select(dialog,'Kabellar nishonlari','Tanlangan nishonlar')
    await dialog.locator('[data-failure-group="links"]').getByRole('checkbox',{name:'l1',exact:true}).check()
    await dialog.getByRole('checkbox',{name:'Qurilmalar nosozliklari',exact:true}).check()
    await select(dialog,'Qurilmalar nishonlari','Tanlangan nishonlar')
    await dialog.locator('[data-failure-group="devices"]').getByRole('checkbox',{name:'r1',exact:true}).check()
    await dialog.getByRole('button',{name:'Hodisa qo‘shish',exact:true}).click()
    const event=dialog.locator('[data-event-index="0"]');await select(event,'Hodisa amali','Kabel parametrlari')
    await event.getByRole('spinbutton',{name:'Navbat (paket)',exact:true}).fill('20')
    const sc=(await draft()).scenarios.Muharrir
    assert.equal(sc.failures.distribution,'fixed');assert.deepEqual(sc.failures.links.targets,['l1'])
    assert.deepEqual(sc.failures.devices.targets,['r1']);assert.equal(sc.events[0].params.queue,20)
    await dialog.getByRole('button',{name:'Nosozliklar',exact:true}).click();await screenshot('scenario-failures-dark')
  })
  await step('native input validation prevents submitting a blank duration',async()=>{
    const duration=dialog.getByRole('spinbutton',{name:'Davomiylik (s)',exact:true})
    await duration.fill('');await start().click();await pause(150)
    assert.equal(runPosts,0);assert.equal(await duration.evaluate(input=>input.validity.valueMissing),true)
    await duration.fill('10')
  })
  await step('backend preflight blocks IPv6 multicast without creating a run',async()=>{
    await select(dialog,'IP versiyasi','IPv6');await start().click()
    await until(async()=> (await dialog.innerText()).includes('IPv6 multicast'),'compatibility errors missing')
    assert.equal(runPosts,0)
    await select(dialog,'IP versiyasi','IPv4')
    assert.equal(await dialog.locator('[data-preflight]').count(),0,'stale diagnostics must clear after edits')
    await dialog.getByRole('button',{name:'Tekshirish',exact:true}).click()
    await until(async()=> (await dialog.innerText()).includes('Ishga tushirishga tayyor'),'preflight did not complete')
    assert.ok(!(await dialog.innerText()).includes('Ishga tushirishga tayyor emas'));assert.equal(runPosts,0)
  })
  await step('fast completed real analytic run is observed despite an intentionally delayed POST response',async()=>{
    let failStatus=true
    await page.route('**/api/runs/scenario_review/*',async route=>{
      if(failStatus) {failStatus=false;await route.fulfill({status:503,contentType:'application/json',body:'{"error":"Browser fixture: status unavailable"}'})}
      else await route.continue()
    })
    await page.route('**/api/runs',async route=>{
      const response=await route.fetch();const result=await response.json();lastRun=result.run_id
      await until(async()=> (await backendState('runs',lastRun)).status==='done','analytic fixture failed')
      await pause(200);await route.fulfill({response})
    })
    await start().dblclick()
    const job=dialog.locator('[data-job-kind="run"]')
    await until(async()=> (await job.innerText()).includes('Browser fixture: status unavailable'),'status read error hidden')
    await job.getByRole('button',{name:'Qayta urinish',exact:true}).click()
    await closeResults()
    await until(async()=>await start().isEnabled(),'run stayed stuck after a missed terminal event')
    assert.equal(runPosts,1)
    const result=await backendState('runs',lastRun)
    for(const id of ['a1','a2','a3','a4']) assert.ok(result.apps[id],id)
    assert.equal(result.apps.a4.reached,2);assert.ok(result.availability.flows)
    assert.equal(result.scenario.sample,.125);assert.equal(result.scenario.seed,7)
    await page.unroute('**/api/runs')
    await page.unroute('**/api/runs/scenario_review/*')
  })
  await step('application codec study exposes model parameters and handles a missed study completion event',async()=>{
    await select(dialog,'1-parametr','ilova a1 · kodek')
    assert.equal(await dialog.getByRole('textbox',{name:'1-parametr qiymatlari',exact:true}).inputValue(),'g711,g729')
    await page.route('**/api/studies',async route=>{
      const response=await route.fetch();const result=await response.json();lastStudy=result.study_id
      await until(async()=> (await backendState('studies',lastStudy)).settled,'study did not settle')
      await pause(200);await route.fulfill({response})
    })
    await dialog.getByRole('button',{name:'Tadqiqotni boshlash',exact:true}).click();await closeResults()
    await until(async()=>await dialog.getByRole('button',{name:'Tadqiqotni boshlash',exact:true}).isEnabled(),'study stayed active after terminal event')
    assert.equal(studyPosts,1);assert.equal((await backendState('studies',lastStudy)).complete,true)
    await page.unroute('**/api/studies')
    await dialog.getByRole('textbox',{name:'1-parametr qiymatlari',exact:true}).fill('g711,,g729')
    assert.equal(await dialog.getByRole('button',{name:'Tadqiqotni boshlash',exact:true}).isEnabled(),false)
    assert.ok((await dialog.innerText()).includes('bo‘sh element'))
    await dialog.getByRole('textbox',{name:'1-parametr qiymatlari',exact:true}).fill('g711,g729')
  })
  await step('two-dimensional failure and codec study sends correct group metadata',async()=>{
    await select(dialog,'1-parametr','nosozlik: kabellar · MTBF (s)')
    await dialog.getByRole('textbox',{name:'1-parametr qiymatlari',exact:true}).fill('3,6')
    await select(dialog,'2-parametr','ilova a1 · kodek')
    await dialog.getByRole('button',{name:'Tadqiqotni boshlash',exact:true}).click();await closeResults()
    const result=await context.request.get(url+'/api/runs/scenario_review?limit=50')
    const history=await result.json(),id=history.runs.find(row=>row.study_param?.kind==='failure').study_id
    const state=await backendState('studies',id)
    assert.equal(state.planned_runs,4);assert.equal(state.complete,true)
    const record=await backendState('runs',state.runs[0].run_id)
    assert.equal(record.study_param.group,'links');assert.equal(record.scenario.failures.links.mtbf_s,record.study_value)
    assert.equal(record.scenario.apps[0].codec,record.study_value2)
    await select(dialog,'1-parametr','ilova a1 · kodek')
    await until(async()=> (await dialog.getByRole('combobox',{name:'2-parametr',exact:true}).innerText()).includes('Ikkinchi parametr yo‘q'),'duplicate study parameter not cleared')
  })
  await step('failed study stays failed and remains inspectable',async()=>{
    await dialog.getByRole('textbox',{name:'Ssenariy izohi',exact:true}).fill('browser-study-error')
    await dialog.getByRole('button',{name:'Tadqiqotni boshlash',exact:true}).click()
    await until(async()=> (await dialog.locator('[data-job-kind="study"]').innerText()).includes('Browser fixture: codec variant failed'),'study failure hidden')
    const text=await dialog.locator('[data-job-kind="study"]').innerText()
    assert.ok(text.includes('Xato'));assert.ok(!text.includes('Barcha variantlar muvaffaqiyatli\n'))
    await screenshot('scenario-study-error-dark')
  })
  await step('new run does not inherit previous completion and real cancellation is visible',async()=>{
    await dialog.getByRole('textbox',{name:'Ssenariy izohi',exact:true}).fill('browser-held')
    await start().click()
    const status=dialog.locator('[data-job-kind="run"]')
    await status.getByRole('button',{name:'Bekor qilish',exact:true}).waitFor()
    assert.equal(await page.getByRole('dialog',{name:'Natijalar — scenario_review',exact:true}).count(),0)
    await status.getByRole('button',{name:'Bekor qilish',exact:true}).click()
    await until(async()=> (await status.innerText()).includes('Bekor qilingan'),'cancellation did not finish')
    assert.equal(await start().isEnabled(),true)
    await dialog.getByRole('textbox',{name:'Ssenariy izohi',exact:true}).fill('')
  })
  await step('imported multicast arrays and failure targets survive editing and undo',async()=>{
    await dialog.getByRole('button',{name:'Yopish',exact:true}).click()
    await page.getByRole('button',{name:/^Import.*1 ilova/}).click()
    const imported=page.getByRole('dialog',{name:'Ssenariy: Import',exact:true})
    assert.equal(await imported.locator('[data-app-index="0"]').getByRole('checkbox',{name:'h1x0',exact:true}).isChecked(),true)
    assert.equal(await imported.locator('[data-app-index="0"]').getByRole('checkbox',{name:'h2x0',exact:true}).isChecked(),true)
    await imported.getByRole('textbox',{name:'Ko‘rinadigan nom',exact:true}).fill('Televizion oqim')
    assert.deepEqual((await draft()).scenarios.Import.apps[0].dst,['h1x0','h2x0'])
    assert.deepEqual((await draft()).scenarios.Import.failures.devices.targets,['r1'])
    await imported.getByRole('button',{name:'Yopish',exact:true}).click()
    await page.getByRole('button',{name:/^Muharrir/}).click()
    // Duplication/deletion participate in the existing project undo history.
    const before=(await draft()).scenarios.Muharrir.apps.length
    await dialog.getByRole('button',{name:'Nusxalash: a1',exact:true}).click()
    assert.equal((await draft()).scenarios.Muharrir.apps.length,before+1)
    const id=(await draft()).scenarios.Muharrir.apps.at(-1).id
    await pause(750)
    await dialog.getByRole('button',{name:'O‘chirish: '+id,exact:true}).click()
    assert.equal((await draft()).scenarios.Muharrir.apps.length,before)
    await page.locator('body').click({position:{x:1400,y:930}})
    await page.keyboard.press('Control+z');assert.equal((await draft()).scenarios.Muharrir.apps.length,before+1)
    await page.keyboard.press('Control+y');assert.equal((await draft()).scenarios.Muharrir.apps.length,before)
  })
  await step('mobile layout keeps application controls inside the window',async()=>{
    await dialog.getByRole('button',{name:'Kattalashtirish',exact:true}).click()
    await page.setViewportSize({width:390,height:844})
    const control=dialog.getByRole('spinbutton',{name:'Davomiylik (s)',exact:true})
    await control.scrollIntoViewIfNeeded();const box=await control.boundingBox()
    assert.ok(box.x>=0 && box.x+box.width<=390)
    await screenshot('scenario-mobile-dark')
    await page.setViewportSize({width:1440,height:960})
    await dialog.getByRole('button',{name:'Oldingi o‘lcham',exact:true}).click()
    await page.getByTitle('Kun / tun').click();await dialog.getByRole('button',{name:'Sozlamalar',exact:true}).click();await screenshot('scenario-desktop-light')
  })
  await step('pending preflight cannot submit an edited scenario',async()=>{
    let release,started=false
    const held=new Promise(resolve=>release=resolve)
    await page.route('**/api/scenario/preflight',async route=>{started=true;const response=await route.fetch();await held;await route.fulfill({response}).catch(()=>{})})
    const before=runPosts;await start().click();await until(async()=>started,'preflight not intercepted')
    await dialog.getByRole('spinbutton',{name:'Davomiylik (s)',exact:true}).fill('11')
    release();await until(async()=>await start().isEnabled(),'aborted preflight stayed pending')
    assert.equal(runPosts,before);assert.equal(await dialog.locator('[data-preflight]').count(),0)
    await page.unroute('**/api/scenario/preflight')
  })
  await step('accepted old-project submission does not appear after a project rename',async()=>{
    let release,started=false
    const held=new Promise(resolve=>release=resolve)
    await page.route('**/api/runs',async route=>{const response=await route.fetch();started=true;await held;await route.fulfill({response}).catch(()=>{})})
    await start().click();await until(async()=>started,'run submission not intercepted')
    const name=page.getByRole('textbox',{name:'Loyiha nomi',exact:true});await name.fill('renamed_scenario');await name.press('Tab');release()
    await until(async()=>await start().isEnabled(),'new project inherited submission state')
    assert.equal(await dialog.locator('[data-job-kind]').count(),0)
    const history=await context.request.get(url+'/api/runs/renamed_scenario?limit=50')
    assert.equal((await history.json()).total,0)
  })
  assert.deepEqual(errors,[]);console.log('PASS no browser errors or React warnings')
}catch(error){await screenshot('failure');console.error(error);process.exitCode=1}
finally{await browser.close()}
