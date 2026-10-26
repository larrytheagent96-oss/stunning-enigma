"""Turn a Project into one long video.

The timeline is cut into pieces: the body of each clip and, where clips
crossfade, a short transition piece made from the end of one clip and the start
of the next. Every piece is encoded to the same resolution, frame rate, pixel
format and audio layout, and cached, so changing one clip in a long project only
re-encodes the pieces it touches. The pieces are then joined with FFmpeg's
concat demuxer without re-encoding the video, which keeps the final step fast
even for multi-hour outputs.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import math
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

from . import ffmpeg
from .ffmpeg import Cancelled, FFmpegError, MediaInfo
from .project import Clip, Output, Project, ProjectError

CACHE_VERSION = "2"
DEFAULT_IMAGE_SECONDS = 5.0
DEFAULT_TITLE_SECONDS = 4.0
AUDIO_RATE = 48000
MIN_PIECE_SECONDS = 0.02  # anything shorter than this is dropped rather than encoded

ProgressCallback = Callable[[float, str], None]


class RenderError(RuntimeError):
    pass


@dataclass
class ClipPlan:
    index: int
    clip: Clip
    source: Path | None
    info: MediaInfo | None
    duration: float
    transition_in: float = 0.0  # seconds overlapped with the previous clip
    transition_out: float = 0.0  # seconds overlapped with the next clip

    @property
    def label(self) -> str:
        if self.clip.chapter:
            return self.clip.chapter
        if self.clip.type == "title":
            return (self.clip.text or "Title").strip().splitlines()[0]
        return self.source.stem if self.source else f"Clip {self.index + 1}"


@dataclass
class Part:
    """A window of one clip: `length` seconds starting `offset` seconds into it."""
    clip: ClipPlan
    offset: float
    length: float


@dataclass
class Piece:
    kind: str  # "body" or "transition"
    parts: list[Part]  # one part for a body, outgoing then incoming clip for a transition
    length: float
    path: Path
    style: str | None = None
    fade_in: float = 0.0
    fade_out: float = 0.0

    @property
    def starts_clip(self) -> int | None:
        """Index of the clip that first appears in this piece, if any."""
        if self.kind == "transition":
            return self.parts[1].clip.index
        plan = self.parts[0].clip
        return plan.index if plan.transition_in == 0 else None

    @property
    def label(self) -> str:
        if self.kind == "transition":
            a, b = self.parts[0].clip, self.parts[1].clip
            return f"Crossfading clip {a.index + 1} into {b.index + 1}"
        return f"Encoding clip {self.parts[0].clip.index + 1}: {self.parts[0].clip.label}"


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


def plan(project: Project, base_dir: Path) -> list[ClipPlan]:
    """Resolve sources, probe them and work out each clip's length and transitions."""
    project.validate()
    plans = []
    for i, clip in enumerate(project.clips):
        source = info = None
        if clip.type in ("video", "image"):
            source = (base_dir / os.path.expanduser(clip.path)).resolve()
            try:
                info = ffmpeg.probe(source)
            except FFmpegError as e:
                raise RenderError(f"clip {i + 1}: {e}") from e
        plans.append(ClipPlan(i, clip, source, info, _clip_duration(i, clip, info)))
    # Work in whole frames so every piece's audio is exactly as long as its video;
    # otherwise rounding gaps add up over hundreds of pieces and the sound drifts.
    fps = frame_rate(project.output)
    frames = [max(1, round(p.duration * fps)) for p in plans]
    for p, n in zip(plans, frames):
        p.duration = float(n / fps)
    for (prev, a), (cur, b) in zip(zip(plans, frames), zip(plans[1:], frames[1:])):
        wanted = cur.clip.transition if cur.clip.transition is not None else project.transition
        # A clip can give at most half its length to each neighbour, so its body never goes negative.
        overlap = min(math.floor(wanted * fps), a // 2, b // 2)
        if overlap / fps >= MIN_PIECE_SECONDS:
            prev.transition_out = cur.transition_in = float(overlap / fps)
    return plans


def frame_rate(o: Output) -> Fraction:
    num, _, den = str(o.fps).partition("/")
    return Fraction(num) / Fraction(den or 1)


def pieces_for(project: Project, plans: list[ClipPlan], cache_dir: Path) -> list[Piece]:
    """Lay the clips out as bodies and transition pieces, in playing order."""
    o = project.output
    pieces = []
    for p in plans:
        if p.transition_in:
            prev = plans[p.index - 1]
            style = p.clip.transition_style or project.transition_style
            t = p.transition_in
            pieces.append(_piece("transition", [Part(prev, prev.duration - t, t), Part(p, 0.0, t)], o, cache_dir,
                                 style=style))
        body = p.duration - p.transition_in - p.transition_out
        if body >= MIN_PIECE_SECONDS:
            # A clip's own fade only applies to an edge that isn't crossfading.
            fade_in = 0.0 if p.transition_in else min(p.clip.fade_in, body / 2)
            fade_out = 0.0 if p.transition_out else min(p.clip.fade_out, body / 2)
            pieces.append(_piece("body", [Part(p, p.transition_in, body)], o, cache_dir,
                                 fade_in=fade_in, fade_out=fade_out))
    return pieces


def _piece(kind: str, parts: list[Part], o: Output, cache_dir: Path, **extra) -> Piece:
    length = parts[-1].length
    material = {
        "v": CACHE_VERSION,
        "kind": kind,
        "length": round(length, 4),
        "parts": [_part_material(part) for part in parts],
        "output": [o.width, o.height, str(o.fps), o.fit, o.background, o.crf, o.preset],
        **{k: round(v, 4) if isinstance(v, float) else v for k, v in extra.items()},
    }
    key = hashlib.sha1(json.dumps(material, sort_keys=True).encode()).hexdigest()[:20]
    return Piece(kind, parts, length, cache_dir / f"seg-{key}.mov", **extra)


def _part_material(part: Part) -> dict:
    p = part.clip
    stat = p.source.stat() if p.source else None
    ignored = {"chapter": None, "transition": None, "transition_style": None, "fade_in": None, "fade_out": None}
    return {
        "clip": asdict(p.clip) | ignored | {"path": str(p.source) if p.source else None},
        "source": [stat.st_size, stat.st_mtime_ns] if stat else None,
        "offset": round(part.offset, 4),
        "length": round(part.length, 4),
    }


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


def _part_chain(part: Part, first_input: int, tag: str, o: Output, work: Path) -> tuple[list[str], list[str]]:
    """Inputs and filters that turn one part into normalised streams labelled [v<tag>] and [a<tag>]."""
    p, clip, length = part.clip, part.clip.clip, part.length
    size, fps = f"{o.width}x{o.height}", str(o.fps)
    silence = ["-f", "lavfi", "-t", f"{length:.6f}", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]
    args: list[str] = []
    vf: list[str] = []
    video_input, audio_input = 0, 1  # relative to first_input
    use_source_audio = False

    if clip.type == "title":
        args += ["-f", "lavfi", "-i", f"color=c={clip.background}:s={size}:r={fps}:d={length:.6f}", *silence]
        text = clip.text.strip()
        text_file = work / f"title-{hashlib.sha1(text.encode()).hexdigest()[:16]}.txt"
        text_file.write_text(text, encoding="utf-8")
        font_size = clip.font_size or max(12, o.height // 12)
        drawtext = (f"drawtext=textfile={text_file.name}:fontcolor={clip.color}:fontsize={font_size}"
                    f":line_spacing={font_size // 3}:x=(w-text_w)/2:y=(h-text_h)/2")
        if _drawtext_has_align():
            drawtext += ":text_align=C"
        vf.append(drawtext)
    elif p.info.is_image:
        args += ["-loop", "1", "-framerate", fps, "-t", f"{length:.6f}", "-i", str(p.source), *silence]
        vf.append(_fit_filter(o))
    else:
        seek = clip.start + part.offset
        trim = (["-ss", f"{seek:.6f}"] if seek > 0 else []) + ["-t", f"{length + 0.25:.6f}"]
        use_source_audio = p.info.has_audio and not clip.mute
        if p.info.has_video:
            args += [*trim, "-i", str(p.source)]
            vf.append(_fit_filter(o))
            audio_input = 0 if use_source_audio else 1
        else:  # audio-only file: play it over a plain background
            args += ["-f", "lavfi", "-i", f"color=c={o.background}:s={size}:r={fps}:d={length:.6f}",
                     *trim, "-i", str(p.source)]
            audio_input = 1 if use_source_audio else 2
        if not use_source_audio:
            args += silence

    # Every chain ends with the same frame rate, timebase, format and exact length, so
    # pieces can be crossfaded with each other and joined without re-encoding.
    vf += [f"fps={fps}", "format=yuv420p", "tpad=stop_mode=clone:stop_duration=2",
           f"trim=duration={length:.6f}", "setpts=PTS-STARTPTS"]
    af = [f"aresample={AUDIO_RATE}", "aformat=sample_fmts=s16:channel_layouts=stereo"]
    if use_source_audio and clip.volume != 1:
        af.append(f"volume={clip.volume:.6f}")
    af += ["apad", f"atrim=duration={length:.6f}", "asetpts=PTS-STARTPTS"]
    filters = [
        f"[{first_input + video_input}:v]{','.join(vf)}[v{tag}]",
        f"[{first_input + audio_input}:a]{','.join(af)}[a{tag}]",
    ]
    return args, filters


def _count_inputs(args: list[str]) -> int:
    return sum(1 for a in args if a == "-i")


def _piece_args(piece: Piece, o: Output, work: Path) -> list[str]:
    inputs: list[str] = []
    graph: list[str] = []
    for n, part in enumerate(piece.parts):
        args, filters = _part_chain(part, _count_inputs(inputs), str(n), o, work)
        inputs += args
        graph += filters
    length = piece.length
    if piece.kind == "transition":
        graph.append(f"[v0][v1]xfade=transition={piece.style}:duration={length:.6f}:offset=0[vout]")
        graph.append(f"[a0][a1]acrossfade=d={length:.6f}[aout]")
    else:
        vfades, afades = [], []
        if piece.fade_in > 0:
            vfades.append(f"fade=t=in:st=0:d={piece.fade_in:.6f}")
            afades.append(f"afade=t=in:st=0:d={piece.fade_in:.6f}")
        if piece.fade_out > 0:
            st = length - piece.fade_out
            vfades.append(f"fade=t=out:st={st:.6f}:d={piece.fade_out:.6f}")
            afades.append(f"afade=t=out:st={st:.6f}:d={piece.fade_out:.6f}")
        graph.append(f"[v0]{','.join(vfades) or 'null'}[vout]")
        graph.append(f"[a0]{','.join(afades) or 'anull'}[aout]")
    return [
        *inputs,
        "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{length:.6f}",
        "-c:v", "libx264", "-preset", o.preset, "-crf", str(o.crf), "-pix_fmt", "yuv420p", "-r", str(o.fps),
        "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-video_track_timescale", "90000",
        "-f", "mov",
    ]


def _media_duration(path: Path) -> float:
    info = ffmpeg.probe(path)
    if info.duration is None:
        raise RenderError(f"could not read the length of {path}")
    return info.duration


def _chapters(project: Project, plans: list[ClipPlan], pieces: list[Piece], lengths: list[float]) -> list[Chapter]:
    if project.chapters == "none":
        return []
    clip_starts: dict[int, float] = {}
    t = 0.0
    for piece, length in zip(pieces, lengths):
        if piece.starts_clip is not None:
            clip_starts.setdefault(piece.starts_clip, t)
        t += length
    total = t
    marks = [(clip_starts[p.index], p.label) for p in plans
             if p.index in clip_starts and (project.chapters == "clips" or p.clip.chapter)]
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
    main = "[2:a]"
    if music:
        fade = min(music.fade_out, total / 2)
        m = (f"[3:a]aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
             f"volume={music.volume:.6f}")
        if fade > 0:
            m += f",afade=t=out:st={total - fade:.6f}:d={fade:.6f}"
        parts.append(m + "[music]")
        if music.duck:
            parts.append("[2:a]asplit=2[main][side]")
            parts.append("[music][side]sidechaincompress=threshold=0.03:ratio=8:attack=50:release=600[bed]")
            parts.append("[main][bed]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            parts.append("[2:a][music]amix=inputs=2:duration=first:normalize=0[mix]")
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
        plans = plan(project, base_dir)
    except ProjectError as e:
        raise RenderError(str(e)) from e
    pieces = pieces_for(project, plans, cache)
    total = sum(p.length for p in pieces)
    # Identical pieces (same file, trim and settings) are encoded once and reused.
    groups: dict[Path, list[int]] = {}
    for n, piece in enumerate(pieces):
        groups.setdefault(piece.path, []).append(n)
    done = {n: (piece.length if piece.path.exists() else 0.0) for n, piece in enumerate(pieces)}
    to_encode = [group for path, group in groups.items() if not path.exists()]
    lock = threading.Lock()
    # Encoding the pieces is most of the work; the final join is the last 10%.
    segment_share = 0.9

    def update(group: list[int], seconds: float, message: str) -> None:
        with lock:
            for n in group:
                done[n] = seconds
            fraction = segment_share * sum(done.values()) / total if total else 0
        report(fraction, message)

    def build(group: list[int]) -> None:
        piece = pieces[group[0]]
        tmp = piece.path.with_name(piece.path.stem + ".partial.mov")
        label = f"{piece.label} (of {len(plans)})"
        update(group, 0.0, label)
        try:
            ffmpeg.run(
                [*_piece_args(piece, o, cache), str(tmp)],
                duration=piece.length, cancel=cancel, cwd=cache,
                on_progress=lambda s: update(group, s, label),
            )
        except Cancelled:
            tmp.unlink(missing_ok=True)
            raise
        except FFmpegError as e:
            tmp.unlink(missing_ok=True)
            clips = " and ".join(f"clip {part.clip.index + 1} ({part.clip.label})" for part in piece.parts)
            raise RenderError(f"{clips}: {e}") from e
        tmp.replace(piece.path)
        update(group, piece.length, label)

    cached = len(groups) - len(to_encode)
    transitions = sum(1 for p in pieces if p.kind == "transition")
    report(0.0, f"{len(plans)} clips, {transitions} transitions, {format_timestamp(total)} total "
                f"({cached} pieces already encoded)")
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(build, g) for g in to_encode]
        try:
            for f in futures:
                f.result()
        except BaseException:
            for f in futures:
                f.cancel()
            raise

    measured = {path: _media_duration(path) for path in groups}
    lengths = [measured[p.path] for p in pieces]
    total = sum(lengths)
    chapters = _chapters(project, plans, pieces, lengths)

    job_dir = cache / f"job-{os.getpid()}-{threading.get_ident()}"
    job_dir.mkdir(exist_ok=True)
    try:
        concat_list = job_dir / "list.txt"
        concat_list.write_text(
            "".join("file '" + str(p.path).replace("'", "'\\''") + "'\n" for p in pieces), encoding="utf-8")
        metadata = job_dir / "metadata.txt"
        _write_metadata(metadata, project, chapters)

        concat = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
        # Inputs: 0 = pieces (video), 1 = chapters, 2 = pieces again (audio), 3 = music.
        # The audio is read separately because when one input feeds both a stream copy and a
        # filter graph, FFmpeg can drop the last few audio frames once the video copy finishes.
        args = [*concat, "-f", "ffmetadata", "-i", str(metadata), *concat]
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
            args += ["-map", "2:a:0"]
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
