"""Caption words -> pages, SRT (for CapCut) and ASS (for the FFmpeg render fallback).

Caption words live in the clip's EDL in **source time**: `{start, end, text, emphasis}`. That keeps
them valid when the editor splits or removes segments: a word is shown if the midpoint of its
time falls inside a segment, and is mapped to clip time through the segments.

Paging (`build_pages`) is mirrored exactly by `remotion/src/lib/captions.ts`; both are pinned
by the shared vectors in `frontend/src/lib/captions.vectors.json`. A new page starts when:
- the page already has `words_per_page` words,
- there is a pause longer than `max_gap` seconds,
- the previous word ends a sentence (. ! ? …),
- or the words are in different segments (a jump cut).
A page is shown from its first word until the next page starts, at most `hold` seconds after
its last word ends.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

SENTENCE_END = (".", "!", "?", "…", "。")
MAX_GAP = 0.6
HOLD = 0.4
CAPTION_STYLES = ("bold", "bounce", "clean")
FONT_FAMILY = "Be Vietnam Pro"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def clip_duration(segments: Sequence[Any]) -> float:
    return sum(_get(s, "end") - _get(s, "start") for s in segments)


def map_words(words: Sequence[Any], segments: Sequence[Any]) -> list[dict[str, Any]]:
    """Source-time words -> clip-time words. Each result keeps `index` (position in `words`) and
    `seg` (segment number). Words with empty text or outside every segment are dropped."""
    offsets = []
    acc = 0.0
    for seg in segments:
        offsets.append(acc)
        acc += _get(seg, "end") - _get(seg, "start")
    out: list[dict[str, Any]] = []
    for i, w in enumerate(words):
        text = str(_get(w, "text", "")).strip()
        if not text:
            continue
        start, end = float(_get(w, "start")), float(_get(w, "end"))
        mid = (start + end) / 2
        for k, seg in enumerate(segments):
            s0, s1 = _get(seg, "start"), _get(seg, "end")
            if s0 <= mid < s1:
                out.append({
                    "index": i,
                    "seg": k,
                    "start": offsets[k] + max(start, s0) - s0,
                    "end": offsets[k] + min(end, s1) - s0,
                    "text": text,
                    "emphasis": bool(_get(w, "emphasis", False)),
                })
                break
    return out


def build_pages(words: Sequence[Any], segments: Sequence[Any], words_per_page: int = 3, *,
                max_gap: float = MAX_GAP, hold: float = HOLD) -> list[dict[str, Any]]:
    """Caption pages in clip time: `{start, end, show_end, words: [...]}`."""
    per_page = max(1, int(words_per_page))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for w in map_words(words, segments):
        if current:
            prev = current[-1]
            if (len(current) >= per_page or w["start"] - prev["end"] > max_gap or prev["seg"] != w["seg"]
                    or prev["text"].endswith(SENTENCE_END)):
                groups.append(current)
                current = []
        current.append(w)
    if current:
        groups.append(current)
    duration = clip_duration(segments)
    pages = []
    for i, group in enumerate(groups):
        start = group[0]["start"]
        end = max(start, group[-1]["end"])
        next_start = groups[i + 1][0]["start"] if i + 1 < len(groups) else duration
        show_end = max(end, min(next_start, end + hold)) if next_start >= end else max(start, next_start)
        pages.append({"start": start, "end": end, "show_end": show_end, "words": group})
    return pages


def page_text(page: dict[str, Any]) -> str:
    return " ".join(w["text"] for w in page["words"])


def display_text(text: str, style: str) -> str:
    """Bold captions are ALL CAPS; str.upper() keeps Vietnamese diacritics (ế -> Ế, đ -> Đ)."""
    return text.upper() if style == "bold" else text


# ----------------------------------------------------------------------------- transcript words

def words_in_range(transcript_words: Iterable[Any], start: float, end: float) -> list[dict[str, Any]]:
    """Transcript words whose midpoint lies in [start, end), as caption words."""
    out = []
    for w in transcript_words:
        s, e = float(_get(w, "start")), float(_get(w, "end"))
        text = str(_get(w, "text", "")).strip()
        if text and start <= (s + e) / 2 < end:
            out.append({"start": round(s, 3), "end": round(e, 3), "text": text, "emphasis": False})
    return out


def merge_words(existing: Sequence[Any], transcript_words: Iterable[Any], start: float, end: float) -> list[dict[str, Any]]:
    """After the clip range changed: keep the (possibly edited) words still inside [start, end),
    fill the rest of the range from the transcript."""
    kept = [dict(w) for w in existing if start <= (float(_get(w, "start")) + float(_get(w, "end"))) / 2 < end]

    def overlaps(w: dict[str, Any]) -> bool:
        return any(min(w["end"], k["end"]) - max(w["start"], k["start"]) > 0.01 for k in kept)

    added = [w for w in words_in_range(transcript_words, start, end) if not overlaps(w)]
    return sorted(kept + added, key=lambda w: (w["start"], w["end"]))


# ----------------------------------------------------------------------------- SRT

def srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(pages: Sequence[dict[str, Any]]) -> str:
    """SubRip text (original case; CapCut applies its own styling)."""
    blocks = []
    for n, page in enumerate(pages, start=1):
        blocks.append(f"{n}\n{srt_time(page['start'])} --> {srt_time(page['show_end'])}\n{page_text(page)}\n")
    return "\n".join(blocks)


def edl_srt(edl: dict[str, Any]) -> str:
    captions = edl.get("captions") or {}
    pages = build_pages(captions.get("words") or [], edl.get("segments") or [], captions.get("words_per_page", 3))
    return to_srt(pages)


# ----------------------------------------------------------------------------- ASS (FFmpeg fallback)

def _ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _bgr(hex_color: str, alpha: int = 0) -> str:
    c = hex_color.lstrip("#")
    return f"&H{alpha:02X}{c[4:6]}{c[2:4]}{c[0:2]}".upper()


# libass sizes a font by its Windows ascent + descent (GDI behaviour), CSS by its em: for the
# bundled Be Vietnam Pro (usWinAscent 1261 + usWinDescent 265, 1000 units/em) that is 1.526 em.
ASS_SIZE_PER_EM = 1.526

HIGHLIGHT = "#FFD400"
EMPHASIS = "#FF3D7F"
BOUNCE_COLORS = ("#FFD400", "#3DDC97", "#4FC3F7", "#FF6B6B", "#C77DFF")


def to_ass(edl: dict[str, Any]) -> str:
    """ASS subtitles for the FFmpeg + libass render path: one event per spoken word so the active
    word is highlighted (karaoke), plus the hook text. Needs the Be Vietnam Pro font in the
    `fontsdir` given to the subtitles filter."""
    out = edl.get("output") or {"w": 1080, "h": 1920}
    w, h = int(out["w"]), int(out["h"])
    captions = edl.get("captions") or {}
    style = captions.get("style", "bold")
    scale = w / 1080
    css = captions.get("font_size", 72) * scale  # CSS px, as in the Remotion composition
    size = int(round(css * ASS_SIZE_PER_EM))
    # CSS text-stroke is centered on the outline and half hidden under the fill (paint-order)
    outline = max(2, round(css * (0.07 if style == "bold" else 0.045 if style == "bounce" else 0.0)))
    # font weights as in the Remotion composition (libass takes a weight in the Bold field)
    bold = {"bold": 900, "bounce": 800}.get(style, 600)
    shadow = 2 if style == "clean" else 0
    hook = edl.get("hook_text") or {}
    hook_css = 58 * scale
    hook_size = int(round(hook_css * ASS_SIZE_PER_EM))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Caption,{FONT_FAMILY},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,{bold},0,0,0,100,100,0,0,1,"
        f"{outline},{shadow},5,{int(w * 0.06)},{int(w * 0.06)},0,1",
        f"Style: Hook,{FONT_FAMILY},{hook_size},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,800,0,0,0,100,100,0,0,3,"
        f"{int(hook_css * 0.4)},0,5,{int(w * 0.07 + hook_css * 0.6)},{int(w * 0.07 + hook_css * 0.6)},0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if captions.get("enabled", True):
        x, y = w // 2, int(round(captions.get("position_y", 0.72) * h))
        pages = build_pages(captions.get("words") or [], edl.get("segments") or [], captions.get("words_per_page", 3))
        for page in pages:
            words = page["words"]
            for i, active in enumerate(words):
                t0 = page["start"] if i == 0 else active["start"]
                t1 = words[i + 1]["start"] if i + 1 < len(words) else page["show_end"]
                if t1 - t0 < 0.01:
                    continue
                parts = []
                for j, word in enumerate(words):
                    text = _ass_escape(display_text(word["text"], style))
                    if style == "bounce":
                        if j > i:
                            parts.append(r"{\alpha&HFF&}" + text)
                            continue
                        color = BOUNCE_COLORS[word["index"] % len(BOUNCE_COLORS)]
                        parts.append(r"{\alpha&H00&\c" + _bgr(color) + "&}" + text)
                    elif style == "clean":
                        alpha = "&H00&" if j == i else "&H40&"
                        parts.append(r"{\alpha" + alpha + r"\c&HFFFFFF&}" + text)
                    else:
                        color = HIGHLIGHT if j == i else EMPHASIS if word["emphasis"] else "#FFFFFF"
                        parts.append(r"{\c" + _bgr(color) + "&}" + text)
                lines.append(f"Dialogue: 1,{_ass_time(t0)},{_ass_time(t1)},Caption,,0,0,0,,"
                             + r"{\an5\pos(" + f"{x},{y}" + ")}" + " ".join(parts))
    if hook.get("enabled") and (hook.get("text") or "").strip():
        y = int(round(hook.get("position_y", 0.18) * h))
        end = min(float(hook.get("end", 3.5)), clip_duration(edl.get("segments") or []))
        lines.append(f"Dialogue: 2,{_ass_time(float(hook.get('start', 0)))},{_ass_time(end)},Hook,,0,0,0,,"
                     + r"{\an5\pos(" + f"{w // 2},{y}" + r")\fad(150,200)}" + _ass_escape(hook["text"].strip()))
    return "\n".join(lines) + "\n"
