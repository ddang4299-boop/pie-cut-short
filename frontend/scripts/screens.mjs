// Screenshots of every page (EN + VI) for visual review, plus a side-by-side sheet with the
// style reference (sampleUIUX/website/screenshots/index-desktop.png, top of the page).
// Usage: node scripts/screens.mjs [tag]   Env: SMOKE_URL (default http://localhost:5173), SMOKE_BROWSER
// Output: smoke-output/screens/<tag>-<lang>-<page>.png and <tag>-compare.png
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

const BASE = process.env.SMOKE_URL ?? "http://localhost:5173";
const CHANNEL = process.env.SMOKE_BROWSER ?? "msedge";
const tag = process.argv[2] ?? "now";
const out = resolve("smoke-output/screens");
mkdirSync(out, { recursive: true });

const PAGES = ["library", "moments", "editor", "export", "settings"];
const browser = await chromium.launch({ channel: CHANNEL, headless: true });
const shots = {};
try {
  for (const lang of ["en", "vi"]) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: lang === "vi" ? "vi-VN" : "en-US" });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(`${BASE}/library`);
    await page.evaluate((l) => localStorage.setItem("shortforge.uiLanguage", l), lang);
    for (const name of PAGES) {
      await page.goto(`${BASE}/${name}`);
      await page.waitForTimeout(1500);
      const file = `${out}/${tag}-${lang}-${name}.png`;
      await page.screenshot({ path: file });
      shots[`${lang}-${name}`] = file;
      const overflow = await page.evaluate(() => {
        const main = document.querySelector("main");
        return Math.max(document.documentElement.scrollWidth - window.innerWidth, main ? main.scrollWidth - main.clientWidth : 0);
      });
      console.log(`${lang} ${name}: ${file}${overflow > 0 ? `  OVERFLOW ${overflow}px` : ""}`);
    }
    if (errors.length) console.log(`  page errors (${lang}): ${errors.join(" | ")}`);
    await context.close();
  }

  const ref = resolve("../sampleUIUX/website/screenshots/index-desktop.png");
  if (existsSync(ref)) {
    const data = (p) => `data:image/png;base64,${readFileSync(p).toString("base64")}`;
    const ctx = await browser.newContext({ viewport: { width: 1520, height: 1000 } });
    const tab = await ctx.newPage();
    const cell = (label, src, crop) =>
      `<figure><figcaption>${label}</figcaption><div style="width:720px;height:450px;overflow:hidden;border:1px solid #333">
       <img src="${src}" style="width:720px;${crop ? "margin-top:0" : ""}"></div></figure>`;
    await tab.setContent(`<body style="margin:0;background:#1a1a1a;color:#aaa;font:13px sans-serif;display:grid;
      grid-template-columns:720px 720px;gap:16px;padding:16px">
      ${cell("Style reference (sample website, top)", data(ref), true)}
      ${cell("PieCut · Library (EN)", data(shots["en-library"]))}
      ${cell("PieCut · Editor (VI)", data(shots["vi-editor"]))}
      ${cell("PieCut · Settings (VI)", data(shots["vi-settings"]))}</body>`);
    await tab.screenshot({ path: `${out}/${tag}-compare.png`, fullPage: true });
    console.log(`compare: ${out}/${tag}-compare.png`);
  }
} finally {
  await browser.close();
}
