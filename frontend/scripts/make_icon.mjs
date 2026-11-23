// Renders public/piecut.svg to PNGs (one per icon size, so small sizes stay crisp) in ../desktop/icon/.
// Then: ..\.venv\Scripts\python.exe ..\desktop\make_icon.py  -> ../desktop/piecut.ico
import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright-core";

const svg = readFileSync(resolve("public/piecut.svg"), "utf-8");
const out = resolve("../desktop/icon");
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ channel: process.env.SMOKE_BROWSER ?? "msedge", headless: true });
try {
  for (const size of [16, 24, 32, 48, 64, 128, 256]) {
    const page = await browser.newPage({ viewport: { width: size, height: size } });
    await page.setContent(`<body style="margin:0;background:transparent">${svg.replace("<svg", `<svg width="${size}" height="${size}"`)}</body>`);
    await page.screenshot({ path: `${out}/piecut-${size}.png`, omitBackground: true });
    await page.close();
  }
} finally {
  await browser.close();
}
console.log(`icons: ${out}`);
