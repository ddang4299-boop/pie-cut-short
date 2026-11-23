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
  const existing = await fetch(`${BASE}/api/sources`).then((r) => r.json()).then((list) => new Set(list.map((s) => s.id)));
  await page.getByTestId("file-input").setInputFiles(SAMPLE);

  // the new card (the same file may already be in the library from an earlier run)
  const newCard = page.locator('[data-testid="source-card"]').filter({ hasText: "speech_en_sample" });
  let id = null;
  for (let i = 0; i < 240 && !id; i++) {
    const ids = await newCard.evaluateAll((els) => els.map((e) => e.getAttribute("data-source-id")));
    id = ids.find((x) => !existing.has(x)) ?? null;
    if (!id) await page.waitForTimeout(500);
  }
  const sel = `[data-testid="source-card"][data-source-id="${id}"]`;
  const card = page.locator(sel);
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
  // with the default claude_code provider the pipeline ends "waiting" for /find-moments
  await page.waitForSelector(["ready", "waiting", "error"].map((st) => `${sel}[data-status="${st}"]`).join(", "), { timeout: 900_000 });
  const seconds = Math.round((Date.now() - started) / 1000);
  const status = await card.getAttribute("data-status");
  check(status === "ready" || status === "waiting", `pipeline finished (status=${status}, ${seconds}s)`);
  for (const step of ["preflight", "proxy", "transcribe", "analyze"]) {
    const state = await card.locator(`[data-step="${step}"]`).getAttribute("data-state");
    check(state === "done", `step ${step} is done (${state})`);
  }
  if (status === "waiting") {
    const panel = card.getByTestId("waiting-panel");
    check((await panel.getByTestId("find-command").innerText()) === `/find-moments ${id}`, "waiting card shows the /find-moments command");
    await page.screenshot({ path: shot("en-library-waiting.png") });
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

/**
 * Moments Review (Phase 2) on a source that already has proposals (moments.json imported):
 * 8–12 proposals shown, adjust in/out (typed + handle drag, snapped), approve 2, create 1 from a
 * transcript selection, reject + undo with the keyboard, reload -> everything persisted.
 * The source is restored afterwards (statuses + ranges reset, the manual moment deleted).
 */
async function momentsFlow(source) {
  const api = (path, init) => fetch(`${BASE}/api${path}`, init).then((r) => r.json());
  const before = await api(`/sources/${source.id}/moments`);
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "en-US" });
  const page = await context.newPage();
  await page.goto(`${BASE}/library`);
  await page.getByTestId("lang-en").click();
  await page.goto(`${BASE}/moments`);
  await page.getByTestId("moments-source-select").selectOption(source.id);
  await page.waitForSelector('[data-testid="moment-card"]', { timeout: 20_000 });
  const cards = page.getByTestId("moment-card");
  const count = await cards.count();
  check(count >= 8 && count <= 12, `Moments shows 8–12 proposals (${count})`);
  const ringTones = await page.getByTestId("score-ring").evaluateAll((els) => els.map((e) => e.getAttribute("data-tone")));
  check(ringTones.every(Boolean), "every card has a colored score ring");
  await page.waitForFunction(() => document.querySelector('[data-testid="player-video"]')?.readyState >= 1, null, { timeout: 20_000 });
  await page.screenshot({ path: shot("en-moments.png") });

  // adjust in/out on the 2nd card: type an in point, then drag the out handle
  await cards.nth(1).click();
  const id = Number(await cards.nth(1).getAttribute("data-moment-id"));
  const original = before.find((m) => m.id === id);
  const inField = page.getByTestId("in-field");
  await inField.click();
  await inField.fill(formatTc(original.start + 1.3));
  await inField.press("Enter");
  await page.waitForSelector('[data-testid="snapped-flash"]', { timeout: 5_000 }).catch(() => {});
  check(await page.getByTestId("snapped-flash").isVisible().catch(() => false), "typed in point is snapped and the result is shown");
  const handle = page.getByTestId("handle-end");
  const box = await handle.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x - 60, box.y + box.height / 2, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(1200); // 500 ms debounce + request
  const adjusted = (await api(`/sources/${source.id}/moments`)).find((m) => m.id === id);
  check(adjusted.start > original.start + 0.5 && adjusted.end < original.end - 0.5,
    `in/out adjusted and saved (${original.start}–${original.end} -> ${adjusted.start}–${adjusted.end})`);
  const words = (await api(`/sources/${source.id}/transcript`)).words;
  const inside = (t) => words.some((w) => w.start + 1e-3 < t && t < w.end - 1e-3);
  check(!inside(adjusted.start) && !inside(adjusted.end), "adjusted cut points are not inside a word");

  // approve two (button + keyboard)
  await cards.nth(0).getByTestId("approve-button").click();
  await cards.nth(2).click();
  await page.keyboard.press("a");
  // reject + undo
  await cards.nth(3).click();
  await page.keyboard.press("r");
  check((await cards.nth(3).getAttribute("data-status")) === "rejected", "R rejects the selected moment");
  await page.keyboard.press("Control+z");
  check((await cards.nth(3).getAttribute("data-status")) === "proposed", "Ctrl+Z undoes the reject");

  // create a manual moment from a transcript selection
  const from = page.locator('[data-testid="moments-transcript"] [data-i="300"]');
  const to = page.locator('[data-testid="moments-transcript"] [data-i="330"]');
  await from.scrollIntoViewIfNeeded();
  const a = await from.boundingBox();
  const b = await to.boundingBox();
  await page.mouse.move(a.x + 1, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width - 1, b.y + b.height / 2, { steps: 8 });
  await page.mouse.up();
  await page.getByTestId("create-from-selection").click();
  await page.waitForTimeout(1200);
  check((await page.getByTestId("delete-moment").count()) === 1, "a manual moment was created from the transcript selection");
  const manualTitle = await page.getByTestId("title-field").inputValue();
  check(manualTitle.length > 10, `the manual moment is titled from the selected text ("${manualTitle}")`);
  await page.getByTestId("lang-vi").click();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(overflow <= 0, `vi/moments: no horizontal overflow (${overflow}px)`);
  await page.screenshot({ path: shot("vi-moments-edited.png") });
  await page.getByTestId("lang-en").click();
  check((await page.getByTestId("save-status").getAttribute("data-status").catch(() => "saved")) !== "error", "autosave reports no error");

  // reload -> persisted
  await page.reload();
  await page.waitForSelector('[data-testid="moment-card"]', { timeout: 20_000 });
  const statuses = await page.getByTestId("moment-card").evaluateAll((els) => els.map((e) => e.getAttribute("data-status")));
  check(statuses.filter((s) => s === "approved").length === 3, `after reload: 2 approved + 1 manual (approved) (${statuses.join(",")})`);
  check((await page.getByTestId("delete-moment").count()) === 1, "after reload: the manual moment is still there");
  const persisted = (await api(`/sources/${source.id}/moments`)).find((m) => m.id === id);
  check(persisted.start === adjusted.start && persisted.end === adjusted.end, "after reload: adjusted in/out persisted");
  await page.screenshot({ path: shot("en-moments-reloaded.png") });
  await context.close();

  // restore the source for the next run
  const after = await api(`/sources/${source.id}/moments`);
  for (const m of after) {
    const orig = before.find((o) => o.id === m.id);
    const init = { method: orig ? "PATCH" : "DELETE", headers: { "content-type": "application/json" } };
    if (orig) init.body = JSON.stringify({ status: orig.status, start: orig.start, end: orig.end, snap: false });
    await fetch(`${BASE}/api/moments/${m.id}`, init);
  }
}

/**
 * Clip Editor (Phase 3): open a clip from an approved moment, wait for the automatic framing,
 * edit a keyframe number and check the 9:16 preview redraws and the EDL is saved, drag the crop
 * box (switches to manual), split a segment + undo, safe zones, VI layout, reload persistence.
 */
async function editorFlow(source) {
  const api = (path, init) => fetch(`${BASE}/api${path}`, init).then((r) => r.json());
  const json = (method, body) => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const moments = await api(`/sources/${source.id}/moments`);
  const moment = moments.find((m) => m.status === "approved") ?? moments[0];
  const originalStatus = moment.status;
  if (originalStatus !== "approved") await api(`/moments/${moment.id}`, json("PATCH", { status: "approved", snap: false }));

  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "en-US" });
  try {
    await editorChecks(context, moment);
  } finally {
    await context.close();
    if (originalStatus !== "approved") await api(`/moments/${moment.id}`, json("PATCH", { status: originalStatus, snap: false }));
  }
}

async function editorChecks(context, moment) {
  const api = (path, init) => fetch(`${BASE}/api${path}`, init).then((r) => r.json());
  const page = await context.newPage();
  await page.goto(`${BASE}/library`);
  await page.getByTestId("lang-en").click();
  await page.goto(`${BASE}/editor?moment=${moment.id}`);
  await page.waitForSelector('[data-testid="clip-editor"]', { timeout: 20_000 });
  check(page.url().includes("clip="), "?moment= opens the clip and the URL switches to ?clip=");
  const clipId = await page.getByTestId("clip-editor").getAttribute("data-clip-id");
  await page.waitForFunction(() => ["done", "error"].includes(document.querySelector('[data-testid="reframe-status"]')?.dataset.state), null, { timeout: 120_000 }).catch(() => {});
  const state = await page.getByTestId("reframe-status").getAttribute("data-state");
  check(state === "done", `automatic framing finished (state=${state})`);
  await page.waitForFunction(() => document.querySelector('[data-testid="editor-video"]')?.readyState >= 2, null, { timeout: 20_000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: shot("en-editor.png") });

  const canvasHash = () =>
    page.getByTestId("preview-canvas").evaluate((c) => {
      const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
      let h = 0;
      for (let i = 0; i < d.length; i += 97) h = (h * 31 + d[i]) >>> 0;
      return h;
    });

  // the first keyframe's x: type a new value -> preview redraws, EDL saved
  await page.getByTestId("mode-manual").click();
  await page.getByTestId("fit").selectOption("fill"); // screen recordings without a visible cursor fall back to blur
  await page.waitForTimeout(300);
  const before = await canvasHash();
  const xField = page.getByTestId("kf-x").first();
  const oldX = Number(await xField.inputValue());
  const newX = oldX > 0.4 ? 0.2 : 0.8;
  await xField.fill(String(newX));
  await xField.press("Enter");
  await page.keyboard.press("Tab");
  const t0 = Date.now();
  let after = before;
  for (let i = 0; i < 20 && after === before; i++) {
    await page.waitForTimeout(25);
    after = await canvasHash();
  }
  const redrawMs = Date.now() - t0;
  check(after !== before, `editing a keyframe redraws the 9:16 preview (${redrawMs} ms)`);
  await page.waitForTimeout(1200);
  const savedClip = await api(`/clips/${clipId}`);
  const firstX = [...savedClip.edl.crop.keyframes].sort((a, b) => a.t - b.t)[0].x;
  check(Math.abs(firstX - Number(await xField.inputValue())) < 0.002 && Math.abs(firstX - oldX) > 0.05, `keyframe edit autosaved (x ${oldX} -> ${firstX})`);

  // drag the crop box in auto mode -> manual + keyframe at playhead
  await page.getByTestId("mode-auto").click();
  const box = await page.getByTestId("crop-box").boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2, { steps: 5 });
  await page.mouse.up();
  check((await page.getByTestId("mode-manual").getAttribute("aria-checked")) === "true", "dragging the crop box switches to manual");

  // split + undo
  const segs = () => page.getByTestId("timeline-segment").count();
  await page.getByTestId("timeline-scroller").click({ position: { x: 200, y: 10 } });
  await page.keyboard.press("s");
  check((await segs()) === 2, "S splits the segment at the playhead");
  await page.keyboard.press("Control+z");
  check((await segs()) === 1, "Ctrl+Z undoes the split");

  // safe zones toggle
  const safe = await page.getByTestId("safe-zones").count();
  await page.getByTestId("toggle-safe-zones").click();
  check((await page.getByTestId("safe-zones").count()) !== safe, "safe-zone overlay toggles");
  if (!safe) await page.getByTestId("toggle-safe-zones").click();

  // Vietnamese layout + every tab
  await page.getByTestId("lang-vi").click();
  for (const tab of ["framing", "captions", "hook", "voice", "audio", "export"]) {
    await page.getByTestId(`tab-${tab}`).click();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(overflow <= 0, `vi/editor/${tab}: no horizontal overflow (${overflow}px)`);
  }
  await page.getByTestId("tab-framing").click();
  await page.screenshot({ path: shot("vi-editor.png") });
  await page.getByTestId("lang-en").click();
  await page.waitForTimeout(1200);
  check((await page.getByTestId("save-status").getAttribute("data-status").catch(() => "saved")) !== "error", "editor autosave reports no error");

  // reload -> persisted manual framing
  const saved = (await api(`/clips/${clipId}`)).edl;
  await page.reload();
  await page.waitForSelector('[data-testid="clip-editor"]', { timeout: 20_000 });
  check((await page.getByTestId("mode-manual").getAttribute("aria-checked")) === "true" && saved.crop.mode === "manual", "after reload: manual framing persisted");

  // back to Moments
  await page.getByTestId("back-to-moments").click();
  await page.waitForURL("**/moments");
  check(true, "← Moments returns to Moments");
}

const formatTc = (t) => {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
};

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
      // text marked data-content (titles, transcripts) follows the video's language, not the UI's
      texts[lang][name] = await page.evaluate(() =>
        [...document.querySelectorAll("[data-content]")].reduce((text, el) => text.split(el.innerText).join(" "), document.body.innerText));
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

  // --- 5. Moments Review on a source with proposals (Phase 2) ---------------
  const sources = await fetch(`${BASE}/api/sources`).then((r) => r.json()).catch(() => []);
  const withMoments = sources.find((s) => s.status === "ready" && s.moments.total >= 8);
  if (withMoments) await momentsFlow(withMoments);
  else console.log("SKIP  moments flow (no ready source with ≥ 8 moments: run /find-moments on the sample first)");

  // --- 6. Clip Editor (Phase 3) ---------------------------------------------
  const editable = sources.find((s) => s.status === "ready" && s.moments.total > 0 && s.proxy_url);
  if (editable) await editorFlow(editable);
  else console.log("SKIP  editor flow (no ready source with moments)");
} finally {
  await browser.close();
}

console.log(failures.length ? `\n${failures.length} check(s) failed` : "\nAll smoke checks passed");
process.exit(failures.length ? 1 : 0);
