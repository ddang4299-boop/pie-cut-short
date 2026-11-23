import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { ImportPanel } from "../components/library/ImportPanel";
import { SourceCard } from "../components/library/SourceCard";
import { TranscriptDialog } from "../components/library/TranscriptDialog";
import { ErrorState, PageHeader, Skeleton } from "../components/states";
import { START_COMMAND } from "../components/SystemStatus";
import type { Job } from "../lib/api";
import { translateIssue } from "../lib/errors";
import { useSources } from "../state/sources";

/** Library: import (drop / choose / URL / path), preflight results, Start, source grid. */
export default function Library() {
  const { t } = useTranslation();
  const { sources, jobs, loaded, error, refresh, uploads } = useSources();
  const [transcriptFor, setTranscriptFor] = useState<string | null>(null);

  // Most recent job per source (the list from the backend is newest first).
  const lastJobBySource = useMemo(() => {
    const map = new Map<string, Job>();
    for (const job of jobs) if (job.source_id && !map.has(job.source_id)) map.set(job.source_id, job);
    return map;
  }, [jobs]);

  const transcriptSource = sources.find((s) => s.id === transcriptFor) ?? null;
  const offline = error && sources.length === 0;

  return (
    <div className="mx-auto max-w-[1400px] p-6">
      <PageHeader title={t("pages.library.title")} subtitle={t("pages.library.subtitle")} />

      <ImportPanel compact={sources.length > 0 || uploads.length > 0} />

      <section>
        <div className="mb-3 flex items-baseline gap-3">
          <h2 className="text-sm font-semibold">{t("pages.library.sources_title")}</h2>
          {sources.length > 0 && <span className="text-xs text-muted">{t("pages.library.sources_count", { count: sources.length })}</span>}
        </div>

        {!loaded && (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-4">
            {[0, 1, 2].map((i) => (
              <div key={i} className="rounded-xl border border-line bg-panel surface p-4">
                <div className="mb-4 aspect-video animate-pulse rounded-lg bg-white/5" />
                <Skeleton rows={3} />
              </div>
            ))}
          </div>
        )}

        {loaded && offline && (
          <ErrorState
            message={translateIssue(t, error.issue)}
            onRetry={() => void refresh()}
            log={`${error.issue.code} (HTTP ${error.status || "-"})\n\n${t("system.start_hint")}\n  ${START_COMMAND}`}
          />
        )}

        {loaded && !offline && sources.length === 0 && (
          <p data-testid="library-empty" className="rounded-xl border border-dashed border-line px-6 py-10 text-center text-sm text-muted">
            {t("pages.library.empty")}
          </p>
        )}

        {sources.length > 0 && (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-4" data-testid="source-grid">
            {sources.map((source) => (
              <SourceCard
                key={source.id}
                source={source}
                lastJob={lastJobBySource.get(source.id)}
                onOpenTranscript={() => setTranscriptFor(source.id)}
              />
            ))}
          </div>
        )}
      </section>

      {transcriptSource && <TranscriptDialog source={transcriptSource} onClose={() => setTranscriptFor(null)} />}
    </div>
  );
}
