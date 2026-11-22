import random

import pytest

from pipeline import snap


def w(start, end, text):
    return {"start": start, "end": end, "text": text}


# "Hello there. This is a test. Final words!"  with gaps between sentences
WORDS = [
    w(1.00, 1.40, "Hello"),
    w(1.45, 1.90, "there."),
    w(2.60, 2.80, "This"),
    w(2.85, 3.00, "is"),
    w(3.05, 3.10, "a"),
    w(3.15, 3.60, "test."),
    w(4.40, 4.80, "Final"),
    w(4.85, 5.40, "words!"),
]


def inside_any_word(t, words):
    return any(x["start"] < t < x["end"] for x in words)


def test_sentence_indices():
    assert snap.sentence_start_indices(WORDS) == {0, 2, 6}
    assert snap.sentence_end_indices(WORDS) == {1, 5, 7}
    assert snap.is_sentence_end("xong rồi.") and snap.is_sentence_end("thật sao?”")
    assert not snap.is_sentence_end("và")


def test_start_prefers_sentence_start_over_nearer_word():
    # 2.95 is closest to "is" (2.85) but a sentence starts at 2.60 within the window
    s = snap.snap_start(2.95, WORDS)
    assert s == pytest.approx(2.60 - snap.PAD)


def test_end_prefers_sentence_end():
    e = snap.snap_end(3.2, WORDS)  # inside "test." region; sentence ends at 3.60
    assert e == pytest.approx(3.60 + snap.PAD)


def test_padding_never_reaches_into_neighbour_word():
    words = [w(0.0, 1.0, "One."), w(1.1, 2.0, "Two.")]  # gap 0.1 < 2 * PAD
    s = snap.snap_start(1.1, words)
    assert s == pytest.approx(1.05)  # middle of the gap
    e = snap.snap_end(1.0, words)
    assert e == pytest.approx(1.05)


def test_silence_refines_onset():
    # Whisper says "This" starts at 2.60 but the silence detector says speech starts at 2.45
    s = snap.snap_start(2.6, WORDS, silences=[[1.95, 2.45]])
    assert s == pytest.approx(2.45 - snap.PAD)
    e = snap.snap_end(1.9, WORDS, silences=[[2.05, 2.5]])  # speech energy lasts until 2.05
    assert e == pytest.approx(2.05 + snap.PAD)


def test_long_pause_keeps_time():
    words = [w(0, 1, "a."), w(10, 11, "b.")]
    assert snap.snap_start(5.0, words) == 5.0
    assert snap.snap_end(5.0, words) == 5.0


def test_snap_moment_clamps_and_rounds():
    s, e = snap.snap_moment(-3, 99, WORDS, duration=6.0)
    assert s == 0.0 and e == 6.0
    s, e = snap.snap_moment(1.2, 5.0, WORDS, duration=6.0)
    assert (s, e) == (pytest.approx(0.85), pytest.approx(5.55))


def test_snap_moment_short_selection_stays_word_safe():
    s, e = snap.snap_moment(3.06, 3.08, WORDS)
    assert e - s >= 0.05
    assert not inside_any_word(s, WORDS) and not inside_any_word(e, WORDS)


def random_words(rng, n=400):
    words, t = [], 0.3
    for i in range(n):
        t += rng.choice([0.0, 0.02, 0.05, 0.1, 0.3, 0.8])
        length = rng.uniform(0.08, 0.7)
        text = "word" + (rng.choice([".", "?", "!"]) if rng.random() < 0.12 else "")
        words.append(w(round(t, 3), round(t + length, 3), text))
        t += length
    return words, t + 1


@pytest.mark.parametrize("seed", range(5))
def test_zero_cuts_inside_words(seed):
    """Acceptance criterion: no in/out point inside a word, for any requested range."""
    rng = random.Random(seed)
    words, duration = random_words(rng)
    silences = [[x["end"], nxt["start"]] for x, nxt in zip(words, words[1:]) if nxt["start"] - x["end"] >= 0.3]
    for _ in range(300):
        a = rng.uniform(0, duration)
        b = a + rng.uniform(0.2, 60)
        s, e = snap.snap_moment(a, b, words, silences, duration=duration)
        assert 0 <= s < e <= duration + 1e-9
        assert not inside_any_word(s, words), (a, b, s)
        assert not inside_any_word(e, words), (a, b, e)


def test_dedupe_keeps_best_of_overlapping():
    moments = [
        {"start": 0, "end": 30, "score": 70},
        {"start": 2, "end": 31, "score": 80},  # duplicate of the first, better score
        {"start": 25, "end": 50, "score": 60},  # overlaps by 5-6 s only: kept
    ]
    kept = snap.dedupe(moments)
    assert [m["score"] for m in kept] == [80, 60]
