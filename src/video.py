"""Compose the final video with FFmpeg (subprocess, argument lists only)."""

from __future__ import annotations

import functools
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

from . import BACKGROUNDS, OUTPUT

GAP_SECONDS = 0.3
W, H, FPS = 1080, 1920, 30
DEFAULT_BACKGROUND = "minecraft_parkour.mp4"
END_MARGIN = 2.0  # seconds kept free at the end of the background when picking a random start


class VideoError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise VideoError(f"{' and '.join(missing)} not found on PATH. Install FFmpeg (see README).")


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(
            cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no console flash under pythonw (GUI)
        ).stdout
    except subprocess.CalledProcessError as exc:
        tail = "\n".join((exc.stderr or "").strip().splitlines()[-15:])
        raise VideoError(f"{Path(cmd[0]).name} failed (exit {exc.returncode}):\n{tail}") from exc


def probe(path: Path) -> dict:
    return json.loads(_run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]))


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def list_backgrounds(folder: Path = BACKGROUNDS) -> list[Path]:
    return sorted(p for p in folder.glob("*") if p.suffix.lower() == ".mp4")


def pick_background(folder: Path = BACKGROUNDS) -> Path:
    """minecraft_parkour.mp4 if present, otherwise a random .mp4 from the folder."""
    videos = list_backgrounds(folder)
    if not videos:
        raise VideoError(f"No background video found. Add at least one .mp4 file to {folder}")
    preferred = [v for v in videos if v.name.lower() == DEFAULT_BACKGROUND]
    return preferred[0] if preferred else random.choice(videos)


def background_start(bg_duration: float, total: float) -> float | None:
    """Random seek point leaving END_MARGIN spare; None means the clip is too short and must loop."""
    if bg_duration < total:
        return None
    return random.uniform(0.0, max(0.0, bg_duration - total - END_MARGIN))


def sanitize_filename(title: str, max_len: int = 80) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:max_len].strip("-")
    return slug or "post"


def output_path(title: str, post_id: str, folder: Path = OUTPUT) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9]+", post_id):
        raise ValueError(f"Unsafe post id: {post_id!r}")
    return folder / f"{sanitize_filename(title)}-{post_id}.mp4"


def escape_filter_path(path: Path) -> str:
    """Escape a path for use as a filter option inside an FFmpeg filtergraph (two escaping levels)."""
    p = path.resolve().as_posix()
    p = re.sub(r"([\\':])", r"\\\1", p)  # option-value level
    return re.sub(r"([\\'\[\],;])", r"\\\1", p)  # filtergraph level


NVENC = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22", "-pix_fmt", "yuv420p"]
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]


@functools.cache
def nvenc_listed() -> bool:
    """FFmpeg was built with NVENC. It can still fail at runtime without an NVIDIA GPU/driver."""
    return "h264_nvenc" in _run(["ffmpeg", "-hide_banner", "-encoders"])


def build_command(background: Path, card: Path, title_audio: Path, body_audio: Path,
                  subtitles: Path, title_dur: float, total: float, out: Path,
                  bg_start: float | None = None, encoder: list[str] = X264) -> list[str]:
    """One filter graph: background → card overlay (title only) → subtitles; title + 0.3 s + body audio.
    bg_start seeks into the background before opening it (input -ss/-t: no decoding of earlier footage);
    None loops it instead."""
    bg_input = (["-stream_loop", "-1"] if bg_start is None
                else ["-ss", f"{bg_start:.3f}", "-t", f"{total:.3f}"])
    head = title_dur + GAP_SECONDS
    audio_fmt = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    graph = ";".join([
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}[bg]",
        f"[bg][1:v]overlay=0:0:enable='lt(t,{title_dur:.3f})'[ov]",
        f"[ov]subtitles=filename={escape_filter_path(subtitles)}[v]",
        # pad then trim so the title segment is exactly title_dur + 0.3 s, matching the subtitle offset
        f"[2:a]{audio_fmt},apad,atrim=end={head:.3f}[ta]",
        f"[3:a]{audio_fmt}[ba]",
        "[ta][ba]concat=n=2:v=0:a=1[a]",
    ])
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *bg_input, "-i", str(background),
        "-i", str(card),
        "-i", str(title_audio),
        "-i", str(body_audio),
        "-filter_complex", graph,
        "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
        *encoder, "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(out),
    ]


def render(card: Path, title_audio: Path, body_audio: Path, subtitles: Path,
           title_dur: float, body_dur: float, out: Path, background: Path | None = None) -> Path:
    require_ffmpeg()
    out.parent.mkdir(parents=True, exist_ok=True)
    total = title_dur + GAP_SECONDS + body_dur
    background = background or pick_background()
    bg_start = background_start(duration(background), total)
    args = (background, card, title_audio, body_audio, subtitles, title_dur, total, out, bg_start)
    if nvenc_listed():
        try:
            _run(build_command(*args, encoder=NVENC))
        except VideoError as exc:  # listed but unusable: no NVIDIA GPU, old driver, or busy encoder
            print(f"NVENC failed, falling back to libx264: {str(exc).splitlines()[-1]}")
            _run(build_command(*args, encoder=X264))
    else:
        _run(build_command(*args, encoder=X264))
    verify(out, total)
    return out


def verify(path: Path, expected_duration: float) -> None:
    """Check the rendered file with ffprobe; raise VideoError listing every problem."""
    if not path.exists() or path.stat().st_size == 0:
        raise VideoError(f"Output missing or empty: {path}")
    info = probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    problems = []
    if not v:
        problems.append("no video stream")
    else:
        if (v["width"], v["height"]) != (W, H):
            problems.append(f"resolution {v['width']}x{v['height']}")
        if v["codec_name"] != "h264":
            problems.append(f"video codec {v['codec_name']}")
        if v.get("pix_fmt") != "yuv420p":
            problems.append(f"pix_fmt {v.get('pix_fmt')}")
        if v.get("r_frame_rate") != f"{FPS}/1":
            problems.append(f"frame rate {v.get('r_frame_rate')}")
    if not a or a["codec_name"] != "aac":
        problems.append("missing AAC audio stream")
    actual = float(info["format"]["duration"])
    if abs(actual - expected_duration) > 0.5:
        problems.append(f"duration {actual:.2f}s, expected {expected_duration:.2f}s")
    if problems:
        raise VideoError(f"Output verification failed for {path.name}: " + "; ".join(problems))
