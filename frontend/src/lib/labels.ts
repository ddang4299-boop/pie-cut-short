import type { TFunction } from "i18next";

/** "vi" -> "Vietnamese"; unknown Whisper codes fall back to the upper-cased code. */
export function contentLanguageLabel(t: TFunction, code: string | null | undefined): string {
  if (!code) return "—";
  return t(`content_language.${code}`, { defaultValue: code.toUpperCase() });
}
