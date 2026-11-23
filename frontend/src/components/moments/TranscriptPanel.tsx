import { Plus } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Word } from "../../lib/api";
import { formatTimecode } from "../../lib/format";
import { groupSentences } from "../../lib/moments";
import { wordIndexAt } from "../../lib/transcript";

interface Pending {
  first: number;
  last: number;
  x: number;
  y: number;
}

/**
 * Transcript grouped by sentence. The current word is highlighted; click a word to seek;
 * sentences inside the selected moment get a tinted background; selecting text shows a
 * floating "Create moment" button.
 */
export const TranscriptPanel = memo(function TranscriptPanel({
  words,
  time,
  range,
  playing,
  onSeek,
  onCreate,
}: {
  words: Word[];
  time: number;
  range: { start: number; end: number } | null;
  playing: boolean;
  onSeek: (t: number) => void;
  onCreate: (start: number, end: number, text: string) => void;
}) {
  const { t } = useTranslation();
  const sentences = useMemo(() => groupSentences(words), [words]);
  const current = wordIndexAt(words, time);
  const scroller = useRef<HTMLDivElement>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const currentSentence = useMemo(() => sentences.findIndex((s) => s.first <= current && current <= s.last), [sentences, current]);

  // Follow playback (only while playing so the user can scroll freely when paused).
  useEffect(() => {
    if (!playing || currentSentence < 0) return;
    const el = scroller.current?.querySelector<HTMLElement>(`[data-sentence="${currentSentence}"]`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [currentSentence, playing]);

  // When the selected moment changes, bring its first sentence into view.
  useEffect(() => {
    if (!range) return;
    const index = sentences.findIndex((s) => s.end > range.start);
    if (index < 0) return;
    scroller.current?.querySelector<HTMLElement>(`[data-sentence="${index}"]`)?.scrollIntoView({ block: "center" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range?.start, sentences]);

  const onMouseUp = useCallback(() => {
    const sel = window.getSelection();
    const root = scroller.current;
    if (!sel || sel.isCollapsed || !root || !sel.rangeCount) {
      setPending(null);
      return;
    }
    const pick = (node: Node | null) => {
      const el = node instanceof Element ? node : node?.parentElement;
      const hit = el?.closest<HTMLElement>("[data-i]");
      return hit && root.contains(hit) ? Number(hit.dataset.i) : null;
    };
    const a = pick(sel.anchorNode);
    const b = pick(sel.focusNode);
    if (a == null || b == null) {
      setPending(null);
      return;
    }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    const box = root.getBoundingClientRect();
    setPending({
      first: Math.min(a, b),
      last: Math.max(a, b),
      x: Math.min(box.width - 150, Math.max(8, rect.left - box.left + rect.width / 2 - 70)),
      y: rect.bottom - box.top + root.scrollTop + 6,
    });
  }, []);

  if (!words.length) {
    return <p className="p-4 text-sm text-muted">{t("pages.moments.no_transcript")}</p>;
  }

  return (
    <div ref={scroller} className="relative h-full overflow-y-auto px-4 py-3" onMouseUp={onMouseUp} data-testid="moments-transcript">
      {sentences.map((s, si) => {
        const inside = range != null && s.end > range.start + 0.05 && s.start < range.end - 0.05;
        return (
          <p
            key={si}
            data-sentence={si}
            data-inside={inside || undefined}
            className={`-mx-2 mb-1 rounded-md px-2 py-1 text-sm leading-relaxed ${inside ? "bg-accent/10" : ""}`}
          >
            <span
              className="mr-2 cursor-pointer font-mono text-[10px] text-muted hover:text-fg"
              onClick={() => onSeek(s.start)}
              title={t("pages.moments.seek_here")}
            >
              {formatTimecode(s.start)}
            </span>
            {words.slice(s.first, s.last + 1).map((w, k) => {
              const i = s.first + k;
              return (
                <span key={i}>
                  <span
                    data-i={i}
                    onClick={() => {
                      if (window.getSelection()?.isCollapsed !== false) onSeek(w.start);
                    }}
                    className={`cursor-pointer rounded-sm ${i === current ? "bg-accent text-white" : "hover:bg-white/10"}`}
                  >
                    {w.text.trim()}
                  </span>{" "}
                </span>
              );
            })}
          </p>
        );
      })}
      {pending && (
        <button
          type="button"
          data-testid="create-from-selection"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            const text = words.slice(pending.first, pending.last + 1).map((w) => w.text.trim()).join(" ");
            onCreate(words[pending.first].start, words[pending.last].end, text);
            window.getSelection()?.removeAllRanges();
            setPending(null);
          }}
          className="absolute z-30 flex items-center gap-1.5 rounded-lg btn-primary px-3 py-1.5 text-xs whitespace-nowrap shadow-lg shadow-black/50"
          style={{ left: pending.x, top: pending.y }}
        >
          <Plus className="size-3.5" />
          {t("pages.moments.create_from_selection")}
        </button>
      )}
    </div>
  );
});
