// Playwright é usado nos testes; o HTML funciona sem ele.
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const report = process.argv[2] || pathToFileURL(path.resolve(__dirname, "../docs/examples/report.html")).href;
if (process.argv[2]) {
  const url = new URL(report);
  assert.ok(url.protocol === "http:" && url.hostname === "127.0.0.1", "Only an explicit loopback report is accepted");
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined,
  });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const selections = ["backups", "recovery", "operations", "result"];
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const id of selections) {
        await page.goto(`${report}#${id}`);
        await page.locator(".skip-link").focus();
        await page.keyboard.press("Enter");
        // Aguarde o hashchange antes de conferir o painel e o foco.
        await page.evaluate(() => new Promise(resolve => {
          requestAnimationFrame(() => requestAnimationFrame(resolve));
        }));
        assert.equal(await page.locator("[data-panel]:visible").count(), 1);
        assert.equal(await page.locator("[data-panel]:visible").getAttribute("id"), id);
        assert.equal(await page.evaluate(() => location.hash), `#${id}`);
        assert.equal(await page.evaluate(() => document.activeElement.id), "content");
        assert.equal(await page.locator("[aria-current=page]").getAttribute("data-operation"), id);
      }
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${report}#result`);
    const analysis = page.locator("[data-activity]");
    const totalJobs = await analysis.locator("[data-job-day]").count();
    if (totalJobs) {
      const days = await analysis.locator("[data-job-day]").evaluateAll(rows => rows.map(row => row.dataset.jobDay).sort());
      await analysis.locator("[data-activity-from]").fill(days.at(-1));
      await analysis.locator("[data-activity-from]").dispatchEvent("change");
      await analysis.locator("[data-activity-to]").fill(days.at(-1));
      await analysis.locator("[data-activity-to]").dispatchEvent("change");
      const expected = days.filter(day => day === days.at(-1)).length;
      assert.equal(await analysis.locator("[data-job-day]:not([hidden])").count(), expected);
      assert.equal(await analysis.locator("[data-state-total]").evaluateAll(nodes => nodes.reduce((sum, node) => sum + Number(node.textContent.replaceAll(".", "")), 0)), expected);
      assert.equal(new URL(page.url()).searchParams.get("jobs_from"), days.at(-1));
      await page.reload();
      assert.equal(await analysis.locator("[data-job-day]:not([hidden])").count(), expected);
      await analysis.locator("[data-activity-reset]").click();
      assert.equal(await analysis.locator("[data-job-day]:not([hidden])").count(), totalJobs);
      await page.emulateMedia({ reducedMotion: "reduce" });
      assert.equal(await analysis.locator(".chart-row").first().evaluate(node => getComputedStyle(node).animationName), "none");
    }
    await page.goto(`${report}#recovery`);
    // Aguarde o índice abrir ao sair do mobile; um link oculto pode recusar o foco.
    await page.waitForFunction(() => document.querySelector(".operation-picker").open);
    await page.locator('[data-operation="operations"]').waitFor({ state: "visible" });
    await page.locator('[data-operation="operations"]').focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.activeElement.id === "operations-title");
    await page.goBack();
    await page.locator("#recovery").waitFor({ state: "visible" });
    await page.emulateMedia({ media: "print" });
    assert.equal(await page.locator("[data-panel]:visible").count(), 9);
    const noScript = await browser.newContext({ javaScriptEnabled: false });
    const plainPage = await noScript.newPage();
    await plainPage.goto(report);
    assert.equal(await plainPage.locator("[data-panel]:visible").count(), 9);
    assert.deepEqual(errors, []);
    console.log("Passed: eight skip-link cases, keyboard selection/history, inclusive UTC date filters/URL/reset/reduced motion, nine-panel print and no-JS.");
    await noScript.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
