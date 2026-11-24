import { ArrowRight, BookOpen, Clapperboard, Download, Film, Keyboard, Lightbulb, LifeBuoy, Settings, Sparkles, WandSparkles, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { PageHeader } from "../components/states";

/** In-app user guide: the 4-step workflow, one section per step with an annotated screenshot
 *  (public/guide/<lang>/<id>.jpg, made by scripts/guide_shots.mjs), shortcuts and troubleshooting. */

interface Section {
  id: string;
  icon: LucideIcon;
  steps: number;
  tip?: boolean;
  page?: { path: string; labelKey: string };
  workflowStep?: number;
}

const SECTIONS: Section[] = [
  { id: "import", icon: Film, steps: 4, tip: true, page: { path: "/library", labelKey: "nav.library" }, workflowStep: 1 },
  { id: "find", icon: WandSparkles, steps: 3, tip: true, page: { path: "/library", labelKey: "nav.library" }, workflowStep: 2 },
  { id: "review", icon: Sparkles, steps: 5, tip: true, page: { path: "/moments", labelKey: "nav.moments" }, workflowStep: 2 },
  { id: "edit", icon: Clapperboard, steps: 5, tip: true, page: { path: "/editor", labelKey: "nav.editor" }, workflowStep: 3 },
  { id: "export", icon: Download, steps: 4, tip: true, page: { path: "/export", labelKey: "nav.export" }, workflowStep: 4 },
  { id: "settings", icon: Settings, steps: 3, page: { path: "/settings", labelKey: "nav.settings" } },
];

const WORKFLOW = [
  { key: "library", icon: Film, target: "import" },
  { key: "moments", icon: Sparkles, target: "review" },
  { key: "editor", icon: Clapperboard, target: "edit" },
  { key: "export", icon: Download, target: "export" },
];

const SHORTCUTS: [string[], string][] = [
  [["Space"], "space"],
  [["J", "K", "L"], "jkl"],
  [["←", "→"], "arrows"],
  [["Shift", "← / →"], "shift_arrows"],
  [["I", "O"], "io"],
  [["A", "R"], "ar"],
  [["↑", "↓"], "updown"],
  [["Ctrl+Z", "Ctrl+Y"], "undo"],
  [["Alt+1…6"], "pages"],
];

const scrollTo = (id: string) => document.getElementById(`guide-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });

export default function Guide() {
  const { t, i18n } = useTranslation();
  const lang = i18n.language?.startsWith("vi") ? "vi" : "en";
  const toc = [...SECTIONS.map((s) => ({ id: s.id, icon: s.icon })), { id: "shortcuts", icon: Keyboard }, { id: "trouble", icon: LifeBuoy }];

  return (
    <div className="mx-auto flex max-w-[1240px] gap-10 p-6" data-testid="guide-page">
      <aside className="sticky top-6 hidden h-fit w-56 shrink-0 xl:block">
        <p className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wide text-muted uppercase">
          <BookOpen className="size-3.5" />
          {t("guide.toc")}
        </p>
        <ol className="space-y-0.5">
          {toc.map(({ id, icon: Icon }, i) => (
            <li key={id}>
              <button
                type="button"
                onClick={() => scrollTo(id)}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-sm text-muted hover:bg-white/5 hover:text-fg"
              >
                <span className="w-4 shrink-0 text-right font-mono text-xs text-subtle">{i + 1}</span>
                <Icon className="size-4 shrink-0" />
                <span className="min-w-0 truncate">{t(`guide.${id}.title`)}</span>
              </button>
            </li>
          ))}
        </ol>
      </aside>

      <article className="min-w-0 flex-1">
        <PageHeader title={t("guide.title")} subtitle={t("guide.subtitle")} />

        {/* The workflow at a glance */}
        <section className="mb-12">
          <h2 className="mb-3 text-sm font-semibold">{t("guide.workflow_title")}</h2>
          <ol className="grid grid-cols-4 gap-3">
            {WORKFLOW.map(({ key, icon: Icon, target }, i) => (
              <li key={key}>
                <button
                  type="button"
                  onClick={() => scrollTo(target)}
                  className="group relative flex h-full w-full flex-col gap-2 rounded-xl border border-line bg-panel surface p-4 text-left transition-colors hover:border-accent/60"
                >
                  <span className="flex items-center gap-2">
                    <span className="flex size-6 items-center justify-center rounded-full bg-accent text-xs font-semibold text-white">{i + 1}</span>
                    <Icon className="size-4 text-muted group-hover:text-fg" />
                    <span className="text-sm font-semibold">{t(`nav.${key}`)}</span>
                  </span>
                  <span className="text-[13px] leading-relaxed text-muted">{t(`guide.workflow.${key}`)}</span>
                  {i < WORKFLOW.length - 1 && (
                    <ArrowRight className="absolute top-1/2 -right-[13px] z-10 size-4 -translate-y-1/2 rounded-full bg-app text-subtle" />
                  )}
                </button>
              </li>
            ))}
          </ol>
        </section>

        {SECTIONS.map((section, index) => (
          <GuideSection key={section.id} section={section} number={index + 1} lang={lang} />
        ))}

        {/* Shortcuts */}
        <section id="guide-shortcuts" className="mb-14 scroll-mt-6">
          <SectionTitle number={SECTIONS.length + 1} icon={Keyboard} title={t("guide.shortcuts.title")} />
          <p className="mb-4 text-sm text-muted">{t("guide.shortcuts.intro")}</p>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 rounded-xl border border-line bg-panel surface p-4">
            {SHORTCUTS.map(([keys, label]) => (
              <div key={label} className="flex items-center justify-between gap-4 border-b border-line py-2 last:border-0 [&:nth-last-child(2)]:border-0">
                <span className="text-sm">{t(`guide.shortcuts.${label}`)}</span>
                <span className="flex shrink-0 gap-1">
                  {keys.map((k) => (
                    <kbd key={k} className="rounded-md border border-line-strong bg-elevated px-1.5 py-0.5 font-mono text-xs text-fg">
                      {k}
                    </kbd>
                  ))}
                </span>
              </div>
            ))}
          </div>
        </section>

        {/* Troubleshooting */}
        <section id="guide-trouble" className="mb-10 scroll-mt-6">
          <SectionTitle number={SECTIONS.length + 2} icon={LifeBuoy} title={t("guide.trouble.title")} />
          <dl className="space-y-2">
            {[1, 2, 3, 4].map((n) => (
              <div key={n} className="rounded-xl border border-line bg-panel surface px-4 py-3">
                <dt className="text-sm font-medium">{t(`guide.trouble.q${n}`)}</dt>
                <dd className="mt-1 text-sm text-muted">{t(`guide.trouble.a${n}`)}</dd>
              </div>
            ))}
          </dl>
        </section>
      </article>
    </div>
  );
}

function SectionTitle({ number, icon: Icon, title, badge }: { number: number; icon: LucideIcon; title: string; badge?: string }) {
  return (
    <div className="mb-2 flex items-center gap-3">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-line-strong bg-elevated font-mono text-sm text-fg">{number}</span>
      <Icon className="size-5 shrink-0 text-accent" />
      <h2 className="text-xl font-medium tracking-[-0.02em]">{title}</h2>
      {badge && <span className="rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-xs text-accent">{badge}</span>}
    </div>
  );
}

function GuideSection({ section, number, lang }: { section: Section; number: number; lang: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [imageOk, setImageOk] = useState(true);
  const base = `guide.${section.id}`;
  return (
    <section id={`guide-${section.id}`} className="mb-14 scroll-mt-6" data-testid={`guide-${section.id}`}>
      <SectionTitle
        number={number}
        icon={section.icon}
        title={t(`${base}.title`)}
        badge={section.workflowStep ? t("guide.step_label", { n: section.workflowStep }) : undefined}
      />
      <p className="mb-4 text-sm text-muted">{t(`${base}.intro`)}</p>

      <ol className="mb-4 space-y-2">
        {Array.from({ length: section.steps }, (_, i) => (
          <li key={i} className="flex gap-3 text-sm leading-relaxed">
            <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-white/10 text-xs font-semibold">{i + 1}</span>
            <span>{t(`${base}.step${i + 1}`)}</span>
          </li>
        ))}
      </ol>

      {section.tip && (
        <p className="mb-4 flex gap-2.5 rounded-xl border border-accent/30 bg-accent/[0.07] px-4 py-3 text-sm leading-relaxed">
          <Lightbulb className="mt-0.5 size-4 shrink-0 text-accent" />
          <span>
            <strong className="font-semibold">{t("guide.tip")}: </strong>
            {t(`${base}.tip`)}
          </span>
        </p>
      )}

      {imageOk && (
        <figure className="overflow-hidden rounded-xl border border-line-strong bg-panel shadow-[0_20px_60px_-20px_rgba(124,92,255,0.25)]">
          <img
            src={`/guide/${lang}/${section.id}.jpg`}
            alt={t(`${base}.caption`)}
            loading="lazy"
            onError={() => setImageOk(false)}
            className="block w-full"
          />
          <figcaption className="border-t border-line px-4 py-2 text-xs text-muted">{t(`${base}.caption`)}</figcaption>
        </figure>
      )}

      {section.page && (
        <button
          type="button"
          onClick={() => navigate(section.page!.path)}
          className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-line-strong px-3 py-1.5 text-sm hover:border-accent hover:text-fg"
        >
          {t("guide.go_to", { page: t(section.page.labelKey) })}
          <ArrowRight className="size-3.5" />
        </button>
      )}
    </section>
  );
}
