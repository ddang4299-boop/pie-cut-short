"""Pure logic of the render path: platform presets, the audio filter graph, the FFmpeg compositor's
geometry (must match remotion/src/layers/VideoLayer.tsx), encoder arguments + fallback, CapCut
bundle text."""

from pathlib import Path

import numpy as np
import pytest

from pipeline import audio_mix, compose, export_capcut, presets, render


# --------------------------------------------------------------------------
# presets
# --------------------------------------------------------------------------
def test_presets_defaults_overrides_and_clamping():
    yt = presets.resolve("youtube_shorts")
    assert yt["max_seconds"] == 60 and yt["video_bitrate_mbps"] == 12 and yt["platform"] == "youtube_shorts"
    assert presets.resolve("reels")["fps"] == 30
    assert presets.resolve("nope")["platform"] == "youtube_shorts"
    o = presets.resolve("tiktok", {"tiktok": {"max_seconds": 9999, "video_bitrate_mbps": 0, "fps": 25, "junk": 1},
                                   "reels": {"max_seconds": 10}})
    assert o["max_seconds"] == 600 and o["video_bitrate_mbps"] == 1 and o["fps"] == 25 and "junk" not in o
    assert presets.resolve("tiktok", {"tiktok": {"fps": 5}})["fps"] is None  # invalid -> default kept
    # defaults are not mutated by overrides
    assert presets.DEFAULT_PRESETS["tiktok"]["max_seconds"] == 180
    assert set(presets.all_presets()) == set(presets.PLATFORMS)


def test_length_warning():
    yt = presets.resolve("youtube_shorts")
    assert presets.length_warning(60.03, yt) is None
    assert presets.length_warning(61.27, yt) == {"seconds": 61.3, "max": 60, "platform": "youtube_shorts"}


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------
def _edl(**over):
    base = {"segments": [{"start": 10.0, "end": 14.0}, {"start": 20.0, "end": 21.5}],
            "audio": {"normalize_lufs": -14.0, "volume_db": -3.0},
            "voiceover": {"enabled": False}}
    base.update(over)
    return base


def test_audio_plan_cuts_segments_relative_to_the_media():
    plan = audio_mix.build_plan(_edl(), media=Path("rough.mp4"), media_offset=8.0)
    assert plan.duration == 5.5
    assert plan.inputs[0] == ["-ss", "2", "-t", "4", "-i", "rough.mp4"]
    assert plan.inputs[1] == ["-ss", "12", "-t", "1.5", "-i", "rough.mp4"]
    fc = plan.filter_complex
    assert "concat=n=2:v=0:a=1[orig]" in fc and "volume=-3dB" in fc
    assert fc.endswith("apad=whole_dur=5.5,atrim=duration=5.5,asetpts=PTS-STARTPTS[aout]")
    assert plan.duck is None and "amix" not in fc


def test_audio_plan_without_audio_is_silence():
    plan = audio_mix.build_plan(_edl(), media=None)
    assert plan.inputs == [["-f", "lavfi", "-t", "5.5", "-i", "anullsrc=r=48000:cl=stereo"]]


def test_audio_plan_isolated_audio_replaces_the_original():
    plan = audio_mix.build_plan(_edl(), media=Path("rough.mp4"), media_offset=8.0, isolated=(Path("iso.mp3"), 10.0))
    assert plan.inputs[0] == ["-ss", "0", "-t", "4", "-i", "iso.mp3"]


def test_audio_plan_voiceover_ducking_window():
    edl = _edl(voiceover={"enabled": True, "audio_path": "voice_cache/x.mp3", "start": 1.0, "duck_db": -12.0, "volume_db": 2})
    plan = audio_mix.build_plan(edl, media=Path("m.mp4"), voiceover=Path("vo.mp3"), vo_duration=10.0)
    assert plan.duck == (1.0, 5.5)  # clipped to the clip's length
    fc = plan.filter_complex
    assert "between(t,1,5.5),0.25119,1" in fc and "adelay=1000:all=1" in fc and "volume=2dB" in fc
    assert "amix=inputs=2:duration=first:normalize=0" in fc and plan.inputs[-1] == ["-i", "vo.mp3"]
    # include_voiceover=False (CapCut original audio): no ducking, no voiceover input
    clean = audio_mix.build_plan(edl, media=Path("m.mp4"), voiceover=Path("vo.mp3"), vo_duration=10.0, include_voiceover=False)
    assert clean.duck is None and len(clean.inputs) == 2
    # disabled or starting after the end -> no window
    assert audio_mix.voiceover_window({**edl, "voiceover": {**edl["voiceover"], "enabled": False}}, 3) is None
    assert audio_mix.voiceover_window({**edl, "voiceover": {**edl["voiceover"], "start": 9.0}}, 3) is None


def test_loudnorm_parsing_and_filters():
    err = 'junk\n[Parsed_loudnorm_0 @ 0x1]\n{\n\t"input_i" : "-19.44",\n\t"input_tp" : "-3.34",\n\t"input_lra" : "3.00",\n' \
          '\t"input_thresh" : "-29.74",\n\t"target_offset" : "1.27",\n\t"normalization_type" : "dynamic"\n}\n'
    m = audio_mix.parse_loudnorm_json(err)
    assert m["input_i"] == -19.44 and m["target_offset"] == 1.27 and "normalization_type" not in m
    f = audio_mix.loudnorm_filter(-14, m)
    assert "measured_I=-19.44" in f and "linear=true" in f and f.startswith("loudnorm=I=-14:TP=-1.5")
    assert audio_mix.loudnorm_filter(-14).endswith("print_format=json")
    with pytest.raises(ValueError):
        audio_mix.parse_loudnorm_json("nothing")
    assert "volume=3.20dB" in audio_mix.correction_filter(3.2) and "alimiter" in audio_mix.correction_filter(3.2)


def test_ebur128_parsing():
    err = "[Parsed_ebur128_0] t: 1 ...\nSummary:\n\n  Integrated loudness:\n    I:         -14.2 LUFS\n" \
          "  True peak:\n    Peak:       -1.8 dBFS\n"
    assert audio_mix.parse_ebur128(err) == {"lufs": -14.2, "true_peak": -1.8}
    assert audio_mix.parse_ebur128("Summary:\n I: -inf LUFS\n Peak: -inf dBFS") == {"lufs": None, "true_peak": None}


# --------------------------------------------------------------------------
# compositor geometry
# --------------------------------------------------------------------------
def test_layout_fill_uses_the_keyframed_crop():
    edl = {"output": {"w": 1080, "h": 1920}, "crop": {"mode": "manual", "fit": "fill",
                                                      "keyframes": [{"t": 0, "x": 0.5, "y": 0.5, "zoom": 1.0}]}}
    (p,) = compose.layout(edl, 1920, 1080, 0.0)
    l, t, w, h = p.src
    assert h == pytest.approx(1080) and w == pytest.approx(1080 * 9 / 16) and l == pytest.approx((1920 - w) / 2)
    assert p.dst == (0, 0, 1080, 1920) and not p.blur


def test_layout_blur_and_split():
    edl = {"output": {"w": 1080, "h": 1920}, "crop": {"mode": "auto", "fit": "blur", "keyframes": []}}
    bg, fg = compose.layout(edl, 1920, 1080, 0.0)
    assert bg.blur and bg.dst[2] == pytest.approx(1920 * (1920 / 1080) * 1.08)
    assert fg.dst == pytest.approx((0, (1920 - 607.5) / 2, 1080, 607.5))
    split = {"output": {"w": 1080, "h": 1920}, "crop": {"mode": "split", "split": {
        "top": [0, 0, 0.5, 0.5], "bottom": [0, 0, 1, 1], "ratio": 0.4}}}
    top, bottom = compose.layout(split, 1920, 1080, 0.0)
    assert top.clip == (0, 0, 1080, 768) and bottom.clip == (0, 768, 1080, 1152)
    # the source region is cropped to the panel's aspect (centered)
    assert top.src[2] / top.src[3] == pytest.approx(1080 / 768)


def test_compose_fills_the_canvas_and_draws_the_progress_bar():
    frame = np.full((90, 160, 3), 200, np.uint8)
    edl = {"output": {"w": 108, "h": 192}, "crop": {"mode": "manual", "fit": "fill",
                                                    "keyframes": [{"t": 0, "x": 0.5, "y": 0.5, "zoom": 1.0}]}}
    canvas = compose.compose(frame, compose.layout(edl, 160, 90, 0), 108, 192)
    assert canvas.shape == (192, 108, 3) and abs(int(canvas[96, 54, 0]) - 200) <= 2
    compose.draw_progress(canvas, 0.5, "#FF0000")
    assert tuple(canvas[-1, 10]) == (0, 0, 255) and tuple(canvas[-1, 100]) != (0, 0, 255)


def test_frame_plan_matches_remotion_rounding():
    segs = [{"start": 1.01, "end": 2.0}, {"start": 5.0, "end": 5.52}]
    plan = compose.frame_plan(segs, 30)
    assert [(p.first_frame, p.count) for p in plan] == [(0, 30), (30, 15)]
    assert plan[1].source_start == pytest.approx(150 / 30)
    assert compose.total_frames(segs, 30) == 45
    assert compose.source_time_of_frame(segs, 30, 31) == pytest.approx(5.0 + 1 / 30)
    assert compose.source_time_of_frame(segs, 30, 999) == pytest.approx(5.0 + 14 / 30)


# --------------------------------------------------------------------------
# render helpers
# --------------------------------------------------------------------------
def test_video_args_per_encoder():
    yt = presets.resolve("youtube_shorts")
    x264 = render.video_args("libx264", yt, 30)
    assert x264[:2] == ["-c:v", "libx264"] and "-crf" in x264 and "12M" in x264 and "-color_range" in x264
    nv = render.video_args("h264_nvenc", yt, 30)
    assert "-b:v" in nv and "12M" in nv and "-rc" in nv
    assert render.encoder_label("h264_nvenc") == "nvenc" and render.encoder_label("libx264") == "cpu"
    assert render.audio_args(yt)[:4] == ["-c:a", "aac", "-b:a", "192k"]


def test_encoder_fallback_records_a_warning():
    result = render.RenderResult(final=Path("x.mp4"), renderer="ffmpeg", encoder="", duration=1, width=1, height=1, fps=30)
    tried = []

    def run(enc):
        tried.append(enc)
        if enc == "h264_nvenc":
            raise render.FFmpegError("encode failed", "driver too old")

    assert render.with_encoder_fallback(["h264_nvenc", "libx264"], run, result, render.Hooks()) == "libx264"
    assert tried == ["h264_nvenc", "libx264"]
    assert result.warnings == [{"code": "RENDER_ENCODER_FALLBACK", "params": {"encoder": "nvenc"}}]
    with pytest.raises(render.RenderError):
        render.with_encoder_fallback(["libx264"], lambda e: (_ for _ in ()).throw(render.FFmpegError("x", "y")),
                                     result, render.Hooks())


def test_edl_helpers():
    edl = {"segments": [{"start": 10.0, "end": 12.0}], "captions": {"enabled": True, "style": "bold"},
           "hook_text": {"enabled": True}, "progress_bar": {"enabled": True, "color": "#FFD400"}, "output": {"fps": 30}}
    assert render.local_edl(edl, 9.5)["segments"] == [{"start": 0.5, "end": 2.5}]
    clean = render.without_overlays(edl)
    assert not clean["captions"]["enabled"] and clean["captions"]["style"] == "bold" and edl["captions"]["enabled"]
    assert render.output_fps(edl, {"fps": None}) == 30 and render.output_fps(edl, {"fps": 25}) == 25
    assert render.verify_frames(100) == [25, 50, 75]


# --------------------------------------------------------------------------
# CapCut bundle text
# --------------------------------------------------------------------------
def test_capcut_readme_is_bilingual():
    text = export_capcut.readme(title="Xin chào", duration=75.5, width=1080, height=1920, fps=30, lufs=-14, voiceover=True)
    assert "ENGLISH" in text and "TIẾNG VIỆT" in text and "voiceover.mp3" in text and "01:15.50" in text
    assert "Nhập phụ đề" in text and "Import captions" in text
    no_vo = export_capcut.readme(title="", duration=5, width=1080, height=1920, fps=30, lufs=-14, voiceover=False)
    assert "voiceover.mp3" not in no_vo and "Clip này không có lời thoại" in no_vo


def test_capcut_voiceover_is_padded_to_clip_time():
    edl = {"segments": [{"start": 0, "end": 10}], "voiceover": {"enabled": True, "audio_path": "voice_cache/a.mp3",
                                                                "start": 2.5, "volume_db": 0}}
    cmd = export_capcut.voiceover_command(edl, Path("vo.mp3"), 3.0, Path("out.mp3"))
    af = cmd[cmd.index("-af") + 1]
    assert "adelay=2500:all=1" in af and "apad=whole_dur=10.000" in af and "atrim=duration=3.000" in af
    assert export_capcut.voiceover_command({**edl, "voiceover": {"enabled": False}}, Path("vo.mp3"), 3.0, Path("o")) is None
