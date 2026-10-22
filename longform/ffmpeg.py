"""Thin wrappers around the ffmpeg and ffprobe command-line tools."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .project import IMAGE_EXTENSIONS


class FFmpegError(RuntimeError):
    pass


class Cancelled(FFmpegError):
    pass


def require_tools() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise FFmpegError(
            f"{' and '.join(missing)} not found on PATH. Install FFmpeg (https://ffmpeg.org/download.html)."
        )


@dataclass
class MediaInfo:
    path: str
    duration: float | None
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    is_image: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _parse_rate(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    num, _, den = rate.partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: str | Path) -> MediaInfo:
    path = str(path)
    if not Path(path).is_file():
        raise FFmpegError(f"file not found: {path}")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise FFmpegError(f"cannot read {path}: {result.stderr.strip() or 'ffprobe failed'}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next(
        (s for s in streams if s.get("codec_type") == "video"
         and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    format_name = fmt.get("format_name", "")
    is_image = Path(path).suffix.lower() in IMAGE_EXTENSIONS or format_name.endswith("_pipe") or format_name == "image2"
    if Path(path).suffix.lower() == ".gif":
        is_image = False  # animated GIFs behave like video
    duration = None
    for source in (fmt, video or {}, audio or {}):
        try:
            duration = float(source["duration"])
            break
        except (KeyError, TypeError, ValueError):
            continue
    width = height = None
    if video:
        width, height = video.get("width"), video.get("height")
        rotation = _rotation(video)
        if rotation in (90, 270) and width and height:
            width, height = height, width
    return MediaInfo(
        path=path,
        duration=None if is_image else duration,
        has_video=video is not None,
        has_audio=audio is not None,
        width=width,
        height=height,
        fps=_parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")) if video else None,
        is_image=is_image,
    )


def _rotation(stream: dict) -> int:
    try:
        rotate = int(stream.get("tags", {}).get("rotate", 0))
    except ValueError:
        rotate = 0
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            rotate = int(side["rotation"])
    return abs(rotate) % 360


def run(
    args: list[str],
    *,
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
    cwd: str | Path | None = None,
) -> None:
    """Run ffmpeg, reporting progress as seconds of output written."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", *args]
    stderr_lines: list[str] = []
    cancelled = threading.Event()
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd) as proc:
        stderr_thread = threading.Thread(target=lambda: stderr_lines.extend(proc.stderr), daemon=True)
        stderr_thread.start()
        if cancel is not None:
            def watch() -> None:
                while proc.poll() is None:
                    if cancel.wait(0.2):
                        cancelled.set()
                        proc.kill()
                        return
            threading.Thread(target=watch, daemon=True).start()

        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and on_progress and value.isdigit():
                seconds = int(value) / 1_000_000
                on_progress(min(seconds, duration) if duration else seconds)
        proc.wait()
        stderr_thread.join(timeout=5)
    if cancelled.is_set():
        raise Cancelled("render cancelled")
    if proc.returncode != 0:
        message = "".join(stderr_lines).strip() or f"ffmpeg exited with code {proc.returncode}"
        raise FFmpegError(message[-2000:])
