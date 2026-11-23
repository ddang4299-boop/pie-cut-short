import type { CropFit, CropMode, Easing, Keyframe, Region, Segment } from "./edl";
import type { CaptionWord } from "./captions";

/** Thin fetch wrapper. The backend always answers errors as {code, severity, params}. */

export type Severity = "error" | "warning" | "info";

export interface ApiIssue {
  code: string;
  severity: Severity;
  params: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly issue: ApiIssue;
  readonly status: number;

  constructor(issue: ApiIssue, status: number) {
    super(issue.code);
    this.issue = issue;
    this.status = status;
  }
}

export interface ToolInfo {
  available: boolean;
  version: string | null;
  path: string | null;
}

export interface Health {
  status: "ok" | "degraded";
  app_version: string;
  python: string;
  platform: string;
  ffmpeg: ToolInfo;
  ffprobe: ToolInfo;
  device: "cuda" | "mps" | "cpu";
  gpu: { vendor: string; name: string; memory_mb: number | null } | null;
  encoder: string;
  issues: ApiIssue[];
}

const API_BASE = "/api";

function isIssue(value: unknown): value is ApiIssue {
  return typeof value === "object" && value !== null && typeof (value as ApiIssue).code === "string";
}

function unreachable(): ApiError {
  return new ApiError({ code: "BACKEND_UNREACHABLE", severity: "error", params: {} }, 0);
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>(path, init);
}

/** POST/PATCH/DELETE with an optional JSON body. */
export function apiSend<T>(method: "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** Backend-relative URL (e.g. a source's proxy_url) as something the browser can load. */
export function apiUrl(path: string): string {
  return API_BASE + path;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(API_BASE + path, init);
  } catch {
    throw unreachable();
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Not JSON: when the backend is down the Vite proxy answers with a plain-text 5xx.
    if (response.status >= 500) throw unreachable();
  }
  if (!response.ok) {
    const issue: ApiIssue = isIssue(body)
      ? { code: body.code, severity: body.severity ?? "error", params: body.params ?? {} }
      : { code: "INTERNAL_ERROR", severity: "error", params: {} };
    throw new ApiError(issue, response.status);
  }
  return body as T;
}

export function getHealth(refresh = false): Promise<Health> {
  return apiGet<Health>(refresh ? "/health?refresh=true" : "/health");
}

// ---------------------------------------------------------------------------
// Sources, jobs, transcripts (Phase 1)
// ---------------------------------------------------------------------------
export const STEPS = ["preflight", "proxy", "transcribe", "analyze", "moments"] as const;
export type Step = (typeof STEPS)[number];
export const RERUNNABLE_STEPS: Step[] = ["preflight", "proxy", "transcribe", "analyze", "moments"];
export type StepState = "pending" | "queued" | "running" | "done" | "skipped" | "error" | "waiting";
export const CONTENT_TYPES = ["talking_head", "screen_recording", "podcast", "gameplay_facecam"] as const;
export type ContentType = (typeof CONTENT_TYPES)[number];
export const LANGUAGE_OPTIONS = ["auto", "vi", "en", "mixed"] as const;
export type LanguageOption = (typeof LANGUAGE_OPTIONS)[number];
export type SourceStatus = "downloading" | "new" | "processing" | "waiting" | "ready" | "error";
export const PLATFORMS = ["youtube_shorts", "tiktok", "reels"] as const;
export type Platform = (typeof PLATFORMS)[number];
export const CAPTION_STYLES = ["bold", "bounce", "clean"] as const;
export type CaptionStyle = (typeof CAPTION_STYLES)[number];
export const LLM_PROVIDERS = ["claude_code", "anthropic", "openai", "ollama", "heuristic"] as const;
export type LlmProvider = (typeof LLM_PROVIDERS)[number];
export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";

export interface StepInfo {
  state: StepState;
  progress: number;
  error?: ApiIssue | null;
  stage?: string | null;
  notices?: ApiIssue[];
  provider?: string;
  request_files?: string[];
}

export interface PreflightResult {
  ok: boolean;
  file: { name: string; path: string; size_bytes: number | null };
  probe: {
    duration: number;
    size_bytes: number;
    format_name: string;
    video: { codec: string; display_width: number; display_height: number; fps: number } | null;
    audio: { codec: string; sample_rate: number | null; channels: number | null } | null;
    is_vfr: boolean;
  } | null;
  device: string | null;
  issues: ApiIssue[];
}

export interface Job {
  id: number;
  kind: "download" | "pipeline";
  lane: string;
  source_id: string | null;
  source_name: string | null;
  status: JobStatus;
  progress: number;
  step: string | null;
  params: Record<string, unknown>;
  error: ApiIssue | null;
  attempts: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Source {
  id: string;
  name: string;
  origin: "file" | "path" | "url";
  url: string | null;
  media_path: string | null;
  owns_media: boolean;
  size_bytes: number | null;
  duration: number | null;
  fps: number | null;
  width: number | null;
  height: number | null;
  has_audio: boolean;
  /** Effective type: the user's override, else the detected one (null until analysis ran). */
  content_type: ContentType | null;
  content_type_override: ContentType | null;
  content_type_detected: ContentType | null;
  content_type_info: Record<string, number | string> | null;
  target_platform: Platform | null;
  language: string | null;
  language_override: LanguageOption;
  notes: string;
  status: SourceStatus;
  error: ApiIssue | null;
  preflight: PreflightResult | null;
  steps: Record<Step, StepInfo>;
  can_start: boolean;
  active_job: Job | null;
  thumbnail_url: string | null;
  proxy_url: string | null;
  has_transcript: boolean;
  has_analysis: boolean;
  moments: { approved: number; total: number };
  moments_summary: string | null;
  moments_notes: string | null;
  moments_provider: string | null;
  find_moments_command: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface Word {
  start: number;
  end: number;
  text: string;
  prob?: number;
  speaker?: string;
}

export interface Transcript {
  version: number;
  language: string;
  language_option: LanguageOption;
  language_probability: number | null;
  model: string;
  device: string;
  duration: number;
  words: Word[];
  segments: { start: number; end: number; text: string; first_word: number; last_word: number }[];
}

export const listSources = () => apiGet<Source[]>("/sources");
export const listJobs = (limit = 30) => apiGet<Job[]>(`/jobs?limit=${limit}`);
export const getTranscript = (id: string) => apiGet<Transcript>(`/sources/${id}/transcript`);
export const importSource = (location: string) => apiSend<Source>("POST", "/sources/import", { location });
export const patchSource = (id: string, body: Partial<Pick<Source, "name" | "language_override" | "notes" | "target_platform">> & { content_type?: ContentType | null }) =>
  apiSend<Source>("PATCH", `/sources/${id}`, body);
export const startSource = (id: string, body: { language?: LanguageOption; notes?: string }) =>
  apiSend<Source>("POST", `/sources/${id}/start`, body);
export const rerunSource = (id: string, fromStep: Step) => apiSend<Source>("POST", `/sources/${id}/rerun`, { from_step: fromStep });
export const deleteSource = (id: string, deleteMedia: boolean) =>
  apiSend<{ deleted: boolean; media_deleted: boolean }>("DELETE", `/sources/${id}?delete_media=${deleteMedia}`);
export const cancelJob = (id: number) => apiSend<Job>("POST", `/jobs/${id}/cancel`);

export async function getJobLog(id: number): Promise<string> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/jobs/${id}/log`);
  } catch {
    throw unreachable();
  }
  if (!response.ok) throw new ApiError({ code: "INTERNAL_ERROR", severity: "error", params: {} }, response.status);
  return response.text();
}

export interface UploadHandle {
  promise: Promise<Source>;
  abort: () => void;
}

/** Stream a file to the backend (raw body, no multipart) with progress callbacks. */
export function uploadSource(file: File, onProgress: (loaded: number, total: number) => void): UploadHandle {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<Source>((resolve, reject) => {
    xhr.open("POST", `${API_BASE}/sources/upload?name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => onProgress(e.loaded, e.lengthComputable ? e.total : file.size);
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Source);
      else if (isIssue(body)) reject(new ApiError({ code: body.code, severity: body.severity ?? "error", params: body.params ?? {} }, xhr.status));
      else reject(xhr.status >= 500 ? unreachable() : new ApiError({ code: "UPLOAD_FAILED", severity: "error", params: {} }, xhr.status));
    };
    xhr.onerror = () => reject(unreachable());
    xhr.onabort = () => reject(new ApiError({ code: "UPLOAD_CANCELLED", severity: "info", params: {} }, 0));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}

// ---------------------------------------------------------------------------
// Analysis + moments (Phase 2)
// ---------------------------------------------------------------------------
export const RUBRIC = ["hook_strength", "coherence", "emotion", "value_density", "payoff"] as const;
export type RubricKey = (typeof RUBRIC)[number];
export type MomentStatus = "proposed" | "approved" | "rejected";

export interface Moment {
  id: number;
  source_id: string;
  start: number;
  end: number;
  duration: number;
  scores: Partial<Record<RubricKey, number>>;
  score: number;
  title: string;
  hook: string;
  reason: string;
  category: string;
  key_line: [number, number] | null;
  status: MomentStatus;
  origin: "ai" | "manual";
  provider: string | null;
  caption_style: CaptionStyle;
  platform: Platform;
  raw_start: number | null;
  raw_end: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export type MomentPatch = Partial<Pick<Moment, "start" | "end" | "title" | "hook" | "reason" | "status" | "caption_style" | "platform">> & {
  snap?: boolean;
};

export interface Analysis {
  version: number;
  duration: number;
  scene_cuts: number[];
  loudness: number[];
  peaks: [number, number][];
  silences: [number, number][];
  silence_threshold_db: number | null;
}

export interface Waveform {
  rate: number;
  peaks: number[];
}

export interface AppSettings {
  ui_language: "en" | "vi";
  rubric_weights: Record<RubricKey, number>;
  llm_provider: LlmProvider | null;
  default_platform: Platform;
  default_caption_style: CaptionStyle;
  clip_min: number;
  clip_max: number;
  moments_wanted: [number, number];
  voice_default_id: string;
  voice_model: VoiceModel;
  renderer: Renderer;
  output_dir: string;
  platform_presets: Partial<Record<Platform, Partial<PresetOverride>>>;
  whisper_model: string | null;
  device: Device | null;
  channels: Channel[];
  env: {
    llm_provider: string;
    anthropic_configured: boolean;
    openai_configured: boolean;
    ollama_host: string;
    ollama_model: string;
    elevenlabs_configured: boolean;
    elevenlabs_mock: boolean;
  };
  effective_llm_provider: LlmProvider;
  effective: { whisper_model: string; device: Device; env_whisper_model: string; env_device: Device };
  choices: { whisper_models: string[]; devices: Device[] };
}

export type Device = "auto" | "cuda" | "mps" | "cpu";

/** Per-channel preset (Settings → Channels); applied to clips in one click. */
export interface Channel {
  id: string;
  name: string;
  platform: Platform;
  caption_style: CaptionStyle;
  caption_position_y: number;
  words_per_page: number;
  font_size: number;
  hook_enabled: boolean;
  hook_position_y: number;
  progress_bar_enabled: boolean;
  progress_color: string;
  voice_id: string;
}

/** A new channel with a client-made id (`ch_` + 8 hex, the format the backend keeps). */
export const newChannel = (name: string): Channel => ({
  id: `ch_${Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) => b.toString(16).padStart(2, "0")).join("")}`,
  name,
  platform: "youtube_shorts",
  caption_style: "bold",
  caption_position_y: 0.72,
  words_per_page: 3,
  font_size: 72,
  hook_enabled: true,
  hook_position_y: 0.18,
  progress_bar_enabled: true,
  progress_color: "#FFD400",
  voice_id: "",
});

export type CacheKey = "transcripts" | "remotion_bundle" | "logs" | "voice_previews" | "voice_generated" | "whisper_models" | "outputs";
export interface CacheItem {
  key: CacheKey;
  bytes: number;
  files: number;
  clearable: boolean;
}
export interface CacheSummary {
  items: CacheItem[];
  clearable_bytes: number;
  cleared?: CacheKey[];
  freed_bytes?: number;
  warning?: ApiIssue;
}
export const getCache = () => apiGet<CacheSummary>("/cache");
export const clearCache = (keys?: CacheKey[]) => apiSend<CacheSummary>("POST", "/cache/clear", keys ? { keys } : {});

export const getAnalysis = (id: string) => apiGet<Analysis>(`/sources/${id}/analysis`);
export const getWaveform = (id: string) => apiGet<Waveform>(`/sources/${id}/waveform`);
export const listMoments = (id: string) => apiGet<Moment[]>(`/sources/${id}/moments`);
export const createMoment = (id: string, body: { start: number; end: number; title?: string; hook?: string; snap?: boolean; status?: MomentStatus }) =>
  apiSend<Moment>("POST", `/sources/${id}/moments`, body);
export const patchMoment = (id: number, body: MomentPatch) => apiSend<Moment>("PATCH", `/moments/${id}`, body);
export const batchMoments = (sourceId: string, body: { ids: number[]; status: MomentStatus; channel_id?: string }) =>
  apiSend<{ updated: number[]; skipped: number[]; moments: Moment[] }>("POST", `/sources/${sourceId}/moments/batch`, body);
export const deleteMoment = (id: number) => apiSend<{ deleted: boolean }>("DELETE", `/moments/${id}`);
export const snapRange = (id: string, start: number, end: number) =>
  apiSend<{ start: number; end: number }>("POST", `/sources/${id}/snap`, { start, end });
export const findMoments = (id: string, provider?: LlmProvider) =>
  apiSend<Source>("POST", `/sources/${id}/moments/find`, provider ? { provider } : {});
export const importMomentsNow = (id: string) => apiSend<Source>("POST", `/sources/${id}/moments/import`);
export const getSettings = () => apiGet<AppSettings>("/settings");
export const patchSettings = (body: Partial<Omit<AppSettings, "env" | "effective_llm_provider" | "effective" | "choices">>) => apiSend<AppSettings>("PATCH", "/settings", body);

// --- Clips (Phase 3) --------------------------------------------------------

export type ClipStatus = "draft" | "review" | "approved" | "rendered";
export type WorkState = "pending" | "queued" | "running" | "done" | "error";
export interface WorkStep {
  state: WorkState;
  error: ApiIssue | null;
  info: Record<string, unknown> | null;
}

export interface Edl {
  id: string;
  source_id: string;
  moment_id: number | null;
  segments: Segment[];
  crop: { mode: CropMode; easing: Easing; fit: CropFit; keyframes: Keyframe[]; split: { top: Region; bottom: Region; ratio: number } | null };
  captions: { enabled: boolean; style: CaptionStyle; position_y: number; words_per_page: number; font_size: number; words: CaptionWord[] };
  hook_text: { enabled: boolean; text: string; start: number; end: number; position_y: number };
  progress_bar: { enabled: boolean; color: string };
  voiceover: {
    enabled: boolean;
    script: string;
    voice_id: string;
    model_id: VoiceModel;
    settings: VoiceSettings;
    audio_path: string | null;
    duration: number | null;
    start: number;
    volume_db: number;
    duck_db: number;
  };
  audio: { normalize_lufs: number; isolate_voice: boolean; volume_db: number; isolated_path: string | null; isolated_start: number };
  platform: Platform;
  output: { w: number; h: number; fps: number };
  status: ClipStatus;
}
export type EdlSection = Exclude<keyof Edl, "id" | "source_id" | "moment_id" | "status">;

export interface ClipSummary {
  id: string;
  source_id: string;
  moment_id: number | null;
  status: ClipStatus;
  duration: number;
  title: string;
  busy: boolean;
  moment: Moment | null;
  work: { prepare: WorkStep; reframe: WorkStep; crop_user_edited: boolean; crop_edits: number };
  crop_mode?: CropMode;
  platform?: Platform;
  created_at: string | null;
  updated_at: string | null;
}
export interface Clip extends ClipSummary {
  edl: Edl;
}

export const listClips = (sourceId: string) => apiGet<ClipSummary[]>(`/sources/${sourceId}/clips`);
export const getClip = (id: string) => apiGet<Clip>(`/clips/${id}`);
export const openMomentClip = (momentId: number) => apiSend<Clip>("POST", `/moments/${momentId}/clip`);
export const applyChannel = (clipId: string, channelId: string) => apiSend<Clip>("POST", `/clips/${clipId}/channel`, { channel_id: channelId });
export const patchClip = (id: string, body: { edl?: Partial<Pick<Edl, EdlSection>>; status?: ClipStatus }) =>
  apiSend<Clip>("PATCH", `/clips/${id}`, body);
/** The clip's captions as SubRip, generated from the saved EDL. */
export const clipSrtUrl = (id: string) => apiUrl(`/clips/${id}/captions.srt`);
export const getClipSrt = (id: string) => fetch(clipSrtUrl(id)).then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))));
export const reframeClip = (id: string, mode?: CropMode) => apiSend<Clip>("POST", `/clips/${id}/reframe`, mode ? { mode } : {});

// --- Voice / ElevenLabs (Phase 5) ---------------------------------------------

export const VOICE_MODELS = ["eleven_flash_v2_5", "eleven_v3", "eleven_multilingual_v2"] as const;
export type VoiceModel = (typeof VOICE_MODELS)[number];
export interface VoiceSettings {
  stability: number;
  similarity_boost: number;
  style: number;
  speed: number;
}
export interface VoiceModelInfo {
  id: VoiceModel;
  credits_per_char: number;
  max_chars: number;
  vietnamese: boolean;
}
export interface VoiceStatus {
  configured: boolean;
  mock: boolean;
  models: VoiceModelInfo[];
  default_model: VoiceModel;
  default_voice_id: string;
  isolation_credits_per_minute: number;
  subscription: { tier: string | null; character_count: number | null; character_limit: number | null; next_reset_unix: number | null } | null;
  error: string | null;
}
export interface Voice {
  voice_id: string;
  name: string;
  category: string | null;
  labels: Record<string, string>;
  languages: string[];
  preview_url: string | null;
}
export interface VoiceEstimate {
  characters: number;
  credits: number;
  credits_to_spend: number;
  max_chars: number;
  model_id: VoiceModel;
  cached: boolean;
  audio_path: string | null;
  over_limit: boolean;
}
export interface VoiceRequest {
  script: string;
  voice_id: string;
  model_id: VoiceModel;
  settings: VoiceSettings;
}
export interface IsolationEstimate {
  seconds: number;
  credits: number;
  credits_to_spend: number;
  cached: boolean;
}

export const getVoiceStatus = (refresh = false) => apiGet<VoiceStatus>(`/voice/status${refresh ? "?refresh=true" : ""}`);
export const listVoices = (refresh = false) => apiGet<Voice[]>(`/voice/voices${refresh ? "?refresh=true" : ""}`);
export const estimateVoiceover = (clipId: string, body: VoiceRequest) => apiSend<VoiceEstimate>("POST", `/clips/${clipId}/voiceover/estimate`, body);
export const generateVoiceover = (clipId: string, body: VoiceRequest & { confirm: boolean }) =>
  apiSend<{ clip: Clip; cached: boolean; credits: number; characters: number }>("POST", `/clips/${clipId}/voiceover`, body);
export const suggestVoiceover = (clipId: string) => apiSend<{ script: string; source: string }>("POST", `/clips/${clipId}/voiceover/suggest`);
export const estimateIsolation = (clipId: string) => apiGet<IsolationEstimate>(`/clips/${clipId}/isolate/estimate`);
export const isolateVoice = (clipId: string, confirm: boolean) =>
  apiSend<{ clip: Clip; cached: boolean; credits: number }>("POST", `/clips/${clipId}/isolate`, { confirm });
/** URL of a generated audio file (EDL path "voice_cache/<name>.mp3"). */
export const voiceAudioUrl = (path: string | null) => (path ? apiUrl(`/voice/audio/${path.split("/").pop()}`) : null);
/** Voice preview URLs are absolute (ElevenLabs CDN) or backend paths (mock mode). */
export const voicePreviewUrl = (url: string | null) => (!url ? null : url.startsWith("/") ? apiUrl(url) : url);

// ---------------------------------------------------------------------------
// Render + export (Phase 6)
// ---------------------------------------------------------------------------
export const RENDERERS = ["remotion", "ffmpeg"] as const;
export type Renderer = (typeof RENDERERS)[number];
export type TaskState = "idle" | "queued" | "running" | "done" | "error" | "cancelled";
export type EncoderLabel = "nvenc" | "videotoolbox" | "cpu";

export interface PresetOverride {
  max_seconds: number;
  video_bitrate_mbps: number;
  audio_bitrate_kbps: number;
  fps: number | null;
}
export interface PlatformPreset extends PresetOverride {
  platform: Platform;
  safe_zone: { top: number; bottom: number; left: number; right: number };
}
export interface RenderPresets {
  presets: Record<Platform, PlatformPreset>;
  defaults: Record<Platform, Omit<PlatformPreset, "platform">>;
  renderers: Renderer[];
  remotion_available: boolean;
}

export interface RenderInfo {
  file: string;
  renderer: Renderer;
  encoder: EncoderLabel;
  encoder_name: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  lufs: number | null;
  true_peak: number | null;
  ssim: number[];
  ssim_min: number | null;
  warnings: Pick<ApiIssue, "code" | "params">[];
  render_seconds: number;
  rendered_at: string;
}

export interface ExportTask<I = Record<string, unknown>> {
  state: TaskState;
  error: ApiIssue | null;
  info: I | null;
  job_id: number | null;
  renderer: Renderer | null;
  progress: number;
  step: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface OutputFile {
  name: string;
  size: number;
  mtime: number;
}

export interface ExportClip extends ClipSummary {
  render: ExportTask<RenderInfo>;
  capcut: ExportTask<{ files: string[]; voiceover: boolean; zip: string; folder: string }>;
  out_dir: string;
  files: Record<"final" | "thumb" | "srt" | "bundle" | "imported", OutputFile | null>;
  length_warning: { seconds: number; max: number; platform: Platform } | null;
}

export type OutputKind = keyof ExportClip["files"];

export const getRenderPresets = () => apiGet<RenderPresets>("/render/presets");
export const getEncoders = () => apiGet<{ encoders: string[]; labels: EncoderLabel[] }>("/render/encoders");
export const listExports = () => apiGet<ExportClip[]>("/exports");
export const getExport = (clipId: string) => apiGet<ExportClip>(`/clips/${clipId}/export`);
export const renderClip = (clipId: string, renderer?: Renderer) =>
  apiSend<ExportClip>("POST", `/clips/${clipId}/render`, renderer ? { renderer } : {});
export const renderBatch = (clipIds: string[], renderer?: Renderer) =>
  apiSend<{ queued: string[]; skipped: (ApiIssue & { clip_id: string })[] }>("POST", "/renders/batch", { clip_ids: clipIds, renderer });
export const cancelRender = (clipId: string) => apiSend<ExportClip>("POST", `/clips/${clipId}/render/cancel`);
export const exportCapcut = (clipId: string) => apiSend<ExportClip>("POST", `/clips/${clipId}/capcut`);
export const openClipFolder = (clipId: string) => apiSend<{ ok: boolean; path: string }>("POST", `/clips/${clipId}/open-folder`);
/** URL of a rendered file; `version` busts the browser cache after a re-render. */
export const outputUrl = (clipId: string, kind: OutputKind, opts: { download?: boolean; version?: number } = {}) =>
  apiUrl(`/clips/${clipId}/render/file?kind=${kind}${opts.download ? "&download=true" : ""}${opts.version ? `&v=${opts.version}` : ""}`);

/** Upload an MP4 edited in CapCut (raw body) with progress. */
export function importEdited(clipId: string, file: File, onProgress: (fraction: number) => void): Promise<ExportClip> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/clips/${clipId}/import?name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => onProgress(e.lengthComputable ? e.loaded / e.total : 0);
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as ExportClip);
      else if (isIssue(body)) reject(new ApiError({ code: body.code, severity: body.severity ?? "error", params: body.params ?? {} }, xhr.status));
      else reject(new ApiError({ code: "UPLOAD_FAILED", severity: "error", params: {} }, xhr.status));
    };
    xhr.onerror = () => reject(unreachable());
    xhr.send(file);
  });
}

/** Seconds left from elapsed time and progress (null until there is enough signal). */
export function remainingSeconds(startedAt: string | null, progress: number, now = Date.now()): number | null {
  if (!startedAt || progress < 0.03 || progress >= 1) return null;
  const elapsed = (now - new Date(startedAt).getTime()) / 1000;
  if (!Number.isFinite(elapsed) || elapsed < 3) return null;
  return Math.max(0, (elapsed * (1 - progress)) / progress);
}
