"""Pure helpers of analyze.py and content_type.py (no ffmpeg / OpenCV needed)."""

import numpy as np

from pipeline import analyze, content_type


def test_parse_ebur128_handles_inf_and_noise():
    lines = [
        "[Parsed_ebur128_0 @ 0x1] t: 0.1      TARGET:-23 LUFS    M: -inf S: -inf     I: -70.0 LUFS",
        "random line",
        "[Parsed_ebur128_0 @ 0x1] t: 0.5      TARGET:-23 LUFS    M: -21.4 S: -22.0   I: -21.4 LUFS",
        "[Parsed_ebur128_0 @ 0x1] t: 1.3      TARGET:-23 LUFS    M: -80.0 S: -22.0   I: -21.4 LUFS",
    ]
    assert analyze.parse_ebur128(lines) == [(0.1, -70.0), (0.5, -21.4), (1.3, -70.0)]


def test_per_second_takes_the_max_and_shifts_by_the_window():
    frames = [(0.5, -30.0), (0.9, -20.0), (1.1, -25.0), (2.6, -10.0)]
    # 1.1 - 0.2 = 0.9 -> second 0; 2.6 -> second 2
    assert analyze.per_second(frames, 3.0) == [-20.0, -70.0, -10.0]


def test_relative_and_peaks():
    values = [-40.0] * 20
    values[3], values[5], values[15] = -10.0, -12.0, -20.0
    rel = analyze.relative(values)
    assert rel[3] == 1.0 and rel[0] == 0.0 and 0 < rel[15] < 1
    peaks = analyze.find_peaks(rel, top=5, spacing=5)
    # second 5 is too close to 3 (louder), so it is dropped
    assert [t for t, _ in peaks] == [3.5, 15.5]
    assert analyze.relative([-30.0, -30.0]) == [0.0, 0.0]


def test_parse_silencedetect_closes_a_trailing_silence():
    lines = [
        "[silencedetect @ 0x1] silence_start: 1.5",
        "[silencedetect @ 0x1] silence_end: 2.25 | silence_duration: 0.75",
        "[silencedetect @ 0x1] silence_start: -0.01",
        "[silencedetect @ 0x1] silence_end: 0.6 | silence_duration: 0.6",
        "[silencedetect @ 0x1] silence_start: 9.2",
    ]
    assert analyze.parse_silencedetect(lines, 10.0) == [(1.5, 2.25), (0.0, 0.6), (9.2, 10.0)]


def test_silence_threshold_is_clamped():
    quiet_room = np.array([-80.0] * 50 + [-60.0] * 50)
    assert analyze.silence_threshold(quiet_room) == -55.0
    loud_mix = np.array([-12.0] * 100)
    assert analyze.silence_threshold(loud_mix) == -28.0
    normal = np.array([-60.0] * 30 + [-20.0] * 70)
    assert -55.0 < analyze.silence_threshold(normal) < -28.0
    assert analyze.silence_threshold(np.array([-100.0])) == -50.0


def test_waveform_peaks_scale_to_100():
    sr = 1000
    samples = np.zeros(sr, dtype=np.float32)
    samples[10] = 0.5
    samples[600] = -1.0
    peaks = analyze.waveform_peaks(samples, sr, rate=10)
    assert len(peaks) == 10 and peaks[0] == 50 and peaks[6] == 100 and peaks[3] == 0
    assert analyze.waveform_peaks(np.zeros(5, dtype=np.float32), sr, rate=10) == []


def test_speech_ratio_merges_short_gaps():
    words = [{"start": 0.0, "end": 1.0}, {"start": 1.1, "end": 2.0}, {"start": 5.0, "end": 6.0}]
    assert content_type.speech_ratio(words, 10.0) == 0.3
    assert content_type.speech_ratio([], 10.0) == 0.0


def test_classify_rules():
    assert content_type.classify({"face_ratio": 0.9, "face_area": 0.08, "speech_ratio": 0.8})[0] == "talking_head"
    assert content_type.classify({"face_ratio": 0.9, "multi_face_ratio": 0.6, "face_area": 0.03,
                                  "speech_ratio": 0.7})[0] == "podcast"
    assert content_type.classify({"face_ratio": 0.6, "face_area": 0.005, "motion": 0.08,
                                  "static_ratio": 0.5})[0] == "gameplay_facecam"
    assert content_type.classify({"face_ratio": 0.0, "motion": 0.005, "static_ratio": 0.97})[0] == "screen_recording"
    kind, confidence = content_type.classify({})
    assert kind in content_type_types() and 0 < confidence <= 1


def content_type_types():
    return {"talking_head", "screen_recording", "podcast", "gameplay_facecam"}
