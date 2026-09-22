// Real operation records only. No generated state/fixture is accepted here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL, fileURLToPath} = require('node:url');
const playwrightPath = process.env.PLAYWRIGHT_MODULE || 'playwright';
const {chromium} = require(playwrightPath);
const report = path.resolve(process.argv[2]);
const output = path.resolve(process.argv[3]);
const read = name => JSON.parse(fs.readFileSync(path.join(path.dirname(report), 'evidence', name + '.json'), 'utf8'));
(async () => {
  const demo = read('demo'), rollback = read('rollback'), restore = read('restore');
  assert.equal(restore.status, 'passed'); assert.equal(restore.cleanup_succeeded, true);
  assert.equal(rollback.injected_smoke_failure, true);
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined});
  const errors = [], checks = [];
  const record = {status:'in_progress', recorded_at:new Date().toISOString(), browser:browser.version(),
    playwright: require(path.join(playwrightPath, 'package.json')).version,
    source:'real operational evidence; static report', checks, errors};
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}, reducedMotion:'reduce'});
    page.setDefaultTimeout(10000); page.on('pageerror', error => errors.push(error.message));
    for (const width of [1440,390]) {
      await page.setViewportSize({width,height:1000});
      for (const [panel, job, filename] of [
        ['result', demo.job, 'job'], ['rollback', rollback.rollback_job, 'rollback'],
        ['recovery', restore.new_job, 'restore-new-job'],
      ]) {
        await page.goto(pathToFileURL(report).href + '#' + panel);
        const section = page.locator('#' + panel); await section.waitFor({state:'visible'});
        assert.equal(await page.locator('[data-panel]:visible').count(),1);
        // Open actual disclosure widgets so identity/results appear in the capture.
        await section.locator('details').evaluateAll(elements => elements.forEach(e => e.open=true));
        const content = await section.innerText();
        assert.ok(content.includes(job.id), panel + ' job identity');
        assert.ok(content.includes(job.result.checksum), panel + ' result checksum');
        if (panel === 'recovery') {
          assert.equal(await section.locator('.operation-summary strong').first().innerText(), String(restore.restored_jobs));
        }
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), panel + ' overflow');
        for (const href of await page.locator('a[href]').evaluateAll(elements => elements.map(e=>e.href))) {
          const url = new URL(href); if(url.protocol === 'file:') {url.hash=''; assert.ok(fs.existsSync(fileURLToPath(url)),href);}
        }
        await page.mouse.move(0,0); await page.evaluate(() => {document.activeElement?.blur(); scrollTo(0,0);});
        const screenshot = filename + '-' + width + '.png';
        await page.screenshot({path:path.join(output,screenshot),fullPage:true});
        checks.push({panel,width,job_id:job.id,screenshot});
      }
    }
    assert.deepEqual(errors, []); record.status='passed';
  } catch(error) { record.status='failed'; record.error=error.message; throw error;
  } finally {
    record.completed_at=new Date().toISOString();
    fs.writeFileSync(path.join(output,'browser.json'), JSON.stringify(record,null,2)+'\n');
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode=1;});
