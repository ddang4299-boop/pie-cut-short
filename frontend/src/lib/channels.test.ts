import { describe, expect, it } from "vitest";

import { newChannel, type Edl } from "./api";
import { applyChannelToEdl } from "./channels";

const edl = {
  id: "clip_1",
  source_id: "src_1",
  segments: [{ start: 1, end: 9 }],
  captions: { enabled: true, style: "bold", position_y: 0.72, words_per_page: 3, font_size: 72, words: [{ start: 1, end: 2, text: "Xin" }] },
  hook_text: { enabled: false, text: "Chào bạn", start: 0, end: 3.5, position_y: 0.18 },
  progress_bar: { enabled: true, color: "#FFD400" },
  voiceover: { enabled: true, script: "hi", voice_id: "old", audio_path: "voice_cache/tts_x.mp3", duration: 2 },
  platform: "youtube_shorts",
} as unknown as Edl;

describe("applyChannelToEdl (mirrors backend app/channels.py)", () => {
  const ch = { ...newChannel("TikTok"), platform: "tiktok" as const, caption_style: "bounce" as const, caption_position_y: 0.6,
    words_per_page: 2, hook_position_y: 0.3, progress_bar_enabled: false, progress_color: "#FF3D7F", voice_id: "voice_vi" };

  it("changes the look, keeps content", () => {
    const out = applyChannelToEdl(edl, ch);
    expect(out.platform).toBe("tiktok");
    expect(out.captions).toMatchObject({ style: "bounce", position_y: 0.6, words_per_page: 2, words: edl.captions.words });
    expect(out.hook_text).toEqual({ ...edl.hook_text, enabled: true, position_y: 0.3 });
    expect(out.progress_bar).toEqual({ enabled: false, color: "#FF3D7F" });
    expect(out.segments).toBe(edl.segments);
    expect(out.voiceover).toMatchObject({ voice_id: "voice_vi", audio_path: null, enabled: false, script: "hi" });
  });

  it("no hook text keeps the hook off; no channel voice keeps the clip's", () => {
    const out = applyChannelToEdl({ ...edl, hook_text: { ...edl.hook_text, text: " " } }, { ...ch, voice_id: "" });
    expect(out.hook_text.enabled).toBe(false);
    expect(out.voiceover).toBe(edl.voiceover);
  });
});
