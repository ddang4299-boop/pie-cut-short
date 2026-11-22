// UI smoke test: drives the installed Edge/Chrome (playwright-core, no browser download).
// Needs both dev servers running:  backend (uvicorn) on :8000 and `npm run dev` on :5173.
// Usage:  npm run smoke        Env: SMOKE_URL (default http://localhost:5173), SMOKE_BROWSER (msedge|chrome)
//
// Checks: backend status turns green; every page renders in EN and VI; in VI no English-only
// UI string is visible (and vice versa); the choice persists across reloads; the browser
// language picks the default; the offline error state shows Retry + View log; no horizontal overflow.
// Screenshots go to frontend/smoke-output/.

import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const BASE = process.env.SMOKE_URL ?? "http://localhost:5173";
const CHANNEL = process.env.SMOKE_BROWSER ?? "msedge";
const OUT = new URL("../smoke-output/", import.meta.url);
const PAGES = ["library", "moments", "editor", "export", "settings"];
const SAMPLE = fileURLToPath(new URL("../../samples/speech_en_sample.mp4", import.meta.url));
mkdirSync(OUT, { recursive: true });
const shot = (file) => fileURLToPath(new URL(file, OUT));

const flatten = (tree, prefix = "") =>
  Object.entries(tree).reduce((acc, [k, v]) => {
    const key = prefix ? `${prefix}.${k}` : k;
    return typeof v === "string" ? { ...acc, [key]: v } : { ...acc, ...flatten(v, key) };
  }, {});
const load = (lang) => flatten(JSON.parse(readFileSync(new URL(`../src/locales/${lang}.json`, import.meta.url), "utf8")));
const en = load("en");
const vi = load("vi");

/** Strings that differ between the two locales and have no placeholders (so they appear verbatim). */
const onlyIn = (mine, other) => {
  const otherValues = new Set(Object.values(other)); // e.g. "English" is also the switcher label in vi.json
  return Object.entries(mine)
    .filter(([k, v]) => other[k] !== v && !otherValues.has(v) && !v.includes("{{") && v.length >= 4)
    .map(([k, v]) => ({ key: k, value: v }));
};

const failures = [];
const check = (ok, message) => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${message}`);
  if (!ok) failures.push(message);
};

/**
 * Upload the sample through the file picker, check preflight, press Start, wait for transcription,
 * open the transcript viewer, click a word to seek, then delete the source (with its media).
 */
async function libraryFlow() {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "en-US" });
  const page = await context.newPage();
  await page.goto(`${BASE}/library`);
  await page.getByTestId("lang-en").click();
  await page.getByTestId("file-input").setInputFiles(SAMPLE);

  const card = page.locator('[data-testid="source-card"]').filter({ hasText: "speech_en_sample" }).first();
  await card.waitFor({ timeout: 120_000 });
  const id = await card.getAttribute("data-source-id");
  const sel = `[data-testid="source-card"][data-source-id="${id}"]`;
  const preflight = card.getByTestId("preflight-summary");
  await preflight.waitFor({ timeout: 30_000 });
  check(["ok", "warn"].includes(await preflight.getAttribute("data-level")), "uploaded sample passes preflight");
  check(await card.getByTestId("start-panel").isVisible(), "Start panel is shown; nothing runs before Start");
  check((await page.getByTestId("current-source").innerText()).includes("speech_en_sample"), "header shows the current source");
  await page.screenshot({ path: shot("en-library-preflight.png") });

  await card.getByTestId("language-select").selectOption("en");
  await card.getByTestId("start-button").click();
  await page.waitForSelector(`${sel} [data-testid="active-job"]`, { timeout: 30_000 }).catch(() => {});
  await page.getByTestId("lang-vi").click();
  await page.screenshot({ path: shot("vi-library-running.png") });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(overflow <= 0, `vi/library with a running card: no horizontal overflow (${overflow}px)`);
  await page.getByTestId("jobs-button").click();
  check((await page.getByTestId("job-row").count()) > 0, "jobs drawer lists the running job");
  await page.screenshot({ path: shot("vi-jobs-drawer.png") });
  await page.keyboard.press("Escape");
  await page.getByTestId("lang-en").click();

  const started = Date.now();
  await page.waitForSelector(`${sel}[data-status="ready"], ${sel}[data-status="error"]`, { timeout: 900_000 });
  const seconds = Math.round((Date.now() - started) / 1000);
  const status = await card.getAttribute("data-status");
  check(status === "ready", `pipeline finished (status=${status}, ${seconds}s)`);
  for (const step of ["preflight", "proxy", "transcribe"]) {
    const state = await card.locator(`[data-step="${step}"]`).getAttribute("data-state");
    check(state === "done", `step ${step} is done (${state})`);
  }
  await page.screenshot({ path: shot("en-library-ready.png") });

  await card.getByTestId("transcript-button").click();
  await page.waitForSelector('[data-testid="transcript-segments"] [data-word]', { timeout: 20_000 });
  const words = await page.locator("[data-word]").count();
  const text = await page.getByTestId("transcript-segments").innerText();
  check(words > 100, `transcript has words (${words})`);
  check(/welcome back to the channel/i.test(text), "transcript text matches the sample speech");
  await page.locator('[data-word="40"]').click();
  await page.waitForTimeout(800);
  const time = await page.getByTestId("transcript-video").evaluate((v) => v.currentTime);
  check(time > 1, `clicking a word seeks the video (t=${time.toFixed(2)}s)`);
  await page.getByTestId("transcript-video").evaluate((v) => v.pause());
  await page.screenshot({ path: shot("en-transcript.png") });
  await page.keyboard.press("Escape");

  await card.getByTestId("source-menu-button").click();
  await page.getByTestId("source-menu").getByRole("menuitem", { name: en["pages.library.delete"] }).click();
  await page.getByTestId("delete-media-checkbox").check();
  await page.getByTestId("confirm-button").click();
  await page.waitForSelector(sel, { state: "detached", timeout: 20_000 }).catch(() => {});
  check((await page.locator(sel).count()) === 0, "source deleted from the library");
  await context.close();
}

const browser = await chromium.launch({ channel: CHANNEL, headless: true });
try {
  // --- 1. Main run: English browser ----------------------------------------
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "en-US" });
  const page = await context.newPage();
  await page.goto(`${BASE}/library`);

  const status = page.getByTestId("backend-status");
  await page.waitForSelector('[data-testid="backend-status"][data-level="online"]', { timeout: 20_000 }).catch(() => {});
  const level = await status.getAttribute("data-level");
  check(level === "online", `backend status is green (data-level=${level})`);
  check((await page.locator("html").getAttribute("lang")) === "en", "English browser defaults to EN");

  const texts = { en: {}, vi: {} };
  for (const lang of ["en", "vi"]) {
    await page.getByTestId(`lang-${lang}`).click();
    for (const name of PAGES) {
      await page.getByTestId(`nav-${name}`).click();
      await page.waitForURL(`**/${name}`);
      if (name === "library" || name === "settings") {
        await page.waitForSelector('[data-testid="system-backend"]', { timeout: 10_000 }).catch(() => {});
      }
      texts[lang][name] = await page.evaluate(() => document.body.innerText);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      check(overflow <= 0, `${lang}/${name}: no horizontal overflow (${overflow}px)`);
      await page.waitForTimeout(250); // let nav color transitions finish
      await page.screenshot({ path: shot(`${lang}-${name}.png`) });
    }
    // Jobs drawer
    await page.getByTestId("jobs-button").click();
    texts[lang].jobs = await page.getByTestId("jobs-drawer").innerText();
    await page.keyboard.press("Escape");
  }

  for (const [lang, otherLocale, other] of [["vi", en, "en"], ["en", vi, "vi"]]) {
    const foreign = onlyIn(otherLocale, lang === "vi" ? vi : en);
    for (const [name, text] of Object.entries(texts[lang])) {
      const leaked = foreign.filter(({ value }) => text.includes(value));
      check(leaked.length === 0, `${lang}/${name}: no ${other} strings visible${leaked.length ? ` -> ${leaked.map((l) => l.key).join(", ")}` : ""}`);
    }
  }
  check(texts.vi.library.includes(vi["pages.library.drop"]), "VI library shows Vietnamese text");
  check(texts.en.library.includes(en["pages.library.drop"]), "EN library shows English text");

  // Shortcut + tooltip
  await page.keyboard.press("Alt+2");
  await page.waitForURL("**/moments");
  check(true, "Alt+2 navigates to Moments");

  // Persistence: currently EN (last loop). Switch to VI, reload, still VI.
  await page.getByTestId("lang-vi").click();
  await page.reload();
  check((await page.locator("html").getAttribute("lang")) === "vi", "language choice persists after reload");
  await context.close();

  // --- 2. Vietnamese browser default ---------------------------------------
  const viContext = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "vi-VN" });
  const viPage = await viContext.newPage();
  await viPage.goto(`${BASE}/library`);
  check((await viPage.locator("html").getAttribute("lang")) === "vi", "Vietnamese browser defaults to VI");
  await viContext.close();

  // --- 3. Backend offline -> error state -----------------------------------
  const offContext = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "en-US" });
  const offPage = await offContext.newPage();
  await offPage.route("**/api/**", (route) => route.abort());
  await offPage.goto(`${BASE}/library`);
  await offPage.waitForSelector('[data-testid="error-state"]', { timeout: 10_000 }).catch(() => {});
  check((await offPage.getByTestId("backend-status").getAttribute("data-level")) === "offline", "offline backend shows red status");
  const errorText = await offPage.getByTestId("error-state").innerText().catch(() => "");
  check(errorText.includes(en["errors.BACKEND_UNREACHABLE"]) && errorText.includes(en["common.retry"]), "offline error state with Retry");
  await offPage.getByRole("button", { name: en["common.view_log"] }).click();
  check((await offPage.getByTestId("error-state").innerText()).includes("uvicorn"), "View log shows how to start the backend");
  await offPage.screenshot({ path: shot("en-offline.png") });
  await offContext.close();

  // --- 4. Library flow on the real sample (Phase 1) ------------------------
  if (existsSync(SAMPLE) && process.env.SMOKE_LIBRARY !== "0") {
    await libraryFlow();
  } else {
    console.log("SKIP  library flow (samples/speech_en_sample.mp4 missing or SMOKE_LIBRARY=0)");
  }
} finally {
  await browser.close();
}

console.log(failures.length ? `\n${failures.length} check(s) failed` : "\nAll smoke checks passed");
process.exit(failures.length ? 1 : 0);
