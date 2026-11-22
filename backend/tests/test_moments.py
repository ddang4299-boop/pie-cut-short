"""Moment proposals: sentences, request file format (PROMPTS.md A.1), moments.json parsing (A.3),
scoring, finalize (snap + dedupe), the heuristic provider, and the Anthropic provider (mocked)."""

import json
from types import SimpleNamespace

import pytest

from pipeline import moments as mp


def w(start, end, text):
    return {"start": start, "end": end, "text": text}


def sentence_words(n_sentences=12, words_per=8, word_len=0.4, gap=0.6, text="word"):
    """n sentences of `words_per` words each, the last word ends with a period."""
    out, t = [], 0.0
    for s in range(n_sentences):
        for k in range(words_per):
            last = k == words_per - 1
            token = f"{text}{s}" + ("." if last else "")
            if k == 0 and s % 3 == 0:
                token = "Why" if not last else token
            out.append(w(round(t, 3), round(t + word_len, 3), token))
            t += word_len + 0.05
        t += gap
    return out


def meta(**kw):
    base = dict(source_id="src_1", file_name="talk.mp4", duration=2537.5, language="vi", ui_language="en",
                content_type="talking_head", target_platform="tiktok", user_notes="focus on tips\nno intro")
    base.update(kw)
    return mp.RequestMeta(**base)


# --------------------------------------------------------------------------
def test_build_sentences_splits_on_punctuation_and_pauses():
    words = [w(0, 0.4, "Hello"), w(0.5, 0.9, "world."), w(1.0, 1.3, "Next"), w(1.4, 1.7, "one"),
             w(3.0, 3.3, "after"), w(3.4, 3.8, "pause")]
    s = mp.build_sentences(words)
    assert [x["text"] for x in s] == ["Hello world.", "Next one", "after pause"]
    assert (s[1]["first"], s[1]["last"]) == (2, 3) and s[2]["start"] == 3.0


def test_build_sentences_breaks_long_unpunctuated_runs():
    words = [w(i * 0.5, i * 0.5 + 0.45, "x") for i in range(120)]  # 60 s, no punctuation, no pauses
    s = mp.build_sentences(words)
    assert len(s) >= 3
    assert all(x["end"] - x["start"] <= 25.5 for x in s)


def test_fmt_hms():
    assert mp.fmt_hms(2537.5) == "00:42:17.50"
    assert mp.fmt_hms(3725.257) == "01:02:05.26"


def test_request_matches_a1_format():
    words = sentence_words(3)
    sentences = mp.build_sentences(words)
    analysis = {"peaks": [[123.4, 0.92], [456.7, 0.88]], "scene_cuts": [12.0, 45.3],
                "silences": [[1.0, 1.2], [3.5, 4.1]]}
    (text,) = mp.build_requests(meta(), sentences, analysis)
    lines = text.splitlines()
    assert lines[:11] == [
        "# Analysis request: src_1",
        "- file: talk.mp4",
        "- duration: 00:42:17.50 (2537.5s)",
        "- language: vi",
        "- ui_language: en",
        "- content_type: talking_head",
        "- target_platform: tiktok",
        "- clip_length: min 15s, max 60s",
        "- wanted: 8-12 moments",
        "- user_notes: focus on tips / no intro",
        "- part: 1/1",
    ]
    assert "## Audio peaks (top 30, seconds:relative loudness 0-1)" in lines
    assert "123.4:0.92, 456.7:0.88" in lines
    assert "12.0, 45.3" in lines
    assert "3.5-4.1" in text and "1.0-1.2" not in text  # silences < 0.4 s are left out
    first = sentences[0]
    assert f"[{first['start']:.2f}-{first['end']:.2f}] {first['text']}" in lines


def test_empty_analysis_prints_none():
    (text,) = mp.build_requests(meta(), [], {})
    assert text.count("(none)") == 3 and "(no speech)" in text


def test_long_transcripts_are_split_into_overlapping_parts(tmp_path):
    words = sentence_words(60, words_per=10)
    sentences = mp.build_sentences(words)
    analysis = {"loudness": [-30.0] * 400, "peaks": [], "scene_cuts": [], "silences": []}
    texts = mp.build_requests(meta(duration=400), sentences, analysis, max_words=200, overlap=10)
    assert len(texts) >= 3
    assert all(f"- part: {k}/{len(texts)}" in t for k, t in enumerate(texts, 1))
    # every sentence is in at least one part; consecutive parts share at least one sentence
    for s in sentences:
        assert any(f"[{s['start']:.2f}-" in t for t in texts)
    a, b = texts[0].splitlines()[-1], texts[1]
    assert a in b

    paths = mp.write_request_files(tmp_path, meta(duration=400), texts)
    assert paths[0].name == mp.REQUEST_FILE and len(paths) == len(texts) + 1
    index = paths[0].read_text("utf-8")
    assert "analysis_request.part1.md" in index and "- part: split into" in index
    # a later short request removes the stale part files
    mp.write_request_files(tmp_path, meta(), texts[:1])
    assert not list(tmp_path.glob("analysis_request.part*.md"))


# --------------------------------------------------------------------------
SCORES = {"hook_strength": 8, "coherence": 7, "emotion": 6, "value_density": 5, "payoff": 9}


def moments_doc(**overrides):
    doc = {"source_id": "src_1", "summary": "A talk.", "notes": "",
           "moments": [{"start": 10.0, "end": 40.0, "title": "Tiêu đề", "hook": "Hook", "reason": "Because",
                        "category": "tip", "scores": dict(SCORES), "score": 99, "key_line": [12.0, 15.0]}]}
    doc.update(overrides)
    return doc


def test_parse_moments_accepts_fences_and_normalizes():
    doc = moments_doc()
    doc["moments"][0]["category"] = "Funny|reaction"
    doc["moments"][0]["key_line"] = "bad"
    doc["notes"] = ["a", "b"]
    parsed = mp.parse_moments("Here you go:\n```json\n" + json.dumps(doc, ensure_ascii=False) + "\n```",
                              source_id="src_1", duration=100)
    m = parsed.moments[0]
    assert m.category == "funny" and m.key_line is None and parsed.notes == "a\nb" and m.title == "Tiêu đề"


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(source_id="other"), "source_id"),
    (lambda d: d["moments"][0].update(end=5.0), "end"),
    (lambda d: d["moments"][0]["scores"].update(emotion=11), "emotion"),
    (lambda d: d["moments"][0].update(start=500.0, end=520.0), "past the end"),
    (lambda d: d.pop("moments"), "moments"),
])
def test_parse_moments_rejects_bad_documents(mutate, message):
    doc = moments_doc()
    mutate(doc)
    with pytest.raises(mp.MomentsInvalid, match=message):
        mp.parse_moments(json.dumps(doc), source_id="src_1", duration=100)


def test_parse_moments_rejects_non_json():
    with pytest.raises(mp.MomentsInvalid, match="not valid JSON"):
        mp.parse_moments("{nope", source_id="src_1")
    with pytest.raises(mp.MomentsInvalid, match="object"):
        mp.parse_moments("[1, 2]")


def test_compute_score_uses_normalized_weights():
    assert mp.compute_score(SCORES) == round((8 * .3 + 7 * .2 + 6 * .2 + 5 * .15 + 9 * .15) * 10)
    doubled = {k: v * 2 for k, v in mp.DEFAULT_WEIGHTS.items()}
    assert mp.compute_score(SCORES, doubled) == mp.compute_score(SCORES)
    only_hook = {k: (1.0 if k == "hook_strength" else 0.0) for k in mp.RUBRIC}
    assert mp.compute_score(SCORES, only_hook) == 80
    assert mp.compute_score({k: 10 for k in mp.RUBRIC}) == 100


def test_finalize_snaps_rescores_and_dedupes():
    words = sentence_words(12)
    doc = moments_doc()
    doc["moments"] = [
        {"start": 3.1, "end": 20.2, "scores": SCORES, "score": 1},
        {"start": 3.3, "end": 20.0, "scores": {**SCORES, "hook_strength": 2}},  # duplicate, lower score
        {"start": 25.0, "end": 25.5, "scores": SCORES},  # too short after clamping -> dropped
        {"start": 30.0, "end": 900.0, "scores": {k: 3 for k in mp.RUBRIC}},  # end clamped to duration
    ]
    parsed = mp.parse_moments(json.dumps(doc))
    duration = words[-1]["end"] + 1
    out = mp.finalize(parsed, words=words, silences=[], duration=duration)
    assert len(out) == 2
    assert out[0]["score"] == mp.compute_score(SCORES) and out[0]["raw_start"] == 3.1
    assert out[1]["end"] <= duration
    for m in out:  # never inside a word
        for word in words:
            assert not (word["start"] < m["start"] < word["end"])
            assert not (word["start"] < m["end"] < word["end"])


# --------------------------------------------------------------------------
def propose_input(words, **meta_kw):
    sentences = mp.build_sentences(words)
    duration = words[-1]["end"] + 1
    analysis = {"loudness": [-30.0] * int(duration + 1), "peaks": [], "scene_cuts": [], "silences": []}
    m = meta(duration=duration, **meta_kw)
    return mp.ProposeInput(meta=m, texts=mp.build_requests(m, sentences, analysis), sentences=sentences,
                           analysis=analysis)


def test_heuristic_proposes_valid_non_overlapping_moments():
    words = sentence_words(80)
    inp = propose_input(words)
    result = mp.make_provider("heuristic").propose(inp)
    assert 8 <= len(result.moments) <= 12
    spans = sorted((m.start, m.end) for m in result.moments)
    for m in result.moments:
        assert 15 <= m.end - m.start <= 50 and m.title and m.reason
        assert m.category in mp.CATEGORIES
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        assert max(0.0, min(a1, b1) - max(a0, b0)) <= 0.3 * min(a1 - a0, b1 - b0) + 1e-6
    # survives a round trip through the moments.json schema
    mp.parse_moments(result.model_dump_json(), source_id="src_1", duration=inp.meta.duration)


def test_heuristic_reason_follows_ui_language():
    words = sentence_words(40)
    en = mp.heuristic_propose(propose_input(words, ui_language="en"))
    vi = mp.heuristic_propose(propose_input(words, ui_language="vi"))
    assert en.moments[0].reason != vi.moments[0].reason
    assert en.summary != vi.summary


def test_heuristic_short_video_reports_fewer():
    words = sentence_words(4)  # ~16 s of speech
    result = mp.heuristic_propose(propose_input(words))
    assert len(result.moments) < 8 and result.notes


# --------------------------------------------------------------------------
class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text, stop = self.replies.pop(0)
        return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)])


def anthropic_with(replies):
    pytest.importorskip("anthropic")
    provider = mp.AnthropicProvider("sk-test")
    fake = FakeMessages(replies)
    provider.client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    return provider, fake


def test_anthropic_provider_retries_with_the_validation_error():
    words = sentence_words(12)
    inp = propose_input(words)
    good = moments_doc()
    good["moments"][0].update(start=1.0, end=20.0)
    provider, fake = anthropic_with([("not json", "end_turn"), (json.dumps(good), "end_turn")])
    result = provider.propose(inp)
    assert len(result.moments) == 1 and len(fake.calls) == 2
    first = fake.calls[0]
    assert first["model"] == mp.ANTHROPIC_MODEL
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert "src_1" in first["system"] and first["messages"][0]["content"].startswith("# Analysis request: src_1")
    retry = fake.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"] and "schema" in retry[-1]["content"]


def test_anthropic_provider_surfaces_refusal_and_truncation():
    inp = propose_input(sentence_words(12))
    provider, _ = anthropic_with([("", "refusal")])
    with pytest.raises(mp.ProviderError, match="declined"):
        provider.propose(inp)
    provider, _ = anthropic_with([("{", "max_tokens")])
    with pytest.raises(mp.ProviderError, match="cut off"):
        provider.propose(inp)


def test_providers_need_configuration():
    for name, kwargs in (("anthropic", {}), ("openai", {}), ("ollama", {"ollama_host": ""}), ("claude_code", {})):
        with pytest.raises(mp.ProviderNotConfigured):
            mp.make_provider(name, **kwargs)


def test_merge_parts_keeps_best_non_overlapping():
    m = meta(wanted=(1, 2))
    a = mp.MomentsFile(source_id="src_1", summary="one", moments=[
        mp.MomentIn(start=0, end=30, scores=mp.Scores(**SCORES)),
        mp.MomentIn(start=100, end=130, scores=mp.Scores(**{k: 2 for k in mp.RUBRIC}))])
    b = mp.MomentsFile(source_id="src_1", summary="two", moments=[
        mp.MomentIn(start=5, end=32, scores=mp.Scores(**{k: 9 for k in mp.RUBRIC})),
        mp.MomentIn(start=200, end=230, scores=mp.Scores(**{k: 5 for k in mp.RUBRIC}))])
    merged = mp.merge_parts([a, b], m, mp.DEFAULT_WEIGHTS)
    assert [(x.start, x.end) for x in merged.moments] == [(5, 32), (200, 230)]
    assert merged.summary == "one\ntwo"
