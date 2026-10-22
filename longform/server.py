"""Local web editor: upload clips, arrange them, and render, all in the browser.

Everything lives in one workspace folder:
    media/         uploaded or copied-in source files
    renders/       finished videos
    project.json   the current timeline
"""

from __future__ import annotations

import json
import mimetypes
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

from . import ffmpeg
from .ffmpeg import Cancelled, FFmpegError
from .project import Project, ProjectError, is_media_file
from .render import RenderError, format_timestamp, render

STATIC_DIR = Path(__file__).parent / "static"
CHUNK = 1024 * 1024


def safe_name(name: str) -> str:
    name = Path(unquote(name)).name
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" .")
    return name or "upload"


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.media = self.root / "media"
        self.renders = self.root / "renders"
        self.thumbs = self.root / ".thumbs"
        self.project_file = self.root / "project.json"
        for d in (self.media, self.renders, self.thumbs):
            d.mkdir(parents=True, exist_ok=True)
        self._probe_cache: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        self.job: dict | None = None
        self._cancel: threading.Event | None = None

    def inside(self, base: Path, name: str) -> Path:
        path = (base / unquote(name)).resolve()
        if base.resolve() not in path.parents:
            raise ProjectError("path is outside the workspace")
        return path

    def media_info(self, path: Path) -> dict:
        stat = path.stat()
        key = (path.name, stat.st_size, stat.st_mtime_ns)
        if key not in self._probe_cache:
            try:
                info = ffmpeg.probe(path).to_dict()
                info["error"] = None
            except FFmpegError as e:
                info = {"error": str(e)}
            info.update(name=path.name, path=f"media/{path.name}", size=stat.st_size)
            self._probe_cache[key] = info
        return self._probe_cache[key]

    def list_media(self) -> list[dict]:
        files = sorted((p for p in self.media.iterdir() if p.is_file() and is_media_file(p, include_audio=True)),
                       key=lambda p: p.name.lower())
        return [self.media_info(p) for p in files]

    def thumbnail(self, name: str) -> Path:
        source = self.inside(self.media, name)
        info = self.media_info(source)
        stat = source.stat()
        thumb = self.thumbs / f"{source.stem}-{stat.st_size}-{stat.st_mtime_ns}.jpg"
        if not thumb.exists():
            at = min(1.0, (info.get("duration") or 0) / 3)
            seek = [] if info.get("is_image") else ["-ss", f"{at:.2f}"]
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", *seek, "-i", str(source), "-frames:v", "1",
                 "-vf", "scale=240:-2", str(thumb)],
                capture_output=True,
            )
        if not thumb.exists():
            raise FileNotFoundError(name)
        return thumb

    def load_project(self) -> dict:
        if self.project_file.exists():
            return json.loads(self.project_file.read_text(encoding="utf-8"))
        return Project().to_dict()

    def save_project(self, data: dict) -> Project:
        project = Project.from_dict(data)
        self.project_file.write_text(json.dumps(project.to_dict(), indent=2) + "\n", encoding="utf-8")
        return project

    def check_paths(self, project: Project) -> None:
        for clip in project.clips:
            if clip.path:
                self.inside(self.root, clip.path)
        if project.music:
            self.inside(self.root, project.music.path)

    def start_render(self, data: dict) -> dict:
        with self._lock:
            if self.job and self.job["status"] == "running":
                raise ProjectError("a render is already running")
            project = self.save_project(data)
            project.validate()
            self.check_paths(project)
            name = safe_name(data.get("filename") or (project.output.title or "longform") + ".mp4")
            if not name.lower().endswith((".mp4", ".mkv", ".mov")):
                name += ".mp4"
            self.job = {"id": uuid.uuid4().hex[:8], "status": "running", "progress": 0.0,
                        "message": "Starting…", "output": None, "error": None, "chapters": "",
                        "started": time.time()}
            self._cancel = threading.Event()
            job, cancel = self.job, self._cancel

        def progress(fraction: float, message: str) -> None:
            job.update(progress=round(fraction, 4), message=message)

        def work() -> None:
            try:
                result = render(project, self.renders / name, base_dir=self.root,
                                cache_dir=self.root / ".cache", jobs=2, progress=progress, cancel=cancel)
                job.update(status="done", progress=1.0, output=f"renders/{result.output.name}",
                           chapters=result.chapter_text(),
                           message=f"Done: {result.output.name} ({format_timestamp(result.duration)})")
            except Cancelled:
                job.update(status="cancelled", message="Cancelled")
            except (ProjectError, RenderError, FFmpegError, OSError) as e:
                job.update(status="error", error=str(e), message="Render failed")

        threading.Thread(target=work, daemon=True).start()
        return job

    def cancel_render(self) -> None:
        if self._cancel:
            self._cancel.set()

    def list_renders(self) -> list[dict]:
        files = [p for p in self.renders.iterdir() if p.is_file() and ".partial." not in p.name]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": p.name, "path": f"renders/{p.name}", "size": p.stat().st_size} for p in files]


class Handler(BaseHTTPRequestHandler):
    workspace: Workspace
    server_version = "longform"

    def log_message(self, format: str, *args) -> None:  # keep the terminal quiet
        pass

    # --- helpers -------------------------------------------------------
    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            raise ProjectError(f"invalid JSON: {e}") from e

    def send_file(self, path: Path, download: bool = False) -> None:
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
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
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
                pass

    # --- routes --------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        ws = self.workspace
        try:
            if url.path in ("/", "/index.html"):
                return self.send_file(STATIC_DIR / "index.html")
            if url.path == "/api/media":
                return self.send_json(ws.list_media())
            if url.path == "/api/project":
                return self.send_json(ws.load_project())
            if url.path == "/api/renders":
                return self.send_json(ws.list_renders())
            if url.path == "/api/render":
                return self.send_json(ws.job or {"status": "idle"})
            if url.path.startswith("/api/thumb/"):
                return self.send_file(ws.thumbnail(url.path[len("/api/thumb/"):]))
            if url.path.startswith("/media/"):
                return self.send_file(ws.inside(ws.media, url.path[len("/media/"):]))
            if url.path.startswith("/renders/"):
                download = "download" in parse_qs(url.query)
                return self.send_file(ws.inside(ws.renders, url.path[len("/renders/"):]), download)
            self.send_error_json(404, "not found")
        except FileNotFoundError:
            self.send_error_json(404, "not found")
        except ProjectError as e:
            self.send_error_json(400, str(e))

    def do_PUT(self) -> None:
        url = urlparse(self.path)
        if not url.path.startswith("/api/upload/"):
            return self.send_error_json(404, "not found")
        name = safe_name(url.path[len("/api/upload/"):])
        if not is_media_file(name, include_audio=True):
            return self.send_error_json(400, f"{name} is not a supported video, image or audio file")
        target = self.workspace.media / name
        stem, suffix, n = target.stem, target.suffix, 1
        while target.exists():
            target = self.workspace.media / f"{stem} ({n}){suffix}"
            n += 1
        remaining = int(self.headers.get("Content-Length") or 0)
        partial = target.with_name(target.name + ".uploading")
        try:
            with open(partial, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ConnectionError("upload interrupted")
                    f.write(chunk)
                    remaining -= len(chunk)
            partial.replace(target)
        except (ConnectionError, OSError) as e:
            partial.unlink(missing_ok=True)
            return self.send_error_json(400, str(e))
        self.send_json(self.workspace.media_info(target))

    def do_POST(self) -> None:
        url = urlparse(self.path)
        ws = self.workspace
        try:
            if url.path == "/api/project":
                project = ws.save_project(self.read_json())
                return self.send_json(project.to_dict())
            if url.path == "/api/render":
                return self.send_json(ws.start_render(self.read_json()))
            if url.path == "/api/render/cancel":
                ws.cancel_render()
                return self.send_json({"ok": True})
            self.send_error_json(404, "not found")
        except ProjectError as e:
            self.send_error_json(400, str(e))

    def do_DELETE(self) -> None:
        url = urlparse(self.path)
        ws = self.workspace
        try:
            if url.path.startswith("/api/media/"):
                ws.inside(ws.media, url.path[len("/api/media/"):]).unlink()
                return self.send_json({"ok": True})
            if url.path.startswith("/api/renders/"):
                ws.inside(ws.renders, url.path[len("/api/renders/"):]).unlink()
                return self.send_json({"ok": True})
            self.send_error_json(404, "not found")
        except FileNotFoundError:
            self.send_error_json(404, "not found")
        except ProjectError as e:
            self.send_error_json(400, str(e))


def make_server(root: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    ffmpeg.require_tools()
    workspace = Workspace(root)
    handler = type("BoundHandler", (Handler,), {"workspace": workspace})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(root: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = make_server(root, host, port)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{server.server_port}/"
    print(f"longform editor running at {url}")
    print(f"Workspace: {server.RequestHandlerClass.workspace.root}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
