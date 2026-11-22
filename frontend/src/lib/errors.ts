import type { TFunction } from "i18next";

import type { ApiIssue } from "./api";

/** Translate a backend issue ({code, params}) using the `errors.<CODE>` locale keys. */
export function translateIssue(t: TFunction, issue: Pick<ApiIssue, "code" | "params">): string {
  const key = `errors.${issue.code}`;
  const fallback = t("errors.UNKNOWN", { code: issue.code });
  return t(key, { ...issue.params, defaultValue: fallback });
}
