import { Check, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { getSettings, type Channel, type MomentStatus } from "../../lib/api";
import { Tooltip } from "../Tooltip";

/** Moments list header for multi-select: select all visible, channel preset, approve / reject the checked ones. */
export function BatchBar({
  visibleIds,
  checked,
  onCheckAll,
  onApply,
  busy,
}: {
  visibleIds: number[];
  checked: Set<number>;
  onCheckAll: (all: boolean) => void;
  onApply: (status: MomentStatus, channelId: string | null) => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [channel, setChannel] = useState("");
  useEffect(() => {
    getSettings()
      .then((s) => setChannels(s.channels ?? []))
      .catch(() => setChannels([]));
  }, []);

  const count = visibleIds.filter((id) => checked.has(id)).length;
  const all = count > 0 && count === visibleIds.length;
  return (
    <div className="space-y-2 border-t border-line pt-2" data-testid="batch-bar">
      <div className="flex items-center gap-2 text-xs">
        <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 text-muted">
          <input
            type="checkbox"
            className="size-3.5 accent-[#7c5cff]"
            data-testid="check-all"
            disabled={!visibleIds.length}
            checked={all}
            ref={(el) => {
              if (el) el.indeterminate = count > 0 && !all;
            }}
            onChange={(e) => onCheckAll(e.target.checked)}
          />
          <span className="truncate">{count ? t("pages.moments.batch_selected", { count }) : t("pages.moments.batch_select_all")}</span>
        </label>
      </div>
      {count > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {channels.length > 0 && (
            <select
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              aria-label={t("pages.moments.batch_channel")}
              data-testid="batch-channel"
              className="min-w-0 flex-1 basis-full rounded-md border border-line bg-app px-1.5 py-1 text-xs text-fg outline-none"
            >
              <option value="">{t("pages.moments.batch_no_channel")}</option>
              {channels.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )}
          <Tooltip label={t("pages.moments.batch_approve_hint")} side="bottom">
            <button
              type="button"
              disabled={busy}
              data-testid="batch-approve"
              onClick={() => onApply("approved", channel || null)}
              className="flex flex-1 items-center justify-center gap-1 rounded-md bg-success/15 px-2 py-1 text-xs text-success hover:bg-success/25 disabled:opacity-40"
            >
              <Check className="size-3" />
              {t("pages.moments.batch_approve", { count })}
            </button>
          </Tooltip>
          <Tooltip label={t("pages.moments.batch_reject_hint")} side="bottom">
            <button
              type="button"
              disabled={busy}
              data-testid="batch-reject"
              onClick={() => onApply("rejected", null)}
              className="flex flex-1 items-center justify-center gap-1 rounded-md bg-white/5 px-2 py-1 text-xs text-muted hover:bg-white/10 hover:text-fg disabled:opacity-40"
            >
              <X className="size-3" />
              {t("pages.moments.batch_reject", { count })}
            </button>
          </Tooltip>
        </div>
      )}
    </div>
  );
}
