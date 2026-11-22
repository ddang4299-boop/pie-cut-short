"""Step 2 - Transcribe: faster-whisper word timestamps (vad_filter=True).

ElevenLabs Scribe is added as an alternative provider in Phase 5.

The job queue runs this module in a child process (`python -m pipeline.transcribe ...`) so a
CUDA/driver crash cannot take the backend down, and cancelling is a simple kill. The child
prints one JSON object per line on stdout:
    {"event": "stage", "stage": "loading_model" | "transcribing"}
    {"event": "progress", "progress": 0.42}
    {"event": "warning", "code": "TRANSCRIBE_CPU_FALLBACK"}
    {"event": "done", "out": "..."}
and writes the transcript JSON to --out.

Transcript format (version 1):
    {"version": 1, "language": "vi", "language_option": "auto", "language_probability": 0.98,
     "model": "large-v3", "device": "cuda", "duration": 180.0,
     "words": [{"start": 0.0, "end": 0.32, "text": "Xin", "prob": 0.99}, ...],
     "segments": [{"start": 0.0, "end": 2.1, "text": "Xin chào các bạn.", "first_word": 0, "last_word": 3}]}
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

TRANSCRIPT_VERSION = 1
LANGUAGE_OPTIONS = ("auto", "vi", "en", "mixed")

EmitFn = Callable[[dict[str, Any]], None]


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def cache_key(file_hash: str, model: str, language_option: str) -> str:
    """Transcripts are cached per (media content, model, language option, format version)."""
    raw = f"v{TRANSCRIPT_VERSION}|{file_hash}|{model}|{language_option}|vad"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def whisper_language_args(option: str) -> dict[str, Any]:
    """Map our language option to faster-whisper transcribe() kwargs."""
    if option not in LANGUAGE_OPTIONS:
        raise ValueError(f"unknown language option {option!r}")
    if option == "mixed":
        return {"language": None, "multilingual": True}
    if option == "auto":
        return {"language": None}
    return {"language": option}


def _r(x: float) -> float:
    return round(float(x), 3)


def normalize_words(
    raw: Iterable[dict[str, Any]], duration: float | None = None, floor: float = 0.0
) -> list[dict[str, Any]]:
    """Strip text, drop empty words, keep times monotonic (>= floor) and inside [0, duration]."""
    out: list[dict[str, Any]] = []
    last_end = max(0.0, floor)
    for w in raw:
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        start = max(float(w["start"]), last_end)
        end = max(float(w["end"]), start)
        if duration:
            start, end = min(start, duration), min(end, duration)
        item: dict[str, Any] = {"start": _r(start), "end": _r(end), "text": text}
        if w.get("prob") is not None:
            item["prob"] = round(float(w["prob"]), 3)
        out.append(item)
        last_end = end
    return out


def build_transcript(
    segments: Iterable[dict[str, Any]],
    *,
    language: str,
    language_option: str,
    language_probability: float | None,
    model: str,
    device: str,
    duration: float,
) -> dict[str, Any]:
    """Assemble the transcript dict from segments shaped like {start, end, text, words: [...]}."""
    words: list[dict[str, Any]] = []
    segs: list[dict[str, Any]] = []
    for seg in segments:
        # keep word times monotonic across segments too
        floor = words[-1]["end"] if words else 0.0
        seg_words = normalize_words(seg.get("words") or [], duration, floor=floor)
        if not seg_words:
            continue
        first = len(words)
        words.extend(seg_words)
        segs.append({
            "start": seg_words[0]["start"],
            "end": seg_words[-1]["end"],
            "text": " ".join(w["text"] for w in seg_words),
            "first_word": first,
            "last_word": len(words) - 1,
        })
    return {
        "version": TRANSCRIPT_VERSION,
        "language": "mixed" if language_option == "mixed" else language,
        "language_option": language_option,
        "language_probability": None if language_probability is None else round(float(language_probability), 3),
        "model": model,
        "device": device,
        "duration": _r(duration),
        "words": words,
        "segments": segs,
    }


# --------------------------------------------------------------------------
# faster-whisper
# --------------------------------------------------------------------------
def setup_cuda_dlls() -> None:
    """Windows: expose the cuBLAS/cuDNN DLLs from the nvidia-* pip packages to ctranslate2."""
    if sys.platform != "win32":
        return
    try:
        import nvidia  # type: ignore[import-not-found]
    except ImportError:
        return
    roots = [Path(p) for p in getattr(nvidia, "__path__", [])]
    for root in roots:
        for bin_dir in root.glob("*/bin"):
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


SAMPLE_RATE = 16000


def load_audio(path: Path, ffmpeg: str = "ffmpeg"):  # noqa: ANN201  (numpy array)
    """Mono 16 kHz float32 samples.

    We decode ourselves instead of letting faster-whisper use PyAV: faster-whisper 1.2 passes
    an argument that PyAV >= 15 removed. 16 kHz mono 16-bit WAV (what the pipeline extracts)
    is read directly; anything else is decoded by ffmpeg.
    """
    import subprocess
    import wave

    import numpy as np

    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                if w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1 and w.getsampwidth() == 2:
                    data = w.readframes(w.getnframes())
                    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        except wave.Error:
            pass
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-vn",
           "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0


def _is_cuda_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(s in text for s in ("cuda", "cublas", "cudnn", "gpu", "out of memory", "no kernel image"))


def _load_model(model: str, device: str, models_dir: Path):  # noqa: ANN202
    from faster_whisper import WhisperModel

    compute_type = "float16" if device == "cuda" else "int8"
    return WhisperModel(model, device=device, compute_type=compute_type, download_root=str(models_dir))


def _run_whisper(whisper_model, audio, language_option: str, emit: EmitFn) -> tuple[list[dict[str, Any]], Any]:  # noqa: ANN001
    segments_iter, info = whisper_model.transcribe(
        audio,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,  # fewer repetition loops on long videos
        **whisper_language_args(language_option),
    )
    emit({"event": "stage", "stage": "transcribing", "language": info.language})
    total = float(info.duration or 0) or 1.0
    segments: list[dict[str, Any]] = []
    last_emit = 0.0
    for seg in segments_iter:
        segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
            "words": [{"start": w.start, "end": w.end, "text": w.word, "prob": w.probability} for w in (seg.words or [])],
        })
        now = time.monotonic()
        if now - last_emit > 0.5:
            emit({"event": "progress", "progress": round(min(0.99, seg.end / total), 4)})
            last_emit = now
    return segments, info


def transcribe_file(
    audio: Path,
    *,
    model: str,
    device: str,
    language_option: str,
    models_dir: Path,
    emit: EmitFn,
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    """Transcribe with faster-whisper on `device` (cuda|cpu); retries on CPU if CUDA fails."""
    if device == "cuda":
        setup_cuda_dlls()
    emit({"event": "stage", "stage": "decoding_audio"})
    samples = load_audio(audio, ffmpeg)
    devices = [device] if device == "cpu" else [device, "cpu"]
    last_exc: BaseException | None = None
    for dev in devices:
        try:
            emit({"event": "stage", "stage": "loading_model", "model": model, "device": dev})
            whisper_model = _load_model(model, dev, models_dir)
            segments, info = _run_whisper(whisper_model, samples, language_option, emit)
            if dev != device:
                emit({"event": "warning", "code": "TRANSCRIBE_CPU_FALLBACK"})
            return build_transcript(
                segments,
                language=info.language,
                language_option=language_option,
                language_probability=getattr(info, "language_probability", None),
                model=model,
                device=dev,
                duration=float(info.duration or 0),
            )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if dev == "cpu" or not _is_cuda_error(exc):
                raise
            emit({"event": "log", "message": f"CUDA failed ({exc}); retrying on CPU"})
    assert last_exc is not None
    raise last_exc


# --------------------------------------------------------------------------
# CLI (child process)
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Transcribe audio with faster-whisper (JSON lines on stdout).")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="cpu", choices=("cuda", "cpu"))
    parser.add_argument("--language", default="auto", choices=LANGUAGE_OPTIONS)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args(argv)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    def emit(event: dict[str, Any]) -> None:
        print(json.dumps(event, ensure_ascii=False), flush=True)

    try:
        transcript = transcribe_file(
            args.audio, model=args.model, device=args.device, language_option=args.language,
            models_dir=args.models_dir, emit=emit, ffmpeg=args.ffmpeg,
        )
    except Exception as exc:  # noqa: BLE001
        emit({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        return 1
    tmp = args.out.with_name(args.out.name + ".tmp")
    tmp.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    tmp.replace(args.out)
    emit({"event": "done", "out": str(args.out), "words": len(transcript["words"])})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
