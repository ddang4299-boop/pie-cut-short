import wave

import numpy as np
import pytest

from pipeline import transcribe


def test_cache_key_depends_on_inputs():
    base = transcribe.cache_key("h1", "large-v3", "auto")
    assert base == transcribe.cache_key("h1", "large-v3", "auto")
    assert base != transcribe.cache_key("h2", "large-v3", "auto")
    assert base != transcribe.cache_key("h1", "small", "auto")
    assert base != transcribe.cache_key("h1", "large-v3", "vi")


def test_whisper_language_args():
    assert transcribe.whisper_language_args("auto") == {"language": None}
    assert transcribe.whisper_language_args("vi") == {"language": "vi"}
    assert transcribe.whisper_language_args("en") == {"language": "en"}
    assert transcribe.whisper_language_args("mixed") == {"language": None, "multilingual": True}
    with pytest.raises(ValueError):
        transcribe.whisper_language_args("fr")


def test_normalize_words_strips_drops_and_orders():
    raw = [
        {"start": 0.0, "end": 0.4, "text": " Xin", "prob": 0.91234},
        {"start": 0.4, "end": 0.4, "text": "  "},  # empty -> dropped
        {"start": 0.35, "end": 0.7, "text": " chào"},  # overlaps -> pushed to previous end
        {"start": 0.9, "end": 0.8, "text": "các"},  # end < start -> end = start
        {"start": 9.0, "end": 12.0, "text": "bạn."},  # clamped to duration
    ]
    words = transcribe.normalize_words(raw, duration=10.0)
    assert [w["text"] for w in words] == ["Xin", "chào", "các", "bạn."]
    assert words[0]["prob"] == 0.912
    assert words[1]["start"] == 0.4
    assert words[2]["start"] == words[2]["end"] == 0.9
    assert words[3]["end"] == 10.0
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)
    assert all(w["end"] >= w["start"] for w in words)


def test_build_transcript_segments_and_monotonic_across_segments():
    segments = [
        {"start": 0, "end": 1.0, "text": " Hello there.", "words": [
            {"start": 0.0, "end": 0.5, "text": " Hello"}, {"start": 0.5, "end": 1.0, "text": " there."}]},
        {"start": 1.0, "end": 1.1, "text": " ", "words": []},  # no words -> dropped
        {"start": 0.9, "end": 2.0, "text": " Xin chào.", "words": [
            {"start": 0.95, "end": 1.5, "text": " Xin"}, {"start": 1.5, "end": 2.0, "text": " chào."}]},
    ]
    t = transcribe.build_transcript(segments, language="en", language_option="mixed", language_probability=0.5,
                                    model="tiny", device="cpu", duration=2.0)
    assert t["version"] == transcribe.TRANSCRIPT_VERSION
    assert t["language"] == "mixed" and t["language_option"] == "mixed"
    assert [w["text"] for w in t["words"]] == ["Hello", "there.", "Xin", "chào."]
    assert t["words"][2]["start"] == 1.0  # pushed after the previous segment's last word
    assert len(t["segments"]) == 2
    s1 = t["segments"][1]
    assert (s1["first_word"], s1["last_word"]) == (2, 3)
    assert s1["text"] == "Xin chào."
    assert s1["start"] == 1.0 and s1["end"] == 2.0


def test_build_transcript_keeps_detected_language():
    t = transcribe.build_transcript([], language="vi", language_option="auto", language_probability=0.987654,
                                    model="tiny", device="cuda", duration=0)
    assert t["language"] == "vi" and t["language_probability"] == 0.988
    assert t["words"] == [] and t["segments"] == []


def test_load_audio_reads_16k_mono_wav(tmp_path):
    path = tmp_path / "a.wav"
    samples = (np.sin(np.linspace(0, 100, 16000)) * 16000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(samples.tobytes())
    audio = transcribe.load_audio(path)
    assert audio.dtype == np.float32 and audio.shape == (16000,)
    assert np.allclose(audio, samples / 32768.0)
