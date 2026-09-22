const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { pathToFileURL } = require("node:url");

// A replay is prepared explicitly, keeping published evidence immutable.
const directory = process.argv[2];
if (!directory || directory === "--help") {
  console.log(
    "Usage: node scripts/audit_art_direction.cjs <replay-directory>\nPrepare with: python scripts/render_art_direction.py\nRequires Playwright + Chromium; PLAYWRIGHT_MODULE and PLAYWRIGHT_CHANNEL can select an external installation.",
  );
  process.exit(directory ? 0 : 1);
}
const out = path.resolve(directory);
const shots = path.join(out, "screenshots");
const files = {
  baseline: path.join(out, "docs/baseline.html"),
  candidate: path.join(out, "docs/report.html"),
};
(async () => {
  const review = {
    checkedAt: new Date().toISOString(),
    scope:
      "Static report, identical historical records and generation clock; no operation executed",
    status: "running",
    views: [],
    fonts: [],
    network: [],
    pageErrors: [],
    comparisons: [],
  };
  let browser;
  try {
    for (const file of Object.values(files)) {
      assert.ok(
        fs.existsSync(file),
        `Missing ${file}; run python scripts/render_art_direction.py first`,
      );
    }
    const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
    browser = await chromium.launch({
      channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
      executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined,
      headless: true,
    });
    fs.mkdirSync(shots, { recursive: true });
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
      reducedMotion: "reduce",
    });
    const ids = [
      "overview",
      "result",
      "tls",
      "recovery",
      "operations",
      "rollback",
      "artifacts",
      "evidence",
    ];
    page.on("request", (r) => {
      if (!["file:", "data:"].some((p) => r.url().startsWith(p)))
        review.network.push(r.url());
    });
    page.on("pageerror", (error) => review.pageErrors.push(error.message));
    const urls = {
      baseline: pathToFileURL(files.baseline).href,
      candidate: pathToFileURL(files.candidate).href,
    };
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const id of ids) {
        for (const mode of ["baseline", "candidate"]) {
          await page.goto(urls[mode] + "#" + id);
          await page.locator("#" + id + ":visible").waitFor();
          await page.evaluate(() => document.fonts.ready);
          const fields = await page.locator("#" + id).evaluate((el) => ({
            times: [...el.querySelectorAll("time")].map((e) =>
              e.getAttribute("datetime"),
            ),
            proofs: [...el.querySelectorAll("a[href]")].map((e) =>
              e.getAttribute("href"),
            ),
            values: [...el.querySelectorAll("code")].map((e) => e.textContent),
          }));
          review.comparisons.push({ mode, width, id, fields });
          await page.mouse.move(0, 0);
          await page.evaluate(() => {
            document.activeElement?.blur();
            scrollTo(0, 0);
          });
          if (width === 1440 || width === 390)
            await page.screenshot({
              path: path.join(shots, `${mode}-${id}-${width}.png`),
              fullPage: true,
            });
          if (mode === "baseline") continue;
          const metrics = await page.evaluate(() => {
            const rgb = (v) => v.match(/[\d.]+/g).map(Number);
            const lum = (c) =>
              c
                .slice(0, 3)
                .map((n) => {
                  n /= 255;
                  return n <= 0.04045
                    ? n / 12.92
                    : ((n + 0.055) / 1.055) ** 2.4;
                })
                .reduce((s, n, i) => s + n * [0.2126, 0.7152, 0.0722][i], 0);
            const bg = (e) => {
              while (e) {
                let c = rgb(getComputedStyle(e).backgroundColor);
                if (c.length < 4 || c[3] === 1) return c;
                e = e.parentElement;
              }
              return [255, 255, 255];
            };
            const failures = [];
            let checked = 0,
              min = 99;
            for (const e of document.querySelectorAll("body *")) {
              if (
                !e.checkVisibility({ checkVisibilityCSS: true }) ||
                ![...e.childNodes].some(
                  (n) => n.nodeType === 3 && n.textContent.trim(),
                )
              )
                continue;
              const s = getComputedStyle(e),
                l1 = lum(rgb(s.color)),
                l2 = lum(bg(e)),
                ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05),
                large =
                  parseFloat(s.fontSize) >= 24 ||
                  (parseFloat(s.fontSize) >= 18.66 &&
                    parseInt(s.fontWeight) >= 700);
              checked++;
              min = Math.min(min, ratio);
              if (ratio < (large ? 3 : 4.5))
                failures.push({ text: e.textContent.slice(0, 70), ratio });
            }
            return {
              overflow: document.documentElement.scrollWidth > innerWidth,
              contrast: { checked, min, failures },
              visibleApprovalsInNav: [
                ...document.querySelectorAll(".operation-meta"),
              ]
                .filter((e) => e.checkVisibility())
                .map((e) => e.textContent.trim()),
            };
          });
          assert.equal(metrics.overflow, false);
          assert.ok(
            metrics.contrast.checked > 0,
            `${id}: no visible text checked`,
          );
          assert.deepEqual(metrics.contrast.failures, []);
          assert.ok(!metrics.visibleApprovalsInNav.includes("Aprovado"));
          review.views.push({ width, id, ...metrics });
        }
        const pair = review.comparisons.slice(-2);
        assert.deepEqual(pair[0].fields.times, pair[1].fields.times);
        assert.deepEqual(
          pair[0].fields.proofs.sort(),
          pair[1].fields.proofs.sort(),
        );
        assert.deepEqual(
          pair[0].fields.values.sort(),
          pair[1].fields.values.sort(),
        );
      }
    }
    await page.goto(urls.candidate + "#result");
    await page.evaluate(() => document.fonts.ready);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("DOM.enable");
    await cdp.send("CSS.enable");
    const { root: dom } = await cdp.send("DOM.getDocument");
    for (const selector of ["#result-title", ".job h3", ".job-values dd"]) {
      const { nodeId } = await cdp.send("DOM.querySelector", {
        nodeId: dom.nodeId,
        selector,
      });
      assert.ok(nodeId, `Missing font target: ${selector}`);
      const { fonts } = await cdp.send("CSS.getPlatformFontsForNode", {
        nodeId,
      });
      assert.ok(fonts.length > 0, `No rendered fonts found: ${selector}`);
      assert.ok(fonts.every((f) => f.isCustomFont));
      review.fonts.push({ selector, fonts });
    }
    await page.context().setOffline(true);
    await page.reload();
    await page.locator("#result:visible").waitFor();
    assert.ok(await page.locator("#result .job-values").isVisible());
    await page.locator(".operation-picker summary").click();
    await page
      .getByRole("link", { name: "Backup e restauração", exact: true })
      .click();
    await page.locator("#recovery:visible").waitFor();
    review.offline = {
      reload: true,
      navigation: true,
      scope: "file report with embedded font, brand, CSS and JavaScript",
    };
    assert.deepEqual(review.network, []);
    assert.deepEqual(review.pageErrors, []);
    review.sizes = {
      baselineBytes: fs.statSync(files.baseline).size,
      candidateBytes: fs.statSync(files.candidate).size,
    };
    review.status = "passed";
    console.log(
      JSON.stringify({
        views: review.views.length,
        dataPairs: review.comparisons.length / 2,
        fonts: review.fonts,
        offline: review.offline,
        sizes: review.sizes,
      }),
    );
  } catch (error) {
    review.status = "failed";
    review.error = error.message;
    throw error;
  } finally {
    try {
      if (fs.existsSync(out)) {
        fs.writeFileSync(
          path.join(out, "audit.json"),
          JSON.stringify(review, null, 2) + "\n",
        );
      }
    } finally {
      if (browser) await browser.close();
    }
  }
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
