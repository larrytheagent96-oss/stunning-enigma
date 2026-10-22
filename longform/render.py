"""Turn a Project into one long video.

Every clip is first converted into a normalised segment (same resolution, frame
rate, pixel format and audio layout). Segments are cached, so changing one clip
in a long project only re-encodes that clip. The segments are then joined with
FFmpeg's concat demuxer without re-encoding the video, which keeps the final
step fast even for multi-hour outputs.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import ffmpeg
from .ffmpeg import Cancelled, FFmpegError, MediaInfo
from .project import Clip, Output, Project, ProjectError

CACHE_VERSION = "1"
DEFAULT_IMAGE_SECONDS = 5.0
DEFAULT_TITLE_SECONDS = 4.0
AUDIO_RATE = 48000

ProgressCallback = Callable[[float, str], None]


class RenderError(RuntimeError):
    pass


@dataclass
class Segment:
    index: int
    clip: Clip
    source: Path | None
    info: MediaInfo | None
    duration: float
    path: Path

    @property
    def label(self) -> str:
        if self.clip.chapter:
            return self.clip.chapter
        if self.clip.type == "title":
            return (self.clip.text or "Title").strip().splitlines()[0]
        return self.source.stem if self.source else f"Clip {self.index + 1}"


@dataclass
class Chapter:
    start: float
    end: float
    title: str


@dataclass
class RenderResult:
    output: Path
    duration: float
    chapters: list[Chapter]
    segments_rendered: int
    segments_cached: int

    def chapter_text(self) -> str:
        """Chapters as timestamps, the format YouTube and most players accept."""
        return "\n".join(f"{format_timestamp(c.start)} {c.title}" for c in self.chapters)


def format_timestamp(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def plan(project: Project, base_dir: Path, cache_dir: Path) -> list[Segment]:
    """Resolve sources, probe them and work out each clip's length."""
    project.validate()
    segments = []
    for i, clip in enumerate(project.clips):
        source = info = None
        if clip.type in ("video", "image"):
            source = (base_dir / os.path.expanduser(clip.path)).resolve()
            try:
                info = ffmpeg.probe(source)
            except FFmpegError as e:
                raise RenderError(f"clip {i + 1}: {e}") from e
        duration = _clip_duration(i, clip, info)
        key = _segment_key(clip, project.output, source, duration)
        segments.append(Segment(i, clip, source, info, duration, cache_dir / f"seg-{key}.mov"))
    return segments


def _clip_duration(i: int, clip: Clip, info: MediaInfo | None) -> float:
    where = f"clip {i + 1}"
    if clip.type == "title":
        return clip.duration or DEFAULT_TITLE_SECONDS
    if clip.type == "image" or (info and info.is_image):
        return clip.duration or DEFAULT_IMAGE_SECONDS
    assert info is not None
    if not info.has_video and not info.has_audio:
        raise RenderError(f"{where}: {clip.path} has no video or audio")
    end = clip.end
    if info.duration is not None:
        if clip.start >= info.duration:
            raise RenderError(f"{where}: start {clip.start}s is past the end of {clip.path} ({info.duration:.2f}s)")
        end = min(end, info.duration) if end is not None else info.duration
    if end is None:
        raise RenderError(f"{where}: cannot tell how long {clip.path} is; set an end time")
    duration = end - clip.start
    if clip.duration:
        duration = min(duration, clip.duration)
    return duration


def _segment_key(clip: Clip, output: Output, source: Path | None, duration: float) -> str:
    stat = source.stat() if source else None
    material = {
        "v": CACHE_VERSION,
        "clip": asdict(clip) | {"chapter": None, "path": str(source) if source else None},
        "duration": round(duration, 4),
        "source": [stat.st_size, stat.st_mtime_ns] if stat else None,
        "output": [output.width, output.height, str(output.fps), output.fit, output.background,
                   output.crf, output.preset],
    }
    return hashlib.sha1(json.dumps(material, sort_keys=True).encode()).hexdigest()[:20]


@functools.cache
def _drawtext_has_align() -> bool:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=drawtext"], capture_output=True, text=True)
    return "text_align" in result.stdout


def _fit_filter(o: Output) -> str:
    w, h = o.width, o.height
    if o.fit == "cover":
        fit = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    elif o.fit == "stretch":
        fit = f"scale={w}:{h}"
    else:
        fit = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={o.background}")
    return fit + ",setsar=1"


def _fades(prefix: str, clip: Clip, duration: float) -> list[str]:
    filters = []
    fade_in = min(clip.fade_in, duration / 2)
    fade_out = min(clip.fade_out, duration / 2)
    if fade_in > 0:
        filters.append(f"{prefix}fade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        filters.append(f"{prefix}fade=t=out:st={duration - fade_out:.3f}:d={fade_out:.3f}")
    return filters


def _segment_args(seg: Segment, o: Output, work: Path) -> list[str]:
    clip, d = seg.clip, seg.duration
    size, fps = f"{o.width}x{o.height}", str(o.fps)
    silence = ["-f", "lavfi", "-t", f"{d:.3f}", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]
    args: list[str] = []
    vf: list[str] = []
    audio_input = 1
    use_source_audio = False

    if clip.type == "title":
        color = f"color=c={clip.background}:s={size}:r={fps}:d={d:.3f}"
        args += ["-f", "lavfi", "-i", color, *silence]
        text_file = work / f"title-{seg.path.stem}.txt"
        text_file.write_text(clip.text.strip(), encoding="utf-8")
        font_size = clip.font_size or max(12, o.height // 12)
        drawtext = (f"drawtext=textfile={text_file.name}:fontcolor={clip.color}:fontsize={font_size}"
                    f":line_spacing={font_size // 3}:x=(w-text_w)/2:y=(h-text_h)/2")
        if _drawtext_has_align():
            drawtext += ":text_align=C"
        vf.append(drawtext)
    elif seg.info.is_image:
        args += ["-loop", "1", "-framerate", fps, "-t", f"{d:.3f}", "-i", str(seg.source), *silence]
        vf += [_fit_filter(o), f"fps={fps}"]
    else:
        trim = (["-ss", f"{clip.start:.3f}"] if clip.start > 0 else []) + ["-t", f"{d + 0.25:.3f}"]
        if seg.info.has_video:
            args += [*trim, "-i", str(seg.source)]
            vf += [_fit_filter(o), f"fps={fps}"]
            use_source_audio = seg.info.has_audio and not clip.mute
            if not use_source_audio:
                args += silence
        else:  # audio-only file: play it over a plain background
            args += ["-f", "lavfi", "-i", f"color=c={o.background}:s={size}:r={fps}:d={d:.3f}",
                     *trim, "-i", str(seg.source)]
            use_source_audio = not clip.mute
            if not use_source_audio:
                args += silence
                audio_input = 2

    vf += ["format=yuv420p", "tpad=stop_mode=clone:stop_duration=2", *_fades("", clip, d)]
    af = [f"aresample={AUDIO_RATE}", "aformat=sample_fmts=s16:channel_layouts=stereo"]
    if use_source_audio:
        audio_input = 0 if seg.info.has_video else 1
        if clip.volume != 1:
            af.append(f"volume={clip.volume:.3f}")
    af += ["apad", *_fades("a", clip, d)]

    return [
        *args,
        "-map", "0:v:0", "-map", f"{audio_input}:a:0",
        "-vf", ",".join(vf), "-af", ",".join(af),
        "-t", f"{d:.3f}",
        "-c:v", "libx264", "-preset", o.preset, "-crf", str(o.crf), "-pix_fmt", "yuv420p", "-r", fps,
        "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-video_track_timescale", "90000",
        "-f", "mov",
    ]


def _media_duration(path: Path) -> float:
    info = ffmpeg.probe(path)
    if info.duration is None:
        raise RenderError(f"could not read the length of {path}")
    return info.duration


def _chapters(project: Project, segments: list[Segment], durations: list[float]) -> list[Chapter]:
    if project.chapters == "none":
        return []
    starts, t = [], 0.0
    for d in durations:
        starts.append(t)
        t += d
    total = t
    marks = [(starts[s.index], s.label) for s in segments
             if project.chapters == "clips" or s.clip.chapter]
    if not marks:
        return []
    if marks[0][0] > 0.5:
        marks.insert(0, (0.0, "Start"))
    chapters = []
    for i, (start, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else total
        if end > start:
            chapters.append(Chapter(start, end, title))
    return chapters


def _escape_meta(value: str) -> str:
    for ch in ("\\", "=", ";", "#", "\n"):
        value = value.replace(ch, "\\" + ch)
    return value


def _write_metadata(path: Path, project: Project, chapters: list[Chapter]) -> None:
    lines = [";FFMETADATA1"]
    if project.output.title:
        lines.append(f"title={_escape_meta(project.output.title)}")
    for c in chapters:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(c.start * 1000)}",
                  f"END={round(c.end * 1000)}", f"title={_escape_meta(c.title)}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _audio_graph(project: Project, total: float) -> str | None:
    music, o = project.music, project.output
    if not music and not o.loudnorm:
        return None
    parts = []
    main = "[0:a]"
    if music:
        fade = min(music.fade_out, total / 2)
        m = (f"[2:a]aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
             f"volume={music.volume:.3f}")
        if fade > 0:
            m += f",afade=t=out:st={total - fade:.3f}:d={fade:.3f}"
        parts.append(m + "[music]")
        if music.duck:
            parts.append("[0:a]asplit=2[main][side]")
            parts.append("[music][side]sidechaincompress=threshold=0.03:ratio=8:attack=50:release=600[bed]")
            parts.append("[main][bed]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            parts.append("[0:a][music]amix=inputs=2:duration=first:normalize=0[mix]")
        main = "[mix]"
    if o.loudnorm:
        parts.append(f"{main}loudnorm=I=-16:TP=-1.5:LRA=11,aresample={AUDIO_RATE}[aout]")
    else:
        parts.append(f"{main}anull[aout]")
    return ";".join(parts)


def render(
    project: Project,
    output: str | os.PathLike,
    *,
    base_dir: str | os.PathLike = ".",
    cache_dir: str | os.PathLike | None = None,
    keep_cache: bool = True,
    jobs: int = 2,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> RenderResult:
    ffmpeg.require_tools()
    base_dir = Path(base_dir).resolve()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cache = Path(cache_dir).resolve() if cache_dir else output.parent / ".longform-cache"
    cache.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda fraction, message: None)
    o = project.output

    try:
        segments = plan(project, base_dir, cache)
    except ProjectError as e:
        raise RenderError(str(e)) from e
    total = sum(s.duration for s in segments)
    # Identical clips (same file, trim and settings) share one encoded segment.
    groups: dict[Path, list[Segment]] = {}
    for s in segments:
        groups.setdefault(s.path, []).append(s)
    done = {s.index: (s.duration if s.path.exists() else 0.0) for s in segments}
    to_encode = [group for path, group in groups.items() if not path.exists()]
    lock = threading.Lock()
    # Encoding the segments is most of the work; the final join is the last 10%.
    segment_share = 0.9

    def update(group: list[Segment], seconds: float, message: str) -> None:
        with lock:
            for s in group:
                done[s.index] = seconds
            fraction = segment_share * sum(done.values()) / total if total else 0
        report(fraction, message)

    def build(group: list[Segment]) -> None:
        seg = group[0]
        tmp = seg.path.with_name(seg.path.stem + ".partial.mov")
        label = f"Encoding clip {seg.index + 1}/{len(segments)}: {seg.label}"
        update(group, 0.0, label)
        try:
            ffmpeg.run(
                [*_segment_args(seg, o, cache), str(tmp)],
                duration=seg.duration, cancel=cancel, cwd=cache,
                on_progress=lambda s: update(group, s, label),
            )
        except Cancelled:
            tmp.unlink(missing_ok=True)
            raise
        except FFmpegError as e:
            tmp.unlink(missing_ok=True)
            raise RenderError(f"clip {seg.index + 1} ({seg.label}): {e}") from e
        tmp.replace(seg.path)
        update(group, seg.duration, label)

    cached = len(groups) - len(to_encode)
    report(0.0, f"{len(segments)} clips, {format_timestamp(total)} total ({cached} already encoded)")
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(build, g) for g in to_encode]
        try:
            for f in futures:
                f.result()
        except BaseException:
            for f in futures:
                f.cancel()
            raise

    lengths = {path: _media_duration(path) for path in groups}
    durations = [lengths[s.path] for s in segments]
    total = sum(durations)
    chapters = _chapters(project, segments, durations)

    job_dir = cache / f"job-{os.getpid()}-{threading.get_ident()}"
    job_dir.mkdir(exist_ok=True)
    try:
        concat_list = job_dir / "list.txt"
        concat_list.write_text(
            "".join("file '" + str(s.path).replace("'", "'\\''") + "'\n" for s in segments), encoding="utf-8")
        metadata = job_dir / "metadata.txt"
        _write_metadata(metadata, project, chapters)

        args = ["-f", "concat", "-safe", "0", "-i", str(concat_list), "-f", "ffmetadata", "-i", str(metadata)]
        if project.music:
            music_path = (base_dir / os.path.expanduser(project.music.path)).resolve()
            if not music_path.is_file():
                raise RenderError(f"music file not found: {project.music.path}")
            args += ["-stream_loop", "-1", "-i", str(music_path)]
        args += ["-map", "0:v:0"]
        graph = _audio_graph(project, total)
        if graph:
            args += ["-filter_complex", graph, "-map", "[aout]"]
        else:
            args += ["-map", "0:a:0"]
        args += ["-map_metadata", "1", "-map_chapters", "1" if chapters else "-1",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", o.audio_bitrate, "-ar", str(AUDIO_RATE)]
        if output.suffix.lower() in (".mp4", ".m4v", ".mov"):
            args += ["-movflags", "+faststart"]
        partial = output.with_name(output.stem + ".partial" + output.suffix)
        message = "Joining everything into the final video"
        report(segment_share, message)
        try:
            ffmpeg.run(
                [*args, str(partial)], duration=total, cancel=cancel,
                on_progress=lambda s: report(segment_share + (1 - segment_share) * s / total, message),
            )
        except Cancelled:
            partial.unlink(missing_ok=True)
            raise
        except FFmpegError as e:
            partial.unlink(missing_ok=True)
            raise RenderError(f"joining failed: {e}") from e
        partial.replace(output)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        if not keep_cache:
            for path in groups:
                path.unlink(missing_ok=True)
            for leftover in cache.glob("title-*.txt"):
                leftover.unlink(missing_ok=True)
            try:
                cache.rmdir()
            except OSError:
                pass

    report(1.0, f"Done: {output.name} ({format_timestamp(total)})")
    return RenderResult(output, total, chapters, len(to_encode), cached)
