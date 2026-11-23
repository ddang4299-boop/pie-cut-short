"""Child-process entry point for the OpenCV / PySceneDetect / MediaPipe work.

Native vision libraries run in their own process, like faster-whisper: a crash or a stray file
descriptor operation there can never touch the app's SQLite connection (running them in-process
was seen to corrupt the test database). Output is JSON lines on stdout:

  {"event": "progress", "progress": 0.42}
  {"event": "result", "data": {...}}
  {"event": "error", "message": "..."}

Usage:
  python -m pipeline.vision_worker scenes VIDEO
  python -m pipeline.vision_worker content_type VIDEO --duration 123.4 [--transcript T.json] --models-dir DIR
  python -m pipeline.vision_worker reframe VIDEO --segments '[{"start":1,"end":9}]' --src-w 1920 --src-h 1080
      [--mode auto|split|cursor] [--content-type talking_head] [--out-w 1080 --out-h 1920] --models-dir DIR
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _progress(f: float) -> None:
    emit({"event": "progress", "progress": round(float(f), 4)})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("task", choices=["scenes", "content_type", "reframe"])
    p.add_argument("video", type=Path)
    p.add_argument("--duration", type=float, default=0.0)
    p.add_argument("--transcript", type=Path)
    p.add_argument("--models-dir", type=Path, default=Path("../data/models"))
    p.add_argument("--segments", default="[]")
    p.add_argument("--mode")
    p.add_argument("--content-type")
    p.add_argument("--src-w", type=int, default=0)
    p.add_argument("--src-h", type=int, default=0)
    p.add_argument("--out-w", type=int, default=1080)
    p.add_argument("--out-h", type=int, default=1920)
    args = p.parse_args(argv)
    try:
        if args.task == "scenes":
            from pipeline.analyze import scene_cuts

            data: Any = scene_cuts(args.video, on_progress=_progress)
        elif args.task == "reframe":
            from pipeline import reframe

            data = reframe.compute(args.video, json.loads(args.segments), content_type=args.content_type,
                                   mode=args.mode, src_w=args.src_w, src_h=args.src_h, out_w=args.out_w,
                                   out_h=args.out_h, models_dir=args.models_dir, on_progress=_progress)
        else:
            from pipeline import content_type

            words = []
            if args.transcript and args.transcript.exists():
                words = json.loads(args.transcript.read_text("utf-8")).get("words") or []
            data = content_type.detect(args.video, duration=args.duration, words=words,
                                       models_dir=args.models_dir, on_progress=_progress)
    except Exception as exc:  # noqa: BLE001 - reported to the parent as an event
        emit({"event": "error", "message": f"{type(exc).__name__}: {exc}"[:500]})
        return 1
    emit({"event": "result", "data": data})
    return 0


if __name__ == "__main__":
    sys.exit(main())
