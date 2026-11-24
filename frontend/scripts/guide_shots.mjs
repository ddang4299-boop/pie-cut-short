// Screenshots for the in-app guide (Guide.tsx): one per section and language, with the control the
// text talks about outlined in the accent color. Run against a backend that has demo data
// (a ready source with clips, one rendered, and a source waiting for /find-moments).
// Usage: node scripts/guide_shots.mjs   Env: GUIDE_URL (default http://127.0.0.1:8899),
//        GUIDE_SOURCE (ready source id), GUIDE_CLIP (clip id), SMOKE_BROWSER (default msedge)
// Output: public/guide/<lang>/<section>.jpg
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

const BASE = process.env.GUIDE_URL ?? "http://127.0.0.1:8899";
const SOURCE = process.env.GUIDE_SOURCE ?? "";
const CLIP = process.env.GUIDE_CLIP ?? "";
const CHANNEL = process.env.SMOKE_BROWSER ?? "msedge";

const SHOTS = [
  { id: "import", path: "/library", highlight: ['[data-testid="drop-zone"]', "#import-location"] },
  { id: "find", path: "/library", scrollTo: '[data-testid="waiting-panel"]', highlight: ['[data-testid="run-claude"]'] },
  { id: "review", path: "/moments", highlight: ['[data-testid="moment-list"] > div:has(button)', '[data-testid="approve-open"]'] },
  { id: "edit", path: `/editor?clip=${CLIP}`, highlight: [] },
  { id: "export", path: "/export", highlight: ['[data-testid="render-all"]', `[data-testid="capcut-${CLIP}"]`] },
  { id: "settings", path: "/settings", highlight: ['[data-testid="llm-provider"]'] },
];

const HIGHLIGHT_CSS = `
  .guide-mark { outline: 3px solid #7c5cff !important; outline-offset: 3px !important; border-radius: 10px;
    box-shadow: 0 0 0 7px rgba(124, 92, 255, .25), 0 0 32px rgba(124, 92, 255, .55) !important; }
  * { caret-color: transparent !important; }`;

const browser = await chromium.launch({ channel: CHANNEL, headless: true });
try {
  for (const lang of ["en", "vi"]) {
    const out = resolve(`public/guide/${lang}`);
    mkdirSync(out, { recursive: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: lang === "vi" ? "vi-VN" : "en-US" });
    const page = await context.newPage();
    await page.goto(`${BASE}/library`);
    await page.evaluate(([l, src]) => {
      localStorage.setItem("shortforge.uiLanguage", l);
      if (src) localStorage.setItem("shortforge.currentSource", src);
    }, [lang, SOURCE]);
    for (const shot of SHOTS) {
      await page.goto(`${BASE}${shot.path}`);
      await page.waitForTimeout(2500);
      await page.addStyleTag({ content: HIGHLIGHT_CSS });
      if (shot.scrollTo) {
        await page.locator(shot.scrollTo).first().evaluate((el) => el.scrollIntoView({ block: "center" }));
        await page.waitForTimeout(400);
      }
      for (const selector of shot.highlight) {
        const el = page.locator(selector).first();
        if (await el.count()) await el.evaluate((node) => node.classList.add("guide-mark"));
        else console.log(`  (${lang}/${shot.id}) not found: ${selector}`);
      }
      await page.mouse.move(0, 899);
      const file = `${out}/${shot.id}.jpg`;
      await page.screenshot({ path: file, type: "jpeg", quality: 82 });
      console.log(file);
    }
    await context.close();
  }
} finally {
  await browser.close();
}
