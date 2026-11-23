import type { Channel, Edl } from "./api";

/**
 * Apply a channel preset to an EDL (same rules as backend app/channels.py `edl_patch`):
 * platform, caption look, hook on/off + position, progress bar, and the voice. Segments, framing,
 * caption words, hook text and the voiceover script are kept. A new voice drops generated audio.
 */
export function applyChannelToEdl(edl: Edl, ch: Channel): Edl {
  const next: Edl = {
    ...edl,
    platform: ch.platform,
    captions: {
      ...edl.captions,
      style: ch.caption_style,
      position_y: ch.caption_position_y,
      words_per_page: ch.words_per_page,
      font_size: ch.font_size,
    },
    hook_text: { ...edl.hook_text, enabled: ch.hook_enabled && Boolean(edl.hook_text.text.trim()), position_y: ch.hook_position_y },
    progress_bar: { ...edl.progress_bar, enabled: ch.progress_bar_enabled, color: ch.progress_color },
  };
  if (ch.voice_id && edl.voiceover.voice_id !== ch.voice_id) {
    next.voiceover = { ...edl.voiceover, voice_id: ch.voice_id, audio_path: null, duration: null, enabled: false };
  }
  return next;
}
