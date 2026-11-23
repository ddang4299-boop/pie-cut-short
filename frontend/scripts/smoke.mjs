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
  const overflow = await page.evaluate(OVERFLOW);
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
  const overflow = await page.evaluate(OVERFLOW);
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
    if (restore) await api(`/clips/${restore.id}`, json("PATCH", { edl: restore.edl })); // undo the caption/hook test edits
    if (originalStatus !== "approved") await api(`/moments/${moment.id}`, json("PATCH", { status: originalStatus, snap: false }));
  }
}

let restore = null;

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

  await captionChecks(page, clipId);
  await voiceChecks(context, page, clipId);

  // Vietnamese layout + every tab
  await page.getByTestId("lang-vi").click();
  for (const tab of ["framing", "captions", "hook", "voice", "music", "audio", "export"]) {
    await page.getByTestId(`tab-${tab}`).click();
    const overflow = await page.evaluate(OVERFLOW);
    check(overflow <= 0, `vi/editor/${tab}: no horizontal overflow (${overflow}px)`);
    if (tab !== "framing") await page.screenshot({ path: shot(`vi-editor-${tab}.png`) });
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

/**
 * Captions + hook (Phase 4): editing a block's text updates the Remotion overlay on the 9:16
 * preview and the clip's SRT; Bold / Bounce / Clean all render Vietnamese and English text.
 */
async function captionChecks(page, clipId) {
  const api = (path) => fetch(`${BASE}/api${path}`).then((r) => r.json());
  const original = (await api(`/clips/${clipId}`)).edl;
  restore = { id: clipId, edl: { captions: original.captions, hook_text: original.hook_text } };

  await page.getByTestId("tab-captions").click();
  await page.getByTestId("wpp-3").click();
  const blocks = page.getByTestId("caption-block");
  check((await blocks.count()) >= 2, `captions tab lists caption blocks (${await blocks.count()})`);
  const VI = "Đặc biệt quá";
  const EN = "Really fast words";
  for (const [i, text] of [[0, VI], [1, EN]]) {
    const input = blocks.nth(i).getByTestId("caption-text");
    await input.fill(text);
    await input.press("Enter");
  }
  check((await blocks.nth(0).getByTestId("caption-text").inputValue()) === VI, "caption block text is editable");

  const overlayText = async (i) => {
    await blocks.nth(i).locator("button").first().click(); // seek to the block
    await page.waitForTimeout(350);
    return page.getByTestId("preview-overlay").getByTestId("caption-page").first()
      .evaluate((el) => ({ text: [...el.querySelectorAll("[data-word]")].map((w) => w.textContent.trim()).join(" "), style: el.dataset.style }))
      .catch(() => ({ text: "", style: "" }));
  };
  for (const style of ["bold", "bounce", "clean"]) {
    await page.getByTestId(`style-${style}`).click();
    for (const [i, text] of [[0, VI], [1, EN]]) {
      const want = style === "bold" ? text.toLocaleUpperCase("vi") : text;
      const got = await overlayText(i);
      check(got.style === style && got.text === want, `${style}: preview shows "${want}" (got "${got.text}")`);
    }
  }
  await page.screenshot({ path: shot("en-editor-captions.png") });
  check(await page.evaluate(() => document.fonts.check('900 72px "Be Vietnam Pro"', "Đặc biệt")), "Be Vietnam Pro is loaded for captions");

  // emphasis, find & replace
  const chip = blocks.nth(0).getByTestId("emphasis-chip").first();
  await chip.click();
  check((await chip.getAttribute("aria-pressed")) === "true", "clicking a word marks it emphasized");
  await page.getByTestId("find-input").fill("quá");
  check((await page.getByTestId("find-count").innerText()).includes("1"), "find counts the matches");
  await page.getByTestId("replace-input").fill("lắm");
  await page.getByTestId("replace-all").click();
  check((await blocks.nth(0).getByTestId("caption-text").inputValue()) === "Đặc biệt lắm", "replace all edits the caption words");

  // drag guide (keyboard) moves the caption position
  const y0 = Number(await page.getByTestId("caption-y").inputValue());
  await page.getByTestId("guide-captions").focus();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowUp");
  check(Number(await page.getByTestId("caption-y").inputValue()) === y0 - 2, "the caption guide on the preview moves the captions");

  // autosave -> SRT
  await page.waitForTimeout(1500);
  const srt = await fetch(`${BASE}/api/clips/${clipId}/captions.srt`).then((r) => r.text());
  check(srt.includes("Đặc biệt lắm") && srt.includes(EN), "the clip's SRT follows the caption edits");
  const saved = (await api(`/clips/${clipId}`)).edl;
  check(saved.captions.style === "clean" && saved.captions.words.some((w) => w.emphasis), "caption style and emphasis autosaved");

  // hook
  await page.getByTestId("tab-hook").click();
  await page.getByTestId("hook-enabled").check();
  await page.getByTestId("hook-input").fill("Đừng bỏ lỡ điều này");
  await page.getByTestId("editor-play").focus();
  await page.keyboard.press("Home");
  await page.waitForTimeout(350);
  const hook = await page.getByTestId("preview-overlay").getByTestId("hook-text").textContent().catch(() => "");
  check(hook.includes("Đừng bỏ lỡ điều này"), `hook text shows on the preview at 0s (got "${hook}")`);
  check((await page.getByTestId("guide-hook").count()) === 1, "hook tab shows the hook position guide");
  await page.screenshot({ path: shot("en-editor-hook.png") });
}

/**
 * Voiceover (Phase 5), only with the backend in mock mode (ELEVENLABS_MOCK=1: local test tones,
 * no network, no credits): the confirmation shows characters + credits, the voiceover plays in the
 * preview over the ducked original, regenerating the same script is free and makes no API call.
 */
async function voiceChecks(context, page, clipId) {
  const api = (path, init) => fetch(`${BASE}/api${path}`, init).then((r) => r.json());
  const status = await api("/voice/status");
  if (!status.mock) {
    console.log("SKIP  voice checks (start the backend with ELEVENLABS_MOCK=1)");
    return;
  }
  const original = (await api(`/clips/${clipId}`)).edl;
  restore = { id: clipId, edl: { ...(restore?.edl ?? {}), voiceover: original.voiceover, audio: original.audio } };
  const used = async () => (await api("/voice/status?refresh=true")).subscription.character_count;

  await page.getByTestId("tab-voice").click();
  await page.waitForSelector('[data-testid="voice-status"][data-mock="1"]', { timeout: 10_000 }).catch(() => {});
  check((await page.getByTestId("voice-status").count()) === 1, "voice tab shows the ElevenLabs status (mock mode)");
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="voice-select"] option').length >= 2, null, { timeout: 10_000 }).catch(() => {});
  await page.getByTestId("voice-select").selectOption("mock_vi_linh");

  const script = `Xin chào, đây là bài thử số ${Date.now() % 100000}`;
  await page.getByTestId("voice-script").fill(script);
  await page.waitForFunction(() => document.querySelector('[data-testid="voice-estimate"]')?.dataset.credits > 0, null, { timeout: 5000 }).catch(() => {});
  const credits = Number(await page.getByTestId("voice-estimate").getAttribute("data-credits"));
  check(credits === Math.ceil(script.length * 0.5), `estimate shows the credits before any call (${credits} for ${script.length} chars)`);

  const before = await used();
  await page.getByTestId("voice-generate").click();
  await page.waitForSelector('[data-testid="confirm-dialog"]', { timeout: 5000 }).catch(() => {});
  const body = await page.getByTestId("credit-body").innerText().catch(() => "");
  check(body.includes(String(script.length)) && body.includes(String(credits)), `generating asks first with characters + credits ("${body}")`);
  check((await used()) === before, "nothing is spent before confirming");
  await page.getByTestId("confirm-button").click();
  await page.waitForSelector('[data-testid="voiceover-block"]', { timeout: 20_000 }).catch(() => {});
  const vo = (await api(`/clips/${clipId}`)).edl.voiceover;
  check(!!vo.audio_path && vo.enabled && vo.duration > 0.5, `voiceover generated and placed on the timeline (${vo.audio_path}, ${vo.duration}s)`);
  check((await used()) === before + credits, "the mock quota counts the confirmed credits");

  // plays over the ducked original
  await page.getByTestId("voice-play").click();
  await page.waitForTimeout(900);
  const audio = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="clip-audio"]');
    const a = document.querySelector('[data-testid="voiceover-audio"]');
    const v = document.querySelector('[data-testid="editor-video"]');
    return { state: el?.dataset.voice, volume: Number(el?.dataset.volume), playing: a && !a.paused, at: a?.currentTime ?? 0, video: v?.volume };
  });
  const ducked = Math.pow(10, (original.audio.volume_db + vo.duck_db) / 20);
  check(audio.state === "on" && audio.playing && audio.at > 0.2, `voiceover plays in the preview (t=${audio.at.toFixed(2)}s)`);
  check(Math.abs(audio.video - Math.min(1, ducked)) < 0.01, `original audio is ducked by ${vo.duck_db} dB while it plays (volume ${audio.video?.toFixed(3)})`);
  await page.getByTestId("editor-play").click();

  // the same script again: cached, free, no confirmation, no API call
  await page.getByTestId("voice-script").fill(script + "!");
  await page.waitForTimeout(400);
  check((await page.getByTestId("voice-stale").count()) === 1, "editing the script marks the voiceover as outdated");
  await page.getByTestId("voice-script").fill(script);
  await page.waitForSelector('[data-testid="voice-estimate"][data-cached="1"]', { timeout: 5000 }).catch(() => {});
  check((await page.getByTestId("voice-estimate").getAttribute("data-credits")) === "0", "the same script shows as cached (0 credits)");
  const spent = await used();
  await page.getByTestId("voice-generate").click();
  await page.waitForTimeout(800);
  check((await page.getByTestId("confirm-dialog").count()) === 0 && (await used()) === spent, "regenerating the same script costs nothing and asks nothing");

  // drag the block on the timeline + undo
  const block = await page.getByTestId("voiceover-block").boundingBox();
  await page.mouse.move(block.x + 6, block.y + block.height / 2);
  await page.mouse.down();
  await page.mouse.move(block.x + 66, block.y + block.height / 2, { steps: 6 });
  await page.mouse.up();
  const moved = Number(await page.getByTestId("voiceover-block").getAttribute("data-start"));
  check(moved > vo.start + 0.2, `dragging the voiceover block moves its start (${vo.start} -> ${moved})`);
  await page.keyboard.press("Control+z");
  check(Number(await page.getByTestId("voiceover-block").getAttribute("data-start")) === vo.start, "Ctrl+Z undoes the move");
  await page.screenshot({ path: shot("en-editor-voice.png") });

  // Voice Isolator: asks first, then the preview uses the clean voice
  await page.getByTestId("tab-audio").click();
  await page.waitForFunction(() => /\d/.test(document.querySelector('[data-testid="isolate-estimate"]')?.textContent ?? ""), null, { timeout: 5000 }).catch(() => {});
  const isoEst = await api(`/clips/${clipId}/isolate/estimate`);
  await page.getByTestId("isolate-toggle").click();
  if (isoEst.cached) {
    await page.waitForTimeout(800);
    check((await page.getByTestId("confirm-dialog").count()) === 0, "Voice Isolator reuses the cached result without asking");
  } else {
    await page.waitForSelector('[data-testid="confirm-dialog"]', { timeout: 5000 }).catch(() => {});
    check((await page.getByTestId("confirm-dialog").count()) === 1, `Voice Isolator asks before spending credits (${isoEst.credits})`);
    await page.getByTestId("confirm-button").click();
  }
  await page.waitForFunction(() => document.querySelector('[data-testid="isolate-toggle"]')?.checked, null, { timeout: 20_000 }).catch(() => {});
  const iso = (await api(`/clips/${clipId}`)).edl.audio;
  check(iso.isolate_voice && !!iso.isolated_path, `isolated voice saved in the EDL (${iso.isolated_path})`);
  await page.getByTestId("isolate-toggle").click();
  await page.waitForTimeout(800);
  check((await api(`/clips/${clipId}`)).edl.audio.isolate_voice === false, "turning the isolator off is free and saved");

  // Settings shows the key status
  const settings = await context.newPage();
  await settings.goto(`${BASE}/settings`);
  await settings.waitForSelector('[data-testid="voice-settings"]', { timeout: 10_000 }).catch(() => {});
  const keyText = await settings.getByTestId("voice-key-status").innerText().catch(() => "");
  check(keyText.includes(en["pages.settings.voice_key_mock"]), `Settings shows the ElevenLabs key status ("${keyText}")`);
  await settings.close();
}

const formatTc = (t) => {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
};

// `value` as a whole word (letters/digits around it do not count as a match)
const wordRe = (value) => new RegExp(`(?<![\\p{L}\\p{N}])${value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\p{L}\\p{N}])`, "u");
const wordIn = (text, value) => wordRe(value).test(text);

// horizontal overflow of the window and of the page area (<main> scrolls on its own)
const OVERFLOW = () => {
  const main = document.querySelector("main");
  return Math.max(document.documentElement.scrollWidth - window.innerWidth, main ? main.scrollWidth - main.clientWidth : 0);
};

/**
 * Phase 7: Settings (channel preset add/rename/delete through the UI, cache panel + confirm then
 * cancel, VI layout with the channel editor open), Moments multi-select bar.
 */
async function phase7Flow(sourceWithMoments) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "vi-VN" });
  const page = await context.newPage();
  const before = await fetch(`${BASE}/api/settings`).then((r) => r.json());
  await page.goto(`${BASE}/settings`);
  await page.getByTestId("lang-vi").click();
  await page.waitForSelector('[data-testid="settings-ai"]', { timeout: 15_000 });
  for (const id of ["settings-ai", "settings-transcribe", "settings-render", "settings-presets", "settings-channels", "settings-cache"]) {
    check(await page.getByTestId(id).isVisible(), `vi/settings: section ${id} visible`);
  }
  await page.getByTestId("channel-add").click();
  await page.waitForSelector('[data-testid="channel-editor"]', { timeout: 10_000 });
  const name = page.getByTestId("channel-name");
  await name.fill("Kênh thử nghiệm");
  await name.press("Enter");
  await page.getByTestId("channel-style").selectOption("bounce");
  await page.waitForTimeout(800);
  const saved = await fetch(`${BASE}/api/settings`).then((r) => r.json());
  const created = saved.channels.find((c) => !before.channels.some((b) => b.id === c.id));
  check(created?.name === "Kênh thử nghiệm" && created?.caption_style === "bounce", "channel preset saved from the UI (name + style)");
  const overflow = await page.evaluate(OVERFLOW);
  check(overflow <= 0, `vi/settings with channel editor open: no horizontal overflow (${overflow}px)`);
  await page.getByTestId("settings-channels").screenshot({ path: shot("vi-settings-channels.png") });

  // cache: sizes listed, confirm dialog opens, cancel keeps everything
  await page.waitForSelector('[data-testid="cache-transcripts"]', { timeout: 15_000 });
  const cacheText = await page.getByTestId("settings-cache").innerText();
  check(cacheText.includes(vi["cache.transcripts"]) && cacheText.includes(vi["cache.voice_generated"]), "vi/settings: cache rows translated");
  if (await page.getByTestId("cache-clear-all").isEnabled()) {
    await page.getByTestId("cache-clear-all").click();
    check(await page.getByTestId("confirm-button").isVisible(), "clearing the cache asks for confirmation");
    await page.keyboard.press("Escape");
  }
  await page.getByTestId("settings-cache").screenshot({ path: shot("vi-settings-cache.png") });

  // delete the test channel through the UI
  const row = page.getByTestId("channel-row").filter({ hasText: "Kênh thử nghiệm" });
  await row.getByRole("button", { name: vi["pages.settings.channel_delete"] }).click();
  await page.getByTestId("confirm-button").click();
  await page.waitForTimeout(800);
  const after = await fetch(`${BASE}/api/settings`).then((r) => r.json());
  check(after.channels.length === before.channels.length, "channel preset deleted from the UI");

  // Moments multi-select (no change is saved)
  if (sourceWithMoments) {
    await page.goto(`${BASE}/moments`);
    await page.getByTestId("moments-source-select").selectOption(sourceWithMoments.id);
    await page.waitForSelector('[data-testid="moment-card"]', { timeout: 20_000 });
    const boxes = page.getByTestId("moment-check");
    await boxes.nth(0).check();
    await boxes.nth(1).check();
    const bar = await page.getByTestId("batch-bar").innerText();
    check(bar.includes(vi["pages.moments.batch_selected"].replace("{{count}}", "2")), "vi/moments: batch bar shows 2 selected");
    check(await page.getByTestId("batch-approve").isVisible() && await page.getByTestId("batch-reject").isVisible(), "batch approve / reject buttons shown");
    const ov = await page.evaluate(OVERFLOW);
    check(ov <= 0, `vi/moments with batch bar: no horizontal overflow (${ov}px)`);
    await page.screenshot({ path: shot("vi-moments-batch.png") });
    await page.getByTestId("check-all").check();
    await page.getByTestId("check-all").uncheck();
    check((await page.getByTestId("batch-approve").count()) === 0, "unchecking all hides the batch buttons");
  }
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
      // text marked data-content (titles, transcripts) follows the video's language, not the UI's
      texts[lang][name] = await page.evaluate(() =>
        [...document.querySelectorAll("[data-content]")].reduce((text, el) => text.split(el.innerText).join(" "), document.body.innerText));
      const overflow = await page.evaluate(OVERFLOW);
      check(overflow <= 0, `${lang}/${name}: no horizontal overflow (${overflow}px)`);
      const cutTabs = await page.$$eval('[role="tab"]', (els) => els.filter((e) => e.scrollWidth > e.clientWidth).map((e) => e.innerText));
      check(cutTabs.length === 0, `${lang}/${name}: no truncated tab labels${cutTabs.length ? ` -> ${cutTabs.join(", ")}` : ""}`);
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
      // whole words only, so "Open" does not match inside the brand name "OpenAI"
      const leaked = foreign.filter(({ value }) => wordIn(text, value));
      check(leaked.length === 0, `${lang}/${name}: no ${other} strings visible${leaked.length ? ` -> ${leaked.map((l) => l.key).join(", ")}` : ""}`);
      for (const { key, value } of leaked) {
        const i = text.search(wordRe(value));
        console.log(`      ${key}: …${JSON.stringify(text.slice(Math.max(0, i - 50), i + value.length + 30))}…`);
      }
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

  // --- 5b. Phase 7: Settings + batch selection ------------------------------
  await phase7Flow(withMoments ?? sources.find((s) => s.moments?.total >= 2));

  // --- 6. Clip Editor (Phase 3) ---------------------------------------------
  const editable = sources.find((s) => s.status === "ready" && s.moments.total > 0 && s.proxy_url);
  if (editable) await editorFlow(editable);
  else console.log("SKIP  editor flow (no ready source with moments)");
} finally {
  await browser.close();
}

console.log(failures.length ? `\n${failures.length} check(s) failed` : "\nAll smoke checks passed");
process.exit(failures.length ? 1 : 0);
