import { z } from "zod";

/**
 * Props of the "Short" composition: the clip's EDL (the single source of truth, see CLAUDE.md)
 * plus the source video and the content language. Mirrors backend/app/edl.py; unknown EDL keys
 * are ignored.
 */
const region = z.tuple([z.number(), z.number(), z.number(), z.number()]);

export const captionWordSchema = z.object({
  start: z.number().min(0),
  end: z.number().min(0),
  text: z.string(),
  emphasis: z.boolean().optional(),
});

export const edlSchema = z.object({
  segments: z.array(z.object({ start: z.number().min(0), end: z.number().min(0) })).min(1),
  crop: z.object({
    mode: z.enum(["auto", "manual", "split", "cursor"]),
    easing: z.enum(["linear", "ease_in_out"]),
    fit: z.enum(["fill", "blur"]),
    keyframes: z.array(z.object({ t: z.number(), x: z.number(), y: z.number(), zoom: z.number() })),
    split: z.object({ top: region, bottom: region, ratio: z.number() }).nullable(),
  }),
  captions: z.object({
    enabled: z.boolean(),
    style: z.enum(["bold", "bounce", "clean"]),
    position_y: z.number().min(0).max(1),
    words_per_page: z.number().int().min(1).max(4),
    font_size: z.number().positive(),
    words: z.array(captionWordSchema),
  }),
  hook_text: z.object({
    enabled: z.boolean(),
    text: z.string(),
    start: z.number().min(0),
    end: z.number().min(0),
    position_y: z.number().min(0).max(1),
  }),
  progress_bar: z.object({ enabled: z.boolean(), color: z.string().regex(/^#[0-9a-fA-F]{6}$/) }),
  audio: z.object({ volume_db: z.number() }),
  output: z.object({ w: z.number().int().positive(), h: z.number().int().positive(), fps: z.number().positive() }),
});

export const compositionSchema = z.object({
  edl: edlSchema,
  video: z.object({
    src: z.string().nullable(), // URL or file path the renderer can read; null = no video layer
    width: z.number().int().positive(), // source resolution (keyframes are normalized to it)
    height: z.number().int().positive(),
  }),
  language: z.string(), // content language (en | vi | mixed): used for locale-aware ALL CAPS
  showVideo: z.boolean(), // false = overlay only (the editor draws the video itself)
});

export type EdlProps = z.infer<typeof edlSchema>;
export type CompositionProps = z.infer<typeof compositionSchema>;
