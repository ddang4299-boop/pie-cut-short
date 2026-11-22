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
