"""Step 5 - Propose moments.

Providers (LLMProvider interface):
- claude_code (default): the backend writes data/<source>/analysis_request.md (PROMPTS.md Appendix A.1);
  the user runs `/find-moments <source_id>` in Claude Code, which writes moments.json (schema A.3);
  app/watcher.py notices the file and imports it.
- anthropic / openai / ollama: same prompt (the /find-moments command text), reply validated with
  pydantic, retried with the validation error when malformed.
- heuristic: offline fallback, scores sentence windows by loudness peaks, speech density and
  exclamations.

Whatever the provider, the backend recomputes each total score from the 5 dimension scores with the
weights from Settings and snaps the boundaries (pipeline/snap.py) before anything reaches the DB.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from pipeline import analyze as analyze_mod
from pipeline import snap

RUBRIC = ("hook_strength", "coherence", "emotion", "value_density", "payoff")
DEFAULT_WEIGHTS = {"hook_strength": 0.30, "coherence": 0.20, "emotion": 0.20, "value_density": 0.15, "payoff": 0.15}
CATEGORIES = ("opinion", "story", "tip", "fact", "funny", "reaction", "reveal", "gameplay")
PROVIDERS = ("claude_code", "anthropic", "openai", "ollama", "heuristic")

PART_MAX_WORDS = 25_000
PART_OVERLAP = 60.0
REQUEST_FILE = "analysis_request.md"
RESULT_FILE = "moments.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
COMMAND_FILE = REPO_ROOT / ".claude" / "commands" / "find-moments.md"

ANTHROPIC_MODEL = "claude-opus-5-5"
MAX_ATTEMPTS = 3  # first try + 2 retries with the validation error


class MomentsInvalid(Exception):
    """moments.json / an LLM reply does not match schema A.3. str(exc) is a short detail."""


class ProviderError(Exception):
    def __init__(self, provider: str, detail: str):
        super().__init__(detail)
        self.provider, self.detail = provider, detail[:300]


class ProviderNotConfigured(Exception):
    def __init__(self, provider: str):
        super().__init__(provider)
        self.provider = provider


# --------------------------------------------------------------------------
# Sentences
# --------------------------------------------------------------------------
def build_sentences(words: list[dict[str, Any]], *, max_gap: float = 1.0, soft_len: float = 15.0,
                    hard_len: float = 25.0) -> list[dict[str, Any]]:
    """Group words into sentences: end at sentence punctuation, at a pause >= max_gap, or when a
    sentence without punctuation grows past soft_len (at a comma or short pause) / hard_len."""
    out: list[dict[str, Any]] = []
    first = 0
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 0.0
        length = w["end"] - words[first]["start"]
        text = w["text"].strip()
        boundary = (
            nxt is None
            or snap.is_sentence_end(text)
            or gap >= max_gap
            or (length >= soft_len and (text.endswith((",", ";", ":", "—", "-")) or gap >= 0.25))
            or length >= hard_len
        )
        if boundary:
            chunk = words[first : i + 1]
            out.append({
                "start": round(chunk[0]["start"], 2),
                "end": round(chunk[-1]["end"], 2),
                "text": " ".join(x["text"].strip() for x in chunk if x["text"].strip()),
                "first": first,
                "last": i,
            })
            first = i + 1
    return out


# --------------------------------------------------------------------------
# Request file (Appendix A.1)
# --------------------------------------------------------------------------
@dataclass
class RequestMeta:
    source_id: str
    file_name: str
    duration: float
    language: str  # vi | en | mixed
    ui_language: str  # vi | en
    content_type: str
    target_platform: str
    clip_min: int = 15
    clip_max: int = 60
    wanted: tuple[int, int] = (8, 12)
    user_notes: str = ""


def fmt_hms(seconds: float) -> str:
    """2537.5 -> '00:42:17.50'."""
    cs = int(round(max(0.0, seconds) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h:02d}:{m:02d}:{s:02d}.{c:02d}"


def _num(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}"


def request_header(meta: RequestMeta, part: str) -> list[str]:
    notes = " / ".join(line.strip() for line in (meta.user_notes or "").splitlines() if line.strip())
    return [
        f"# Analysis request: {meta.source_id}",
        f"- file: {meta.file_name}",
        f"- duration: {fmt_hms(meta.duration)} ({meta.duration:.1f}s)",
        f"- language: {meta.language}",
        f"- ui_language: {meta.ui_language}",
        f"- content_type: {meta.content_type}",
        f"- target_platform: {meta.target_platform}",
        f"- clip_length: min {meta.clip_min}s, max {meta.clip_max}s",
        f"- wanted: {meta.wanted[0]}-{meta.wanted[1]} moments",
        f"- user_notes: {notes}",
        f"- part: {part}",
    ]


def _part_ranges(sentences: list[dict[str, Any]], max_words: int, overlap: float) -> list[tuple[int, int]]:
    """Split sentence indices into parts of <= max_words words; each part starts `overlap` seconds
    before the previous part ended (at a sentence boundary)."""
    if not sentences:
        return [(0, -1)]
    counts = [s["last"] - s["first"] + 1 for s in sentences]
    if sum(counts) <= max_words:
        return [(0, len(sentences) - 1)]
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(sentences):
        total, end = 0, start
        while end < len(sentences) and (total + counts[end] <= max_words or end == start):
            total += counts[end]
            end += 1
        ranges.append((start, end - 1))
        if end >= len(sentences):
            break
        cut = sentences[end - 1]["end"] - overlap
        nxt = end
        while nxt - 1 > start and sentences[nxt - 1]["start"] >= cut:
            nxt -= 1
        start = max(start + 1, nxt)
    return ranges


def build_requests(meta: RequestMeta, sentences: list[dict[str, Any]], analysis: dict[str, Any],
                   *, max_words: int = PART_MAX_WORDS, overlap: float = PART_OVERLAP) -> list[str]:
    """Return the request text of each part (one element when no split is needed)."""
    ranges = _part_ranges(sentences, max_words, overlap)
    loud = analysis.get("loudness") or []
    texts = []
    for k, (a, b) in enumerate(ranges, 1):
        part_sentences = sentences[a : b + 1] if b >= a else []
        lo = part_sentences[0]["start"] if part_sentences and len(ranges) > 1 else 0.0
        hi = part_sentences[-1]["end"] if part_sentences and len(ranges) > 1 else meta.duration
        if len(ranges) > 1 and loud:
            i0, i1 = int(lo), min(len(loud), int(hi) + 1)
            rel = analyze_mod.relative(loud[i0:i1])
            peaks = [(round(t + i0, 1), r) for t, r in analyze_mod.find_peaks(rel)]
        else:
            peaks = [tuple(p) for p in analysis.get("peaks") or []]
        cuts = [c for c in analysis.get("scene_cuts") or [] if lo <= c <= hi]
        sil = [s for s in analysis.get("silences") or [] if s[1] >= lo and s[0] <= hi and s[1] - s[0] >= 0.4]
        lines = request_header(meta, f"{k}/{len(ranges)}")
        lines += ["", f"## Audio peaks (top {analyze_mod.PEAKS_TOP}, seconds:relative loudness 0-1)",
                  ", ".join(f"{_num(t)}:{r:.2f}" for t, r in peaks) or "(none)"]
        lines += ["", "## Scene cuts (seconds)", ", ".join(_num(c) for c in cuts) or "(none)"]
        lines += ["", f"## Silences ≥ {analyze_mod.SILENCE_MIN}s (start-end, seconds)",
                  ", ".join(f"{_num(s0)}-{_num(s1)}" for s0, s1 in sil) or "(none)"]
        lines += ["", "## Transcript (one sentence per line, times in seconds)"]
        lines += [f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in part_sentences] or ["(no speech)"]
        texts.append("\n".join(lines) + "\n")
    return texts


def write_request_files(folder: Path, meta: RequestMeta, texts: list[str]) -> list[Path]:
    """Write analysis_request.md (and analysis_request.partN.md for long videos). Stale part files
    from an earlier, longer request are removed."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("analysis_request.part*.md"):
        old.unlink(missing_ok=True)
    written: list[Path] = []
    if len(texts) == 1:
        path = folder / REQUEST_FILE
        path.write_text(texts[0], encoding="utf-8")
        return [path]
    names = [f"analysis_request.part{i}.md" for i in range(1, len(texts) + 1)]
    index = request_header(meta, f"split into {len(texts)} parts: " + ", ".join(names))
    index += ["", "This video is long: the audio peaks, scene cuts, silences and transcript are in the part files,",
              "each part overlapping the next by 60 seconds."]
    (folder / REQUEST_FILE).write_text("\n".join(index) + "\n", encoding="utf-8")
    written.append(folder / REQUEST_FILE)
    for name, text in zip(names, texts):
        (folder / name).write_text(text, encoding="utf-8")
        written.append(folder / name)
    return written


# --------------------------------------------------------------------------
# Result schema (Appendix A.3)
# --------------------------------------------------------------------------
class Scores(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hook_strength: float = Field(ge=0, le=10)
    coherence: float = Field(ge=0, le=10)
    emotion: float = Field(ge=0, le=10)
    value_density: float = Field(ge=0, le=10)
    payoff: float = Field(ge=0, le=10)


class MomentIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start: float = Field(ge=0)
    end: float
    title: str = ""
    hook: str = ""
    reason: str = ""
    category: str = "story"
    scores: Scores
    score: float | None = None
    key_line: list[float] | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v: Any) -> str:
        text = str(v or "").strip().lower()
        for token in re.split(r"[|,/ ]+", text):
            if token in CATEGORIES:
                return token
        return "story"

    @field_validator("key_line", mode="before")
    @classmethod
    def _key_line(cls, v: Any) -> Any:
        if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v) and v[0] < v[1]:
            return [float(v[0]), float(v[1])]
        return None

    @field_validator("title", "hook", "reason", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        return "" if v is None else str(v).strip()

    @model_validator(mode="after")
    def _order(self) -> MomentIn:
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be after start ({self.start})")
        return self


class MomentsFile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source_id: str = ""
    summary: str = ""
    notes: str = ""
    moments: list[MomentIn]

    @field_validator("summary", "notes", mode="before")
    @classmethod
    def _join(cls, v: Any) -> str:
        if isinstance(v, list):
            return "\n".join(str(x) for x in v)
        return "" if v is None else str(v)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _extract_json(text: str) -> str:
    text = text.strip().lstrip("﻿")
    text = _FENCE.sub("", text).strip()
    if not text.startswith("{"):
        a, b = text.find("{"), text.rfind("}")
        if a != -1 and b > a:
            text = text[a : b + 1]
    return text


def _short_validation(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts)


def parse_moments(text: str, *, source_id: str | None = None, duration: float | None = None) -> MomentsFile:
    """Parse + validate a moments.json document. Raises MomentsInvalid with a short detail."""
    try:
        raw = json.loads(_extract_json(text))
    except ValueError as exc:
        raise MomentsInvalid(f"not valid JSON ({exc})") from None
    if not isinstance(raw, dict):
        raise MomentsInvalid("the top level must be a JSON object")
    try:
        parsed = MomentsFile.model_validate(raw)
    except ValidationError as exc:
        raise MomentsInvalid(_short_validation(exc)) from None
    if source_id and parsed.source_id and parsed.source_id != source_id:
        raise MomentsInvalid(f"source_id is {parsed.source_id}, expected {source_id}")
    if duration is not None:
        for i, m in enumerate(parsed.moments):
            if m.start >= duration:
                raise MomentsInvalid(f"moments.{i}: start {m.start} is past the end of the video ({duration:.1f}s)")
    return parsed


def compute_score(scores: dict[str, float], weights: dict[str, float] | None = None) -> int:
    """Weighted total 0..100. Weights are normalized, so they need not sum to 1."""
    w = weights or DEFAULT_WEIGHTS
    total_w = sum(max(0.0, float(w.get(k, 0))) for k in RUBRIC) or 1.0
    value = sum(float(scores.get(k, 0)) * max(0.0, float(w.get(k, 0))) for k in RUBRIC) / total_w
    return int(round(max(0.0, min(10.0, value)) * 10))


def finalize(parsed: MomentsFile, *, words: list[dict[str, Any]], silences: list[list[float]], duration: float,
             weights: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Recompute scores, snap boundaries, drop duplicates. Returns plain dicts sorted by score."""
    out = []
    for m in parsed.moments:
        raw_start, raw_end = max(0.0, m.start), min(duration, m.end)
        if raw_end - raw_start < 1.0:
            continue
        start, end = snap.snap_moment(raw_start, raw_end, words, silences, duration=duration)
        scores = m.scores.model_dump()
        out.append({
            "start": start, "end": end, "raw_start": round(raw_start, 3), "raw_end": round(raw_end, 3),
            "scores": scores, "score": compute_score(scores, weights),
            "title": m.title[:120], "hook": m.hook[:160], "reason": m.reason[:600],
            "category": m.category, "key_line": m.key_line,
        })
    kept = snap.dedupe(out)
    return sorted(kept, key=lambda m: (-m["score"], m["start"]))


# --------------------------------------------------------------------------
# Prompt for API providers (the /find-moments command, adapted to a chat reply)
# --------------------------------------------------------------------------
SCHEMA_EXAMPLE = """{
  "source_id": "src_01",
  "summary": "3–5 line summary of the video (in ui_language)",
  "notes": "",
  "moments": [
    {
      "start": 312.40,
      "end": 356.10,
      "title": "Sai lầm khiến kênh mất 50% view",
      "hook": "Đừng bao giờ làm điều này",
      "reason": "Opens with a strong warning, tells a concrete story and ends with a clear lesson.",
      "category": "opinion|story|tip|fact|funny|reaction|reveal|gameplay",
      "scores": { "hook_strength": 9, "coherence": 8, "emotion": 7, "value_density": 8, "payoff": 9 },
      "score": 83,
      "key_line": [318.20, 321.75]
    }
  ]
}"""

_SCORE = {"type": "number"}
JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string"},
        "summary": {"type": "string"},
        "notes": {"type": "string"},
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "scores": {
                        "type": "object",
                        "properties": {k: _SCORE for k in RUBRIC},
                        "required": list(RUBRIC),
                        "additionalProperties": False,
                    },
                    "score": {"type": "number"},
                    "key_line": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["start", "end", "title", "hook", "reason", "category", "scores", "score", "key_line"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["source_id", "summary", "notes", "moments"],
    "additionalProperties": False,
}


def system_prompt(source_id: str) -> str:
    """The /find-moments command text, with file reading/writing replaced by message in/out."""
    try:
        body = COMMAND_FILE.read_text("utf-8")
    except OSError:
        body = "You are an experienced short-form video editor. Pick moments that stand alone as 9:16 shorts."
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :]
    body = body.replace("$ARGUMENTS", source_id)
    return (
        body.strip()
        + "\n\n## How this run differs\n"
        "- The analysis request is in the user message instead of a file (one part at a time for long videos).\n"
        "- Instead of writing moments.json, reply with **only** the JSON object (no prose, no code fence) in this schema:\n"
        + SCHEMA_EXAMPLE
        + "\n- Do not print a table."
    )


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------
@dataclass
class ProposeInput:
    meta: RequestMeta
    texts: list[str]  # request text of each part
    sentences: list[dict[str, Any]]
    analysis: dict[str, Any]
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    log: Any = None  # Callable[[str], None] | None


class LLMProvider(Protocol):
    name: str

    def propose(self, inp: ProposeInput) -> MomentsFile: ...


def _log(inp: ProposeInput, msg: str) -> None:
    if inp.log:
        inp.log(msg)


class ChatProvider:
    """Shared loop for API providers: one request per part, retry with the validation error."""

    name = "chat"

    def send(self, system: str, messages: list[dict[str, str]]) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def propose(self, inp: ProposeInput) -> MomentsFile:
        system = system_prompt(inp.meta.source_id)
        results: list[MomentsFile] = []
        for k, text in enumerate(inp.texts, 1):
            messages = [{"role": "user", "content": text}]
            last_error = ""
            for attempt in range(1, MAX_ATTEMPTS + 1):
                _log(inp, f"{self.name}: part {k}/{len(inp.texts)}, attempt {attempt}")
                reply = self.send(system, messages)
                try:
                    results.append(parse_moments(reply, source_id=inp.meta.source_id, duration=inp.meta.duration))
                    break
                except MomentsInvalid as exc:
                    last_error = str(exc)
                    _log(inp, f"{self.name}: invalid reply: {last_error}")
                    messages += [
                        {"role": "assistant", "content": reply},
                        {"role": "user", "content": f"Your reply did not match the schema: {last_error}. "
                                                    "Reply again with only the corrected JSON object."},
                    ]
            else:
                raise ProviderError(self.name, f"invalid reply after {MAX_ATTEMPTS} attempts: {last_error}")
        return merge_parts(results, inp.meta, inp.weights)


def merge_parts(results: list[MomentsFile], meta: RequestMeta, weights: dict[str, float]) -> MomentsFile:
    if len(results) == 1:
        return results[0]
    moments = [m for r in results for m in r.moments]
    moments.sort(key=lambda m: -compute_score(m.scores.model_dump(), weights))
    picked: list[MomentIn] = []
    for m in moments:
        if all(snap.overlap((m.start, m.end), (p.start, p.end)) <= 0.3 * min(m.end - m.start, p.end - p.start)
               for p in picked):
            picked.append(m)
        if len(picked) >= meta.wanted[1]:
            break
    return MomentsFile(source_id=meta.source_id, summary="\n".join(r.summary for r in results if r.summary),
                       notes="\n".join(r.notes for r in results if r.notes), moments=picked)


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], provider: str, timeout: float = 900) -> Any:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"content-type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        raise ProviderError(provider, f"HTTP {exc.code}: {body}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ProviderError(provider, str(getattr(exc, "reason", exc))) from None


class AnthropicProvider(ChatProvider):
    """Claude via the official `anthropic` SDK, JSON enforced with structured outputs."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str = ANTHROPIC_MODEL):
        if not api_key:
            raise ProviderNotConfigured(self.name)
        try:
            import anthropic
        except ImportError:
            raise ProviderNotConfigured(self.name) from None
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=900)
        self.model = model

    def send(self, system: str, messages: list[dict[str, str]]) -> str:
        anthropic = self._anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=messages,  # type: ignore[arg-type]
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": JSON_SCHEMA}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise ProviderError(self.name, "authentication failed (check ANTHROPIC_API_KEY)") from None
        except anthropic.RateLimitError:
            raise ProviderError(self.name, "rate limited, try again later") from None
        except anthropic.APIStatusError as exc:
            raise ProviderError(self.name, f"HTTP {exc.status_code}: {exc.message}") from None
        except anthropic.APIConnectionError:
            raise ProviderError(self.name, "cannot reach the Anthropic API") from None
        if response.stop_reason == "refusal":
            raise ProviderError(self.name, "the model declined this request")
        if response.stop_reason == "max_tokens":
            raise ProviderError(self.name, "the reply was cut off (max_tokens)")
        return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")


class OpenAIProvider(ChatProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ProviderNotConfigured(self.name)
        self.api_key, self.model = api_key, model

    def send(self, system: str, messages: list[dict[str, str]]) -> str:
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"model": self.model, "messages": [{"role": "system", "content": system}, *messages],
             "response_format": {"type": "json_object"}},
            {"authorization": f"Bearer {self.api_key}"}, self.name,
        )
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ProviderError(self.name, "unexpected response shape") from None


class OllamaProvider(ChatProvider):
    name = "ollama"

    def __init__(self, host: str, model: str):
        if not host or not model:
            raise ProviderNotConfigured(self.name)
        self.host, self.model = host.rstrip("/"), model

    def send(self, system: str, messages: list[dict[str, str]]) -> str:
        data = _post_json(
            f"{self.host}/api/chat",
            {"model": self.model, "messages": [{"role": "system", "content": system}, *messages],
             "stream": False, "format": "json", "options": {"num_ctx": 32768, "temperature": 0.2}},
            {}, self.name,
        )
        try:
            return data["message"]["content"] or ""
        except (KeyError, TypeError):
            raise ProviderError(self.name, "unexpected response shape") from None


# --------------------------------------------------------------------------
# Heuristic provider (offline)
# --------------------------------------------------------------------------
FILLERS = {
    "so", "and", "but", "like", "um", "uh", "well", "also", "because", "then", "okay", "ok", "anyway", "or", "yeah",
    "ừ", "ừm", "thì", "và", "nhưng", "mà", "rồi", "à", "ờ", "thế", "vậy", "là", "nên", "còn",
}
PRONOUNS = {"it", "this", "that", "he", "she", "they", "these", "those", "nó", "họ", "cái", "đó", "ấy"}

REASONS = {
    "en": {
        "prefix": "Heuristic pick: ",
        "question": "opens with a question",
        "number": "concrete numbers",
        "loud": "a clear loudness peak",
        "excl": "emphatic lines",
        "dense": "fast, dense speech",
        "clean": "starts and ends on sentence boundaries",
        "summary": "Offline proposals (heuristic): segments scored by loudness peaks, speech density and "
                   "exclamations. Review each one; the text is not understood, only measured.",
        "fewer": "Only {n} segments met the criteria for this video.",
    },
    "vi": {
        "prefix": "Chọn theo heuristic: ",
        "question": "mở đầu bằng một câu hỏi",
        "number": "có số liệu cụ thể",
        "loud": "có đỉnh âm lượng rõ",
        "excl": "có câu cảm thán mạnh",
        "dense": "nói nhanh, nhiều thông tin",
        "clean": "bắt đầu và kết thúc trọn câu",
        "summary": "Đề xuất offline (heuristic): chấm điểm theo đỉnh âm lượng, mật độ lời nói và câu cảm thán. "
                   "Hãy duyệt từng đoạn vì nội dung chưa được hiểu, chỉ được đo đạc.",
        "fewer": "Video này chỉ có {n} đoạn đạt tiêu chí.",
    },
}


def _first_word(text: str) -> str:
    m = re.match(r"\W*(\w+)", text.lower())
    return m.group(1) if m else ""


def _clamp10(x: float) -> float:
    return round(max(0.0, min(10.0, x)), 1)


def _trim_words(text: str, max_words: int, max_chars: int) -> str:
    words = text.split()
    out = " ".join(words[:max_words])
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0]
    return out.rstrip(",;:—- ")


def heuristic_propose(inp: ProposeInput) -> MomentsFile:
    meta, sents = inp.meta, inp.sentences
    lang = meta.ui_language if meta.ui_language in REASONS else "en"
    texts = REASONS[lang]
    rel = analyze_mod.relative(inp.analysis.get("loudness") or [])

    def loud(a: float, b: float) -> float:
        lo, hi = int(a), max(int(a) + 1, int(b))
        seg = rel[lo:hi]
        return max(seg) if seg else 0.0

    lo_len, hi_len = float(meta.clip_min), float(min(meta.clip_max, 50))
    candidates = []
    for i, first in enumerate(sents):
        fw = _first_word(first["text"])
        filler = fw in FILLERS
        pause_before = i == 0 or first["start"] - sents[i - 1]["end"] >= 0.5
        for j in range(i, len(sents)):
            last = sents[j]
            length = last["end"] - first["start"]
            if length > hi_len:
                break
            if length < lo_len:
                continue
            pause_after = j == len(sents) - 1 or sents[j + 1]["start"] - last["end"] >= 0.5
            window = sents[i : j + 1]
            n_words = sum(s["last"] - s["first"] + 1 for s in window)
            wps = n_words / max(1.0, length)
            excl = sum(1 for s in window if s["text"].rstrip("\"'”’").endswith("!"))
            question = first["text"].rstrip("\"'”’").endswith("?")
            number = bool(re.search(r"\d", first["text"]))
            peak, hook_loud = loud(first["start"], last["end"]), loud(first["start"], first["start"] + 3)
            scores = {
                "hook_strength": _clamp10(5 + 2 * (question or first["text"].endswith("!")) + 1 * number
                                          + 2 * hook_loud - 3 * filler),
                "coherence": _clamp10(6 + pause_before + pause_after - 3 * filler - (fw in PRONOUNS)),
                "emotion": _clamp10(2 + 5 * peak + min(3.0, 1.5 * excl)),
                "value_density": _clamp10(2 + (wps - 1.2) / 2.0 * 8),
                "payoff": _clamp10(5 + 2 * bool(re.search(r"[!?]|\d", last["text"])) + 1 * pause_after),
            }
            score = compute_score(scores, inp.weights)
            bonus = 3 if 25 <= length <= 45 else 0
            flags = [k for k, on in (("question", question), ("number", number), ("loud", peak >= 0.8),
                                     ("excl", excl >= 2), ("dense", wps >= 2.6),
                                     ("clean", pause_before and pause_after)) if on]
            candidates.append((score + bonus, score, i, j, scores, flags, peak, excl))
    candidates.sort(key=lambda c: (-c[0], sents[c[2]]["start"]))
    picked = []
    for cand in candidates:
        _, score, i, j, scores, flags, peak, excl = cand
        a, b = sents[i]["start"], sents[j]["end"]
        if all(snap.overlap((a, b), (p[0], p[1])) <= 0.3 * min(b - a, p[1] - p[0]) for p in picked):
            picked.append((a, b, cand))
        if len(picked) >= meta.wanted[1]:
            break

    moments = []
    for a, b, (_, score, i, j, scores, flags, peak, excl) in picked:
        window = sents[i : j + 1]
        key = max(window, key=lambda s: (loud(s["start"], s["end"]), s["text"].endswith(("!", "?"))))
        reason_bits = [texts[f] for f in flags[:2]] or [texts["dense"] if scores["value_density"] >= 6 else texts["clean"]]
        reason = texts["prefix"] + ", ".join(reason_bits) + "."
        if peak >= 0.85 or excl >= 2:
            category = "reaction"
        elif "number" in flags:
            category = "fact"
        elif "question" in flags:
            category = "opinion"
        else:
            category = "story"
        moments.append(MomentIn(
            start=a, end=b, title=_trim_words(window[0]["text"], 12, 60), hook=_trim_words(key["text"], 8, 60),
            reason=reason, category=category, scores=Scores(**scores), score=score,
            key_line=[key["start"], key["end"]],
        ))
    notes = texts["fewer"].format(n=len(moments)) if len(moments) < meta.wanted[0] else ""
    return MomentsFile(source_id=meta.source_id, summary=texts["summary"], notes=notes, moments=moments)


class HeuristicProvider:
    name = "heuristic"

    def propose(self, inp: ProposeInput) -> MomentsFile:
        return heuristic_propose(inp)


def make_provider(name: str, *, anthropic_key: str = "", anthropic_model: str = ANTHROPIC_MODEL,
                  openai_key: str = "", openai_model: str = "gpt-4.1", ollama_host: str = "",
                  ollama_model: str = "") -> LLMProvider:
    if name == "heuristic":
        return HeuristicProvider()
    if name == "anthropic":
        return AnthropicProvider(anthropic_key, anthropic_model)
    if name == "openai":
        return OpenAIProvider(openai_key, openai_model)
    if name == "ollama":
        return OllamaProvider(ollama_host, ollama_model)
    raise ProviderNotConfigured(name)  # claude_code is handled by the pipeline step (request file + wait)
