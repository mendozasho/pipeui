// Frontend bootstrap smoke test — loads the REAL index.html in a REAL browser.
//
// Why this exists (and why vitest can't replace it): the vitest harness imports
// each .jsx under jsdom and compiles JSX with esbuild's classic runtime, hardcoded
// in vitest.config.js to "match the CDN setup." Nothing enforces that match. When
// @babel/standalone's in-browser default flipped to the AUTOMATIC JSX runtime, every
// module started emitting `import {jsx} from "react/jsx-runtime"` — an unresolvable
// bare specifier — so window.__UI__/__Screen*__ were never assigned and #root stayed
// empty (black screen). vitest stayed green because its transform never goes through
// Babel-standalone, index.html, or the CDN. This test does: it serves the frontend
// statically and drives Chromium through the genuine load path, asserting the app
// actually mounts. It is the object that should fail loudly on a regression like that.
//
// Backend-independent by design: it asserts MOUNT (globals defined, #root non-empty,
// no page errors), which happens before any /api fetch. Missing API data does not
// blank the screen; an unresolved module specifier does.
//
// Run: npm run smoke   (CI must first run: npx playwright install chromium)
// Local without bundled browsers: SMOKE_CHROME="/path/to/Chrome" npm run smoke

import http from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const ROOT = fileURLToPath(new URL(".", import.meta.url));
const MIME = {
  ".html": "text/html",
  ".jsx": "text/javascript",
  ".js": "text/javascript",
  ".json": "application/json",
  ".css": "text/css",
};

// Minimal static file server scoped to the frontend dir.
const server = http.createServer(async (req, res) => {
  try {
    let p = decodeURIComponent(req.url.split("?")[0]);
    if (p === "/") p = "/index.html";
    const safe = normalize(p).replace(/^(\.\.[/\\])+/, "");
    const body = await readFile(join(ROOT, safe));
    res.writeHead(200, { "content-type": MIME[extname(safe)] || "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404);
    res.end("not found");
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const { port } = server.address();

const launchOpts = process.env.SMOKE_CHROME ? { executablePath: process.env.SMOKE_CHROME } : {};
const browser = await chromium.launch(launchOpts);
const page = await browser.newPage();
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(e.message));

try {
  await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  const probe = await page.evaluate(() => ({
    rootLen: document.getElementById("root").innerHTML.length,
    UI: typeof window.__UI__,
    screens: ["__ScreenData__", "__ScreenModules__", "__ScreenBuilder__", "__ScreenResults__", "__ScreenSettings__"].map(
      (k) => [k, typeof window[k]]
    ),
  }));

  const failures = [];
  if (probe.UI !== "object") failures.push(`window.__UI__ is ${probe.UI} (expected "object")`);
  for (const [k, t] of probe.screens) {
    if (t !== "function") failures.push(`window.${k} is ${t} (expected "function")`);
  }
  if (probe.rootLen < 100) failures.push(`#root rendered ${probe.rootLen} chars (app did not mount)`);
  if (pageErrors.length) failures.push(`page errors:\n    ${pageErrors.join("\n    ")}`);

  if (failures.length) {
    console.error("FRONTEND SMOKE TEST FAILED:\n- " + failures.join("\n- "));
    process.exitCode = 1;
  } else {
    console.log(
      `frontend smoke OK — #root mounted (${probe.rootLen} chars), all globals present, 0 page errors`
    );
  }
} finally {
  await browser.close();
  server.close();
}
