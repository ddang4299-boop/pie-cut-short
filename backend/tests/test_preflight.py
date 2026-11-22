from pathlib import Path

import pytest

from pipeline import preflight
from pipeline.preflight import AudioStream, ProbeInfo, VideoStream, evaluate, parse_probe

from .conftest import make_media, needs_ffmpeg

GIB = preflight.GIB


def codes(issues):
    return [i["code"] for i in issues]


def probe_raw(**overrides):
    video = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "r_frame_rate": "30000/1001",
        "avg_frame_rate": "30000/1001",
        "pix_fmt": "yuv420p",
    }
    audio = {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2}
    raw = {
        "format": {"duration": "180.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [video, audio],
    }
    video.update(overrides.pop("video", {}))
    raw.update(overrides)
    return raw


def make_probe(**kw) -> ProbeInfo:
    video = kw.pop("video", VideoStream("h264", 1920, 1080, 1920, 1080, 30.0, 30.0, 0, "yuv420p"))
    audio = kw.pop("audio", AudioStream("aac", 48000, 2))
    base = dict(duration=180.0, size_bytes=1 * GIB, format_name="mp4", video=video, audio=audio, is_vfr=False)
    base.update(kw)
    return ProbeInfo(**base)


# --- parse_probe ------------------------------------------------------------
def test_parse_basic():
    p = parse_probe(probe_raw(), size_bytes=123)
    assert p.duration == 180.5
    assert p.size_bytes == 123
    assert p.video.codec == "h264"
    assert (p.video.display_width, p.video.display_height) == (1920, 1080)
    assert p.video.fps == pytest.approx(29.97, abs=0.01)
    assert p.audio == AudioStream("aac", 48000, 2)
    assert p.is_vfr is False


def test_parse_rotation_swaps_display_size():
    raw = probe_raw(video={"side_data_list": [{"rotation": -90}]})
    p = parse_probe(raw, 1)
    assert p.video.rotation == -90
    assert (p.video.display_width, p.video.display_height) == (1080, 1920)


def test_parse_rotation_from_tags():
    p = parse_probe(probe_raw(video={"tags": {"rotate": "270"}}), 1)
    assert (p.video.display_width, p.video.display_height) == (1080, 1920)


def test_parse_ignores_cover_art_video_stream():
    raw = {
        "format": {"duration": "60"},
        "streams": [
            {"codec_type": "audio", "codec_name": "mp3"},
            {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        ],
    }
    p = parse_probe(raw, 1)
    assert p.video is None
    assert p.audio.codec == "mp3"


def test_parse_detects_vfr():
    p = parse_probe(probe_raw(video={"r_frame_rate": "60/1", "avg_frame_rate": "2997/100"}), 1)
    assert p.is_vfr is True


def test_parse_handles_zero_rates_and_stream_duration_fallback():
    raw = probe_raw(video={"r_frame_rate": "0/0", "avg_frame_rate": "0/0", "duration": "42.0"})
    raw["format"].pop("duration")
    p = parse_probe(raw, 1)
    assert p.video.fps == 0 and p.is_vfr is False
    assert p.duration == 42.0


# --- evaluate ---------------------------------------------------------------
def test_evaluate_clean_file_on_gpu():
    assert evaluate(make_probe(), free_bytes=100 * GIB, device="cuda") == []


def test_evaluate_disk_space_low_with_params():
    issues = evaluate(make_probe(size_bytes=4 * GIB), free_bytes=5 * GIB, device="cuda")
    assert codes(issues) == ["DISK_SPACE_LOW"]
    assert issues[0]["severity"] == "error"
    assert issues[0]["params"] == {"needed_gb": 12.0, "free_gb": 5.0}


def test_evaluate_disk_exactly_3x_is_ok():
    assert evaluate(make_probe(size_bytes=GIB), free_bytes=3 * GIB, device="cuda") == []


def test_evaluate_no_audio_is_warning_only():
    issues = evaluate(make_probe(audio=None), free_bytes=100 * GIB, device="cuda")
    assert codes(issues) == ["NO_AUDIO_STREAM"]
    assert issues[0]["severity"] == "warning"
    assert not preflight.has_errors(issues)


def test_evaluate_no_video_is_error():
    issues = evaluate(make_probe(video=None), free_bytes=100 * GIB, device="cuda")
    assert codes(issues) == ["NO_VIDEO_STREAM"]
    assert preflight.has_errors(issues)


def test_evaluate_decode_failures():
    issues = evaluate(make_probe(), free_bytes=100 * GIB, device="cuda",
                      video_decodable=False, audio_decodable=False)
    assert sorted(codes(issues)) == ["AUDIO_DECODE_FAILED", "VIDEO_DECODE_FAILED"]
    assert {i["params"]["codec"] for i in issues} == {"h264", "aac"}


def test_evaluate_cpu_warns():
    issues = evaluate(make_probe(), free_bytes=100 * GIB, device="cpu")
    assert codes(issues) == ["GPU_NOT_AVAILABLE"]


def test_evaluate_vertical_low_res_vfr_long_sorted_by_severity():
    video = VideoStream("h264", 480, 854, 480, 854, 30.0, 25.0, 0, "yuv420p")
    issues = evaluate(make_probe(video=video, is_vfr=True, duration=3 * 3600),
                      free_bytes=100 * GIB, device="cuda")
    assert codes(issues) == ["LOW_RESOLUTION", "SOURCE_ALREADY_VERTICAL", "VARIABLE_FRAME_RATE", "VERY_LONG_VIDEO"]
    assert issues[0]["params"] == {"width": 480, "height": 854}
    assert issues[-1]["params"] == {"hours": 3.0}


def test_evaluate_invalid_duration():
    issues = evaluate(make_probe(duration=0), free_bytes=100 * GIB, device="cuda")
    assert "INVALID_DURATION" in codes(issues)


# --- device detection -------------------------------------------------------
@pytest.mark.parametrize(
    "pref, gpu, expected",
    [
        ("auto", {"vendor": "nvidia"}, "cuda"),
        ("auto", {"vendor": "apple"}, "mps"),
        ("auto", {}, "cpu"),
        ("cpu", {"vendor": "nvidia"}, "cpu"),
        ("cuda", {}, "cpu"),  # forced but unavailable -> CPU fallback
        ("mps", {"vendor": "apple"}, "mps"),
        ("CUDA", {"vendor": "nvidia"}, "cuda"),
        ("weird", {"vendor": "nvidia"}, "cuda"),
    ],
)
def test_detect_device(pref, gpu, expected):
    assert preflight.detect_device(pref, gpu) == expected


def test_machine_issues():
    ok = preflight.ToolInfo(True, "9.0", "/x/ffmpeg")
    missing = preflight.ToolInfo(False)
    assert preflight.machine_issues(ok, ok, "cuda") == []
    assert codes(preflight.machine_issues(missing, ok, "cpu")) == ["FFMPEG_NOT_FOUND", "GPU_NOT_AVAILABLE"]


# --- integration with real ffmpeg -------------------------------------------
@needs_ffmpeg
def test_run_preflight_good_file(tmp_path: Path):
    f = make_media(tmp_path / "ok.mp4")
    r = preflight.run_preflight(f, tmp_path)
    assert r["ok"] is True, r
    assert r["probe"]["video"]["width"] == 1280
    assert r["probe"]["audio"]["codec"] == "aac"
    assert r["probe"]["duration"] == pytest.approx(2.0, abs=0.1)
    assert "DISK_SPACE_LOW" not in codes(r["issues"])


@needs_ffmpeg
def test_run_preflight_vietnamese_file_name(tmp_path: Path):
    f = make_media(tmp_path / "video_tiếng_việt_đẹp.mp4")
    r = preflight.run_preflight(f, tmp_path)
    assert r["ok"] is True, r
    assert r["file"]["name"] == "video_tiếng_việt_đẹp.mp4"


@needs_ffmpeg
def test_run_preflight_video_without_audio(tmp_path: Path):
    f = make_media(tmp_path / "silent.mp4", audio=False)
    r = preflight.run_preflight(f, tmp_path)
    assert r["ok"] is True
    assert "NO_AUDIO_STREAM" in codes(r["issues"])


@needs_ffmpeg
def test_run_preflight_audio_only(tmp_path: Path):
    f = make_media(tmp_path / "audio.m4a", video=False)
    r = preflight.run_preflight(f, tmp_path)
    assert r["ok"] is False
    assert "NO_VIDEO_STREAM" in codes(r["issues"])


@needs_ffmpeg
def test_run_preflight_corrupt_file(tmp_path: Path):
    f = tmp_path / "corrupt.mp4"
    f.write_bytes(b"not a video at all " * 1000)
    r = preflight.run_preflight(f, tmp_path)
    assert r["ok"] is False
    assert codes(r["issues"]) == ["PROBE_FAILED"]
    assert r["issues"][0]["params"] == {"name": "corrupt.mp4"}


@needs_ffmpeg
def test_run_preflight_missing_and_empty(tmp_path: Path):
    r = preflight.run_preflight(tmp_path / "nope.mp4", tmp_path)
    assert codes(r["issues"]) == ["FILE_NOT_FOUND"]
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    r = preflight.run_preflight(empty, tmp_path)
    assert codes(r["issues"]) == ["FILE_EMPTY"]


def test_run_preflight_without_ffmpeg(tmp_path: Path):
    r = preflight.run_preflight(tmp_path / "x.mp4", tmp_path, ffmpeg_bin="no-such-ffmpeg-xyz",
                                ffprobe_bin="no-such-ffprobe-xyz")
    assert r["ok"] is False
    assert codes(r["issues"]) == ["FFMPEG_NOT_FOUND", "FFPROBE_NOT_FOUND"]


@needs_ffmpeg
def test_tool_info_reads_version():
    info = preflight.tool_info("ffmpeg")
    assert info.available and info.version and info.path
