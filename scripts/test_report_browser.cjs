// Optional: Playwright is a developer tool, never a dependency of the HTML.
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const report = process.argv[2] || pathToFileURL(path.resolve(__dirname, "../docs/evidence/uiux-20261012/view/docs/report.html")).href;
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
        // Give hashchange two frames to run: inspecting immediately can miss the regression.
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
    await page.goto(`${report}#recovery`);
    // The matchMedia change opens the native picker after the390px journey.
    // Hidden details descendants can retain a box while rejecting focus.
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
    console.log("Passed: eight skip-link cases, keyboard selection/history, nine-panel print and no-JS.");
    await noScript.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
