"""Background music: EDL field, mix plan (loop, offset, fades, ducking), the library API, and a real
ffmpeg mix."""

import subprocess
from pathlib import Path

import numpy as np
import pytest

from app import edl as edl_mod, errors
from pipeline import audio_mix
from tests.conftest import needs_ffmpeg
from tests.test_api_sources import client  # noqa: F401 - fixture

TRACK = "0123456789abcdef0123456789abcdef"


def _edl(music=None, voiceover=None):
    return {"segments": [{"start": 10.0, "end": 14.0}, {"start": 20.0, "end": 26.0}],  # 10 s clip
            "audio": {"normalize_lufs": -14.0, "volume_db": 0.0},
            "voiceover": voiceover or {"enabled": False},
            "music": {"enabled": True, "track_id": TRACK, "volume_db": -20, "start": 1.0, "offset": 3.0,
                      "fade_in": 0.5, "fade_out": 2.0, "loop": True, "duck_db": -10, **(music or {})}}


def test_music_edl_defaults_and_validation():
    data = edl_mod.build_default(clip_id="c", source_id="s", moment={"id": 1, "start": 0, "end": 5}, mode="auto")
    assert data["music"] == {"enabled": False, "track_id": None, "name": "", "duration": None, "volume_db": -20.0,
                             "start": 0.0, "offset": 0.0, "fade_in": 0.5, "fade_out": 1.5, "loop": True, "duck_db": -10.0}
    ok = edl_mod.validate({**data, "music": {"enabled": True, "track_id": TRACK, "volume_db": -12}})
    assert ok["music"]["track_id"] == TRACK and ok["music"]["volume_db"] == -12
    for bad in ({"track_id": "../../secret"}, {"volume_db": 20}, {"fade_in": -1}):
        with pytest.raises(errors.AppError):
            edl_mod.validate({**data, "music": bad})


def test_music_window_loop_and_no_loop():
    assert audio_mix.music_window(_edl(), 30.0) == (1.0, 10.0)  # loops to the end of the clip
    # no loop: stops when the track runs out (30 s track from 3 s -> 27 s, longer than the clip)
    assert audio_mix.music_window(_edl({"loop": False}), 30.0) == (1.0, 10.0)
    assert audio_mix.music_window(_edl({"loop": False}), 5.0) == (1.0, 3.0)  # 2 s left after the offset
    assert audio_mix.music_window(_edl({"enabled": False}), 30.0) is None
    assert audio_mix.music_window(_edl({"start": 10.0}), 30.0) is None
    assert audio_mix.music_window(_edl(), None) is None


def test_music_plan_loops_from_the_offset_with_fades():
    plan = audio_mix.build_plan(_edl(), media=Path("m.mp4"), media_offset=8.0, music=(Path("song.mp3"), 30.0))
    assert plan.inputs[-1] == ["-stream_loop", "-1", "-i", "song.mp3"] and plan.music == (1.0, 10.0)
    fc = plan.filter_complex
    assert "atrim=start=3:duration=9,asetpts=PTS-STARTPTS,volume=-20dB" in fc
    assert "afade=t=in:st=0:d=0.5" in fc and "afade=t=out:st=7:d=2" in fc and "adelay=1000:all=1[mus]" in fc
    assert "[base][mus]amix=inputs=2:duration=first:normalize=0" in fc
    assert fc.endswith("[mixed]apad=whole_dur=10,atrim=duration=10,asetpts=PTS-STARTPTS[aout]")
    # without loop: no -stream_loop; include_music=False drops it (CapCut original audio)
    once = audio_mix.build_plan(_edl({"loop": False}), media=Path("m.mp4"), music=(Path("song.mp3"), 30.0))
    assert once.inputs[-1] == ["-i", "song.mp3"]
    off = audio_mix.build_plan(_edl(), media=Path("m.mp4"), music=(Path("song.mp3"), 30.0), include_music=False)
    assert off.music is None and "amix" not in off.filter_complex


def test_music_is_ducked_under_the_voiceover():
    vo = {"enabled": True, "audio_path": "voice_cache/x.mp3", "start": 2.0, "duck_db": -12.0, "volume_db": 0}
    plan = audio_mix.build_plan(_edl(voiceover=vo), media=Path("m.mp4"), voiceover=Path("vo.mp3"), vo_duration=3.0,
                                music=(Path("song.mp3"), 30.0))
    fc = plan.filter_complex
    assert "[base][vo][mus]amix=inputs=3" in fc
    # music: duck_db -10 -> x0.31623 while the voiceover plays (clip time 2..5)
    assert "adelay=1000:all=1,volume='if(between(t,2,5),0.31623,1)':eval=frame[mus]" in fc


def test_short_clip_fades_are_capped():
    edl = _edl({"start": 9.0, "fade_in": 5, "fade_out": 5})  # 1 s of music
    fc = audio_mix.build_plan(edl, media=None, music=(Path("song.mp3"), 30.0)).filter_complex
    assert "afade=t=in:st=0:d=0.5" in fc and "afade=t=out:st=0.5:d=0.5" in fc


def _tone(path: Path, freq: int, seconds: float) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ac", "2", str(path)], check=True, capture_output=True)
    return path


def _rms(path: Path, t0: float, t1: float) -> float:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ss", str(t0), "-t", str(t1 - t0), "-f", "f32le",
                          "-ac", "1", "-ar", "48000", "-"], check=True, capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.float32)
    return float(np.sqrt(np.mean(a**2))) if a.size else 0.0


@needs_ffmpeg
def test_real_mix_loops_the_track_and_stays_silent_before_start(tmp_path):
    song = _tone(tmp_path / "song.wav", 440, 2.0)  # 2 s track, looped over a 10 s clip
    edl = _edl({"start": 1.0, "offset": 0.0, "fade_in": 0, "fade_out": 0, "volume_db": 0})
    plan = audio_mix.build_plan(edl, media=None, music=(song, 2.0))
    out = audio_mix.render_mix(plan, tmp_path / "mix.wav")
    assert _rms(out, 0.1, 0.9) < 1e-4  # before music.start
    for t in (1.5, 4.5, 8.5):  # still playing after several loops
        assert _rms(out, t, t + 0.3) > 0.05  # ffmpeg's sine source: amplitude 1/8 -> RMS ~0.088


@needs_ffmpeg
def test_music_library_api(client, tmp_path):  # noqa: F811
    song = _tone(tmp_path / "Lofi beat.mp3", 440, 1.5)
    r = client.post("/music/upload", params={"name": "Lofi beat.mp3"}, content=song.read_bytes())
    assert r.status_code == 200, r.text
    track = r.json()
    assert track["name"] == "Lofi beat" and 1.4 < track["duration"] < 1.7 and len(track["id"]) == 32
    # same content again -> same track
    again = client.post("/music/upload", params={"name": "copy.mp3"}, content=song.read_bytes()).json()
    assert again["id"] == track["id"]
    assert [t["id"] for t in client.get("/music").json()] == [track["id"]]
    assert client.get(track["url"]).content == song.read_bytes()

    from app import music_service
    path, duration = music_service.resolve({"enabled": True, "track_id": track["id"]})
    assert path.exists() and duration == track["duration"]
    assert music_service.resolve({"enabled": False, "track_id": track["id"]}) is None

    bad = client.post("/music/upload", params={"name": "notes.txt"}, content=b"hello")
    assert bad.status_code == 415 and bad.json()["code"] == "UNSUPPORTED_FILE_TYPE"
    fake = client.post("/music/upload", params={"name": "fake.mp3"}, content=b"not audio at all")
    assert fake.status_code == 415

    assert client.delete(f"/music/{track['id']}").status_code == 200
    assert client.get("/music").json() == []
    assert client.get(track["url"]).status_code == 404
    assert music_service.resolve({"enabled": True, "track_id": track["id"]}) is None
