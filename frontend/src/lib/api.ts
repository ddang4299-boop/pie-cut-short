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
export const RERUNNABLE_STEPS: Step[] = ["preflight", "proxy", "transcribe"];
export type StepState = "pending" | "queued" | "running" | "done" | "skipped" | "error" | "waiting";
export const CONTENT_TYPES = ["talking_head", "screen_recording", "podcast", "gameplay_facecam"] as const;
export type ContentType = (typeof CONTENT_TYPES)[number];
export const LANGUAGE_OPTIONS = ["auto", "vi", "en", "mixed"] as const;
export type LanguageOption = (typeof LANGUAGE_OPTIONS)[number];
export type SourceStatus = "downloading" | "new" | "processing" | "ready" | "error";
export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";

export interface StepInfo {
  state: StepState;
  progress: number;
  error?: ApiIssue | null;
  stage?: string | null;
  notices?: ApiIssue[];
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
  content_type: ContentType | null;
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
  moments: { approved: number; total: number };
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
export const patchSource = (id: string, body: Partial<Pick<Source, "name" | "content_type" | "language_override" | "notes">>) =>
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
