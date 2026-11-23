// Render driver for the backend (pipeline/render.py): bundles the composition once (cached by a
// hash of src/ + package.json), serves the clip's media over a local HTTP server, then renders the
// muted video and/or stills of the "Short" composition.
//
// Usage: node scripts/render.mjs <request.json>
// request: { entry, bundleCache, media, props, output?, stills?: [{frame, output, props?}],
//            browser?, concurrency?, crf?, x264Preset? }
// stdout: one JSON object per line: {event: "bundle"|"progress"|"still"|"done"|"error", ...}

import { createHash } from "node:crypto";
import { createReadStream, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { bundle } from "@remotion/bundler";
import { openBrowser, renderMedia, renderStill, selectComposition } from "@remotion/renderer";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const emit = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");

function hashSources() {
  const h = createHash("sha256");
  const walk = (dir) => {
    for (const name of readdirSync(dir).sort()) {
      const p = path.join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else h.update(path.relative(ROOT, p)).update(readFileSync(p));
    }
  };
  walk(path.join(ROOT, "src"));
  h.update(readFileSync(path.join(ROOT, "package.json")));
  return h.digest("hex").slice(0, 16);
}

async function getBundle(entry, cacheDir) {
  const dir = path.join(cacheDir, hashSources());
  if (existsSync(path.join(dir, "index.html"))) {
    emit({ event: "bundle", progress: 1, cached: true });
    return dir;
  }
  const tmp = `${dir}.part`;
  rmSync(tmp, { recursive: true, force: true });
  mkdirSync(cacheDir, { recursive: true });
  await bundle({ entryPoint: entry, outDir: tmp, onProgress: (p) => emit({ event: "bundle", progress: p / 100 }) });
  rmSync(dir, { recursive: true, force: true });
  renameSync(tmp, dir);
  return dir;
}

/** Minimal static server for one file, with Range support (the compositor seeks in the video). */
function serveFile(file) {
  const size = statSync(file).size;
  const server = http.createServer((req, res) => {
    const range = /bytes=(\d*)-(\d*)/.exec(req.headers.range || "");
    const headers = { "Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Access-Control-Allow-Origin": "*" };
    if (range) {
      const start = range[1] ? Number(range[1]) : 0;
      const end = range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
      res.writeHead(206, { ...headers, "Content-Range": `bytes ${start}-${end}/${size}`, "Content-Length": end - start + 1 });
      if (req.method === "HEAD") return res.end();
      createReadStream(file, { start, end }).pipe(res);
    } else {
      res.writeHead(200, { ...headers, "Content-Length": size });
      if (req.method === "HEAD") return res.end();
      createReadStream(file).pipe(res);
    }
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

async function main() {
  const req = JSON.parse(readFileSync(process.argv[2], "utf8"));
  const serveUrl = await getBundle(req.entry || path.join(ROOT, "src", "index.ts"), req.bundleCache);
  let server = null;
  const withSrc = (props) => props;
  if (req.media) {
    server = await serveFile(req.media);
    const url = `http://127.0.0.1:${server.address().port}/${encodeURIComponent(path.basename(req.media))}`;
    for (const p of [req.props, ...(req.stills || []).map((s) => s.props).filter(Boolean)]) p.video.src = url;
  }
  const browser = await openBrowser("chrome", {
    browserExecutable: req.browser || null,
    chromiumOptions: { gl: "swiftshader" },
  });
  try {
    const composition = await selectComposition({ serveUrl, id: "Short", inputProps: withSrc(req.props), puppeteerInstance: browser });
    emit({ event: "composition", frames: composition.durationInFrames, fps: composition.fps, width: composition.width, height: composition.height });
    for (const still of req.stills || []) {
      await renderStill({
        serveUrl,
        composition,
        inputProps: still.props || req.props,
        frame: Math.min(still.frame, composition.durationInFrames - 1),
        output: still.output,
        imageFormat: "png",
        puppeteerInstance: browser,
      });
      emit({ event: "still", frame: still.frame, output: still.output });
    }
    if (req.output) {
      let last = -1;
      await renderMedia({
        serveUrl,
        composition,
        inputProps: req.props,
        codec: "h264",
        crf: req.crf ?? 16,
        x264Preset: req.x264Preset || "veryfast",
        pixelFormat: "yuv420p",
        muted: true,
        outputLocation: req.output,
        concurrency: req.concurrency ?? null,
        puppeteerInstance: browser,
        onProgress: ({ progress, renderedFrames }) => {
          const p = Math.round(progress * 1000) / 1000;
          if (p !== last) {
            last = p;
            emit({ event: "progress", progress: p, rendered: renderedFrames });
          }
        },
      });
    }
    emit({ event: "done" });
  } finally {
    await browser.close({ silent: true }).catch(() => {});
    server?.close();
  }
}

main().catch((err) => {
  emit({ event: "error", message: String(err?.stack || err).slice(0, 4000) });
  process.exit(1);
});
