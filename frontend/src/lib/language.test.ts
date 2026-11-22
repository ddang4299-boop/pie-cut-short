import { describe, expect, it } from "vitest";

import { backendLevel } from "../state/health";
import { ApiError, type Health } from "./api";
import { detectInitialLanguage, languageFromBrowser } from "./language";

describe("language detection", () => {
  it("uses Vietnamese when the browser prefers vi", () => {
    expect(languageFromBrowser(["vi-VN", "en-US"])).toBe("vi");
    expect(languageFromBrowser(["vi"])).toBe("vi");
  });

  it("falls back to English otherwise", () => {
    expect(languageFromBrowser(["en-US", "vi-VN"])).toBe("en");
    expect(languageFromBrowser(["fr-FR"])).toBe("en");
    expect(languageFromBrowser([])).toBe("en");
  });

  it("a saved choice wins over the browser language", () => {
    expect(detectInitialLanguage("en", ["vi-VN"])).toBe("en");
    expect(detectInitialLanguage("vi", ["en-US"])).toBe("vi");
  });

  it("ignores invalid saved values", () => {
    expect(detectInitialLanguage("de", ["vi-VN"])).toBe("vi");
    expect(detectInitialLanguage(null, ["en-GB"])).toBe("en");
  });
});

describe("backendLevel", () => {
  const health = (overrides: Partial<Health> = {}): Health => ({
    status: "ok",
    app_version: "0.1.0",
    python: "3.14",
    platform: "Windows 11",
    ffmpeg: { available: true, version: "9", path: "ffmpeg" },
    ffprobe: { available: true, version: "9", path: "ffprobe" },
    device: "cuda",
    gpu: null,
    encoder: "h264_nvenc",
    issues: [],
    ...overrides,
  });

  it("is green when healthy", () => {
    expect(backendLevel(health(), null, false)).toBe("online");
  });

  it("is checking before the first answer", () => {
    expect(backendLevel(null, null, true)).toBe("checking");
  });

  it("is offline on request errors", () => {
    const err = new ApiError({ code: "BACKEND_UNREACHABLE", severity: "error", params: {} }, 0);
    expect(backendLevel(health(), err, false)).toBe("offline");
  });

  it("is warning on warnings and degraded on errors", () => {
    expect(backendLevel(health({ issues: [{ code: "GPU_NOT_AVAILABLE", severity: "warning", params: {} }] }), null, false)).toBe(
      "warning",
    );
    expect(backendLevel(health({ status: "degraded" }), null, false)).toBe("degraded");
  });
});
