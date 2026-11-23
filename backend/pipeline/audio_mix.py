"""Audio for a rendered clip: cut the original audio to the EDL segments, apply volume, duck it
under the voiceover, add background music, mix, and loudness-normalize (two-pass loudnorm, -14 LUFS by default).

`build_plan()` is pure (ffmpeg inputs + filter graph) so it can be unit tested; `render_mix()`
runs it and `normalize()` does the two loudnorm passes.

Ducking uses a fixed gain while the voiceover plays (original x 10^(duck_db/20)), exactly what the
editor preview does, so preview and render sound the same (see DECISIONS.md).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pipeline.ingest import FFmpegError, run_ffmpeg
from pipeline.keyframes import clip_duration

SAMPLE_RATE = 48000
TRUE_PEAK = -1.5  # target true peak (acceptance: <= -1 dBTP)
LRA = 11
SILENCE_LUFS = -70.0  # below this the mix is treated as silent and not normalized
_FMT = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"

ProcessFn = Callable[[subprocess.Popen], None]
LogFn = Callable[[str], None]


@dataclass
class AudioPlan:
    inputs: list[list[str]] = field(default_factory=list)  # ffmpeg args per input, in order
    graph: list[str] = field(default_factory=list)  # filter chains
    out: str = "aout"
    duration: float = 0.0
    duck: tuple[float, float] | None = None  # clip-time window where the original is ducked
    music: tuple[float, float] | None = None  # clip-time window where the background music plays

    @property
    def filter_complex(self) -> str:
        return ";".join(self.graph)


def _f(x: float) -> str:
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _gain(db: float) -> float:
    return 10 ** (db / 20)


def voiceover_window(edl: dict[str, Any], vo_duration: float | None) -> tuple[float, float] | None:
    """Clip-time [start, end) the voiceover plays, clipped to the clip; None when it is off."""
    vo = edl.get("voiceover") or {}
    if not vo.get("enabled") or not vo.get("audio_path") or not vo_duration:
        return None
    total = clip_duration(edl.get("segments") or [])
    start = max(0.0, float(vo.get("start", 0.0)))
    end = min(total, start + float(vo_duration))
    return (start, end) if end > start + 0.01 else None


def music_window(edl: dict[str, Any], track_duration: float | None) -> tuple[float, float] | None:
    """Clip-time [start, end) the background music plays; None when it is off.

    The track plays from `music.offset`; with `loop` it wraps to 0 at its end (the editor preview
    uses the same rule: track time = (offset + elapsed) mod duration)."""
    mu = edl.get("music") or {}
    if not mu.get("enabled") or not mu.get("track_id") or not track_duration:
        return None
    total = clip_duration(edl.get("segments") or [])
    start = max(0.0, float(mu.get("start", 0.0)))
    end = total
    if not mu.get("loop", True):
        end = min(total, start + max(0.0, track_duration - float(mu.get("offset", 0.0))))
    return (start, end) if end > start + 0.01 else None


def build_plan(edl: dict[str, Any], *, media: Path | None, media_offset: float = 0.0,
               isolated: tuple[Path, float] | None = None, voiceover: Path | None = None,
               vo_duration: float | None = None, include_voiceover: bool = True,
               music: tuple[Path, float] | None = None, include_music: bool = True) -> AudioPlan:
    """ffmpeg inputs + filter graph producing [aout]: the clip's audio before loudness normalization.

    - media: file with the original audio (None = the source has no audio -> silence), whose time 0
      is source time `media_offset` (a rough cut starts a little before the clip).
    - isolated: (Voice Isolator output, source time it starts at) used instead of `media`.
    - voiceover: generated voiceover file; mixed at `voiceover.start` with ducking when
      `include_voiceover`.
    - music: (library track, its duration); mixed from `music.start` with fades, looped when
      `music.loop`, and ducked by `music.duck_db` while the voiceover plays, when `include_music`.
    """
    segments = edl.get("segments") or []
    audio = edl.get("audio") or {}
    total = round(clip_duration(segments), 3)
    plan = AudioPlan(duration=total)
    src, offset = (isolated if isolated else (media, media_offset)) if (isolated or media) else (None, 0.0)

    if src is None:
        plan.inputs.append(["-f", "lavfi", "-t", _f(total), "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"])
        plan.graph.append(f"[0:a]{_FMT}[orig]")
    else:
        labels = []
        for i, seg in enumerate(segments):
            length = seg["end"] - seg["start"]
            at = max(0.0, seg["start"] - offset)
            plan.inputs.append(["-ss", _f(at), "-t", _f(length), "-i", str(src)])
            plan.graph.append(f"[{i}:a]{_FMT},apad=whole_dur={_f(length)},atrim=duration={_f(length)},"
                              f"asetpts=PTS-STARTPTS[s{i}]")
            labels.append(f"[s{i}]")
        if len(labels) == 1:
            plan.graph.append(f"{labels[0]}anull[orig]")
        else:
            plan.graph.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[orig]")

    chain = f"[orig]volume={_f(float(audio.get('volume_db', 0.0)))}dB"
    window = voiceover_window(edl, vo_duration) if include_voiceover and voiceover else None
    if window:
        vo = edl["voiceover"]
        duck = _gain(float(vo.get("duck_db", -12.0)))
        chain += f",volume='if(between(t,{_f(window[0])},{_f(window[1])}),{duck:.5f},1)':eval=frame"
        plan.duck = window
    plan.graph.append(chain + "[base]")

    layers = ["[base]"]
    if window:
        idx = len(plan.inputs)
        plan.inputs.append(["-i", str(voiceover)])
        vo = edl["voiceover"]
        delay = int(round(window[0] * 1000))
        plan.graph.append(f"[{idx}:a]{_FMT},volume={_f(float(vo.get('volume_db', 0.0)))}dB,"
                          f"atrim=duration={_f(window[1] - window[0])},adelay={delay}:all=1[vo]")
        layers.append("[vo]")

    mwin = music_window(edl, music[1]) if include_music and music else None
    if mwin and music:
        mu = edl["music"]
        idx = len(plan.inputs)
        plan.inputs.append((["-stream_loop", "-1"] if mu.get("loop", True) else []) + ["-i", str(music[0])])
        length = mwin[1] - mwin[0]
        chain = (f"[{idx}:a]{_FMT},atrim=start={_f(float(mu.get('offset', 0.0)))}:duration={_f(length)},"
                 f"asetpts=PTS-STARTPTS,volume={_f(float(mu.get('volume_db', -20.0)))}dB")
        fade_in = min(float(mu.get("fade_in", 0.0)), length / 2)
        fade_out = min(float(mu.get("fade_out", 0.0)), length / 2)
        if fade_in > 0.01:
            chain += f",afade=t=in:st=0:d={_f(fade_in)}"
        if fade_out > 0.01:
            chain += f",afade=t=out:st={_f(length - fade_out)}:d={_f(fade_out)}"
        chain += f",adelay={int(round(mwin[0] * 1000))}:all=1"
        if window:  # same window as the original's ducking; t is clip time after adelay
            chain += (f",volume='if(between(t,{_f(window[0])},{_f(window[1])}),"
                      f"{_gain(float(mu.get('duck_db', -10.0))):.5f},1)':eval=frame")
        plan.graph.append(chain + "[mus]")
        plan.music = mwin
        layers.append("[mus]")

    if len(layers) > 1:
        plan.graph.append(f"{''.join(layers)}amix=inputs={len(layers)}:duration=first:normalize=0:dropout_transition=0[mixed]")
        last = "[mixed]"
    else:
        last = "[base]"
    plan.graph.append(f"{last}apad=whole_dur={_f(total)},atrim=duration={_f(total)},asetpts=PTS-STARTPTS[{plan.out}]")
    return plan


def plan_command(plan: AudioPlan, out: Path, ffmpeg: str = "ffmpeg", codec: str = "pcm_f32le") -> list[str]:
    cmd = [ffmpeg, "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1"]
    for args in plan.inputs:
        cmd += args
    cmd += ["-filter_complex", plan.filter_complex, "-map", f"[{plan.out}]",
            "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", codec, str(out)]
    return cmd


def render_mix(plan: AudioPlan, out: Path, *, ffmpeg: str = "ffmpeg", log: LogFn | None = None,
               on_process: ProcessFn | None = None, codec: str = "pcm_f32le") -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(plan_command(plan, out, ffmpeg, codec), duration=plan.duration, log=log, on_process=on_process)
    return out


# --------------------------------------------------------------------------
# loudness
# --------------------------------------------------------------------------
def _run_capture(cmd: list[str], on_process: ProcessFn | None = None) -> str:
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if on_process:
        on_process(proc)
    _, err = proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg exited with code {proc.returncode}", err[-2000:])
    return err


def parse_loudnorm_json(stderr: str) -> dict[str, float]:
    """The measurement block loudnorm prints with print_format=json (last {...} in stderr)."""
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, flags=re.S)
    if not m:
        raise ValueError("no loudnorm measurement in ffmpeg output")
    data = json.loads(m[-1])
    out = {}
    for k, v in data.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def loudnorm_filter(target: float, measured: dict[str, float] | None = None) -> str:
    base = f"loudnorm=I={_f(target)}:TP={_f(TRUE_PEAK)}:LRA={LRA}"
    if not measured:
        return base + ":print_format=json"
    return (base + f":measured_I={measured['input_i']:.2f}:measured_TP={measured['input_tp']:.2f}"
            f":measured_LRA={measured['input_lra']:.2f}:measured_thresh={measured['input_thresh']:.2f}"
            f":offset={measured.get('target_offset', 0.0):.2f}:linear=true:print_format=summary")


def normalize(src: Path, dst: Path, *, target: float = -14.0, ffmpeg: str = "ffmpeg",
              log: LogFn | None = None, on_process: ProcessFn | None = None) -> dict[str, Any]:
    """Two-pass loudnorm src -> dst (48 kHz stereo 16-bit WAV). Returns the first-pass measurement."""
    err = _run_capture([ffmpeg, "-nostdin", "-hide_banner", "-i", str(src), "-af", loudnorm_filter(target),
                        "-f", "null", "-"], on_process)
    measured = parse_loudnorm_json(err)
    if log:
        log(f"loudnorm pass 1: I={measured.get('input_i')} TP={measured.get('input_tp')} LRA={measured.get('input_lra')}")
    silent = measured.get("input_i", -99.0) < SILENCE_LUFS
    af = f"aresample={SAMPLE_RATE}" if silent else f"{loudnorm_filter(target, measured)},aresample={SAMPLE_RATE}"
    run_ffmpeg([ffmpeg, "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(src),
                "-af", af, "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s16le", str(dst)],
               log=log, on_process=on_process)
    result: dict[str, Any] = {"measured": measured, "silent": silent}
    if not silent:
        result["corrected"] = _correct(dst, target, ffmpeg=ffmpeg, log=log, on_process=on_process)
    return result


CORRECT_TOLERANCE = 0.4  # LU


def correction_filter(gain_db: float) -> str:
    """Gain + a limiter run at 4x the sample rate (approximates a true-peak limit at TRUE_PEAK)."""
    limit = 10 ** ((TRUE_PEAK - 0.3) / 20)
    return (f"volume={gain_db:.2f}dB,aresample={SAMPLE_RATE * 4},"
            f"alimiter=limit={limit:.4f}:attack=2:release=60:level=disabled,aresample={SAMPLE_RATE}")


def _correct(path: Path, target: float, *, ffmpeg: str, log: LogFn | None, on_process: ProcessFn | None) -> float | None:
    """When loudnorm could not reach the target (it switches to dynamic mode when the linear gain
    would exceed the true-peak limit, and then lands ~1 LU low), apply the remaining gain with a
    limiter. Returns the gain applied (dB) or None."""
    got = measure(path, ffmpeg=ffmpeg, on_process=on_process).get("lufs")
    if got is None or abs(got - target) <= CORRECT_TOLERANCE:
        return None
    gain = target - got
    tmp = path.with_name(path.stem + "_corr.wav")
    run_ffmpeg([ffmpeg, "-y", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(path),
                "-af", correction_filter(gain), "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s16le", str(tmp)],
               log=log, on_process=on_process)
    tmp.replace(path)
    if log:
        log(f"loudness correction: {got:.1f} LUFS -> +{gain:.2f} dB with limiter")
    return round(gain, 2)


def measure(path: Path, *, ffmpeg: str = "ffmpeg", on_process: ProcessFn | None = None) -> dict[str, float | None]:
    """Integrated loudness (LUFS) and true peak (dBTP) of a file's audio, via ebur128."""
    err = _run_capture([ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
                        "-af", "ebur128=peak=true", "-f", "null", "-"], on_process)
    return parse_ebur128(err)


def parse_ebur128(stderr: str) -> dict[str, float | None]:
    summary = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else stderr
    i = re.search(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", summary)
    tp = re.search(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", summary)

    def num(m: re.Match[str] | None) -> float | None:
        if not m or m.group(1) == "-inf":
            return None
        return float(m.group(1))

    return {"lufs": num(i), "true_peak": num(tp)}
