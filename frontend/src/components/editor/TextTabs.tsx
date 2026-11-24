import { Short } from "@composition/Short";
import { Player } from "@remotion/player";
import { Download, Replace, Wand2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { CAPTION_STYLES, PLATFORMS, clipSrtUrl, getSettings, type Channel, type Edl, type Platform } from "../../lib/api";
import { countMatches, findReplace, pageAt, pagesOf, retimePage, setPageText, toggleEmphasis, type CaptionPage, type CaptionStyle } from "../../lib/captions";
import { formatTimecode } from "../../lib/format";
import { TimeField } from "../TimeField";
import { Tooltip } from "../Tooltip";
import { Row, Section, selectCls } from "./Inspector";

type Mode = "record" | "live";
const inputCls = "w-full min-w-0 rounded-md border border-line bg-app px-2 py-1 text-sm outline-none focus:border-accent";
const rangeCls = "min-w-0 flex-1 accent-[var(--color-accent)]";

/** A looping 3-second sample of a caption style (same composition as the render). */
function StylePreview({ style, words, language }: { style: CaptionStyle; words: string[]; language: string }) {
  const props = useMemo(() => {
    const step = 2.6 / Math.max(1, words.length);
    return {
      edl: {
        segments: [{ start: 0, end: 3 }],
        crop: { mode: "manual" as const, easing: "linear" as const, fit: "fill" as const, keyframes: [{ t: 0, x: 0.5, y: 0.5, zoom: 1 }], split: null },
        captions: {
          enabled: true,
          style,
          position_y: 0.5,
          words_per_page: 3,
          font_size: 96,
          words: words.map((text, i) => ({ start: 0.1 + i * step, end: 0.1 + (i + 1) * step, text })),
        },
        hook_text: { enabled: false, text: "", start: 0, end: 0, position_y: 0.2 },
        progress_bar: { enabled: false, color: "#FFD400" },
        audio: { volume_db: 0 },
        output: { w: 1080, h: 810, fps: 30 },
      },
      video: { src: null, width: 1080, height: 810 },
      language,
      showVideo: false,
    };
  }, [style, words, language]);
  return (
    <Player
      component={Short}
      inputProps={props}
      durationInFrames={90}
      compositionWidth={1080}
      compositionHeight={810}
      fps={30}
      autoPlay
      loop
      controls={false}
      clickToPlay={false}
      acknowledgeRemotionLicense
      style={{ width: "100%", aspectRatio: "4 / 3", backgroundColor: "transparent" }}
    />
  );
}

/** One caption page: time range, editable text, word chips to mark emphasis. */
function PageRow({
  page,
  number,
  active,
  duration,
  onSeek,
  onText,
  onTime,
  onEmphasis,
}: {
  page: CaptionPage;
  number: number;
  active: boolean;
  duration: number;
  onSeek: (t: number) => void;
  onText: (text: string) => void;
  onTime: (start: number, end: number) => void;
  onEmphasis: (index: number) => void;
}) {
  const { t } = useTranslation();
  const text = page.words.map((w) => w.text).join(" ");
  const [draft, setDraft] = useState(text);
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => setDraft(text), [text]);
  useEffect(() => {
    if (active) row.current?.scrollIntoView({ block: "nearest" });
  }, [active]);
  const commit = () => {
    if (draft.trim() !== text) onText(draft);
  };
  return (
    <div
      ref={row}
      data-testid="caption-block"
      data-active={active || undefined}
      className={`rounded-lg border p-2 ${active ? "border-accent/70 bg-accent/10" : "border-line bg-app/40"}`}
    >
      <div className="mb-1.5 flex items-center gap-1.5">
        <button type="button" onClick={() => onSeek(page.start)} className="shrink-0 rounded px-1 font-mono text-xs text-muted hover:bg-white/5 hover:text-fg" title={t("pages.moments.seek_here")}>
          #{number}
        </button>
        <TimeField value={page.start} max={duration} label={t("pages.editor.block_start")} onChange={(v) => onTime(v, page.end)} className="w-[86px]" />
        <span className="text-muted">–</span>
        <TimeField value={page.end} max={duration} label={t("pages.editor.block_end")} onChange={(v) => onTime(page.start, v)} className="w-[86px]" />
      </div>
      <input
        value={draft}
        data-testid="caption-text"
        aria-label={t("pages.editor.block_text")}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") setDraft(text);
        }}
        className={inputCls}
        data-content
      />
      <div className="mt-1.5 flex flex-wrap gap-1">
        {page.words.map((w) => (
          <button
            key={w.index}
            type="button"
            aria-pressed={w.emphasis}
            data-testid="emphasis-chip"
            title={t("pages.editor.emphasis_hint")}
            onClick={() => onEmphasis(w.index)}
            className={`rounded px-1.5 py-0.5 text-xs ${w.emphasis ? "bg-accent text-white" : "bg-white/5 text-muted hover:text-fg"}`}
            data-content
          >
            {w.text}
          </button>
        ))}
      </div>
    </div>
  );
}

export function CaptionsTab({
  edl,
  clipTime,
  duration,
  language,
  onCaptions,
  onSeek,
}: {
  edl: Edl;
  clipTime: number;
  duration: number;
  language: string;
  onCaptions: (c: Edl["captions"], mode?: Mode, coalesceKey?: string) => void;
  onSeek: (t: number) => void;
}) {
  const { t, i18n } = useTranslation();
  const c = edl.captions;
  const pages = useMemo(() => pagesOf(c.words, edl.segments, c.words_per_page), [c.words, edl.segments, c.words_per_page]);
  const current = pageAt(pages, clipTime);
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [replaced, setReplaced] = useState<number | null>(null);
  const matches = useMemo(() => countMatches(c.words, find), [c.words, find]);
  const sample = useMemo(() => {
    const words = pages.flatMap((p) => p.words.map((w) => w.text)).slice(0, 6);
    return words.length >= 3 ? words : t("pages.editor.caption_sample").split(" ");
  }, [pages, t, i18n.language]); // eslint-disable-line react-hooks/exhaustive-deps
  const setWords = (words: Edl["captions"]["words"]) => onCaptions({ ...c, words });
  // first frame on which a block is visible (frame n shows time n / fps)
  const seekFrame = (t: number) => onSeek(Math.ceil(t * edl.output.fps - 1e-6) / edl.output.fps + 0.001);

  return (
    <div data-testid="captions-tab">
      <Section
        title={t("pages.editor.caption_style")}
        actions={
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input type="checkbox" checked={c.enabled} data-testid="captions-enabled" onChange={(e) => onCaptions({ ...c, enabled: e.target.checked })} className="accent-[var(--color-accent)]" />
            {t("pages.editor.show")}
          </label>
        }
      >
        <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label={t("pages.editor.caption_style")}>
          {CAPTION_STYLES.map((s) => (
            <button
              key={s}
              type="button"
              role="radio"
              aria-checked={c.style === s}
              data-testid={`style-${s}`}
              onClick={() => onCaptions({ ...c, style: s })}
              className={`overflow-hidden rounded-lg border text-left ${c.style === s ? "border-accent ring-1 ring-accent" : "border-line hover:border-muted"}`}
            >
              <div className="bg-gradient-to-br from-[#2a2a38] to-[#101018]">
                <StylePreview style={s} words={sample} language={language} />
              </div>
              <div className="truncate px-2 py-1 text-xs">{t(`caption_style.${s}`)}</div>
            </button>
          ))}
        </div>
      </Section>
      <Section title={t("pages.editor.caption_layout")}>
        <Row label={t("pages.editor.font_size")}>
          <span className="flex items-center gap-2">
            <input
              type="range"
              min={40}
              max={140}
              step={2}
              value={c.font_size}
              aria-label={t("pages.editor.font_size")}
              data-testid="caption-size"
              onChange={(e) => onCaptions({ ...c, font_size: Number(e.target.value) }, "record", "caption-size")}
              className={rangeCls}
            />
            <span className="w-10 text-right font-mono text-xs">{c.font_size}</span>
          </span>
        </Row>
        <Row label={t("pages.editor.position_y")} hint={t("pages.editor.position_hint")}>
          <span className="flex items-center gap-2">
            <input
              type="range"
              min={5}
              max={95}
              step={1}
              value={Math.round(c.position_y * 100)}
              aria-label={t("pages.editor.position_y")}
              data-testid="caption-y"
              onChange={(e) => onCaptions({ ...c, position_y: Number(e.target.value) / 100 }, "record", "caption-y")}
              className={rangeCls}
            />
            <span className="w-10 text-right font-mono text-xs">{Math.round(c.position_y * 100)}%</span>
          </span>
        </Row>
        <Row label={t("pages.editor.words_per_page")}>
          <span className="grid grid-cols-4 gap-1 rounded-lg border border-line bg-app p-0.5">
            {[1, 2, 3, 4].map((n) => (
              <button
                key={n}
                type="button"
                aria-pressed={c.words_per_page === n}
                data-testid={`wpp-${n}`}
                onClick={() => onCaptions({ ...c, words_per_page: n })}
                className={`rounded-md py-1 text-xs ${c.words_per_page === n ? "bg-accent text-white" : "text-muted hover:text-fg"}`}
              >
                {n}
              </button>
            ))}
          </span>
        </Row>
        <p className="text-xs text-muted">{t("pages.editor.position_hint")}</p>
      </Section>
      <Section title={t("pages.editor.find_replace")}>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] gap-1.5">
          <input
            value={find}
            placeholder={t("pages.editor.find")}
            aria-label={t("pages.editor.find")}
            data-testid="find-input"
            onChange={(e) => {
              setFind(e.target.value);
              setReplaced(null);
            }}
            onKeyDown={(e) => e.stopPropagation()}
            className={inputCls}
          />
          <input
            value={replace}
            placeholder={t("pages.editor.replace_with")}
            aria-label={t("pages.editor.replace_with")}
            data-testid="replace-input"
            onChange={(e) => setReplace(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
            className={inputCls}
          />
          <Tooltip label={t("pages.editor.replace_all")} side="top">
            <button
              type="button"
              disabled={!matches}
              data-testid="replace-all"
              aria-label={t("pages.editor.replace_all")}
              onClick={() => {
                const res = findReplace(c.words, find, replace);
                setWords(res.words);
                setReplaced(res.count);
              }}
              className="flex size-8 items-center justify-center rounded-md border border-line hover:bg-white/5 disabled:opacity-30"
            >
              <Replace className="size-4" />
            </button>
          </Tooltip>
        </div>
        <p className="mt-1.5 text-xs text-muted" data-testid="find-count">
          {replaced !== null ? t("pages.editor.replaced", { count: replaced }) : find ? t("pages.editor.matches", { count: matches }) : t("pages.editor.find_hint")}
        </p>
      </Section>
      <Section
        title={t("pages.editor.blocks", { count: pages.length })}
        actions={
          <a href={clipSrtUrl(edl.id)} download className="flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-muted hover:bg-white/5 hover:text-fg" data-testid="download-srt">
            <Download className="size-3.5" />
            SRT
          </a>
        }
      >
        {pages.length === 0 ? (
          <p className="text-sm text-muted">{t("pages.editor.no_words")}</p>
        ) : (
          <div className="space-y-1.5" data-testid="caption-blocks">
            {pages.map((p, i) => (
              <PageRow
                number={i + 1}
                key={`${p.words[0].index}-${p.words.length}`}
                page={p}
                active={current === p || (current !== null && current.start === p.start)}
                duration={duration}
                onSeek={seekFrame}
                onText={(text) => setWords(setPageText(c.words, p, text))}
                onTime={(start, end) => setWords(retimePage(c.words, edl.segments, p, start, end))}
                onEmphasis={(i) => setWords(toggleEmphasis(c.words, i))}
              />
            ))}
          </div>
        )}
        <p className="mt-2 text-xs text-muted">{t("pages.editor.blocks_hint", { time: formatTimecode(clipTime) })}</p>
      </Section>
    </div>
  );
}

export function HookTab({ edl, duration, onHook }: { edl: Edl; duration: number; onHook: (h: Edl["hook_text"], mode?: Mode, coalesceKey?: string) => void }) {
  const { t } = useTranslation();
  const h = edl.hook_text;
  return (
    <div data-testid="hook-tab">
      <Section
        title={t("pages.editor.hook_text")}
        actions={
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input type="checkbox" checked={h.enabled} data-testid="hook-enabled" onChange={(e) => onHook({ ...h, enabled: e.target.checked })} className="accent-[var(--color-accent)]" />
            {t("pages.editor.show")}
          </label>
        }
      >
        <textarea
          value={h.text}
          maxLength={200}
          rows={3}
          data-testid="hook-input"
          aria-label={t("pages.editor.hook_text")}
          placeholder={t("pages.editor.hook_placeholder")}
          onChange={(e) => onHook({ ...h, text: e.target.value }, "record", "hook-text")}
          onKeyDown={(e) => e.stopPropagation()}
          className={`${inputCls} resize-none`}
          data-content
        />
        <p className="mt-1 text-right font-mono text-xs text-muted">{h.text.length}/200</p>
      </Section>
      <Section title={t("pages.editor.hook_timing")}>
        <Row label={t("pages.editor.hook_start")}>
          <TimeField value={h.start} max={Math.max(0, h.end - 0.2)} label={t("pages.editor.hook_start")} onChange={(v) => onHook({ ...h, start: v })} testId="hook-start" />
        </Row>
        <Row label={t("pages.editor.hook_end")}>
          <TimeField value={h.end} min={h.start + 0.2} max={Math.max(duration, h.start + 0.2)} label={t("pages.editor.hook_end")} onChange={(v) => onHook({ ...h, end: v })} testId="hook-end" />
        </Row>
        <Row label={t("pages.editor.position_y")} hint={t("pages.editor.position_hint")}>
          <span className="flex items-center gap-2">
            <input
              type="range"
              min={5}
              max={95}
              step={1}
              value={Math.round(h.position_y * 100)}
              aria-label={t("pages.editor.position_y")}
              data-testid="hook-y"
              onChange={(e) => onHook({ ...h, position_y: Number(e.target.value) / 100 }, "record", "hook-y")}
              className={rangeCls}
            />
            <span className="w-10 text-right font-mono text-xs">{Math.round(h.position_y * 100)}%</span>
          </span>
        </Row>
        <p className="text-xs text-muted">{t("pages.editor.hook_default")}</p>
      </Section>
    </div>
  );
}

const BAR_COLORS = ["#FFD400", "#7c5cff", "#3DDC97", "#4FC3F7", "#FFFFFF"];

/** Pick a channel preset (Settings → Channels) and apply it to this clip (undoable). */
function ChannelPicker({ onApply }: { onApply: (c: Channel) => void }) {
  const { t } = useTranslation();
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [pick, setPick] = useState("");
  useEffect(() => {
    getSettings()
      .then((s) => {
        setChannels(s.channels ?? []);
        setPick(s.channels?.[0]?.id ?? "");
      })
      .catch(() => setChannels([]));
  }, []);
  if (!channels) return null;
  const chosen = channels.find((c) => c.id === pick);
  return (
    <Section title={t("pages.editor.channel_title")}>
      {channels.length === 0 ? (
        <p className="text-xs text-muted" data-testid="channel-none">{t("pages.editor.channel_none")}</p>
      ) : (
        <div className="flex items-center gap-2">
          <select className={selectCls} value={pick} data-testid="channel-pick" aria-label={t("pages.editor.channel_title")} onChange={(e) => setPick(e.target.value)}>
            {channels.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <Tooltip label={t("pages.editor.channel_apply_hint")} side="top">
            <button
              type="button"
              data-testid="channel-apply"
              disabled={!chosen}
              onClick={() => chosen && onApply(chosen)}
              className="flex shrink-0 items-center gap-1 rounded-md border border-line px-2 py-1.5 text-xs hover:border-accent disabled:opacity-40"
            >
              <Wand2 className="size-3.5" />
              {t("pages.editor.channel_apply")}
            </button>
          </Tooltip>
        </div>
      )}
    </Section>
  );
}

export function ExportTab({
  edl,
  onProgress,
  onPlatform,
  onChannel,
  render,
}: {
  edl: Edl;
  onProgress: (p: Edl["progress_bar"]) => void;
  onPlatform: (p: Platform) => void;
  onChannel?: (c: Channel) => void;
  render?: ReactNode;
}) {
  const { t } = useTranslation();
  const p = edl.progress_bar;
  return (
    <div data-testid="export-tab">
      {onChannel && <ChannelPicker onApply={onChannel} />}
      <Section title={t("pages.editor.target")}>
        <Row label={t("pages.moments.platform")}>
          <select className={selectCls} value={edl.platform} data-testid="platform" onChange={(e) => onPlatform(e.target.value as Platform)}>
            {PLATFORMS.map((k) => (
              <option key={k} value={k}>
                {t(`platform.${k}`)}
              </option>
            ))}
          </select>
        </Row>
      </Section>
      <Section
        title={t("pages.editor.progress_bar")}
        actions={
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input type="checkbox" checked={p.enabled} data-testid="progress-enabled" onChange={(e) => onProgress({ ...p, enabled: e.target.checked })} className="accent-[var(--color-accent)]" />
            {t("pages.editor.show")}
          </label>
        }
      >
        <Row label={t("pages.editor.color")}>
          <span className="flex items-center gap-1.5">
            {BAR_COLORS.map((col) => (
              <button
                key={col}
                type="button"
                aria-label={col}
                aria-pressed={p.color.toUpperCase() === col}
                onClick={() => onProgress({ ...p, color: col })}
                className={`size-6 shrink-0 rounded-full border ${p.color.toUpperCase() === col ? "border-white ring-2 ring-accent" : "border-line"}`}
                style={{ backgroundColor: col }}
              />
            ))}
            <input
              type="color"
              value={p.color}
              aria-label={t("pages.editor.custom_color")}
              data-testid="progress-color"
              onChange={(e) => onProgress({ ...p, color: e.target.value.toUpperCase() })}
              className="h-6 w-8 shrink-0 cursor-pointer rounded border border-line bg-transparent"
            />
          </span>
        </Row>
      </Section>
      {render}
    </div>
  );
}
