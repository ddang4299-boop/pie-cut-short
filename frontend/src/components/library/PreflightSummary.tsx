import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { PreflightResult, Severity } from "../../lib/api";
import { translateIssue } from "../../lib/errors";

const ISSUE_ICON: Record<Severity, { icon: typeof Info; color: string }> = {
  error: { icon: XCircle, color: "text-danger" },
  warning: { icon: AlertTriangle, color: "text-warning" },
  info: { icon: Info, color: "text-muted" },
};

export type PreflightLevel = "ok" | "warn" | "fail";

export function preflightLevel(result: PreflightResult): PreflightLevel {
  if (!result.ok || result.issues.some((i) => i.severity === "error")) return "fail";
  if (result.issues.some((i) => i.severity === "warning")) return "warn";
  return "ok";
}

const LEVEL_STYLE: Record<PreflightLevel, { box: string; icon: typeof Info; color: string }> = {
  ok: { box: "border-success/30 bg-success/5", icon: CheckCircle2, color: "text-success" },
  warn: { box: "border-warning/30 bg-warning/5", icon: AlertTriangle, color: "text-warning" },
  fail: { box: "border-danger/40 bg-danger/5", icon: XCircle, color: "text-danger" },
};

/** Green / yellow / red preflight result with every issue translated. */
export function PreflightSummary({ result }: { result: PreflightResult }) {
  const { t } = useTranslation();
  const level = preflightLevel(result);
  const style = LEVEL_STYLE[level];
  const Icon = style.icon;
  return (
    <div className={`rounded-lg border px-3 py-2 text-xs ${style.box}`} data-testid="preflight-summary" data-level={level}>
      <div className={`flex items-center gap-1.5 font-medium ${style.color}`}>
        <Icon className="size-3.5" />
        {t(`pages.library.preflight_${level}`)}
      </div>
      {result.issues.length > 0 && (
        <ul className="mt-1.5 space-y-1">
          {result.issues.map((issue, index) => {
            const { icon: IssueIcon, color } = ISSUE_ICON[issue.severity] ?? ISSUE_ICON.info;
            return (
              <li key={`${issue.code}-${index}`} className="flex items-start gap-1.5 text-fg/85">
                <IssueIcon className={`mt-0.5 size-3 shrink-0 ${color}`} />
                <span>{translateIssue(t, issue)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
