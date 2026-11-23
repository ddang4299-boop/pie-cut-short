import { Check, Trash2, User, X } from "lucide-react";
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

import { RUBRIC, type Moment } from "../../lib/api";
import { formatDuration, formatTimecode } from "../../lib/format";
import { scoreTone } from "../../lib/moments";
import { Tooltip } from "../Tooltip";

const RING: Record<ReturnType<typeof scoreTone>, string> = {
  high: "stroke-success text-success",
  mid: "stroke-warning text-warning",
  low: "stroke-muted text-muted",
};

export function ScoreRing({ score, size = 40 }: { score: number; size?: number }) {
  const tone = scoreTone(score);
  const r = size / 2 - 3;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} className={`shrink-0 ${RING[tone]}`} data-testid="score-ring" data-tone={tone}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={3} className="stroke-white/10" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        strokeWidth={3}
        strokeLinecap="round"
        strokeDasharray={`${(c * Math.max(0, Math.min(100, score))) / 100} ${c}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" className="fill-current font-mono text-[12px] font-medium">
        {Math.round(score)}
      </text>
    </svg>
  );
}

const STATUS_BORDER: Record<Moment["status"], string> = {
  proposed: "border-line",
  approved: "border-success/50",
  rejected: "border-line opacity-55",
};

/** One moment in the left list. */
export const MomentCard = memo(function MomentCard({
  moment,
  selected,
  onSelect,
  onSetStatus,
  onDelete,
  checked = false,
  onCheck,
}: {
  moment: Moment;
  selected: boolean;
  onSelect: (id: number) => void;
  onSetStatus: (id: number, status: Moment["status"]) => void;
  onDelete: (id: number) => void;
  checked?: boolean;
  onCheck?: (id: number, checked: boolean) => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const manual = moment.origin === "manual";
  const title = moment.title || t("pages.moments.untitled");

  return (
    <div
      role="button"
      tabIndex={-1}
      onClick={() => onSelect(moment.id)}
      data-testid="moment-card"
      data-moment-id={moment.id}
      data-status={moment.status}
      aria-current={selected}
      className={`cursor-pointer rounded-lg border bg-app/40 p-3 transition-colors ${STATUS_BORDER[moment.status]} ${
        selected ? "ring-1 ring-accent" : "hover:bg-white/[0.03]"
      }`}
    >
      <div className="flex items-start gap-3">
        {onCheck && (
          <input
            type="checkbox"
            className="mt-3 size-3.5 shrink-0 cursor-pointer accent-[#7c5cff]"
            data-testid="moment-check"
            aria-label={t("pages.moments.batch_check", { title })}
            checked={checked}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => onCheck(moment.id, e.target.checked)}
          />
        )}
        {manual ? (
          <Tooltip label={t("pages.moments.manual")} side="right">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-full border border-line text-muted">
              <User className="size-4" />
            </span>
          </Tooltip>
        ) : (
          <ScoreRing score={moment.score} />
        )}
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 text-sm leading-snug font-medium [overflow-wrap:anywhere]" title={title}>
            {title}
          </div>
          {moment.hook && <div className="mt-0.5 line-clamp-1 text-xs text-muted italic">“{moment.hook}”</div>}
          <div className="mt-1 flex items-center gap-2 font-mono text-[11px] text-muted">
            <span>{formatTimecode(moment.start)}</span>
            <span className="text-line">·</span>
            <span>{formatDuration(moment.end - moment.start)}</span>
          </div>
        </div>
      </div>

      {!manual && (
        <div className="mt-2.5 grid grid-cols-5 gap-1" data-testid="rubric-bars">
          {RUBRIC.map((key) => {
            const value = moment.scores[key] ?? 0;
            return (
              <Tooltip key={key} label={t("pages.moments.rubric_value", { name: t(`rubric.${key}`), value })} side="top">
                <div className="h-1 w-full overflow-hidden rounded-full bg-white/10">
                  <div className="h-full rounded-full bg-fg/70" style={{ width: `${value * 10}%` }} />
                </div>
              </Tooltip>
            );
          })}
        </div>
      )}

      {moment.reason && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)} // bubbles up: clicking the reason also selects the moment
          className={`mt-2 block w-full text-left text-xs leading-relaxed text-muted [overflow-wrap:anywhere] ${expanded ? "" : "line-clamp-2"}`}
          title={expanded ? t("pages.moments.collapse") : t("pages.moments.expand")}
        >
          {moment.reason}
        </button>
      )}

      <div className="mt-2.5 flex items-center gap-1.5">
        <Tooltip label={t("pages.moments.approve")} shortcut="A" side="top">
          <button
            type="button"
            aria-label={t("pages.moments.approve")}
            aria-pressed={moment.status === "approved"}
            data-testid="approve-button"
            onClick={(e) => {
              e.stopPropagation();
              onSetStatus(moment.id, moment.status === "approved" ? "proposed" : "approved");
            }}
            className={`flex size-7 items-center justify-center rounded-md border ${
              moment.status === "approved" ? "border-success bg-success/15 text-success" : "border-line text-muted hover:text-success"
            }`}
          >
            <Check className="size-4" />
          </button>
        </Tooltip>
        <Tooltip label={t("pages.moments.reject")} shortcut="R" side="top">
          <button
            type="button"
            aria-label={t("pages.moments.reject")}
            aria-pressed={moment.status === "rejected"}
            data-testid="reject-button"
            onClick={(e) => {
              e.stopPropagation();
              onSetStatus(moment.id, moment.status === "rejected" ? "proposed" : "rejected");
            }}
            className={`flex size-7 items-center justify-center rounded-md border ${
              moment.status === "rejected" ? "border-danger bg-danger/15 text-danger" : "border-line text-muted hover:text-danger"
            }`}
          >
            <X className="size-4" />
          </button>
        </Tooltip>
        <span className="ml-1 truncate text-[11px] text-muted">{t(`moment_status.${moment.status}`)}</span>
        {manual && (
          <Tooltip label={t("pages.moments.delete")} side="top">
            <button
              type="button"
              aria-label={t("pages.moments.delete")}
              data-testid="delete-moment"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(moment.id);
              }}
              className="ml-auto flex size-7 items-center justify-center rounded-md text-muted hover:bg-white/5 hover:text-danger"
            >
              <Trash2 className="size-3.5" />
            </button>
          </Tooltip>
        )}
      </div>
    </div>
  );
});
