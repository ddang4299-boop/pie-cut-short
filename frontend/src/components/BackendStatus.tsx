import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useHealth, type BackendLevel } from "../state/health";
import { Tooltip } from "./Tooltip";

export const LEVEL_DOT: Record<BackendLevel, string> = {
  checking: "bg-muted animate-pulse",
  online: "bg-success",
  warning: "bg-warning",
  degraded: "bg-danger",
  offline: "bg-danger",
};

/** Header pill: green = backend online, yellow = warnings, red = offline/degraded. */
export function BackendStatus() {
  const { t } = useTranslation();
  const { level, data } = useHealth();
  const detail =
    data && level !== "offline"
      ? `${t(`device.${data.device}`)} · FFmpeg ${data.ffmpeg.version ?? t("system.not_found")}`
      : t(`backend.${level}`);

  return (
    <Tooltip label={detail} side="bottom-end">
      <Link
        to="/settings"
        data-testid="backend-status"
        data-level={level}
        className="flex items-center gap-2 rounded-full border border-line px-3 py-1 text-xs text-fg hover:bg-white/5"
      >
        <span className={`size-2 rounded-full ${LEVEL_DOT[level]}`} />
        {t(`backend.${level}`)}
      </Link>
    </Tooltip>
  );
}
