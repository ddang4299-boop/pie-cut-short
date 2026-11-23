import { ChevronDown, ChevronRight, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { CAPTION_STYLES, PLATFORMS, listVoices, newChannel, type AppSettings, type CaptionStyle, type Channel, type Platform, type Voice } from "../../lib/api";
import { ConfirmDialog } from "../ConfirmDialog";
import { Tooltip } from "../Tooltip";
import { Card, Field, NumberInput, TextInput, Toggle, selectCls } from "./Controls";
import type { SaveFn } from "./GeneralSections";

const pct = (v: number) => Math.round(v * 100);

/** Settings → Channels: named presets (platform, caption look, hook, progress bar, voice). */
export function ChannelsPanel({ s, save }: { s: AppSettings; save: SaveFn }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Channel | null>(null);
  const [voices, setVoices] = useState<Voice[] | null>(null);
  const voiceOn = s.env.elevenlabs_configured || s.env.elevenlabs_mock;

  useEffect(() => {
    if (voiceOn) listVoices().then(setVoices, () => setVoices(null));
  }, [voiceOn]);

  const write = (list: Channel[]) => save({ channels: list });
  const update = (id: string, patch: Partial<Channel>) => void write(s.channels.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  const add = async () => {
    const before = new Set(s.channels.map((c) => c.id));
    await write([...s.channels, newChannel(t("pages.settings.channel_new_name", { n: s.channels.length + 1 }))]);
    // the backend assigns the id; open the new one
    setOpen("__new__");
    setPendingNew(before);
  };
  const [pendingNew, setPendingNew] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (!pendingNew) return;
    const created = s.channels.find((c) => !pendingNew.has(c.id));
    if (created) setOpen(created.id);
    setPendingNew(null);
  }, [s.channels, pendingNew]);

  const summary = (c: Channel) =>
    [t(`platform.${c.platform}`), t(`caption_style.${c.caption_style}`), t("pages.settings.channel_words", { count: c.words_per_page })].join(" · ");

  return (
    <Card
      title={t("pages.settings.channels_title")}
      hint={t("pages.settings.channels_hint")}
      testId="settings-channels"
      actions={
        <Tooltip label={t("pages.settings.channel_add")} side="top">
          <button type="button" data-testid="channel-add" onClick={() => void add()} disabled={s.channels.length >= 30}
            className="flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-sm hover:border-accent disabled:opacity-40">
            <Plus className="size-4" />
            {t("pages.settings.channel_add")}
          </button>
        </Tooltip>
      }
    >
      {s.channels.length === 0 && <p className="text-sm text-muted" data-testid="channels-empty">{t("pages.settings.channels_empty")}</p>}
      <ul className="space-y-2">
        {s.channels.map((c) => {
          const expanded = open === c.id;
          return (
            <li key={c.id} className="rounded-lg border border-line" data-testid="channel-row">
              <div className="flex items-center gap-2 px-3 py-2">
                <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : c.id)}>
                  {expanded ? <ChevronDown className="size-4 shrink-0 text-muted" /> : <ChevronRight className="size-4 shrink-0 text-muted" />}
                  <span className="truncate text-sm font-medium">{c.name}</span>
                  <span className="truncate text-xs text-muted">{summary(c)}</span>
                </button>
                <Tooltip label={t("pages.settings.channel_delete")} side="top">
                  <button type="button" aria-label={t("pages.settings.channel_delete")} onClick={() => setDeleting(c)}
                    className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted hover:bg-white/5 hover:text-danger">
                    <Trash2 className="size-4" />
                  </button>
                </Tooltip>
              </div>
              {expanded && (
                <div className="space-y-3 border-t border-line px-3 py-3" data-testid="channel-editor">
                  <Field label={t("pages.settings.channel_name")}>
                    <TextInput label={t("pages.settings.channel_name")} testId="channel-name" value={c.name} maxLength={60}
                      onCommit={(v) => v && update(c.id, { name: v })} />
                  </Field>
                  <Field label={t("pages.settings.platform")}>
                    <select className={selectCls} value={c.platform} onChange={(e) => update(c.id, { platform: e.target.value as Platform })}>
                      {PLATFORMS.map((p) => (
                        <option key={p} value={p}>
                          {t(`platform.${p}`)}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label={t("pages.settings.channel_captions")}>
                    <div className="flex flex-wrap items-end gap-3">
                      <label className="flex flex-col gap-1 text-xs text-muted">
                        {t("pages.settings.channel_style")}
                        <select className={`${selectCls} w-32`} data-testid="channel-style" value={c.caption_style}
                          onChange={(e) => update(c.id, { caption_style: e.target.value as CaptionStyle })}>
                          {CAPTION_STYLES.map((st) => (
                            <option key={st} value={st}>
                              {t(`caption_style.${st}`)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="flex flex-col gap-1 text-xs text-muted">
                        {t("pages.settings.channel_position")}
                        <NumberInput label={t("pages.settings.channel_position")} value={pct(c.caption_position_y)} min={5} max={95}
                          onCommit={(v) => update(c.id, { caption_position_y: v / 100 })} className="w-20" />
                      </label>
                      <label className="flex flex-col gap-1 text-xs text-muted">
                        {t("pages.settings.channel_words_per_page")}
                        <NumberInput label={t("pages.settings.channel_words_per_page")} value={c.words_per_page} min={1} max={4}
                          onCommit={(v) => update(c.id, { words_per_page: Math.round(v) })} className="w-20" />
                      </label>
                      <label className="flex flex-col gap-1 text-xs text-muted">
                        {t("pages.settings.channel_font_size")}
                        <NumberInput label={t("pages.settings.channel_font_size")} value={c.font_size} min={24} max={160}
                          onCommit={(v) => update(c.id, { font_size: Math.round(v) })} className="w-20" />
                      </label>
                    </div>
                  </Field>
                  <Field label={t("pages.settings.channel_hook")} hint={t("pages.settings.channel_hook_hint")}>
                    <div className="flex flex-wrap items-center gap-4">
                      <Toggle label={t("pages.settings.channel_show")} checked={c.hook_enabled} onChange={(v) => update(c.id, { hook_enabled: v })} />
                      <label className="flex items-center gap-2 text-xs text-muted">
                        {t("pages.settings.channel_position")}
                        <NumberInput label={t("pages.settings.channel_hook_position")} value={pct(c.hook_position_y)} min={5} max={95}
                          onCommit={(v) => update(c.id, { hook_position_y: v / 100 })} className="w-20" />
                      </label>
                    </div>
                  </Field>
                  <Field label={t("pages.settings.channel_progress")}>
                    <div className="flex flex-wrap items-center gap-4">
                      <Toggle label={t("pages.settings.channel_show")} checked={c.progress_bar_enabled} onChange={(v) => update(c.id, { progress_bar_enabled: v })} />
                      <input type="color" aria-label={t("pages.settings.channel_color")} value={c.progress_color}
                        onChange={(e) => update(c.id, { progress_color: e.target.value.toUpperCase() })}
                        className="h-8 w-12 cursor-pointer rounded border border-line bg-app" />
                    </div>
                  </Field>
                  <Field label={t("pages.settings.channel_voice")} hint={t("pages.settings.channel_voice_hint")}>
                    <select className={selectCls} value={c.voice_id} disabled={!voiceOn} onChange={(e) => update(c.id, { voice_id: e.target.value })}>
                      <option value="">{t("pages.settings.channel_voice_keep")}</option>
                      {c.voice_id && !voices?.some((v) => v.voice_id === c.voice_id) && <option value={c.voice_id}>{c.voice_id}</option>}
                      {(voices ?? []).map((v) => (
                        <option key={v.voice_id} value={v.voice_id}>
                          {v.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <ConfirmDialog
        open={deleting !== null}
        danger
        title={t("pages.settings.channel_delete_title", { name: deleting?.name ?? "" })}
        confirmLabel={t("common.delete")}
        onCancel={() => setDeleting(null)}
        onConfirm={() => {
          const id = deleting?.id;
          setDeleting(null);
          if (id) void write(s.channels.filter((c) => c.id !== id));
        }}
      >
        {t("pages.settings.channel_delete_body")}
      </ConfirmDialog>
    </Card>
  );
}
