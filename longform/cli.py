"""Command-line interface: `longform join`, `init`, `render`, `probe` and `ui`."""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__, ffmpeg, presets
from .captions import CAPTION_STYLES, default_models_dir
from .ffmpeg import Cancelled, FFmpegError
from .project import TRANSITION_STYLES, Clip, Music, Project, ProjectError, clip_type_for, is_media_file
from .render import RenderError, format_timestamp, render


def natural_key(path: Path) -> list:
    """Sort 'clip2' before 'clip10'."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def expand_inputs(inputs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for item in inputs:
        p = Path(item).expanduser()
        if p.is_dir():
            found = sorted((f for f in p.iterdir() if f.is_file() and is_media_file(f)), key=natural_key)
            if not found:
                raise ProjectError(f"no video or image files in {p}")
            paths += found
        elif p.is_file():
            paths.append(p)
        else:
            raise ProjectError(f"not found: {item}")
    return paths


def parse_size(value: str) -> tuple[int, int]:
    presets = {"4k": (3840, 2160), "2160p": (3840, 2160), "1440p": (2560, 1440), "1080p": (1920, 1080),
               "720p": (1280, 720), "480p": (854, 480), "vertical": (1080, 1920), "square": (1080, 1080)}
    if value.lower() in presets:
        return presets[value.lower()]
    match = re.fullmatch(r"(\d+)[xX:](\d+)", value)
    if not match:
        raise argparse.ArgumentTypeError(f"size must look like 1920x1080 or one of {', '.join(presets)}")
    return int(match[1]), int(match[2])


def add_output_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--size", type=parse_size, help="output size, e.g. 1920x1080, 720p, 4k, vertical")
    p.add_argument("--fps", help="output frame rate (default 30)")
    p.add_argument("--fit", choices=["contain", "cover", "stretch"],
                   help="contain = letterbox (default), cover = crop to fill, stretch = distort")
    p.add_argument("--crf", type=int, help="quality, lower is better (default 20)")
    p.add_argument("--preset", help="x264 speed preset, e.g. veryfast, medium, slow")
    p.add_argument("--loudnorm", action="store_true", default=None, help="normalise loudness to -16 LUFS")
    p.add_argument("--crossfade", type=float, metavar="SECONDS",
                   help="crossfade between every pair of clips (0 = straight cuts)")
    p.add_argument("--transition", choices=TRANSITION_STYLES, metavar="STYLE",
                   help=f"crossfade style: {', '.join(TRANSITION_STYLES)} (default fade)")
    p.add_argument("--template", choices=[t["id"] for t in presets.TEMPLATES],
                   help="one-click look: " + ", ".join(t["id"] for t in presets.TEMPLATES))
    p.add_argument("--export", choices=[e["id"] for e in presets.EXPORTS],
                   help="size and quality for a platform: " + ", ".join(e["id"] for e in presets.EXPORTS))
    p.add_argument("--captions", action="store_true", default=None,
                   help="add automatic captions (needs: pip install faster-whisper)")
    p.add_argument("--caption-style", choices=list(CAPTION_STYLES))


def apply_output_options(project: Project, args: argparse.Namespace) -> Project:
    """Apply --template and --export first, then any individual settings on top."""
    if args.template or args.export:
        data = project.to_dict()
        if args.template:
            data = presets.apply(data, presets.find(presets.TEMPLATES, args.template))
        if args.export:
            data = presets.apply(data, presets.find(presets.EXPORTS, args.export))
        project = Project.from_dict(data)
    if args.captions:
        project.captions.enabled = True
    if args.caption_style:
        project.captions.style = args.caption_style
    if args.crossfade is not None:
        project.transition = args.crossfade
    if args.transition:
        project.transition_style = args.transition
    output = project.output
    if args.size:
        output.width, output.height = args.size
    for name in ("fps", "fit", "crf", "preset", "loudnorm"):
        value = getattr(args, name)
        if value is not None:
            setattr(output, name, value)
    return project


def project_from_files(args: argparse.Namespace) -> Project:
    clips = []
    for path in expand_inputs(args.inputs):
        clip = Clip(type=clip_type_for(str(path)), path=str(path.resolve()),
                    fade_in=args.fade, fade_out=args.fade)
        if clip.type == "image":
            clip.duration = args.image_duration
        clips.append(clip)
    project = Project(clips=clips, chapters="clips" if args.chapters else "none")
    if args.title:
        project.output.title = args.title
        project.clips.insert(0, Clip(type="title", text=args.title, fade_in=args.fade, fade_out=args.fade))
    if args.music:
        project.music = Music(path=str(Path(args.music).resolve()))
    project = apply_output_options(project, args)
    if project.music and args.music_volume is not None:
        project.music.volume = args.music_volume
    return project


class ProgressBar:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self.started = time.monotonic()
        self.seen: set[str] = set()
        self.tty = sys.stderr.isatty()

    def __call__(self, fraction: float, message: str) -> None:
        if self.quiet:
            return
        if not self.tty:
            if message not in self.seen:
                print(message, file=sys.stderr)
                self.seen.add(message)
            return
        width = 30
        filled = int(width * fraction)
        elapsed = time.monotonic() - self.started
        eta = f" ETA {format_timestamp(elapsed / fraction - elapsed)}" if 0.02 < fraction < 1 else ""
        line = f"\r[{'#' * filled}{'.' * (width - filled)}] {fraction * 100:5.1f}%{eta}  {message}"
        print(line[:150].ljust(150), end="", file=sys.stderr, flush=True)
        if fraction >= 1:
            print(file=sys.stderr)


def do_render(project: Project, output: str, base_dir: Path, args: argparse.Namespace, keep_cache: bool) -> int:
    cancel = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    result = render(
        project, output, base_dir=base_dir, cache_dir=args.cache_dir, keep_cache=keep_cache,
        jobs=args.jobs, progress=ProgressBar(args.quiet), cancel=cancel,
        captions_dir=default_models_dir().parent / "captions",
    )
    print(f"Wrote {result.output} ({format_timestamp(result.duration)})")
    if result.captions_file:
        print(f"Captions also saved as {result.captions_file.name} (upload it to YouTube for editable subtitles)")
    if result.chapters:
        chapters_file = result.output.with_suffix(".chapters.txt")
        chapters_file.write_text(result.chapter_text() + "\n", encoding="utf-8")
        lines = result.chapter_text().splitlines()
        shown = "\n".join(lines[:12] + ([f"... and {len(lines) - 12} more"] if len(lines) > 12 else []))
        print(f"{len(lines)} chapters written to {chapters_file.name}:\n{shown}")
    return 0


def cmd_join(args: argparse.Namespace) -> int:
    project = project_from_files(args)
    return do_render(project, args.output, Path.cwd(), args, keep_cache=args.keep_cache)


def cmd_init(args: argparse.Namespace) -> int:
    project = project_from_files(args)
    out = Path(args.output)
    if out.exists() and not args.force:
        raise ProjectError(f"{out} already exists (use --force to overwrite)")
    base = out.resolve().parent
    for clip in project.clips:
        if clip.path:
            clip.path = _relative(Path(clip.path), base)
    if project.music:
        project.music.path = _relative(Path(project.music.path), base)
    project.save(out)
    print(f"Wrote {out} with {len(project.clips)} clips. Edit it, then run: longform render {out}")
    return 0


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base))
    except ValueError:
        return str(path.resolve())


def cmd_render(args: argparse.Namespace) -> int:
    project_file = Path(args.project)
    project = apply_output_options(Project.load(project_file), args)
    output = args.output or str(project_file.with_suffix(".mp4"))
    return do_render(project, output, project_file.resolve().parent, args, keep_cache=not args.no_cache)


def cmd_probe(args: argparse.Namespace) -> int:
    total = 0.0
    for path in expand_inputs(args.inputs):
        info = ffmpeg.probe(path)
        size = f"{info.width}x{info.height}" if info.width else "no video"
        fps = f"{info.fps:.2f}fps" if info.fps and not info.is_image else ""
        length = "image" if info.is_image else format_timestamp(info.duration or 0)
        audio = "audio" if info.has_audio else "no audio"
        print(f"{length:>9}  {size:>10}  {fps:>9}  {audio:<8}  {path}")
        total += info.duration or 0
    print(f"{format_timestamp(total):>9}  total")
    return 0


def cmd_app(args: argparse.Namespace) -> int:
    from .desktop import run_app
    return run_app(Path(args.workspace) if args.workspace else None)


def cmd_ui(args: argparse.Namespace) -> int:
    from .server import serve
    serve(Path(args.workspace), host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="longform", description="Put any videos together into one long-form video.")
    parser.add_argument("--version", action="version", version=f"longform {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def file_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("inputs", nargs="+", help="video/image files or folders (folders are sorted by name)")
        p.add_argument("-o", "--output", required=True)
        p.add_argument("--title", help="add an opening title card and set the video title")
        p.add_argument("--fade", type=float, default=0.0, help="fade in/out each clip by this many seconds")
        p.add_argument("--image-duration", type=float, default=5.0, help="seconds to show each image")
        p.add_argument("--chapters", action="store_true", help="add a chapter for every clip")
        p.add_argument("--music", help="background music, looped under the whole video")
        p.add_argument("--music-volume", type=float, help="0 to 1 (default 0.25)")
        add_output_options(p)

    def render_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("-j", "--jobs", type=int, default=2, help="clips to encode in parallel (default 2)")
        p.add_argument("--cache-dir", help="where to keep encoded clips (default: .longform-cache next to output)")
        p.add_argument("-q", "--quiet", action="store_true")

    p = sub.add_parser("join", help="join files straight into one video")
    file_options(p)
    render_options(p)
    p.add_argument("--keep-cache", action="store_true", help="keep encoded clips to speed up a re-run")
    p.set_defaults(func=cmd_join)

    p = sub.add_parser("init", help="write a project file from files, to edit before rendering")
    file_options(p)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("render", help="render a project file")
    p.add_argument("project")
    p.add_argument("-o", "--output", help="output file (default: project name with .mp4)")
    p.add_argument("--no-cache", action="store_true", help="delete encoded clips afterwards")
    add_output_options(p)
    render_options(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("probe", help="show length, size and audio of media files")
    p.add_argument("inputs", nargs="+")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("app", help="open the desktop app")
    p.add_argument("--workspace", help="where projects and settings are kept")
    p.set_defaults(func=cmd_app)

    p = sub.add_parser("ui", help="open the editor in your browser")
    p.add_argument("--workspace", default="longform-workspace", help="folder for media, project and renders")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Cancelled:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (ProjectError, RenderError, FFmpegError) as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # Output was piped into something like `head` that stopped reading; that's not an error.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
