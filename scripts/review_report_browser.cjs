// Optional QA tool. Only a whitelisted loopback HTTP review is accepted.
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const positional = process.argv.slice(2).filter(value => !value.startsWith("--"));
const baseline = process.argv.includes("--baseline");
const secondIndex = process.argv.indexOf("--second-engine");
const secondEngine = secondIndex >= 0 ? process.argv[secondIndex + 1] : undefined;
const secondEngineBlocked = process.argv.includes("--second-engine-blocked");
if (positional.length < 2) throw new Error("Usage: node scripts/review_report_browser.cjs <review-root-http-url> <new-output-directory> [--baseline] [--second-engine firefox] [--second-engine-blocked]");
const base = new URL(positional[0]);
assert.equal(base.protocol, "http:", "Review requires HTTP; file URLs are prohibited");
assert.equal(base.hostname, "127.0.0.1", "Only the dedicated loopback review server is allowed");
assert.ok(!base.username && !base.password && !base.search && !base.hash);
const reviewRoot = base.href.replace(/\/$/, "").replace(/\/real\/docs\/report\.html$/, "");
const report = reviewRoot + "/real/docs/report.html";
const output = path.resolve(positional[1]);
assert.ok(!fs.existsSync(output), "Output exists; choose a new directory");
fs.mkdirSync(output, { recursive: true });
const playwright = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const axeRoot = process.env.AXE_CORE_MODULE || "axe-core";
const axePath = axeRoot.endsWith(".js") ? axeRoot : require.resolve(axeRoot + "/axe.min.js");
const axeSource = fs.readFileSync(axePath, "utf8");
const widths = [320, 390, 768, 1024, 1440];
const scenarios = ["catalog-empty", "catalog-malformed", "integrity-without-receipt", "cleanup-failed", "legacy-no-cleanup", "long-ids-imported"];
const record = { reviewed_at: new Date().toISOString(), mode: baseline ? "baseline" : "candidate", source: report,
  harness_sha256: crypto.createHash("sha256").update(fs.readFileSync(__filename)).digest("hex"),
  axe_source_sha256: crypto.createHash("sha256").update(axeSource).digest("hex"),
  scope: "Static loopback replay of public saved evidence; visibly marked presentation fixtures. No Docker or domain operations.",
  widths, checks: [], findings: [], screenshots: [], axe: [], console_errors: [], page_errors: [], network: [], second_engine: null,
  limits: ["Screenshots require separate human visual inspection.", "CSS root text enlargement is exercised; native browser zoom, OS font preferences and screen-reader behavior are unverified."] };
const check = (condition, name, detail = {}) => {
  record.checks.push({ name, passed: Boolean(condition), ...detail });
  if (!condition) record.findings.push({ name, ...detail });
};
const safeRequest = url => {
  const parsed = new URL(url);
  return ["data:", "blob:"].includes(parsed.protocol) || parsed.origin === base.origin;
};

function contrastRatio(foreground, background) {
  const luminance = color => color.match(/[\d.]+/g).slice(0, 3).map(Number)
    .map(value => { value /= 255; return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4; })
    .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  const first = luminance(foreground), second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

async function capture(page, name, selector, viewportOnly = false) {
  await page.mouse.move(0, 0);
  await page.evaluate(() => document.activeElement?.blur());
  let clip;
  if (selector) {
    const element = page.locator(selector).first();
    if (!await element.count()) return;
    await element.evaluate(element => scrollTo(0, element.getBoundingClientRect().top + scrollY));
    const box = await element.boundingBox();
    if (!box) return;
    clip = { x: Math.max(0, Math.floor(box.x)), y: Math.max(0, Math.floor(box.y)),
      width: Math.max(1, Math.min(page.viewportSize().width, Math.ceil(box.width))), height: Math.min(900, Math.ceil(box.height)) };
  } else await page.evaluate(() => scrollTo(0, 0));
  const bytes = await page.screenshot({ path: path.join(output, name + ".png"), ...(clip ? { clip } : { fullPage: !viewportOnly }) });
  record.screenshots.push({ file: name + ".png", viewport: page.viewportSize(), selector: selector || (viewportOnly ? "viewport" : "whole visible operation"), clip,
    sha256: crypto.createHash("sha256").update(bytes).digest("hex"), route: page.url() });
}

async function axe(page, state) {
  await page.addScriptTag({ content: axeSource });
  const result = await page.evaluate(async () => {
    const scanned = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa", "best-practice"] } });
    const compact = list => list.map(rule => ({ id: rule.id, impact: rule.impact, help: rule.help, helpUrl: rule.helpUrl,
      nodes: rule.nodes.map(node => ({ target: node.target, failureSummary: node.failureSummary })) }));
    return { version: scanned.testEngine.version, violations: compact(scanned.violations), incomplete: compact(scanned.incomplete), passes: scanned.passes.length };
  });
  record.axe.push({ ...state, ...result });
  check(!result.violations.length, "axe", { ...state, violations: result.violations.length });
}

async function main() {
  let browser;
  try {
    browser = await playwright.chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
      executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || undefined });
    record.browser = await browser.version();
    const context = await browser.newContext({ locale: "pt-BR", reducedMotion: "reduce", viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on("pageerror", error => record.page_errors.push(error.message));
    page.on("console", message => { if (message.type() === "error") record.console_errors.push(message.text()); });
    page.on("request", request => { if (!safeRequest(request.url())) record.network.push(request.url()); });
    await context.route("**/*", route => safeRequest(route.request().url()) ? route.continue() : route.abort("blockedbyclient"));
    await page.goto(report);
    const inputManifest = await page.request.get(reviewRoot + "/review-manifest.json");
    if (inputManifest.ok()) {
      const bytes = await inputManifest.body();
      record.input_manifest_sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
      record.input_manifest = JSON.parse(bytes);
    }
    const panels = await page.locator("[data-panel]").evaluateAll(elements => elements.map(element => element.id));
    check(panels.includes("recovery") && panels.includes("evidence"), "core anchors available", { panels });
    if (!baseline) check(panels.includes("backups") && panels.length === 9, "nine operation anchors", { panels });
    for (const width of widths) {
      await page.setViewportSize({ width, height: 1000 });
      for (const panel of panels) {
        await page.goto(report + "#" + panel);
        await page.locator("#" + panel).waitFor({ state: "visible" });
        await page.evaluate(() => document.fonts.ready);
        const state = await page.evaluate(() => ({ viewport: innerWidth, scroll_width: document.documentElement.scrollWidth,
          fonts: [...document.fonts].map(font => ({ family: font.family, status: font.status })) }));
        check(state.scroll_width <= width, "page reflow", { width, panel, ...state });
        check(await page.locator("[data-panel]:visible").count() === 1, "single selected operation", { width, panel });
        check(await page.locator('[aria-current="page"]').getAttribute("data-operation") === panel, "navigation context", { width, panel });
        if (width === 1440 || width === 320) await axe(page, { width, panel, scenario: "real" });
      }
      await page.goto(report + "#recovery");
      await capture(page, "recovery-" + width);
      if (width === 1440 || width === 320) {
        await capture(page, "recovery-top-" + width, "#recovery");
        if (panels.includes("backups")) await page.goto(report + "#backups");
        await capture(page, "catalog-" + width, ".backup-catalog");
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(report + "#recovery");
    await page.locator(".skip-link").focus();
    await page.keyboard.press("Enter");
    check(await page.evaluate(() => document.activeElement.id === "content"), "skip link focuses main");
    check(new URL(page.url()).hash === "#recovery", "skip link preserves selected operation");
    await page.goto(report + "#overview");
    // Hash navigation focuses the operation heading. Start at the skip link so
    // a bounded probe does not mistake the normal document boundary for a trap.
    await page.locator(".skip-link").focus();
    const focus = [];
    for (let index = 0; index < 12; index++) {
      await page.keyboard.press("Tab");
      focus.push(await page.evaluate(() => {
        const element = document.activeElement, style = getComputedStyle(element), box = element.getBoundingClientRect();
        return { tag: element.tagName, id: element.id, label: (element.innerText || element.getAttribute("aria-label") || "").slice(0, 160),
          ring: style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0 || style.boxShadow !== "none",
          visible: box.width > 0 && box.height > 0, top: box.top, bottom: box.bottom };
      }));
    }
    record.keyboard_focus = focus;
    check(focus.every(item => item.visible && item.ring), "Tab focus visible", { inspected: focus.length });
    await page.keyboard.press("Shift+Tab");
    check(await page.evaluate(() => document.activeElement !== document.body), "Shift+Tab retains usable focus");
    await page.locator('[data-operation="recovery"]').focus();
    await page.keyboard.press("Enter");
    await page.locator("#recovery").waitFor({ state: "visible" });
    check(await page.evaluate(() => document.activeElement.id === "recovery-title"), "keyboard navigation focuses heading");
    await page.goBack();
    await page.locator("#overview").waitFor({ state: "visible" });
    check(new URL(page.url()).hash === "#overview", "history restores operation");
    if (panels.includes("backups")) {
      await page.goto(report + "#backups");
      const search = page.locator("[data-catalog-search]"), state = page.locator("[data-catalog-restore]");
      if (await search.count() && await state.count()) {
        await search.fill("no-matching-backup-fixture-5927");
        check(await page.locator("[data-backup-row]:visible").count() === 0, "catalog search has no results");
        check(await page.locator("[data-catalog-no-results]").isVisible(), "catalog explains empty filter");
        await page.locator("[data-catalog-reset]").click();
        check(await page.locator("[data-backup-row]:visible").count() > 0, "catalog reset restores rows");
        await state.selectOption("verified");
        check(await page.locator("[data-catalog-status]").innerText() !== "", "catalog announces count");
        await page.locator('[data-operation="overview"]').click();
        await page.goBack();
        await page.locator("#backups").waitFor({ state: "visible" });
        check(await state.inputValue() === "verified", "catalog filter survives history");
        await page.reload();
        check(await state.inputValue() === "verified", "catalog filter survives reload");
        await state.selectOption("all");
        const details = page.locator("[data-backup-row] details").first();
        if (await details.count()) {
          await details.locator("summary").focus(); await page.keyboard.press("Enter");
          check(await details.getAttribute("open") !== null, "catalog native details keyboard activation");
        }
        const copy = page.locator("[data-copy-command]:visible").first();
        if (await copy.count()) {
          await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: base.origin });
          const target = await copy.getAttribute("data-command-target");
          const expected = await page.locator("#" + target).textContent();
          await copy.click();
          await page.waitForFunction(target => document.getElementById(target)?.closest(".code-block")
            ?.querySelector(".copy-feedback")?.textContent.startsWith("Comando copiado."), target);
          const clipboard = await page.evaluate(() => navigator.clipboard.readText());
          // Windows converts LF into CRLF when round-tripping native text.
          // Preserve all other characters; no command is executed by this QA.
          check(clipboard.replace(/\r\n/g, "\n") === expected, "copy writes only displayed local command",
            { native_crlf_normalized: clipboard.includes("\r\n") });
          await page.evaluate(() => Object.defineProperty(navigator.clipboard, "writeText", { configurable: true,
            value: async () => { throw new Error("Presentation fixture clipboard rejection"); } }));
          await copy.click();
          await page.waitForFunction(target => document.activeElement?.id === target &&
            document.getElementById(target)?.closest(".code-block")?.querySelector(".copy-feedback")
              ?.textContent.startsWith("Não foi possível copiar."), target);
          const fallback = await page.evaluate(target => ({ focused: document.activeElement.id === target,
            selected: getSelection().toString(), feedback: document.getElementById(target).closest(".code-block")
              .querySelector(".copy-feedback").textContent }), target);
          check(fallback.focused && fallback.selected === expected && fallback.feedback.startsWith("Não foi possível copiar."),
            "clipboard rejection offers manual selection", { focused: fallback.focused,
              selection_matches_command: fallback.selected === expected, feedback: fallback.feedback });
        }
      } else check(baseline, "catalog controls available");
    }
    for (const scenario of scenarios) {
      for (const width of [320, 1440]) {
        await page.setViewportSize({ width, height: 1000 });
        const url = reviewRoot + "/" + scenario + "/docs/report.html";
        await page.goto(url + "#" + (scenario.includes("cleanup") || scenario.includes("receipt") ? "recovery" : panels.includes("backups") ? "backups" : "recovery"));
        await page.evaluate(() => document.fonts.ready);
        check(await page.locator(".fixture-notice").count() === 1, "presentation fixture visibly marked", { scenario, width });
        check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "fixture reflow", { scenario, width });
        if (scenario === "cleanup-failed") {
          const toolbar = page.locator("#recovery .panel-toolbar").first();
          check(!(await toolbar.innerText()).includes("Passou"), "cleanup failure cannot approve restore", { scenario, width, observed: await toolbar.innerText() });
          await capture(page, "cleanup-failed-" + width);
          await capture(page, "cleanup-failed-top-" + width, "#recovery");
        }
        if (width === 320) await axe(page, { width, scenario });
      }
    }
    await page.goto(report + "#evidence");
    const links = await page.locator("a[href]").evaluateAll(elements => elements.map(element => element.href));
    record.links = [];
    for (const href of [...new Set(links)]) {
      const url = new URL(href);
      if (url.origin !== base.origin || url.hash && url.pathname.endsWith("report.html")) continue;
      url.hash = "";
      const response = await page.request.get(url.href);
      record.links.push({ url: url.href, status: response.status() });
      check(response.ok(), "local evidence or documentation link", { url: url.href, status: response.status() });
    }
    await page.goto(report + "#recovery");
    await page.emulateMedia({ reducedMotion: "reduce" });
    const motion = await page.locator(".operation-link").first().evaluate(element => ({ transition: getComputedStyle(element).transitionDuration, animation: getComputedStyle(element).animationDuration }));
    check(motion.transition.split(",").every(value => parseFloat(value) === 0) && motion.animation.split(",").every(value => parseFloat(value) === 0), "reduced motion", motion);
    await page.setViewportSize({ width: 320, height: 1000 });
    await page.addStyleTag({ content: "html{font-size:200%!important}" });
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "CSS root text enlargement reflow");
    await page.goto(report + "#recovery");
    await page.emulateMedia({ media: "print" });
    check(await page.locator("[data-panel]:visible").count() === panels.length, "print exposes all operations");
    await capture(page, "print-preview", "#recovery");
    const printHeader = await page.locator(".masthead").evaluate(header => {
      const background = getComputedStyle(header).backgroundColor;
      return { background, items: [...header.querySelectorAll(".brand,.brand-mark svg path,.snapshot-label")]
        .filter(element => { const box = element.getBoundingClientRect(); return box.width > 0 && box.height > 0; })
        .map(element => { const graphic = element.matches(".brand-mark svg path");
          return { selector: graphic ? ".brand-mark svg path" : "." + element.className,
            foreground: graphic ? getComputedStyle(element).stroke : getComputedStyle(element).color,
            required_ratio: graphic ? 3 : 4.5 }; }) };
    });
    printHeader.items = printHeader.items.map(item => ({ ...item, contrast_ratio: contrastRatio(item.foreground, printHeader.background) }));
    record.print_header = printHeader;
    check(printHeader.background === "rgb(255, 255, 255)", "print header has white background", printHeader);
    check(printHeader.items.length >= 2 && printHeader.items.every(item => item.contrast_ratio >= item.required_ratio), "print header contrast", printHeader);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await capture(page, "print-top", undefined, true);
    await page.emulateMedia({ media: "screen" });
    const plainContext = await browser.newContext({ javaScriptEnabled: false, locale: "pt-BR", viewport: { width: 390, height: 1000 } });
    const plain = await plainContext.newPage(); await plain.goto(report);
    check(await plain.locator("[data-panel]:visible").count() === panels.length, "no-JS exposes all operations");
    if (await plain.locator("[data-catalog-search]").count()) check(!await plain.locator("[data-catalog-search]").isVisible(), "no-JS hides inactive catalog controls");
    const disclosure = plain.locator("[data-backup-row] details").first();
    if (await disclosure.count()) {
      await disclosure.locator("summary").click();
      check(await disclosure.getAttribute("open") !== null, "no-JS native catalog details remain usable");
    }
    await plainContext.close();
    if (secondEngineBlocked) {
      record.second_engine = { engine: secondEngine || "firefox", status: "unverified",
        action: "Native isolated Firefox QA launch via Start-Process",
        reason: "blocked by policy", reason_detail: "Automatic approval review provided no detailed reason. No alternate launcher was attempted after rejection." };
    } else if (secondEngine) {
      assert.ok(["firefox", "webkit"].includes(secondEngine), "Second engine must differ from Chromium");
      const executable = process.env["PLAYWRIGHT_" + secondEngine.toUpperCase() + "_EXECUTABLE_PATH"] || playwright[secondEngine].executablePath();
      if (!fs.existsSync(executable)) record.second_engine = { engine: secondEngine, status: "unverified", reason: "Matching executable unavailable" };
      else {
        const second = await playwright[secondEngine].launch({ headless: true, executablePath: executable });
        try {
          const test = await second.newPage({ viewport: { width: 390, height: 1000 }, reducedMotion: "reduce" });
          await test.goto(report + "#recovery");
          await test.locator("#recovery").waitFor({ state: "visible" });
          check(await test.locator("[data-panel]:visible").count() === 1, "second engine operation journey", { engine: secondEngine });
          check(await test.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "second engine reflow", { engine: secondEngine });
          record.second_engine = { engine: secondEngine, version: await second.version(), status: "exercised", scope: "Recovery anchor and reflow at390px" };
        } finally { await second.close(); }
      }
    }
    check(record.page_errors.length === 0 && record.console_errors.length === 0, "no browser script or console errors", { page_errors: record.page_errors, console_errors: record.console_errors });
    check(record.network.length === 0, "offline snapshot has no external requests", { requests: record.network });
    if (!baseline) {
      // Supplemental publication crops follow the checks. Each is a new file;
      // prior captures and frozen scenario inputs remain unchanged.
      await page.setViewportSize({ width: 390, height: 1000 });
      await page.goto(report + "#backups");
      await capture(page, "catalog-390", ".backup-catalog");
      await capture(page, "backups-390", "#backups");
      for (const scenario of ["catalog-empty", "catalog-malformed", "long-ids-imported"]) {
        await page.goto(reviewRoot + "/" + scenario + "/docs/report.html#backups");
        await capture(page, scenario + "-390", "#content");
      }
      await page.goto(report + "#backups");
      await page.locator("[data-catalog-search]").fill("no-matching-backup-fixture-5927");
      await capture(page, "catalog-no-results-390", "#backups");
    }
    record.status = baseline ? "baseline-observed" : record.findings.length ? "failed" : "passed";
    if (!baseline && record.findings.length) process.exitCode = 1;
    await context.close();
  } catch (error) {
    record.status = "failed"; record.error = error.message; process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    fs.writeFileSync(path.join(output, "review.json"), JSON.stringify(record, null, 2) + "\n");
    console.log(JSON.stringify({ status: record.status, checks: record.checks.length, findings: record.findings.length, screenshots: record.screenshots.length, output }));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
