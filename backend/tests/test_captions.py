"""Caption paging, SRT/ASS output, transcript words, and the clip captions API.

The paging vectors are shared with the TypeScript mirror (remotion/src/lib/captions.ts):
regenerate with `python -m tests.test_captions` after changing pipeline/captions.py.
"""

import json
from pathlib import Path

import pytest

from app import clips_service
from pipeline import captions as cap
from tests.test_api_clips import ready_source  # noqa: F401 - fixture
from tests.test_api_moments import doc, waiting_source, write_result  # noqa: F401 - fixtures
from tests.test_api_sources import client, fake_transcriber, run_queue  # noqa: F401 - fixtures

VECTORS = Path(__file__).resolve().parents[2] / "frontend/src/lib/captions.vectors.json"


def w(start, end, text, emphasis=False):
    return {"start": start, "end": end, "text": text, "emphasis": emphasis}


WORDS = [
    w(10.0, 10.3, "Xin"), w(10.3, 10.6, "chào"), w(10.6, 11.0, "các"), w(11.0, 11.4, "bạn."),
    w(11.5, 11.8, "Hôm"), w(11.8, 12.0, "nay"), w(13.0, 13.4, "đặc", True), w(13.4, 13.9, "biệt!"),
    w(14.0, 14.2, " "), w(14.2, 14.5, "Really"), w(14.5, 14.9, "fast"), w(14.9, 15.3, "words"),
    w(15.3, 15.6, "here"), w(15.6, 16.0, "now?"), w(16.1, 16.4, "End"),
]
CASES = [
    {"segments": [{"start": 10.0, "end": 17.0}], "wpp": 3},
    {"segments": [{"start": 10.0, "end": 17.0}], "wpp": 1},
    {"segments": [{"start": 10.0, "end": 17.0}], "wpp": 4},
    {"segments": [{"start": 10.0, "end": 11.5}, {"start": 13.2, "end": 15.45}], "wpp": 3},  # jump cut, partial word
    {"segments": [{"start": 9.0, "end": 10.2}, {"start": 14.6, "end": 16.2}], "wpp": 2},
]


def make_vectors() -> dict:
    return {"words": WORDS, "cases": [{**c, "pages": cap.build_pages(WORDS, c["segments"], c["wpp"])} for c in CASES]}


def test_shared_vectors_are_current():
    stored = json.loads(VECTORS.read_text("utf-8"))
    fresh = json.loads(json.dumps(make_vectors(), ensure_ascii=False))
    assert stored == fresh, "run `python -m tests.test_captions` to regenerate the shared vectors"


def test_paging_rules():
    segs = [{"start": 10.0, "end": 17.0}]
    pages = cap.build_pages(WORDS, segs, 3)
    texts = [cap.page_text(p) for p in pages]
    # sentence end closes a page ("bạn."), the long pause before "đặc" too; empty words are skipped
    assert texts[:4] == ["Xin chào các", "bạn.", "Hôm nay", "đặc biệt!"]
    assert texts[4:] == ["Really fast words", "here now?", "End"]
    assert pages[0]["start"] == pytest.approx(0.0) and pages[0]["end"] == pytest.approx(1.0)
    # shown until the next page starts, at most HOLD after the last word
    assert pages[1]["show_end"] == pytest.approx(1.5)  # next page starts at 1.5 < 1.4 + 0.4
    assert pages[2]["show_end"] == pytest.approx(2.0 + cap.HOLD)
    assert pages[-1]["show_end"] == pytest.approx(min(7.0, 6.4 + cap.HOLD))
    assert pages[3]["words"][0]["emphasis"] is True
    for p in pages:
        assert p["start"] <= p["end"] <= p["show_end"]
    for a, b in zip(pages, pages[1:]):
        assert a["show_end"] <= b["start"] + 1e-9


def test_jump_cuts_map_words_to_clip_time():
    segs = [{"start": 10.0, "end": 11.5}, {"start": 13.2, "end": 15.45}]
    pages = cap.build_pages(WORDS, segs, 4)
    # "Hôm" (mid 11.65) and "nay" fall in the removed gap; "đặc" (mid 13.2) starts segment 2
    words = [x["text"] for p in pages for x in p["words"]]
    assert "Hôm" not in words and "nay" not in words and words[4] == "đặc"
    first2 = next(p for p in pages if p["words"][0]["text"] == "đặc")
    assert first2["start"] == pytest.approx(1.5)  # clipped to the segment start
    assert all(len({x["seg"] for x in p["words"]}) == 1 for p in pages)  # never across a cut
    # "here" (15.3-15.6, mid 15.45) is outside [13.2, 15.45)
    assert "here" not in words


def test_srt_and_uppercase():
    pages = cap.build_pages(WORDS, [{"start": 10.0, "end": 17.0}], 3)
    srt = cap.to_srt(pages)
    blocks = srt.strip().split("\n\n")
    assert blocks[0] == "1\n00:00:00,000 --> 00:00:01,000\nXin chào các"
    assert blocks[3].splitlines()[2] == "đặc biệt!"
    assert cap.srt_time(3725.4567) == "01:02:05,457"
    assert cap.display_text("đặc biệt ữ ộ", "bold") == "ĐẶC BIỆT Ữ Ộ"
    assert cap.display_text("đặc biệt", "clean") == "đặc biệt"


def test_ass_highlights_one_word_per_event():
    edl = {"segments": [{"start": 10.0, "end": 17.0}], "output": {"w": 1080, "h": 1920},
           "captions": {"enabled": True, "style": "bold", "words": WORDS, "words_per_page": 3, "position_y": 0.7, "font_size": 72},
           "hook_text": {"enabled": True, "text": "Đừng bỏ lỡ {điều} này", "start": 0, "end": 3.5, "position_y": 0.2}}
    ass = cap.to_ass(edl)
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: 1")]
    n_words = sum(len(p["words"]) for p in cap.build_pages(WORDS, edl["segments"], 3))
    assert len(events) == n_words
    assert "XIN" in events[0] and r"\c&H0000D4FF&" in events[0].split("XIN")[0]  # first word yellow
    assert "ĐẶC" in ass and "Be Vietnam Pro" in ass and "PlayResY: 1920" in ass
    hook = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: 2")]
    assert len(hook) == 1 and "{điều}" not in hook[0] and "(điều)" in hook[0]
    edl["captions"]["style"] = "bounce"
    assert r"\alpha&HFF&" in cap.to_ass(edl)  # words not spoken yet are hidden


def test_words_in_range_and_merge():
    transcript = [{"start": 1.0, "end": 1.4, "text": " one ", "prob": 0.9}, {"start": 1.5, "end": 1.9, "text": "two"},
                  {"start": 2.0, "end": 2.6, "text": "three"}, {"start": 3.0, "end": 3.2, "text": "four"}]
    assert [x["text"] for x in cap.words_in_range(transcript, 1.2, 2.5)] == ["one", "two", "three"]
    edited = [w(1.5, 1.9, "TWO", True)]
    merged = cap.merge_words(edited, transcript, 1.0, 3.5)
    assert [x["text"] for x in merged] == ["one", "TWO", "three", "four"] and merged[1]["emphasis"] is True


def test_clip_captions_are_seeded_and_srt_follows_edits(client, ready_source):  # noqa: F811
    sid = ready_source["id"]
    first = client.get(f"/sources/{sid}/moments").json()[0]
    client.patch(f"/moments/{first['id']}", json={"status": "approved"})
    clip = client.get(f"/sources/{sid}/clips").json()[0]
    edl = client.get(f"/clips/{clip['id']}").json()["edl"]
    words = edl["captions"]["words"]
    assert words, "caption words are seeded from the transcript"
    assert all(first["start"] <= (x["start"] + x["end"]) / 2 < first["end"] for x in words)
    assert set(words[0]) == {"start", "end", "text", "emphasis"}

    srt = client.get(f"/clips/{clip['id']}/captions.srt")
    assert srt.status_code == 200 and srt.headers["content-type"].startswith("application/x-subrip")
    assert words[0]["text"] in srt.text

    # editing a word updates the SRT (endpoint and the file next to the clip)
    words[0] = {**words[0], "text": "Tuyệt vời"}
    r = client.patch(f"/clips/{clip['id']}", json={"edl": {"captions": {**edl["captions"], "words": words}}})
    assert r.status_code == 200, r.text
    assert "Tuyệt vời" in client.get(f"/clips/{clip['id']}/captions.srt").text
    path = clips_service.clip_dir(clips_service._load(clip["id"])) / "captions.srt"
    assert "Tuyệt vời" in path.read_text("utf-8")

    bad = {**edl["captions"], "words": [{"start": 1, "end": 2, "text": "x" * 300}]}
    assert client.patch(f"/clips/{clip['id']}", json={"edl": {"captions": bad}}).status_code == 422


if __name__ == "__main__":
    VECTORS.write_text(json.dumps(make_vectors(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", VECTORS)
