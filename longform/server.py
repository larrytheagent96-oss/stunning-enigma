"""The app's backend: a small local web server the desktop window (or a browser) talks to.

Everything the app keeps lives in one workspace folder:
    library.json   files you added (they stay where they are on your disk)
    media/         files uploaded through a browser (when there is no desktop window)
    projects/      one JSON file per project
    captions/      transcripts, one per source file
    models/        the speech model, downloaded the first time captions are made
    .cache/        encoded pieces, so re-exports are fast
    .preview/      quick preview renders
    .thumbs/       thumbnails, filmstrips and waveforms
Finished videos go to the export folder (Videos/Longform by default).
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import platform
import re
import subprocess
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, captions, ffmpeg, presets
from .ffmpeg import Cancelled, FFmpegError
from .project import AUDIO_EXTENSIONS, Project, ProjectError, is_media_file
from .render import RenderError, format_timestamp, render

STATIC_DIR = Path(__file__).parent / "static"
CHUNK = 1024 * 1024
FILMSTRIP_FRAMES = 8
# Windows can map these to the wrong types in its registry, which stops pages from loading.
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".otf": "font/otf", ".mp4": "video/mp4",
                 ".jpg": "image/jpeg", ".png": "image/png", ".svg": "image/svg+xml"}
PREVIEW_SHORT_SIDE = 360


def safe_name(name: str) -> str:
    name = Path(unquote(name)).name
    name = re.sub(r'[^\w.\- ()\[\]&,!+]+', "_", name).strip(" .")
    return name or "untitled"


def media_id(path: Path | str) -> str:
    return hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:16]


def default_export_dir() -> Path:
    videos = Path.home() / "Videos"
    if not videos.is_dir():
        movies = Path.home() / "Movies"  # macOS
        videos = movies if movies.is_dir() else Path.home()
    return videos / "Longform"


def unique_path(path: Path) -> Path:
    n = 2
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def reveal_in_file_manager(path: Path) -> None:
    system = platform.system()
    if system == "Windows":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif system == "Darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])


class Job:
    def __init__(self, kind: str, title: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.title = title
        self.status = "running"
        self.progress = 0.0
        self.message = "Starting…"
        self.error: str | None = None
        self.result: dict = {}
        self.started = time.time()
        self.finished: float | None = None
        self.cancel = threading.Event()

    def update(self, fraction: float, message: str) -> None:
        self.progress = max(self.progress, round(min(fraction, 1.0), 4))
        self.message = message

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
                "progress": self.progress, "message": self.message, "error": self.error,
                "result": self.result, "started": self.started, "finished": self.finished}


class Workspace:
    def __init__(self, root: Path, export_dir: Path | None = None):
        self.root = Path(root).resolve()
        self.media = self.root / "media"
        self.projects_dir = self.root / "projects"
        self.thumbs = self.root / ".thumbs"
        self.preview_dir = self.root / ".preview"
        self.cache = self.root / ".cache"
        self.captions_dir = self.root / "captions"
        self.models_dir = self.root / "models"
        for d in (self.media, self.projects_dir, self.thumbs, self.preview_dir, self.cache, self.captions_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.library_file = self.root / "library.json"
        self.settings_file = self.root / "settings.json"
        self.captions = captions.CaptionStore(self.captions_dir)
        self._lock = threading.RLock()
        self._probe_cache: dict[tuple, dict] = {}
        self._thumb_locks: dict[str, threading.Lock] = {}
        self.jobs: dict[str, Job] = {}
        settings = self._read_json(self.settings_file, {})
        if export_dir:
            settings["export_dir"] = str(export_dir)
        settings.setdefault("export_dir", str(default_export_dir()))
        self.settings = settings
        self._save_json(self.settings_file, self.settings)
        self._import_legacy_project()

    # --- small helpers -------------------------------------------------------
    @staticmethod
    def _read_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    @staticmethod
    def _save_json(path: Path, data) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _import_legacy_project(self) -> None:
        """Older versions kept a single project.json and copied media into media/."""
        legacy = self.root / "project.json"
        if legacy.exists() and not any(self.projects_dir.glob("*.json")):
            data = self._read_json(legacy, {})
            for clip in data.get("clips", []):
                if clip.get("path") and not Path(clip["path"]).is_absolute():
                    clip["path"] = str((self.root / clip["path"]).resolve())
            if (data.get("music") or {}).get("path") and not Path(data["music"]["path"]).is_absolute():
                data["music"]["path"] = str((self.root / data["music"]["path"]).resolve())
            self.create_project({**data, "name": data.get("name") or "My first project"})
            legacy.rename(legacy.with_suffix(".json.imported"))

    @property
    def export_dir(self) -> Path:
        return Path(self.settings["export_dir"]).expanduser()

    def update_settings(self, changes: dict) -> dict:
        with self._lock:
            if "export_dir" in changes:
                folder = Path(str(changes["export_dir"])).expanduser()
                folder.mkdir(parents=True, exist_ok=True)
                self.settings["export_dir"] = str(folder.resolve())
            self._save_json(self.settings_file, self.settings)
            return self.settings

    # --- library -------------------------------------------------------------
    def library_paths(self) -> list[Path]:
        linked = [Path(p) for p in self._read_json(self.library_file, {"items": []}).get("items", [])]
        uploaded = sorted(p for p in self.media.iterdir()
                          if p.is_file() and is_media_file(p, include_audio=True))
        seen, paths = set(), []
        for p in linked + uploaded:
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                paths.append(p.resolve())
        return paths

    def add_to_library(self, paths: list[str]) -> list[dict]:
        added = []
        with self._lock:
            items = self._read_json(self.library_file, {"items": []}).get("items", [])
            for raw in paths:
                p = Path(raw).expanduser()
                if p.is_dir():
                    candidates = sorted(f for f in p.iterdir() if f.is_file() and is_media_file(f, include_audio=True))
                else:
                    candidates = [p]
                for f in candidates:
                    if not f.is_file():
                        raise ProjectError(f"file not found: {f}")
                    if not is_media_file(f, include_audio=True):
                        raise ProjectError(f"{f.name} is not a video, photo or audio file")
                    resolved = str(f.resolve())
                    if resolved not in items:
                        items.append(resolved)
                    added.append(self.media_info(Path(resolved)))
            self._save_json(self.library_file, {"items": items})
        return added

    def remove_from_library(self, mid: str) -> None:
        path = self.path_for(mid)
        with self._lock:
            items = [p for p in self._read_json(self.library_file, {"items": []}).get("items", [])
                     if media_id(p) != mid]
            self._save_json(self.library_file, {"items": items})
            if self.media in path.parents:  # an uploaded copy belongs to the app, so delete it
                path.unlink(missing_ok=True)

    def path_for(self, mid: str) -> Path:
        for p in self.library_paths():
            if media_id(p) == mid:
                return p
        raise FileNotFoundError(mid)

    def media_info(self, path: Path) -> dict:
        path = Path(path).resolve()
        base = {"id": media_id(path), "name": path.name, "path": str(path),
                "is_audio": path.suffix.lower() in AUDIO_EXTENSIONS}
        if not path.exists():
            return {**base, "missing": True, "error": "This file has been moved or deleted"}
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self._probe_cache:
            try:
                info = ffmpeg.probe(path).to_dict()
                info["error"] = None
            except FFmpegError as e:
                info = {"error": str(e).splitlines()[-1][:200]}
            info.pop("path", None)
            self._probe_cache[key] = {**info, "size": stat.st_size, "modified": stat.st_mtime}
        info = {**self._probe_cache[key], **base, "missing": False}
        transcript = self.captions.load(path) if not info.get("error") else None
        info["has_captions"] = transcript is not None
        return info

    def list_library(self) -> list[dict]:
        return [self.media_info(p) for p in self.library_paths()]

    def upload(self, name: str, stream, length: int) -> dict:
        name = safe_name(name)
        if not is_media_file(name, include_audio=True):
            raise ProjectError(f"{name} is not a video, photo or audio file")
        target = unique_path(self.media / name)
        partial = target.with_name(target.name + ".uploading")
        try:
            with open(partial, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = stream.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ProjectError("the upload was interrupted")
                    f.write(chunk)
                    remaining -= len(chunk)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        return self.media_info(target)

    # --- thumbnails, filmstrips and waveforms -------------------------------------
    def _cached_image(self, mid: str, kind: str, make) -> Path:
        source = self.path_for(mid)
        stat = source.stat()
        out = self.thumbs / f"{mid}-{kind}-{stat.st_size}-{stat.st_mtime_ns}.jpg"
        with self._lock:
            lock = self._thumb_locks.setdefault(f"{mid}-{kind}", threading.Lock())
        with lock:
            if not out.exists():
                tmp = out.with_suffix(".tmp.jpg")
                make(source, self.media_info(source), tmp)
                if tmp.exists():
                    tmp.replace(out)
        if not out.exists():
            raise FileNotFoundError(mid)
        return out

    def thumbnail(self, mid: str) -> Path:
        def make(source: Path, info: dict, out: Path) -> None:
            if info.get("is_audio") or not info.get("has_video"):
                cmd = ["-i", str(source), "-filter_complex",
                       "aformat=channel_layouts=mono,showwavespic=s=320x180:colors=#7c9cff", "-frames:v", "1"]
            else:
                at = 0 if info.get("is_image") else min(1.0, (info.get("duration") or 0) / 3)
                cmd = ["-ss", f"{at:.2f}", "-i", str(source), "-frames:v", "1", "-vf", "scale=320:-2"]
            subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd, "-q:v", "4", str(out)], capture_output=True,
                           **ffmpeg.NO_WINDOW)
        return self._cached_image(mid, "thumb", make)

    def filmstrip(self, mid: str) -> Path:
        """A row of frames across the whole clip, drawn behind it on the timeline."""
        def make(source: Path, info: dict, out: Path) -> None:
            if info.get("is_audio") or not info.get("has_video"):
                cmd = ["-i", str(source), "-filter_complex",
                       "aformat=channel_layouts=mono,showwavespic=s=960x90:colors=#7c9cff", "-frames:v", "1"]
            elif info.get("is_image"):
                cmd = ["-i", str(source), "-vf", "scale=-2:90", "-frames:v", "1"]
            else:
                duration = info.get("duration") or 1
                inputs, labels = [], []
                for n in range(FILMSTRIP_FRAMES):
                    # Seeking each frame separately is fast even for multi-hour files.
                    inputs += ["-ss", f"{duration * (n + 0.5) / FILMSTRIP_FRAMES:.3f}", "-i", str(source)]
                    labels.append(f"[{n}:v]scale=160:90:force_original_aspect_ratio=increase,crop=160:90,"
                                  f"setsar=1,trim=end_frame=1[f{n}]")
                graph = ";".join(labels) + ";" + "".join(f"[f{n}]" for n in range(FILMSTRIP_FRAMES)) + \
                    f"hstack=inputs={FILMSTRIP_FRAMES}"
                cmd = [*inputs, "-filter_complex", graph, "-frames:v", "1"]
            subprocess.run(["ffmpeg", "-v", "error", "-y", *cmd, "-q:v", "5", str(out)], capture_output=True,
                           **ffmpeg.NO_WINDOW)
        return self._cached_image(mid, "strip", make)

    # --- projects ----------------------------------------------------------------
    def _project_file(self, pid: str) -> Path:
        if not re.fullmatch(r"[a-z0-9]{6,32}", pid):
            raise FileNotFoundError(pid)
        return self.projects_dir / f"{pid}.json"

    def list_projects(self) -> list[dict]:
        out = []
        for f in self.projects_dir.glob("*.json"):
            data = self._read_json(f, None)
            if not isinstance(data, dict):
                continue
            first = next((c for c in data.get("clips", []) if c.get("path")), None)
            out.append({"id": f.stem, "name": data.get("name") or "Untitled", "updated": f.stat().st_mtime,
                        "clips": len(data.get("clips", [])),
                        "cover": media_id(first["path"]) if first else None})
        return sorted(out, key=lambda p: p["updated"], reverse=True)

    def load_project(self, pid: str) -> dict:
        f = self._project_file(pid)
        if not f.exists():
            raise FileNotFoundError(pid)
        data = Project.from_dict(self._read_json(f, {})).to_dict()
        return {**data, "id": pid}

    def save_project(self, pid: str, data: dict) -> dict:
        f = self._project_file(pid)
        if not f.exists():
            raise FileNotFoundError(pid)
        project = Project.from_dict(data)
        if not (project.name or "").strip():
            project.name = "Untitled"
        self._save_json(f, project.to_dict())
        return {**project.to_dict(), "id": pid}

    def create_project(self, data: dict | None = None) -> dict:
        data = dict(data or {})
        template = data.pop("template_id", None)
        project = Project.from_dict(data).to_dict()
        if template:
            project = presets.apply(project, presets.find(presets.TEMPLATES, template))
        existing = {p["name"] for p in self.list_projects()}
        name = (project.get("name") or "").strip() or "Untitled project"
        base, n = name, 2
        while name in existing:
            name, n = f"{base} {n}", n + 1
        project["name"] = name
        pid = uuid.uuid4().hex[:12]
        self._save_json(self._project_file(pid), project)
        return {**project, "id": pid}

    def duplicate_project(self, pid: str) -> dict:
        data = self.load_project(pid)
        data.pop("id")
        data["name"] = f"{data.get('name') or 'Untitled'} copy"
        return self.create_project(data)

    def delete_project(self, pid: str) -> None:
        self._project_file(pid).unlink(missing_ok=True)

    def check_paths(self, project: Project) -> None:
        allowed = {str(p) for p in self.library_paths()}
        for path in [c.path for c in project.clips if c.path] + ([project.music.path] if project.music else []):
            resolved = str((self.root / path).resolve())
            if resolved not in allowed:
                raise ProjectError(f"{Path(path).name} is not in the library; add it first")

    # --- jobs ----------------------------------------------------------------------
    def _start(self, job: Job, work) -> Job:
        with self._lock:
            self.jobs[job.id] = job
            # Keep the list short: forget finished jobs older than an hour.
            for jid in [j.id for j in self.jobs.values()
                        if j.finished and time.time() - j.finished > 3600]:
                del self.jobs[jid]

        def run() -> None:
            try:
                work(job)
                job.status = "done"
                job.progress = 1.0
            except Cancelled:
                job.status, job.message = "cancelled", "Cancelled"
            except (ProjectError, RenderError, FFmpegError, captions.CaptionError, OSError) as e:
                job.status, job.error, job.message = "error", str(e), "Something went wrong"
            except Exception as e:  # never leave a job spinning forever
                job.status, job.error, job.message = "error", f"Unexpected error: {e!r}", "Something went wrong"
            finally:
                job.finished = time.time()

        threading.Thread(target=run, daemon=True).start()
        return job

    def _running(self, kind: str, key: str | None = None) -> Job | None:
        return next((j for j in self.jobs.values() if j.kind == kind and j.status == "running"
                     and (key is None or j.result.get("key") == key)), None)

    def _prepared(self, data: dict) -> Project:
        project = Project.from_dict(data)
        project.validate()
        self.check_paths(project)
        return project

    def start_export(self, data: dict) -> Job:
        project = self._prepared(data)
        if self._running("export"):
            raise ProjectError("An export is already running. Wait for it to finish or cancel it.")
        name = safe_name(data.get("filename") or project.name or "My video")
        if not name.lower().endswith((".mp4", ".mov", ".mkv")):
            name += ".mp4"
        folder = self.export_dir
        folder.mkdir(parents=True, exist_ok=True)
        target = unique_path(folder / name)
        job = Job("export", f"Exporting {target.name}")

        def work(job: Job) -> None:
            result = render(project, target, base_dir=self.root, cache_dir=self.cache, jobs=_jobs(),
                            progress=job.update, cancel=job.cancel, captions_dir=self.captions_dir,
                            models_dir=self.models_dir)
            chapters_file = None
            if result.chapters:
                chapters_file = target.with_suffix(".chapters.txt")
                chapters_file.write_text(result.chapter_text() + "\n", encoding="utf-8")
            job.message = f"Saved {target.name} ({format_timestamp(result.duration)})"
            job.result.update(file=str(target), name=target.name, duration=result.duration,
                              size=target.stat().st_size, chapters=result.chapter_text(),
                              captions_file=str(result.captions_file) if result.captions_file else None,
                              chapters_file=str(chapters_file) if chapters_file else None)

        return self._start(job, work)

    def start_preview(self, data: dict) -> Job:
        project = self._prepared(data)
        for j in self.jobs.values():  # a new preview replaces one still rendering
            if j.kind == "preview" and j.status == "running":
                j.cancel.set()
        o = project.output
        scale = PREVIEW_SHORT_SIDE / min(o.width, o.height)
        if scale < 1:
            o.width, o.height = round(o.width * scale / 2) * 2, round(o.height * scale / 2) * 2
        o.preset, o.crf, o.audio_bitrate = "ultrafast", 30, "128k"
        o.loudnorm = False  # loudness analysis is slow; the final export does it
        target = self.preview_dir / f"preview-{uuid.uuid4().hex[:8]}.mp4"
        job = Job("preview", "Building preview")

        def work(job: Job) -> None:
            for old in self.preview_dir.glob("preview-*.mp4"):
                if old != target:
                    old.unlink(missing_ok=True)
            result = render(project, target, base_dir=self.root, cache_dir=self.cache / "preview", jobs=_jobs(),
                            progress=job.update, cancel=job.cancel, captions_dir=self.captions_dir,
                            models_dir=self.models_dir)
            job.message = "Preview ready"
            job.result.update(file=str(target), duration=result.duration)

        return self._start(job, work)

    def start_captions(self, mid: str, options: dict) -> Job:
        source = self.path_for(mid)
        existing = self._running("captions", mid)
        if existing:
            return existing
        model = options.get("model") or "base"
        if model not in captions.WHISPER_MODELS:
            raise ProjectError(f"unknown speech model {model}")
        job = Job("captions", f"Captions for {source.name}")
        job.result["key"] = mid

        def work(job: Job) -> None:
            data = captions.whisper_transcribe(source, model=model, language=options.get("language") or None,
                                               models_dir=self.models_dir, progress=job.update, cancel=job.cancel)
            self.captions.save(source, data)
            job.message = f"{len(data['lines'])} caption lines"

        return self._start(job, work)

    def proxy_path(self, mid: str) -> Path:
        source = self.path_for(mid)
        stat = source.stat()
        return self.thumbs / f"{mid}-proxy-{stat.st_size}-{stat.st_mtime_ns}.mp4"

    def start_proxy(self, mid: str) -> Job:
        """A small H.264 copy for clips the window's built-in player can't decode (HEVC, ProRes, AVI...)."""
        source, target = self.path_for(mid), self.proxy_path(mid)
        existing = self._running("proxy", mid)
        if existing:
            return existing
        job = Job("proxy", f"Preparing {source.name}")
        job.result.update(key=mid)
        if target.exists():
            job.status, job.progress, job.finished = "done", 1.0, time.time()
            job.result["file"] = str(target)
            self.jobs[job.id] = job
            return job
        duration = self.media_info(source).get("duration") or 0

        def work(job: Job) -> None:
            tmp = target.with_suffix(".partial.mp4")
            try:
                ffmpeg.run(["-i", str(source), "-map", "0:v:0?", "-map", "0:a:0?",
                            "-vf", "scale=-2:'min(480,ih)',format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "28", "-g", "30", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                            "-movflags", "+faststart", str(tmp)],
                           duration=duration, cancel=job.cancel,
                           on_progress=lambda t: job.update(t / duration if duration else 0, "Preparing a preview copy"))
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            tmp.replace(target)
            job.result["file"] = str(target)
            job.message = "Ready"

        return self._start(job, work)

    def job_file(self, jid: str) -> Path:
        job = self.jobs.get(jid)
        if not job or not job.result.get("file"):
            raise FileNotFoundError(jid)
        return Path(job.result["file"])


def _jobs() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))


class Handler(BaseHTTPRequestHandler):
    workspace: Workspace
    desktop: bool = False
    server_version = "longform"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # keep the terminal quiet
        pass

    # --- helpers -------------------------------------------------------
    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            raise ProjectError(f"invalid JSON: {e}") from e

    def send_file(self, path: Path, download: bool = False, cache: bool = False) -> None:
        if not path.is_file():
            return self.send_error_json(404, "not found")
        size = path.stat().st_size
        start, end = 0, size - 1
        status = 200
        match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match and size:
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), size - 1) if match[2] else size - 1
            elif match[2]:
                start = max(0, size - int(match[2]))
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        content_type = CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = f.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    def route(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        ws = self.workspace
        try:
            self.dispatch(method, url, parts, ws)
        except FileNotFoundError:
            self.send_error_json(404, "not found")
        except ProjectError as e:
            self.send_error_json(400, str(e))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def dispatch(self, method: str, url, parts: list[str], ws: Workspace) -> None:
        query = parse_qs(url.query)
        if method in ("GET", "HEAD") and not parts:
            return self.send_file(STATIC_DIR / "index.html")
        if method in ("GET", "HEAD") and parts[0] == "static" and len(parts) == 2:
            return self.send_file(STATIC_DIR / safe_name(parts[1]), cache=False)
        if method in ("GET", "HEAD") and parts[0] == "fonts" and len(parts) == 2:
            return self.send_file(captions.FONTS_DIR / safe_name(parts[1]), cache=True)
        if not parts or parts[0] != "api":
            return self.send_error_json(404, "not found")
        api = parts[1:]
        match (method, api):
            case ("GET", ["state"]):
                return self.send_json({"version": __version__, "desktop": self.desktop,
                                       "settings": ws.settings, "projects": ws.list_projects(),
                                       "catalog": presets.catalog(),
                                       "jobs": [j.to_dict() for j in ws.jobs.values()]})
            case ("POST", ["settings"]):
                return self.send_json(ws.update_settings(self.read_json()))
            case ("GET", ["library"]):
                return self.send_json(ws.list_library())
            case ("POST", ["library"]):
                return self.send_json(ws.add_to_library(self.read_json().get("paths") or []))
            case ("DELETE", ["library", mid]):
                ws.remove_from_library(mid)
                return self.send_json({"ok": True})
            case ("PUT", ["upload", name]):
                length = int(self.headers.get("Content-Length") or 0)
                return self.send_json(ws.upload(name, self.rfile, length))
            case ("GET" | "HEAD", ["media", mid]):
                return self.send_file(ws.path_for(mid))
            case ("GET" | "HEAD", ["thumb", mid]):
                return self.send_file(ws.thumbnail(mid), cache=True)
            case ("POST", ["proxy", mid]):
                return self.send_json(ws.start_proxy(mid).to_dict())
            case ("GET" | "HEAD", ["proxy", mid]):
                return self.send_file(ws.proxy_path(mid))
            case ("GET" | "HEAD", ["filmstrip", mid]):
                return self.send_file(ws.filmstrip(mid), cache=True)
            case ("GET", ["projects"]):
                return self.send_json(ws.list_projects())
            case ("POST", ["projects"]):
                return self.send_json(ws.create_project(self.read_json()))
            case ("GET", ["projects", pid]):
                return self.send_json(ws.load_project(pid))
            case ("PUT", ["projects", pid]):
                return self.send_json(ws.save_project(pid, self.read_json()))
            case ("DELETE", ["projects", pid]):
                ws.delete_project(pid)
                return self.send_json({"ok": True})
            case ("POST", ["projects", pid, "duplicate"]):
                return self.send_json(ws.duplicate_project(pid))
            case ("GET", ["captions", mid]):
                data = ws.captions.load(ws.path_for(mid))
                return self.send_json(data) if data is not None else self.send_error_json(404, "no captions yet")
            case ("PUT", ["captions", mid]):
                data = self.read_json()
                return self.send_json(ws.captions.save(ws.path_for(mid), {**data, "edited": True}))
            case ("POST", ["captions", mid]):
                return self.send_json(ws.start_captions(mid, self.read_json()).to_dict())
            case ("POST", ["export"]):
                return self.send_json(ws.start_export(self.read_json()).to_dict())
            case ("POST", ["preview"]):
                return self.send_json(ws.start_preview(self.read_json()).to_dict())
            case ("GET", ["jobs"]):
                return self.send_json([j.to_dict() for j in ws.jobs.values()])
            case ("GET", ["jobs", jid]):
                job = ws.jobs.get(jid)
                return self.send_json(job.to_dict()) if job else self.send_error_json(404, "not found")
            case ("POST", ["jobs", jid, "cancel"]):
                job = ws.jobs.get(jid)
                if job:
                    job.cancel.set()
                return self.send_json({"ok": True})
            case ("GET" | "HEAD", ["jobs", jid, "file"]):
                return self.send_file(ws.job_file(jid), download="download" in query)
            case ("POST", ["jobs", jid, "reveal"]):
                reveal_in_file_manager(ws.job_file(jid))
                return self.send_json({"ok": True})
            case ("POST", ["reveal-exports"]):
                ws.export_dir.mkdir(parents=True, exist_ok=True)
                reveal_in_file_manager(ws.export_dir)
                return self.send_json({"ok": True})
        self.send_error_json(404, "not found")

    def do_GET(self) -> None:
        self.route("GET")

    def do_HEAD(self) -> None:
        self.route("HEAD")

    def do_POST(self) -> None:
        self.route("POST")

    def do_PUT(self) -> None:
        self.route("PUT")

    def do_DELETE(self) -> None:
        self.route("DELETE")


def make_server(root: Path, host: str = "127.0.0.1", port: int = 8765, *,
                export_dir: Path | None = None, desktop: bool = False) -> ThreadingHTTPServer:
    ffmpeg.require_tools()
    workspace = Workspace(root, export_dir=export_dir)
    handler = type("BoundHandler", (Handler,), {"workspace": workspace, "desktop": desktop})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(root: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(root, host, port)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{server.server_port}/"
    print(f"longform is running at {url}")
    print(f"Workspace: {server.RequestHandlerClass.workspace.root}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
