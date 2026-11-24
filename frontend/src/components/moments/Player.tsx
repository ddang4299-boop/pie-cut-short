import { forwardRef } from "react";
import { useTranslation } from "react-i18next";

/**
 * 16:9 proxy player. With "Preview 9:16" on, everything outside the expected vertical crop
 * (centered for now; Phase 3 computes real reframe keyframes) is dimmed.
 */
export const Player = forwardRef<HTMLVideoElement, {
  src: string | null;
  sourceWidth: number | null;
  sourceHeight: number | null;
  preview916: boolean;
  onTogglePlay: () => void;
}>(function Player({ src, sourceWidth, sourceHeight, preview916, onTogglePlay }, ref) {
  const { t } = useTranslation();
  const aspect = sourceWidth && sourceHeight ? sourceWidth / sourceHeight : 16 / 9;
  // Share of the frame width kept by a 9:16 crop; ≥ 1 means the source is already vertical.
  const keep = Math.min(1, 9 / 16 / aspect);
  // The video is letterboxed inside a 16:9 box: its displayed width as a share of the box.
  const shownW = Math.min(1, aspect / (16 / 9));
  const side = ((1 - keep) / 2) * shownW;
  const offset = (1 - shownW) / 2;

  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-lg bg-black" data-testid="player">
      {src ? (
        <video
          ref={ref}
          src={src}
          preload="auto"
          playsInline
          className="size-full object-contain"
          onClick={onTogglePlay}
          data-testid="player-video"
        />
      ) : (
        <div className="flex size-full items-center justify-center text-sm text-muted">{t("pages.moments.no_proxy")}</div>
      )}
      {preview916 && src && (
        <div className="pointer-events-none absolute inset-0" data-testid="crop-overlay">
          {keep < 1 ? (
            <>
              <div className="absolute inset-y-0 left-0 bg-black/65" style={{ width: `${(offset + side) * 100}%` }} />
              <div className="absolute inset-y-0 right-0 bg-black/65" style={{ width: `${(offset + side) * 100}%` }} />
              <div
                className="absolute inset-y-0 border-x-2 border-accent/80"
                style={{ left: `${(offset + side) * 100}%`, right: `${(offset + side) * 100}%` }}
              />
            </>
          ) : (
            <span className="absolute top-2 left-2 rounded bg-black/70 px-2 py-0.5 text-xs text-white">
              {t("pages.moments.already_vertical")}
            </span>
          )}
        </div>
      )}
    </div>
  );
});
