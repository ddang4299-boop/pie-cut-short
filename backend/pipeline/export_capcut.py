"""CapCut bundle (Level 1): everything needed to finish a clip in CapCut, in one folder + a zip.

    clip_9x16_clean.mp4   cropped 9:16 video, original audio, no captions / hook / progress bar
    clip_final.mp4        the rendered short (captions burned in)
    captions.srt          editable captions (CapCut: import local captions)
    voiceover.mp3         the voiceover, padded with silence so it lines up when placed at 0:00
    original_audio.wav    the clip's original audio (loudness-normalized, no voiceover, no music)
    music.wav             the background music as mixed (fades, loop), lined up to clip time
    README.txt            step-by-step import instructions, English + Vietnamese
    manifest.json         what is inside and how it was made

CapCut has no public API. Generating a CapCut desktop draft project (Level 2) was investigated
and skipped (see DECISIONS.md): recent CapCut versions encrypt draft_content.json, so the draft
libraries no longer produce projects they open.
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

from pipeline import audio_mix, render
from pipeline.ingest import run_ffmpeg
from pipeline.keyframes import clip_duration

CLEAN = "clip_9x16_clean.mp4"
FINAL = "clip_final.mp4"
SRT = "captions.srt"
VOICEOVER = "voiceover.mp3"
ORIGINAL = "original_audio.wav"
README = "README.txt"

README_TEXT = """\
PieCut -> CapCut bundle
=======================
Clip: {title}
Length: {duration}   Size: {width}x{height} @ {fps} fps

Files
-----
  clip_9x16_clean.mp4  - cropped 9:16 video with the original audio, NO captions (edit this one)
  clip_final.mp4       - the finished short with captions burned in (for reference / posting as is)
  captions.srt         - the captions as editable text
{voiceover_line_en}{music_line_en}  original_audio.wav   - original audio only (loudness-normalized to {lufs} LUFS)

ENGLISH - finishing the clip in CapCut (desktop)
-----------------------------------------------
1. Open CapCut and create a new project. Set the canvas ratio to 9:16.
2. Import clip_9x16_clean.mp4 and drag it onto the main video track at 0:00.
3. Captions: open Text (or Captions) > "Import captions" / "Local captions", choose captions.srt.
   The captions appear as editable text on their own track; pick any CapCut text style.
{voiceover_steps_en}{music_steps_en}5. Optional: to mix the audio separately, mute the video clip and put original_audio.wav on an
   audio track at 0:00.
6. Export at 1080x1920, {fps} fps.
7. Back in PieCut, Export page > "Import edited version from CapCut" to keep the edited MP4
   next to this clip.
Menu names can differ slightly between CapCut versions.

TIẾNG VIỆT - hoàn thiện clip trong CapCut (bản máy tính)
--------------------------------------------------------
1. Mở CapCut, tạo dự án mới. Chọn tỉ lệ khung hình 9:16.
2. Nhập (Import) clip_9x16_clean.mp4 và kéo vào track video chính tại 0:00.
3. Phụ đề: mở Văn bản (Text) hoặc Phụ đề (Captions) > "Nhập phụ đề" / "Phụ đề cục bộ", chọn captions.srt.
   Phụ đề hiện thành chữ chỉnh sửa được trên track riêng; chọn kiểu chữ CapCut tùy ý.
{voiceover_steps_vi}{music_steps_vi}5. Tùy chọn: muốn chỉnh âm thanh riêng thì tắt tiếng clip video và đặt original_audio.wav lên một
   track âm thanh tại 0:00.
6. Xuất (Export) 1080x1920, {fps} khung hình/giây.
7. Quay lại PieCut, trang Xuất > "Nhập bản đã chỉnh từ CapCut" để lưu MP4 đã chỉnh cạnh clip này.
Tên menu có thể hơi khác giữa các phiên bản CapCut.

Tệp trong gói
-------------
  clip_9x16_clean.mp4  - video 9:16 đã cắt khung, âm thanh gốc, KHÔNG có phụ đề (chỉnh trên tệp này)
  clip_final.mp4       - bản short hoàn chỉnh, phụ đề đã in vào hình (để tham khảo / đăng luôn)
  captions.srt         - phụ đề dạng chữ chỉnh sửa được
{voiceover_line_vi}{music_line_vi}  original_audio.wav   - chỉ âm thanh gốc (chuẩn hóa độ lớn {lufs} LUFS)
"""

VO_LINE_EN = "  voiceover.mp3        - the voiceover; it starts with silence, so place it at 0:00\n"
VO_LINE_VI = "  voiceover.mp3        - lời thoại; tệp bắt đầu bằng khoảng lặng nên đặt tại 0:00\n"
VO_STEPS_EN = ("4. Voiceover: import voiceover.mp3 and drag it onto an audio track at exactly 0:00 (the file\n"
               "   already contains the silence before the voice starts). Lower the clip volume while it plays.\n")
VO_STEPS_VI = ("4. Lời thoại: nhập voiceover.mp3 và kéo vào track âm thanh đúng tại 0:00 (tệp đã có sẵn khoảng\n"
               "   lặng trước khi giọng bắt đầu). Giảm âm lượng clip trong lúc lời thoại phát.\n")
NO_VO_EN = "4. (No voiceover in this clip.)\n"
NO_VO_VI = "4. (Clip này không có lời thoại.)\n"

MUSIC_LINE_EN = "  music.wav            - the background music, already faded and lined up; place it at 0:00\n"
MUSIC_LINE_VI = "  music.wav            - nhạc nền đã fade và canh sẵn thời gian; đặt tại 0:00\n"
MUSIC_STEPS_EN = ("   Music: import music.wav onto another audio track at 0:00 and set its volume to taste\n"
                  "   (clip_final.mp4 already contains it).\n")
MUSIC_STEPS_VI = ("   Nhạc nền: nhập music.wav vào một track âm thanh khác tại 0:00 rồi chỉnh âm lượng theo ý\n"
                  "   (clip_final.mp4 đã có sẵn nhạc).\n")
MUSIC = "music.wav"


def _timecode(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m):02d}:{s:05.2f}"


def readme(*, title: str, duration: float, width: int, height: int, fps: float, lufs: float, voiceover: bool,
           music: bool = False) -> str:
    return README_TEXT.format(
        music_line_en=MUSIC_LINE_EN if music else "", music_line_vi=MUSIC_LINE_VI if music else "",
        music_steps_en=MUSIC_STEPS_EN if music else "", music_steps_vi=MUSIC_STEPS_VI if music else "",
        title=title or "-", duration=_timecode(duration), width=width, height=height, fps=f"{fps:g}", lufs=f"{lufs:g}",
        voiceover_line_en=VO_LINE_EN if voiceover else "", voiceover_line_vi=VO_LINE_VI if voiceover else "",
        voiceover_steps_en=VO_STEPS_EN if voiceover else NO_VO_EN,
        voiceover_steps_vi=VO_STEPS_VI if voiceover else NO_VO_VI)


def voiceover_command(edl: dict[str, Any], voiceover: Path, vo_duration: float, out: Path, ffmpeg: str = "ffmpeg") -> list[str] | None:
    """ffmpeg command writing the voiceover padded to clip time (None when it does not play)."""
    window = audio_mix.voiceover_window(edl, vo_duration)
    if not window:
        return None
    total = clip_duration(edl.get("segments") or [])
    vol = float((edl.get("voiceover") or {}).get("volume_db", 0.0))
    af = (f"aresample=48000,aformat=channel_layouts=stereo,volume={vol:g}dB,atrim=duration={window[1] - window[0]:.3f},"
          f"adelay={int(round(window[0] * 1000))}:all=1,apad=whole_dur={total:.3f},atrim=duration={total:.3f}")
    return [ffmpeg, "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(voiceover),
            "-af", af, "-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3", str(out)]


def zip_folder(folder: Path, zip_path: Path) -> None:
    part = zip_path.with_name(zip_path.name + ".part")
    with zipfile.ZipFile(part, "w") as z:
        for p in sorted(folder.iterdir()):
            if p.is_file() and not p.name.endswith((".part", ".part.wav")):
                media = p.suffix.lower() in (".mp4", ".mp3", ".wav")
                z.write(p, f"{folder.name}/{p.name}", compress_type=zipfile.ZIP_STORED if media else zipfile.ZIP_DEFLATED)
    os.replace(part, zip_path)


def build_bundle(inp: render.RenderInput, bundle_dir: Path, *, final: Path, srt: str, zip_path: Path,
                 hooks: render.Hooks | None = None, title: str = "") -> dict[str, Any]:
    hooks = hooks or render.Hooks()
    bundle_dir.mkdir(parents=True, exist_ok=True)
    inp.work_dir.mkdir(parents=True, exist_ok=True)
    edl = inp.edl
    duration = clip_duration(edl.get("segments") or [])
    fps = render.output_fps(edl, inp.preset)
    lufs = float((edl.get("audio") or {}).get("normalize_lufs", -14.0))

    hooks.progress(0.0, "audio")
    original = render.render_audio(inp, hooks, include_voiceover=False, include_music=False, name="original")
    hooks.check()
    hooks.progress(0.1, "clean")
    render.render_clean(inp, bundle_dir / CLEAN, original, hooks, on_frac=lambda f: hooks.progress(0.1 + 0.75 * f, "clean"))
    hooks.check()

    hooks.progress(0.85, "bundle")
    shutil.copyfile(original, bundle_dir / ORIGINAL)
    if final.exists():
        shutil.copyfile(final, bundle_dir / FINAL)
    (bundle_dir / SRT).write_text(srt, encoding="utf-8")
    has_vo = False
    (bundle_dir / VOICEOVER).unlink(missing_ok=True)
    if inp.voiceover and inp.vo_duration:
        cmd = voiceover_command(edl, inp.voiceover, inp.vo_duration, bundle_dir / (VOICEOVER + ".part"), inp.ffmpeg)
        if cmd:
            run_ffmpeg(cmd, duration=duration, log=hooks.log, on_process=hooks.on_process)
            os.replace(bundle_dir / (VOICEOVER + ".part"), bundle_dir / VOICEOVER)
            has_vo = True
    has_music = False
    (bundle_dir / MUSIC).unlink(missing_ok=True)
    if inp.music and audio_mix.music_window(edl, inp.music[1]):
        # music only (over silence), not loudness-normalized: its level relative to the clip is kept
        plan = audio_mix.build_plan(edl, media=None, music=inp.music, include_voiceover=False)
        part = bundle_dir / (MUSIC + ".part.wav")
        audio_mix.render_mix(plan, part, ffmpeg=inp.ffmpeg, log=hooks.log, on_process=hooks.on_process,
                             codec="pcm_s16le")  # 16-bit: editors read it everywhere
        os.replace(part, bundle_dir / MUSIC)
        has_music = True
    out = edl.get("output") or {"w": 1080, "h": 1920}
    (bundle_dir / README).write_text(
        readme(title=title, duration=duration, width=int(out["w"]), height=int(out["h"]), fps=fps, lufs=lufs,
               voiceover=has_vo, music=has_music), encoding="utf-8-sig")  # BOM: Notepad shows the Vietnamese text correctly
    files = sorted(p.name for p in bundle_dir.iterdir()
                   if p.is_file() and not p.name.endswith((".part", ".part.wav")) and p.name != "manifest.json")
    manifest = {"clip_id": edl.get("id"), "title": title, "duration": round(duration, 3), "fps": fps,
                "width": int(out["w"]), "height": int(out["h"]), "files": files + ["manifest.json"],
                "voiceover": has_vo, "music": has_music, "normalize_lufs": lufs}
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    zip_folder(bundle_dir, zip_path)
    hooks.progress(1.0, "bundle")
    return {"files": manifest["files"], "voiceover": has_vo, "music": has_music, "zip": zip_path.name, "folder": str(bundle_dir)}
