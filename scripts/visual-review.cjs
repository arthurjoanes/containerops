// Optional local browser review; Playwright is not a dependency of the report.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL, fileURLToPath} = require('node:url');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=path.resolve(__dirname,'..');
const images=path.join(root,'docs/screenshots/interface-v3');
const evidence=path.join(root,'docs/evidence/interface-v3');
const report=pathToFileURL(path.join(root,'docs/report.html')).href;
const fixture=name=>pathToFileURL(path.join(root,'artifacts/interface-review',name,'docs/report.html')).href;
(async()=>{
 fs.mkdirSync(images,{recursive:true});fs.mkdirSync(evidence,{recursive:true});
 const browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||undefined,headless:true});
  const results=[];const states=[];const errors=[];let screenshots=0;
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  page.on('pageerror',e=>errors.push(e.message));
  const capture=async name=>{await page.mouse.move(0,0);await page.evaluate(()=>{document.activeElement?.blur();scrollTo(0,0);});await page.screenshot({path:path.join(images,name+'.png'),fullPage:true});screenshots++;};
  const ids=['overview','result','tls','recovery','operations','rollback','artifacts','evidence'];
  for(const width of [1440,1024,768,390,320]){
   await page.setViewportSize({width,height:900});
   for(const id of ids){
    await page.goto(report+'#'+id);await page.locator('#'+id).waitFor({state:'visible'});
    assert.equal(await page.locator('[data-panel]:visible').count(),1);
    assert.equal(await page.locator('[aria-current=page]').getAttribute('data-operation'),id);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),id+' @'+width);
    results.push({id,width});
    if(width===1440 || id==='recovery') await capture(id+'-'+width);
   }
  }
  await page.goto(report+'#recovery');
  const restore=JSON.parse(fs.readFileSync(path.join(root,'docs/evidence/restore.json'),'utf8'));
  assert.equal(await page.locator('#recovery .operation-summary strong').first().innerText(),String(restore.restored_jobs));
  assert.equal(await page.locator('.recovery-job').getAttribute('open'),null);
  await page.locator('.recovery-job > summary').click();
  assert.equal(await page.locator('.recovery-job .job').isVisible(),true);
  await capture('recovery-expanded-320');
  assert.ok((await page.locator('#recovery .result-column').innerText()).includes('Checksum do backup'));
  assert.ok((await page.locator('#recovery .result-column').innerText()).includes('Novo job após restauração'));
  assert.ok((await page.locator('#recovery .identity-column').innerText()).includes('Registro da cópia'));
  await page.locator('.operation-picker > summary').focus();await page.keyboard.press('Enter');
  await page.locator('[data-operation="operations"]').focus();await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.activeElement.id==='operations-title');
  assert.equal(await page.locator('.operation-picker').getAttribute('open'),null);
  await page.setViewportSize({width:1440,height:900});
  await page.locator('[data-operation="overview"]').waitFor({state:'visible'});
  await page.goto(report+'#evidence');
  for(const href of await page.locator('a[href]').evaluateAll(es=>es.map(e=>e.href))){const u=new URL(href);if(u.protocol==='file:'){u.hash='';assert.ok(fs.existsSync(fileURLToPath(u)),href);}}
  await capture('evidence-long');
  await page.goto(report+'#artifacts');
  assert.ok((await page.locator('#artifacts').innerText()).includes('Scan da imagem'));
  await page.goto(report+'#recovery');
  await page.emulateMedia({reducedMotion:'no-preference'});
  assert.equal(await page.locator('.operation-link').first().evaluate(e=>getComputedStyle(e).animationName),'none');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.operation-link').first().evaluate(e=>getComputedStyle(e).transitionDuration),'0s');
  await page.evaluate(()=>document.body.style.zoom='2');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.evaluate(()=>document.body.style.zoom='1');
  await page.emulateMedia({media:'print'});
  assert.equal(await page.locator('[data-panel]:visible').count(),8);
  assert.equal(await page.locator('#operations .record-details .job').isVisible(),true);
  await page.evaluate(()=>scrollTo(0,0));
  await page.screenshot({path:path.join(images,'print-preview.png'),fullPage:false});screenshots++;
  await page.emulateMedia({media:'screen'});
  for(const [name,panel,expected] of [
   ['empty','result','Nenhum resultado registrado'],
   ['backup-only','recovery','Sem resultado'],
   ['release-invalid','operations','Sem resultado'],
   ['release-failed','operations','Falhou'],
   ['job-queued','result','Na fila'],
   ['job-running','result','Processando'],
   ['job-succeeded','result','Concluído'],
   ['job-failed','result','Falhou'],
   ['job-unexpected','result','Estado não reconhecido'],
   ['job-incomplete','result','Resultado incompleto'],
   ['job-zero','result','0'],
   ['job-large','result','123.456.789.012.345'],
   ['restore-failed','recovery','Falhou'],
  ]) {
   for(const width of [1440,390,320]) {
    await page.setViewportSize({width,height:900});await page.goto(fixture(name)+'#'+panel);
    const text=await page.locator('#'+panel).innerText();
    assert.ok(text.includes(expected),name+': '+expected);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    if(name==='backup-only') {
     assert.ok(text.includes('Registro da cópia'));assert.ok(!text.includes('Passou'));
     if(await page.locator('.recovery-job').getAttribute('open')===null) await page.locator('.recovery-job > summary').click();
     assert.ok((await page.locator('.recovery-job').innerText()).includes('Nenhum resultado registrado'));
    }
    if(name==='release-invalid') { assert.ok((await page.locator('[data-operation="operations"]').textContent()).includes('Inválido')); assert.ok((await page.locator('#content').innerText()).includes('release-failed.json')); }
    if(name==='job-incomplete') {
     const label=page.locator('.metric strong.unavailable').first();
     assert.equal(await label.innerText(),'Não informado');
     assert.ok(await label.evaluate(e=>e.getBoundingClientRect().height<=2*parseFloat(getComputedStyle(e).lineHeight)));
    }
    if(name==='restore-failed') assert.equal(await page.locator('#recovery .badge.fail:visible').count(),3);
    if(name==='job-large') {
     await page.locator('#result .record-details > summary').click();
     assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    }
    states.push({name,panel,width,expected});
    if((width===390 && ['backup-only','job-queued','job-incomplete'].includes(name)) || (width===1440 && ['release-invalid','release-failed'].includes(name))) await capture('fixture-'+name+'-'+width);
   }
  }
  const nojs=await browser.newContext({javaScriptEnabled:false,viewport:{width:390,height:844}});
  const plain=await nojs.newPage();await plain.goto(report);
  assert.equal(await plain.locator('[data-panel]:visible').count(),8);
  await plain.locator('#rollback .record-details > summary').first().click();
  assert.equal(await plain.locator('#rollback .record-details .job').isVisible(),true);
  await nojs.close();assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(evidence,'visual-review.json'),JSON.stringify({reviewed_at:new Date().toISOString(),browser:await browser.version(),results,states,screenshots,errors,scope:'Existing JSONs rendered offline. Separate marked presentation fixtures exercise missing/invalid/failed/pending/incomplete states. No Docker operation, service or build. Print panels and native disclosure content checked; full PDF pagination was not audited. CSS zoom 200%, not native browser zoom. No screen-reader audit.'},null,2));
  console.log(JSON.stringify({views:results.length,states:states.length,screenshots,errors}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
