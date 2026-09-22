const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

// Failure before Chromium starts must not leave a previous successful audit in place.
for (const scenario of ["missing-html", "missing-playwright"]) {
  test(`replay invalidates previous approval: ${scenario}`, () => {
    const replay = fs.mkdtempSync(
      path.join(os.tmpdir(), "containerops-replay-"),
    );
    try {
      fs.writeFileSync(path.join(replay, "audit.json"), '{"status":"passed"}');
      if (scenario === "missing-playwright") {
        fs.mkdirSync(path.join(replay, "docs"));
        for (const file of ["baseline.html", "report.html"]) {
          fs.writeFileSync(path.join(replay, "docs", file), "<html></html>");
        }
      }
      const result = spawnSync(
        process.execPath,
        [path.join(__dirname, "audit_art_direction.cjs"), replay],
        {
          encoding: "utf8",
          env: {
            ...process.env,
            PLAYWRIGHT_MODULE: path.join(replay, "uninstalled-playwright"),
          },
        },
      );
      assert.equal(result.status, 1);
      assert.match(
        result.stderr,
        scenario === "missing-html"
          ? /run python scripts\/render_art_direction.py first/
          : /Cannot find module/,
      );
      const record = JSON.parse(
        fs.readFileSync(path.join(replay, "audit.json"), "utf8"),
      );
      assert.equal(record.status, "failed");
      assert.deepEqual(record.views, []);
      assert.ok(record.error);
    } finally {
      // This directory was allocated by this test; no repository evidence is removed.
      fs.rmSync(replay, { recursive: true });
    }
  });
}
