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
  env: { llm_provider: string; anthropic_configured: boolean; openai_configured: boolean; ollama_host: string; ollama_model: string };
  effective_llm_provider: LlmProvider;
}

export const getAnalysis = (id: string) => apiGet<Analysis>(`/sources/${id}/analysis`);
export const getWaveform = (id: string) => apiGet<Waveform>(`/sources/${id}/waveform`);
export const listMoments = (id: string) => apiGet<Moment[]>(`/sources/${id}/moments`);
export const createMoment = (id: string, body: { start: number; end: number; title?: string; hook?: string; snap?: boolean; status?: MomentStatus }) =>
  apiSend<Moment>("POST", `/sources/${id}/moments`, body);
export const patchMoment = (id: number, body: MomentPatch) => apiSend<Moment>("PATCH", `/moments/${id}`, body);
export const deleteMoment = (id: number) => apiSend<{ deleted: boolean }>("DELETE", `/moments/${id}`);
export const snapRange = (id: string, start: number, end: number) =>
  apiSend<{ start: number; end: number }>("POST", `/sources/${id}/snap`, { start, end });
export const findMoments = (id: string, provider?: LlmProvider) =>
  apiSend<Source>("POST", `/sources/${id}/moments/find`, provider ? { provider } : {});
export const importMomentsNow = (id: string) => apiSend<Source>("POST", `/sources/${id}/moments/import`);
export const getSettings = () => apiGet<AppSettings>("/settings");
export const patchSettings = (body: Partial<Omit<AppSettings, "env" | "effective_llm_provider">>) => apiSend<AppSettings>("PATCH", "/settings", body);
