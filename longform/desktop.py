"""The desktop app: the editor in its own window, with native file pickers.

Run with `longform app` (or the `longform-app` command / the packaged Longform app).
Videos are never copied or uploaded: the app works with them where they are.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import threading
import traceback
from pathlib import Path

from .project import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

APP_NAME = "Longform"


def app_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def use_bundled_ffmpeg() -> None:
    """The packaged app ships FFmpeg next to itself; put it first on PATH."""
    roots = [Path(getattr(sys, "_MEIPASS", "")), Path(sys.executable).parent] if getattr(sys, "frozen", False) else []
    for root in roots:
        for candidate in (root / "ffmpeg", root):
            exe = candidate / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if exe.is_file():
                os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
                return


def _patterns(extensions) -> str:
    return ";".join(f"*{e}" for e in sorted(extensions))


class Api:
    """Functions the page can call as window.pywebview.api.<name>()."""

    def __init__(self) -> None:
        self.window = None

    def _dialog(self, kind: str):
        import webview
        dialogs = getattr(webview, "FileDialog", None)
        return getattr(dialogs, kind) if dialogs else getattr(webview, f"{kind}_DIALOG")

    def pick_files(self):
        media = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS
        types = (f"Videos, photos and music ({_patterns(media)})", "All files (*.*)")
        result = self.window.create_file_dialog(self._dialog("OPEN"), allow_multiple=True, file_types=types)
        return list(result or [])

    def pick_folder(self):
        result = self.window.create_file_dialog(self._dialog("FOLDER"))
        return result[0] if result else None


def _watch_drops(window) -> None:
    """Files dragged onto the window arrive here with their real paths."""
    try:
        from webview.dom import DOMEventHandler
    except ImportError:
        return

    def on_drop(event: dict) -> None:
        files = (event.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if paths:
            window.evaluate_js(f"window.app && window.app.addPaths({json.dumps(paths)})")

    try:
        window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=True)
    except Exception:  # drag and drop is a nicety; the Add button always works
        traceback.print_exc()


def run_app(workspace: Path | None = None, debug: bool = False) -> int:
    use_bundled_ffmpeg()
    from .server import make_server
    workspace = workspace or app_data_dir()
    server = make_server(workspace, port=0, desktop=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        import webview
    except ImportError:
        print("The desktop window needs pywebview (pip install pywebview). Opening in your browser instead.")
        import webbrowser
        webbrowser.open(url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0
    api = Api()
    window = webview.create_window(APP_NAME, url, js_api=api, width=1440, height=900, min_size=(1100, 700),
                                   background_color="#0c0e13", text_select=True)
    api.window = window
    window.events.loaded += lambda: _watch_drops(window)
    storage = workspace / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        webview.start(private_mode=False, storage_path=str(storage), debug=debug)
    finally:
        server.shutdown()
    return 0


def self_test(report: Path | None, speech: bool = False) -> int:
    """Check a packaged build works end to end without opening a window (used by CI)."""
    lines: list[str] = []
    ok = True

    def log(message: str) -> None:
        lines.append(message)

    try:
        use_bundled_ffmpeg()
        import shutil
        from . import captions, ffmpeg
        from .project import Project
        from .render import render
        log(f"ffmpeg: {shutil.which('ffmpeg')}")
        log(f"ffprobe: {shutil.which('ffprobe')}")
        ffmpeg.require_tools()
        from .server import STATIC_DIR
        needed = [STATIC_DIR / n for n in ("index.html", "app.js", "app.css")] + \
                 [captions.FONTS_DIR / n for n in ("Inter-Regular.otf", "Inter-Bold.otf", "Inter-Black.otf")]
        missing = [str(p) for p in needed if not p.is_file()]
        if missing:
            ok = False
            log(f"missing app files: {missing}")
        else:
            log("app files: ok")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name, src in (("a.mp4", "testsrc2=s=640x360:r=30:d=2"), ("b.mov", "testsrc=s=360x640:r=24:d=2")):
                ffmpeg.run(["-f", "lavfi", "-i", src, "-f", "lavfi", "-i", "sine=f=440:d=2", "-shortest",
                            "-c:v", "libx264", "-c:a", "aac", str(d / name)])
            store = captions.CaptionStore(d / "captions")
            store.save(d / "a.mp4", {"lines": [{"start": 0.2, "end": 1.5, "text": "Self test captions"}]})
            project = Project.from_dict({
                "clips": [{"type": "title", "text": "Longform\nself test", "duration": 1.5}, "a.mp4", "b.mov"],
                "transition": 0.5, "transition_style": "dissolve",
                "captions": {"enabled": True, "style": "bold"},
                "output": {"width": 640, "height": 360, "preset": "ultrafast", "loudnorm": True},
            })
            result = render(project, d / "out.mp4", base_dir=d, captions_dir=d / "captions",
                            transcriber=lambda *a, **k: {"lines": []})
            log(f"rendered {result.duration:.2f}s with {len(result.chapters)} chapters")
            if abs(result.duration - (1.5 + 2 + 2 - 1.0)) > 0.1:
                ok = False
                log("unexpected length")
            if speech:
                # Runs the real speech model, which proves its native libraries were bundled correctly.
                data = captions.whisper_transcribe(d / "a.mp4", model="tiny", models_dir=d / "models")
                log(f"speech model ran: {len(data['lines'])} lines, language {data['language']}")
        log(f"captions engine available: {captions.captions_available()}")
        if getattr(sys, "frozen", False) and not captions.captions_available():
            ok = False
        try:
            import webview  # noqa: F401
            log("pywebview: ok")
        except ImportError as e:
            ok = False
            log(f"pywebview missing: {e}")
    except Exception:
        ok = False
        log(traceback.format_exc())
    log("SELF TEST " + ("PASSED" if ok else "FAILED"))
    text = "\n".join(lines) + "\n"
    if report:
        Path(report).write_text(text, encoding="utf-8")
    if sys.stdout:
        sys.stdout.write(text)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="longform-app", description="Open the Longform desktop app.")
    parser.add_argument("--workspace", type=Path, help="where projects and settings are kept")
    parser.add_argument("--debug", action="store_true", help="enable the web inspector")
    parser.add_argument("--self-test", action="store_true", help="check the app works, then exit")
    parser.add_argument("--report", type=Path, help="with --self-test: also write the results to this file")
    parser.add_argument("--speech", action="store_true", help="with --self-test: also run the speech model")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test(args.report, args.speech)
    return run_app(args.workspace, args.debug)


if __name__ == "__main__":
    sys.exit(main())
