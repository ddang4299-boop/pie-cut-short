import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, getJobLog } from "../lib/api";
import { translateIssue } from "../lib/errors";

/** Tail of a job's log file. Re-fetches every 2 s while the job is still active. */
export function JobLog({ jobId, live }: { jobId: number; live: boolean }) {
  const { t } = useTranslation();
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const body = await getJobLog(jobId);
        if (!stopped) {
          setText(body);
          setError(null);
        }
      } catch (err) {
        if (!stopped) setError(err instanceof ApiError ? translateIssue(t, err.issue) : String(err));
      }
    };
    void load();
    const timer = live ? window.setInterval(() => void load(), 2000) : undefined;
    return () => {
      stopped = true;
      if (timer) window.clearInterval(timer);
    };
  }, [jobId, live, t]);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [text]);

  return (
    <pre
      ref={ref}
      data-testid={`job-log-${jobId}`}
      className="max-h-64 overflow-auto rounded-lg border border-line bg-app p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-muted"
    >
      {error ?? (text === null ? t("common.loading") : text.trim() || t("jobs.log_empty"))}
    </pre>
  );
}
