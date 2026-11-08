// Optional real-browser regression suite; see docs/ui-results-workspace.md.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdir,readFile} from 'node:fs/promises'
import path from 'node:path'
const require=createRequire(import.meta.url)
const {chromium}=require(process.env.NETLAB_PLAYWRIGHT_MODULE || 'playwright')
const fixture=JSON.parse(await readFile(process.env.NETLAB_UI_FIXTURE || '/tmp/netlab-ui-fixture.json','utf8'))
const artifacts=process.env.NETLAB_UI_ARTIFACTS || '/tmp/netlab-ui-browser'
await mkdir(artifacts,{recursive:true})
const browser=await chromium.launch({executablePath:process.env.NETLAB_BROWSER || undefined,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']})
const context=await browser.newContext({viewport:{width:1440,height:960},acceptDownloads:true})
const page=await context.newPage(), errors=[], failures=[]
page.on('pageerror',error=>errors.push(error.message))
page.on('console',message=>{if(message.type()==='error' && /React|Maximum update|unique.*key/i.test(message.text())) errors.push(message.text())})
const url=process.env.NETLAB_UI_URL || 'http://127.0.0.1:8765'
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms))
async function until(condition,message) {
  for(let i=0;i<100;i++){if(await condition())return;await pause(100)}
  throw new Error(message)
}
async function step(name,fn){try{await fn();console.log('PASS '+name)}catch(error){failures.push(name);throw error}}
await page.addInitScript(project=>{localStorage.setItem('netlab.draft',JSON.stringify(project));localStorage.setItem('netlab.theme','"dark"')},fixture.project)
const dialog=page.getByRole('dialog',{name:'Natijalar — ui_review'})
const row=id=>dialog.locator(`[data-run-id="${id}"]`)
async function choose(id){await row(id).locator('button').filter({has:page.locator('span.font-semibold')}).click();await until(async()=>await dialog.getByRole('button',{name:'CSV',exact:true}).isEnabled(),'result detail not ready')}
async function tab(name){await dialog.getByRole('tab',{name,exact:true}).click();await until(async()=>await dialog.getByRole('tab',{name,exact:true}).getAttribute('aria-selected')==='true','tab not selected')}
async function screenshot(name){await page.screenshot({path:path.join(artifacts,name+'.png')})}
try {
  await step('load real backend, accessible results window and KPI cards',async()=>{
    await page.goto(url);await page.getByRole('button',{name:'Natijalar va solishtirish'}).click()
    await dialog.waitFor();await row(fixture.rich).waitFor();await choose(fixture.rich)
    assert.ok((await dialog.innerText()).includes('OQIM NATIJALARI'))
    await screenshot('results-desktop-dark')
  })
  await step('VoIP MOS, HTTP pages, FTP quality and keyboard tabs',async()=>{
    const charts=dialog.getByRole('tab',{name:'Grafiklar',exact:true})
    await charts.focus();await page.keyboard.press('ArrowRight')
    await until(async()=>await dialog.getByRole('tabpanel').innerText().then(text=>text.includes('MOS:')),'application summary missing')
    const text=await dialog.getByRole('tabpanel').innerText()
    assert.ok(text.includes('sahifa'));assert.ok(text.includes('FTP'))
    await screenshot('results-apps-dark')
    await page.keyboard.press('End');assert.equal(await dialog.getByRole('tab',{name:'Hodisalar',exact:true}).getAttribute('aria-selected'),'true')
    assert.ok((await dialog.getByRole('tabpanel').innerText()).includes('link_down'))
    await tab('Tarmoq sifati')
    const network=await dialog.getByRole('tabpanel').innerText()
    for(const section of ['Mavjudlik','Konvergensiya','Kabel navbatlari','Qurilmalar']) assert.ok(network.includes(section))
    assert.ok(network.includes('voice'));await screenshot('results-network-dark')
  })
  await step('search scope, status filter and real history pagination',async()=>{
    const search=dialog.getByRole('textbox',{name:'Natijalarni qidirish'})
    await search.fill('Ovoz');assert.equal(await dialog.locator('[data-run-id]').count(),1)
    assert.ok((await dialog.innerText()).includes('joriy sahifadagi'))
    await search.fill('nothing-matches');assert.ok((await dialog.innerText()).includes('mos natija topilmadi'))
    await search.fill('');await dialog.getByRole('button',{name:'Keyingi natijalar',exact:true}).click()
    await until(async()=>!(await row(fixture.rich).count()),'history page did not change')
    assert.ok((await dialog.innerText()).includes(`51–${fixture.total} / ${fixture.total}`))
    await dialog.getByRole('button',{name:'Oldingi natijalar',exact:true}).click();await row(fixture.rich).waitFor()
    await dialog.getByRole('combobox',{name:'Holat filtri'}).click();await page.getByRole('option',{name:'Xato',exact:true}).click()
    await until(async()=>await dialog.locator('[data-run-id]').count()===0,'status filter not applied')
    await dialog.getByRole('combobox',{name:'Holat filtri'}).click();await page.getByRole('option',{name:'Barcha holatlar',exact:true}).click()
    await row(fixture.rich).waitFor()
  })
  await step('CSV, ZIP and offline HTML export links',async()=>{
    await choose(fixture.rich)
    const download=page.waitForEvent('download');await dialog.getByRole('button',{name:'CSV',exact:true}).click()
    const csv=await download;assert.equal(csv.suggestedFilename(),'ui_review_natijalar.csv')
    const text=await readFile(await csv.path(),'utf8');assert.ok(text.includes('"voip h0x0→h2x0"'));assert.ok(text.includes('\r\n'))
    const zipEvent=page.waitForEvent('download');await dialog.getByRole('link',{name:'ZIP',exact:true}).click()
    const zip=await zipEvent;assert.ok((await readFile(await zip.path())).subarray(0,2).equals(Buffer.from('PK')))
    const html=await context.request.get(new URL(await dialog.getByRole('link',{name:'HTML',exact:true}).getAttribute('href'),url).href)
    assert.equal(html.status(),200);assert.ok((await html.text()).includes('MOS'))
  })
  await step('multi-result comparison exports all selected results',async()=>{
    await row(fixture.second).getByRole('checkbox').check()
    await until(async()=>await dialog.getByRole('button',{name:'HTML',exact:true}).isEnabled(),'comparison not ready')
    const event=page.waitForEvent('download');await dialog.getByRole('button',{name:'HTML',exact:true}).click()
    const download=await event;assert.ok(download.suggestedFilename().endsWith('_solishtirish.html'))
    const text=await readFile(await download.path(),'utf8');assert.ok(text.includes('Ovoz va veb sifati'));assert.ok(text.includes('Taqqoslash namunasi'))
    await dialog.getByRole('button',{name:'Tanlovni tozalash'}).click();assert.equal(await dialog.getByRole('button',{name:'CSV',exact:true}).isEnabled(),false)
  })
  await step('partial study retains its failed variant and categorical labels',async()=>{
    await dialog.locator(`[data-study-id="${fixture.study}"]`).click()
    await until(async()=> (await dialog.innerText()).includes('Browser fixture: variant failed'),'failed variant hidden')
    assert.ok((await dialog.innerText()).includes('3 / 3 variant'))
    await tab('Grafiklar');await until(async()=>await dialog.locator('[data-chart-line]').count()>0,'study chart not ready')
    await screenshot('results-study-dark')
    await dialog.getByRole('button',{name:'Natijalarga',exact:true}).click()
  })
  await step('real 2D study renders its heatmap and all four variants',async()=>{
    let active=0,peak=0,read=0
    const pattern='**/api/runs/ui_review/*'
    await page.route(pattern,async route=>{
      if(!fixture.study2runs.includes(route.request().url().split('/').at(-1))) return route.continue()
      active++;read++;peak=Math.max(peak,active)
      try{await pause(150);const response=await route.fetch();await route.fulfill({response})}finally{active--}
    })
    await dialog.locator(`[data-study-id="${fixture.study2}"]`).click()
    await until(async()=> (await dialog.innerText()).includes('4 / 4 variant'),'2D variants missing')
    await until(async()=>await dialog.locator('linearGradient').count()>0,'heatmap missing')
    assert.ok((await dialog.innerText()).includes('Barcha variantlar tayyor'))
    await until(async()=>read===4 && active===0,'study detail requests did not finish')
    assert.equal(peak,3,'detail loading must use at most three concurrent requests')
    await page.unroute(pattern)
    await screenshot('results-heatmap-dark')
    await dialog.getByRole('button',{name:'Natijalarga',exact:true}).click()
  })
  await step('corrupt archive diagnostics and explicit reindex',async()=>{
    await dialog.getByRole('button',{name:'O‘qilmagan natijalar',exact:true}).click()
    await until(async()=> (await dialog.innerText()).includes('broken'),'invalid archive not reported')
    await dialog.getByRole('button',{name:'Qayta tekshirish',exact:true}).click()
    await until(async()=>await dialog.getByRole('button',{name:'Qayta tekshirish',exact:true}).isEnabled(),'reindex stuck')
    assert.ok((await dialog.innerText()).includes('broken'))
    await dialog.getByRole('button',{name:'O‘qilmagan natijalar',exact:true}).click()
  })
  await step('100k samples render bounded paths and responsive tooltip',async()=>{
    await choose('large');await tab('Grafiklar')
    await until(async()=>await dialog.locator('[data-chart-line][data-source-points="99999"]').count()>0,'large result not loaded')
    const paths=await dialog.locator('[data-chart-line]').evaluateAll(nodes=>nodes.map(node=>({display:Number(node.dataset.displayPoints),source:Number(node.dataset.sourcePoints),d:node.getAttribute('d')})))
    assert.ok(paths.every(line=>line.display<=1200));assert.ok(paths[0].d.split('M').length>=3)
    const chart=dialog.locator('svg').filter({has:page.locator('[data-chart-line]')}).first()
    const box=await chart.boundingBox();const start=Date.now()
    await page.mouse.move(box.x+box.width*.5,box.y+60)
    await until(async()=>await dialog.locator('.pointer-events-none.absolute.top-2').count()>0,'tooltip missing')
    assert.ok(Date.now()-start<2000);await screenshot('results-large-dark')
  })
  await step('history request failure is visible and retry recovers',async()=>{
    let fail=true
    await page.route('**/api/runs/ui_review?*',async route=>{
      if(fail){fail=false;await route.fulfill({status:503,contentType:'application/json',body:'{"error":"Browser fixture: temporary failure"}'})}
      else await route.continue()
    })
    await dialog.getByRole('button',{name:'Natijalarni yangilash'}).click()
    await until(async()=> (await dialog.innerText()).includes('Browser fixture: temporary failure'),'failure not shown')
    await dialog.getByRole('button',{name:'Qayta urinish',exact:true}).click()
    await until(async()=>!(await dialog.innerText()).includes('Browser fixture: temporary failure'),'retry failed')
    await page.unroute('**/api/runs/ui_review?*')
  })
  await step('real cancellation and confirmed deletion',async()=>{
    await row(fixture.cancel).getByRole('button',{name:'Bekor qilish: Bekor qilinadigan sinov',exact:true}).click()
    await until(async()=> (await row(fixture.cancel).innerText()).includes('Bekor qilingan'),'cancellation not reflected')
    page.once('dialog',modal=>modal.accept())
    await row(fixture.cancel).getByRole('button',{name:'O‘chirish: Bekor qilinadigan sinov',exact:true}).click()
    await until(async()=>await row(fixture.cancel).count()===0,'deleted run remains visible')
  })
  await step('light theme, maximize and mobile viewport keep controls reachable',async()=>{
    await choose(fixture.rich);await tab('Ilovalar')
    await dialog.getByRole('button',{name:'Kattalashtirish',exact:true}).click()
    // Theme can be changed while the floating window is restored.
    await dialog.getByRole('button',{name:'Oldingi o‘lcham',exact:true}).click()
    await page.getByTitle('Kun / tun').click();await screenshot('results-apps-light')
    await dialog.getByRole('button',{name:'Kattalashtirish',exact:true}).click()
    await page.setViewportSize({width:390,height:844})
    await until(async()=> (await dialog.boundingBox()).width<=390,'window overflows viewport')
    await screenshot('results-mobile-light')
    for(const label of ['Yopish','Oldingi o‘lcham']){
      const rect=await dialog.getByRole('button',{name:label,exact:true}).boundingBox()
      assert.ok(rect.x>=0 && rect.x+rect.width<=390 && rect.y>=0)
    }
    assert.ok(await dialog.getByRole('tab',{name:'Ilovalar',exact:true}).isVisible())
    await dialog.getByRole('button',{name:'Tarixni ochish',exact:true}).click()
    assert.equal(await dialog.getByRole('button',{name:'Tarixni yopish',exact:true}).getAttribute('aria-expanded'),'true')
    assert.ok((await dialog.locator('.results-history-scroll').boundingBox()).height>80)
    await screenshot('results-mobile-history-light')
    await dialog.getByRole('button',{name:'Tarixni yopish',exact:true}).click()
    await tab('Grafiklar')
    await screenshot('results-mobile-chart-light')
    const metric=dialog.getByRole('button',{name:'Kabel yuklamasi',exact:true})
    const rect=await metric.boundingBox();assert.ok(rect.x>=0 && rect.x+rect.width<=390)
    await page.setViewportSize({width:1440,height:960});await dialog.getByRole('button',{name:'Oldingi o‘lcham',exact:true}).click()
  })
  await step('project rename aborts old detail requests and clears cached results',async()=>{
    let release,started=false
    const held=new Promise(resolve=>release=resolve)
    await page.route(`**/api/runs/ui_review/${fixture.second}`,async route=>{started=true;await held;await route.continue().catch(()=>{})})
    await row(fixture.second).locator('button').filter({has:page.locator('span.font-semibold')}).click()
    await until(async()=>started,'stale detail request was not intercepted')
    const name=page.getByRole('textbox',{name:'Loyiha nomi'})
    await name.fill('empty_review');await name.press('Tab')
    const next=page.getByRole('dialog',{name:'Natijalar — empty_review'})
    await next.waitFor();release()
    await until(async()=> (await next.innerText()).includes('Hali natija yo‘q'),'old project history leaked')
    assert.equal(await next.getByRole('tab').count(),0)
    assert.equal(await next.getByRole('button',{name:'CSV',exact:true}).isEnabled(),false)
  })
  assert.deepEqual(errors,[])
  console.log('PASS no browser errors or React warnings')
} catch(error){await screenshot('failure');console.error(error);process.exitCode=1}
finally{await browser.close()}
