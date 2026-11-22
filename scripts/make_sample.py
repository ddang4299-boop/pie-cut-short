"""Generate a synthetic test video when no real sample exists.

    .venv\\Scripts\\python scripts\\make_sample.py [--seconds 180] [--out samples/synthetic_3min.mp4]

The video is ffmpeg's testsrc2 pattern (1280x720, 30 fps) with a 440 Hz sine tone that
switches on and off every few seconds (so silence detection has something to find).
It contains NO real speech: transcription quality cannot be judged with it.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_sample(out: Path, seconds: int = 180) -> Path:
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found on PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Tone on for 4 s, off for 1 s.
    audio = f"sine=frequency=440:sample_rate=48000:duration={seconds},volume='if(lt(mod(t,5),4),1,0)':eval=frame"
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-nostdin",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds}",
        "-f", "lavfi", "-i", audio,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "samples" / "synthetic_3min.mp4")
    args = parser.parse_args()
    path = make_sample(args.out, args.seconds)
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
