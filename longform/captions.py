"""Automatic captions: speech-to-text with faster-whisper, cached per source file.

A transcript is a list of short caption lines with times in the *source file's*
timeline, so a clip can be trimmed or re-used without transcribing again. When a
video is rendered, the lines that fall inside each piece are written as an ASS
subtitle file and burned in with FFmpeg's subtitles filter.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

FONTS_DIR = Path(__file__).parent / "fonts"

# How each look is drawn. Sizes are fractions of the shorter side of the video,
# so captions look the same in landscape, vertical and square exports.
CAPTION_STYLES = {
    "clean": {"label": "Clean", "font": "Inter", "bold": True, "size": 0.052, "color": "&H00FFFFFF",
              "outline": 0.0045, "shadow": 0.002, "box": False, "upper": False, "position": 0.07},
    "bold": {"label": "Bold pop", "font": "Inter Black", "bold": False, "size": 0.072, "color": "&H0000E5FF",
             "outline": 0.008, "shadow": 0.0, "box": False, "upper": True, "position": 0.22},
    "boxed": {"label": "Boxed", "font": "Inter", "bold": True, "size": 0.046, "color": "&H00FFFFFF",
              "outline": 0.009, "shadow": 0.0, "box": True, "upper": False, "position": 0.06},
}
CAPTION_SIZES = {"small": 0.8, "medium": 1.0, "large": 1.25}
WHISPER_MODELS = ("tiny", "base", "small")

MAX_LINE_CHARS = 36
MAX_LINE_WORDS = 7
MAX_PAUSE = 0.7  # a silence longer than this starts a new caption

ProgressCallback = Callable[[float, str], None]


class CaptionError(RuntimeError):
    pass


def captions_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


# --- transcription ------------------------------------------------------------

def group_words(words: list[dict]) -> list[dict]:
    """Turn word timings into short caption lines that are easy to read on screen."""
    lines: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if current:
            text = " ".join(w["word"].strip() for w in current).strip()
            if text:
                lines.append({"start": round(current[0]["start"], 3), "end": round(current[-1]["end"], 3),
                              "text": text})
            current.clear()

    for word in words:
        if not word["word"].strip():
            continue
        if current:
            text_len = sum(len(w["word"].strip()) + 1 for w in current) + len(word["word"].strip())
            pause = word["start"] - current[-1]["end"]
            if text_len > MAX_LINE_CHARS or len(current) >= MAX_LINE_WORDS or pause > MAX_PAUSE:
                flush()
        current.append(word)
        if re.search(r"[.?!]$", word["word"].strip()) and len(current) >= 3:
            flush()
    flush()
    # Keep each caption up until the next one starts (if the gap is small) so text doesn't flicker.
    for a, b in zip(lines, lines[1:]):
        if 0 < b["start"] - a["end"] < 0.4:
            a["end"] = b["start"]
    return lines


def whisper_transcribe(
    path: Path,
    *,
    model: str = "base",
    language: str | None = None,
    models_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    """Transcribe a media file with faster-whisper (runs locally on the CPU)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise CaptionError("Captions need the speech engine. Install it with: pip install faster-whisper") from e
    report = progress or (lambda f, m: None)
    report(0.0, f"Loading the speech model ({model}); the first time it downloads, which can take a minute")
    try:
        whisper = WhisperModel(model, device="cpu", compute_type="int8",
                               download_root=str(models_dir) if models_dir else None)
    except Exception as e:  # network problems, disk full, ...
        raise CaptionError(
            "Couldn't get the speech model that captions need. It's downloaded once (about 150 MB), "
            f"so check your internet connection and try again. ({e})") from e
    segments, info = whisper.transcribe(str(path), language=language or None, word_timestamps=True,
                                        vad_filter=True, beam_size=5)
    words = []
    total = info.duration or 0
    for segment in segments:
        if cancel is not None and cancel.is_set():
            from .ffmpeg import Cancelled
            raise Cancelled("cancelled")
        for w in segment.words or []:
            words.append({"start": w.start, "end": w.end, "word": w.word})
        if total:
            report(min(segment.end / total, 1.0), f"Transcribing {path.name}")
    return {"language": info.language, "lines": group_words(words), "edited": False}


Transcriber = Callable[..., dict]


class CaptionStore:
    """Transcripts on disk, one JSON file per source file (keyed by path, size and date)."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def key(self, source: Path) -> str:
        source = Path(source).resolve()
        stat = source.stat()
        raw = f"{source}|{stat.st_size}|{stat.st_mtime_ns}"
        return hashlib.sha1(raw.encode()).hexdigest()[:20]

    def _file(self, source: Path) -> Path:
        return self.dir / f"{self.key(source)}.json"

    def load(self, source: Path) -> dict | None:
        f = self._file(source)
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def save(self, source: Path, data: dict) -> dict:
        lines = []
        for line in data.get("lines", []):
            text = str(line.get("text", "")).strip()
            start, end = float(line["start"]), float(line["end"])
            if text and end > start:
                lines.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        lines.sort(key=lambda l: l["start"])
        clean = {"language": data.get("language"), "lines": lines, "edited": bool(data.get("edited")),
                 "source": str(Path(source).resolve())}
        tmp = self._file(source).with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, indent=1), encoding="utf-8")
        tmp.replace(self._file(source))
        return clean

    def get_or_transcribe(self, source: Path, transcriber: Transcriber, **kwargs) -> dict:
        with self._guard:
            lock = self._locks.setdefault(self.key(source), threading.Lock())
        with lock:  # never transcribe the same file twice at once
            existing = self.load(source)
            if existing is not None:
                return existing
            return self.save(source, transcriber(Path(source), **kwargs))


# --- rendering ----------------------------------------------------------------

def _ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(text: str, upper: bool) -> str:
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    text = re.sub(r"\s*\n\s*", r"\\N", text.strip())
    return text.upper() if upper else text


def lines_in_window(lines: list[dict], start: float, length: float) -> list[dict]:
    """Caption lines that overlap [start, start + length], moved so the window starts at 0."""
    out = []
    for line in lines:
        a, b = line["start"] - start, line["end"] - start
        if b <= 0.05 or a >= length - 0.05:
            continue
        out.append({"start": max(0.0, a), "end": min(length, b), "text": line["text"]})
    return out


def ass_document(lines: list[dict], width: int, height: int, style: str = "clean", size: str = "medium") -> str:
    s = CAPTION_STYLES.get(style, CAPTION_STYLES["clean"])
    unit = min(width, height)
    font_size = round(unit * s["size"] * CAPTION_SIZES.get(size, 1.0))
    outline = max(1, round(unit * s["outline"]))
    shadow = round(unit * s["shadow"])
    margin_v = round(height * s["position"])
    margin_h = round(width * 0.06)
    border_style = 3 if s["box"] else 1
    outline_colour = "&H64000000" if s["box"] else "&H00000000"
    back_colour = "&H64000000" if s["box"] else "&H80000000"
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{s['font']},{font_size},{s['color']},&H000000FF,{outline_colour},{back_colour},"
        f"{-1 if s['bold'] else 0},0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,"
        f"{margin_h},{margin_h},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = "".join(
        f"Dialogue: 0,{_ass_time(l['start'])},{_ass_time(l['end'])},Default,,0,0,0,,{_ass_text(l['text'], s['upper'])}\n"
        for l in lines
    )
    return header + events


def _srt_time(t: float) -> str:
    ms = max(0, round(t * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_document(lines: list[dict]) -> str:
    return "".join(
        f"{i}\n{_srt_time(l['start'])} --> {_srt_time(l['end'])}\n{l['text'].strip()}\n\n"
        for i, l in enumerate(sorted(lines, key=lambda l: l["start"]), 1)
    )


def install_fonts(directory: Path) -> Path:
    """Copy the bundled fonts next to the files FFmpeg renders, so it never depends on system fonts."""
    target = Path(directory) / "fonts"
    target.mkdir(parents=True, exist_ok=True)
    for font in FONTS_DIR.glob("*.otf"):
        dest = target / font.name
        if not dest.exists() or dest.stat().st_size != font.stat().st_size:
            shutil.copyfile(font, dest)
    return target


def default_models_dir() -> Path:
    return Path(os.environ.get("LONGFORM_MODELS", Path.home() / ".cache" / "longform" / "models"))
