import json
import subprocess

import pytest

from pipeline import ingest, preflight
from tests.conftest import make_media, needs_ffmpeg


# -- quick_hash ---------------------------------------------------------------
def test_quick_hash_same_content_same_hash(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    data = bytes(range(256)) * 20000  # ~5 MB, so start/middle/end chunks differ
    a.write_bytes(data)
    b.write_bytes(data)
    assert ingest.quick_hash(a) == ingest.quick_hash(b)
    assert len(ingest.quick_hash(a)) == 32


def test_quick_hash_detects_changes_in_sampled_regions(tmp_path):
    data = bytearray(b"x" * 6_000_000)
    base = tmp_path / "base.bin"
    base.write_bytes(data)
    h0 = ingest.quick_hash(base)
    for pos in (10, len(data) // 2, len(data) - 10):
        changed = bytearray(data)
        changed[pos] = ord("y")
        p = tmp_path / f"c{pos}.bin"
        p.write_bytes(changed)
        assert ingest.quick_hash(p) != h0, pos
    longer = tmp_path / "longer.bin"
    longer.write_bytes(bytes(data) + b"x")
    assert ingest.quick_hash(longer) != h0


def test_quick_hash_small_file(tmp_path):
    p = tmp_path / "small.bin"
    p.write_bytes(b"abc")
    assert ingest.quick_hash(p) == ingest.quick_hash(p)


# -- proxy geometry / rate --------------------------------------------------------
@pytest.mark.parametrize(
    ("w", "h", "expected"),
    [
        (1920, 1080, (1280, 720)),
        (3840, 2160, (1280, 720)),
        (1080, 1920, (720, 1280)),
        (1280, 720, (1280, 720)),
        (640, 360, (640, 360)),  # never upscale
        (1441, 1081, (960, 720)),  # odd input -> even output
        (2560, 1080, (1706, 720)),
    ],
)
def test_proxy_size(w, h, expected):
    out = ingest.proxy_size(w, h)
    assert out == expected
    assert out[0] % 2 == 0 and out[1] % 2 == 0


def test_proxy_size_rejects_invalid():
    with pytest.raises(ValueError):
        ingest.proxy_size(0, 720)


@pytest.mark.parametrize(
    ("fps", "expected"),
    [
        (29.97002997, "30000/1001"),
        (23.976, "24000/1001"),
        (25.0, "25"),
        (30.0, "30"),
        (24.0, "24"),
        (60.0, "30"),
        (59.94005994, "30000/1001"),
        (50.0, "25"),
        (120.0, "30"),
        (0.0, "30"),
        (15.0, "15"),
    ],
)
def test_proxy_fps(fps, expected):
    assert ingest.proxy_fps(fps) == expected


def test_build_proxy_cmd_cfr_and_keyframes(tmp_path):
    cmd = ingest.build_proxy_cmd(tmp_path / "in.mov", tmp_path / "out.mp4", width=1280, height=720,
                                 fps="30000/1001", encoder="libx264", has_audio=True)
    joined = " ".join(cmd)
    assert "-fps_mode cfr" in joined and "-r 30000/1001" in joined
    assert "scale=1280:720" in joined
    assert cmd[cmd.index("-g") + 1] == "30"
    assert "-map 0:a:0" in joined and "-c:a aac" in joined
    assert "+faststart" in joined and cmd[-1].endswith("out.mp4")


def test_build_proxy_cmd_without_audio_and_nvenc(tmp_path):
    cmd = ingest.build_proxy_cmd(tmp_path / "in.mp4", tmp_path / "out.mp4", width=720, height=1280,
                                 fps="25", encoder="h264_nvenc", has_audio=False)
    joined = " ".join(cmd)
    assert "0:a" not in joined and "-c:a" not in joined
    assert "h264_nvenc" in joined


@pytest.mark.parametrize(
    ("line", "seconds"),
    [
        ("out_time_us=1500000", 1.5),
        ("out_time_ms=2000000", 2.0),  # ffmpeg's out_time_ms is microseconds too
        ("out_time=00:01:02.500000", 62.5),
        ("out_time_us=N/A", None),
        ("frame=12", None),
        ("progress=end", None),
    ],
)
def test_parse_progress_line(line, seconds):
    assert ingest.parse_progress_line(line) == seconds


def test_is_video_name():
    assert ingest.is_video_name("Phỏng vấn.MP4")
    assert ingest.is_video_name("a.mkv")
    assert not ingest.is_video_name("notes.txt")


# -- with ffmpeg ------------------------------------------------------------------
def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return preflight.parse_probe(json.loads(out), path.stat().st_size)


@needs_ffmpeg
def test_make_proxy_produces_720p_cfr(tmp_path):
    src = make_media(tmp_path / "src.mp4", seconds=3, size="1920x1080", fps=60)
    dst = tmp_path / "proxy.mp4"
    seen = []
    info = ingest.make_proxy(src, dst, width=1920, height=1080, fps=60.0, duration=3.0, has_audio=True,
                             encoder="libx264", on_progress=seen.append)
    assert dst.exists() and not (tmp_path / "proxy.mp4.tmp").exists()
    assert info == {"width": 1280, "height": 720, "fps": 30.0, "encoder": "libx264"}
    probe = _probe(dst)
    assert (probe.video.width, probe.video.height) == (1280, 720)
    assert abs(probe.video.fps - 30) < 0.01 and not probe.is_vfr
    assert probe.audio is not None
    assert abs(probe.duration - 3.0) < 0.2
    assert seen and seen[-1] == 1.0


@needs_ffmpeg
def test_make_proxy_falls_back_to_libx264(tmp_path):
    src = make_media(tmp_path / "src.mp4", seconds=1, size="640x360")
    dst = tmp_path / "proxy.mp4"
    info = ingest.make_proxy(src, dst, width=640, height=360, fps=30.0, duration=1.0, has_audio=True,
                             encoder="definitely_not_an_encoder")
    assert info["encoder"] == "libx264" and dst.exists()


@needs_ffmpeg
def test_make_thumbnail_and_extract_audio(tmp_path):
    src = make_media(tmp_path / "src.mp4", seconds=2)
    thumb = tmp_path / "t.jpg"
    assert ingest.make_thumbnail(src, thumb, at=1.0)
    assert thumb.stat().st_size > 0
    wav = tmp_path / "a.wav"
    ingest.extract_audio(src, wav, duration=2.0)
    import wave

    with wave.open(str(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert abs(w.getnframes() / 16000 - 2.0) < 0.1


@needs_ffmpeg
def test_make_thumbnail_failure_returns_false(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert ingest.make_thumbnail(bad, tmp_path / "t.jpg", at=0) is False
