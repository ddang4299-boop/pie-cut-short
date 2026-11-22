/** Locale-aware formatting. Timecodes are always mm:ss.ff (ff = hundredths of a second). */

/** 75.456 -> "01:15.45"; >= 1 h -> "1:01:15.45". Negative values clamp to 0. */
export function formatTimecode(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "--:--.--";
  const totalHundredths = Math.floor(Math.max(0, seconds) * 100 + 1e-6);
  const hundredths = totalHundredths % 100;
  const totalSeconds = Math.floor(totalHundredths / 100);
  const s = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const m = totalMinutes % 60;
  const h = Math.floor(totalMinutes / 60);
  const mmss = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(hundredths).padStart(2, "0")}`;
  return h > 0 ? `${h}:${mmss}` : mmss;
}

/** Coarse duration for cards: "2:15", "1:02:03". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "--:--";
  const total = Math.round(Math.max(0, seconds));
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

/** Parse "mm:ss.ff", "h:mm:ss.ff", "ss.ff" or a plain number (also with a decimal comma). */
export function parseTimecode(text: string): number | null {
  const clean = text.trim().replace(",", ".");
  if (!clean) return null;
  const parts = clean.split(":");
  if (parts.length > 3 || parts.some((p) => p === "" || !/^\d+(\.\d+)?$/.test(p))) return null;
  let total = 0;
  for (const part of parts) total = total * 60 + Number(part);
  return total;
}

const UNITS = ["B", "KB", "MB", "GB", "TB"];

export function formatBytes(bytes: number | null | undefined, locale: string): string {
  if (bytes == null || !Number.isFinite(bytes)) return "—";
  let value = Math.max(0, bytes);
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit++;
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value)} ${UNITS[unit]}`;
}

export function formatPercent(fraction: number, locale: string): string {
  return new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(Math.max(0, Math.min(1, fraction)));
}

export function formatDateTime(iso: string | null | undefined, locale: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function formatResolution(width: number | null | undefined, height: number | null | undefined): string {
  return width && height ? `${width}×${height}` : "—";
}
