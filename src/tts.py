"""Edge TTS narration with word timings, and one-word ASS subtitle generation."""

from __future__ import annotations

import json
from pathlib import Path

import edge_tts

from .video import _run

DEFAULT_VOICE = "en-US-AvaMultilingualNeural"
DEFAULT_RATE = "+22%"
DEFAULT_PITCH = "+2Hz"
TICKS_PER_SECOND = 10_000_000  # Edge TTS offsets/durations are 100 ns ticks
LEAD_IN, TAIL = 0.05, 0.15  # seconds of audio kept around the first/last word when trimming
POP_IN = r"{\fscx108\fscy108\t(0,70,\fscx100\fscy100)}"

Word = dict  # {"text": str, "start": float, "duration": float} in seconds


async def synthesize(text: str, voice: str, audio_path: Path,
                     rate: str = DEFAULT_RATE, pitch: str = DEFAULT_PITCH) -> list[Word]:
    """Synthesize the whole text in one streaming pass, collect WordBoundary timings,
    then trim leading/trailing silence. Returned timings match the trimmed MP3."""
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    raw = audio_path.with_suffix(".raw.mp3")
    words: list[Word] = []
    # edge-tts >= 7 defaults to SentenceBoundary; word timings must be requested.
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    with raw.open("wb") as audio:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / TICKS_PER_SECOND,
                    "duration": chunk["duration"] / TICKS_PER_SECOND,
                })
    if raw.stat().st_size == 0:
        raise RuntimeError(f"Edge TTS produced no audio for {audio_path.name}")
    start, end = trim_window(words)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    _run(cmd + ["-c:a", "libmp3lame", "-q:a", "2", str(audio_path)])
    raw.unlink()
    words = shift_words(words, start)
    audio_path.with_suffix(".words.json").write_text(json.dumps(words, indent=1), encoding="utf-8")
    return words


def trim_window(words: list[Word]) -> tuple[float, float | None]:
    """(start, end) of the audio to keep: just around the spoken words. end=None keeps everything."""
    if not words:
        return 0.0, None
    last = words[-1]
    return max(0.0, words[0]["start"] - LEAD_IN), last["start"] + last["duration"] + TAIL


def shift_words(words: list[Word], seconds: float) -> list[Word]:
    return [{**w, "start": round(w["start"] - seconds, 4)} for w in words]


def ass_timestamp(seconds: float) -> str:
    """Seconds -> ASS 'H:MM:SS.cc'."""
    cs = max(0, round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,128,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,10,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words: list[Word], offset: float) -> str:
    """One centered word per WordBoundary cue, shifted by `offset` seconds.
    Each word stays until the next one starts, so cues never overlap."""
    lines = [ASS_HEADER]
    for i, w in enumerate(words):
        start = offset + w["start"]
        if i + 1 < len(words):
            end = offset + words[i + 1]["start"]
        else:
            end = start + max(w["duration"], 0.1)
        if round(end * 100) <= round(start * 100):
            continue  # sub-centisecond cue would collapse to zero length
        text = POP_IN + _ass_escape(w["text"])
        lines.append(f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Caption,,0,0,0,,{text}\n")
    return "".join(lines)


def write_ass(words: list[Word], offset: float, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(words, offset), encoding="utf-8")
    return path
