# PyInstaller recipe for the Longform desktop app: pyinstaller packaging/longform.spec --noconfirm
# Put ffmpeg and ffprobe for the target platform in packaging/ffmpeg/ first (CI downloads them).
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

root = Path(SPECPATH).parent
version = re.search(r'__version__ = "([^"]+)"', (root / "longform" / "__init__.py").read_text())[1]
ffmpeg_dir = Path(os.environ.get("LONGFORM_FFMPEG_DIR", root / "packaging" / "ffmpeg"))
if not any(ffmpeg_dir.glob("ffmpeg*")):
    raise SystemExit(f"Put ffmpeg and ffprobe in {ffmpeg_dir} before building")

datas = [
    (str(root / "longform" / "static"), "longform/static"),
    (str(root / "longform" / "fonts"), "longform/fonts"),
]
datas += collect_data_files("faster_whisper")  # the voice-activity model
binaries = [(str(p), "ffmpeg") for p in ffmpeg_dir.iterdir() if p.is_file() and p.name.startswith(("ffmpeg", "ffprobe"))]
binaries += collect_dynamic_libs("ctranslate2")

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["faster_whisper", "ctranslate2", "onnxruntime", "webview"],
    excludes=["PIL", "tkinter", "matplotlib", "IPython", "pytest", "torch", "tensorflow"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = str(root / "packaging" / "icon.png")
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Longform", console=False, icon=icon,
          upx=False, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, name="Longform", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="Longform.app", icon=icon, bundle_identifier="app.longform.editor", version=version,
        info_plist={
            "CFBundleDisplayName": "Longform",
            "CFBundleShortVersionString": version,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
