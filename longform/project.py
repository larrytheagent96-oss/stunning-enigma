"""Project model: an ordered list of clips plus output settings, stored as JSON."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".ts", ".mts",
    ".m2ts", ".flv", ".wmv", ".3gp", ".ogv", ".vob", ".mxf",
}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

CLIP_TYPES = ("video", "image", "title")
FIT_MODES = ("contain", "cover", "stretch")
CHAPTER_MODES = ("marked", "clips", "none")
# Styles of FFmpeg's xfade filter (all available since FFmpeg 4.3).
TRANSITION_STYLES = (
    "fade", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "smoothup",
    "smoothdown", "circleopen", "circleclose", "circlecrop", "rectcrop", "radial", "pixelize",
    "distance", "hblur", "zoomin",
)

_COLOR_RE = re.compile(r"^[#A-Za-z0-9@.]+$")
_FPS_RE = re.compile(r"^\d+(\.\d+)?(/\d+)?$")


class ProjectError(ValueError):
    """Raised when a project description is invalid."""


def clip_type_for(path: str) -> str:
    return "image" if Path(path).suffix.lower() in IMAGE_EXTENSIONS else "video"


def is_media_file(path: str | os.PathLike, include_audio: bool = False) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in MEDIA_EXTENSIONS or (include_audio and suffix in AUDIO_EXTENSIONS)


@dataclass
class Output:
    width: int = 1920
    height: int = 1080
    fps: str = "30"
    fit: str = "contain"  # contain = letterbox, cover = crop to fill, stretch = distort
    background: str = "black"
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"
    loudnorm: bool = False
    title: str | None = None

    def validate(self) -> None:
        if not (16 <= self.width <= 8192 and 16 <= self.height <= 8192):
            raise ProjectError("output size must be between 16 and 8192 pixels")
        if self.width % 2 or self.height % 2:
            raise ProjectError("output width and height must be even numbers")
        if not _FPS_RE.match(str(self.fps)):
            raise ProjectError(f"invalid fps {self.fps!r} (use e.g. 30, 29.97 or 30000/1001)")
        if self.fit not in FIT_MODES:
            raise ProjectError(f"fit must be one of {', '.join(FIT_MODES)}")
        if not _COLOR_RE.match(self.background):
            raise ProjectError(f"invalid background colour {self.background!r}")
        if not 0 <= self.crf <= 51:
            raise ProjectError("crf must be between 0 and 51")


@dataclass
class Music:
    path: str
    volume: float = 0.25
    duck: bool = True  # lower the music while the clips have sound
    fade_out: float = 3.0


@dataclass
class Captions:
    enabled: bool = False
    style: str = "clean"  # see captions.CAPTION_STYLES
    size: str = "medium"  # small, medium or large
    model: str = "base"  # speech model: tiny (fastest), base, small (most accurate)
    language: str | None = None  # None = detect automatically

    def validate(self) -> None:
        from .captions import CAPTION_SIZES, CAPTION_STYLES, WHISPER_MODELS
        if self.style not in CAPTION_STYLES:
            raise ProjectError(f"captions: style must be one of {', '.join(CAPTION_STYLES)}")
        if self.size not in CAPTION_SIZES:
            raise ProjectError(f"captions: size must be one of {', '.join(CAPTION_SIZES)}")
        if self.model not in WHISPER_MODELS:
            raise ProjectError(f"captions: model must be one of {', '.join(WHISPER_MODELS)}")


@dataclass
class Clip:
    type: str = "video"
    path: str | None = None
    start: float = 0.0
    end: float | None = None
    duration: float | None = None  # images and titles; for video it caps the length
    text: str | None = None
    font_size: int | None = None
    color: str = "white"
    background: str = "black"
    fade_in: float = 0.0
    fade_out: float = 0.0
    volume: float = 1.0
    mute: bool = False
    chapter: str | None = None
    # Crossfade from the previous clip into this one, in seconds. None uses the project default.
    transition: float | None = None
    transition_style: str | None = None
    captions: bool = True  # include this clip when captions are turned on for the project

    def validate(self, index: int) -> None:
        where = f"clip {index + 1}"
        if self.type not in CLIP_TYPES:
            raise ProjectError(f"{where}: type must be one of {', '.join(CLIP_TYPES)}")
        if self.type in ("video", "image") and not self.path:
            raise ProjectError(f"{where}: a {self.type} clip needs a path")
        if self.type == "title" and not (self.text or "").strip():
            raise ProjectError(f"{where}: a title clip needs text")
        if self.start < 0:
            raise ProjectError(f"{where}: start cannot be negative")
        if self.end is not None and self.end <= self.start:
            raise ProjectError(f"{where}: end must be after start")
        if self.duration is not None and self.duration <= 0:
            raise ProjectError(f"{where}: duration must be positive")
        if self.fade_in < 0 or self.fade_out < 0:
            raise ProjectError(f"{where}: fades cannot be negative")
        if self.volume < 0:
            raise ProjectError(f"{where}: volume cannot be negative")
        if self.transition is not None and self.transition < 0:
            raise ProjectError(f"{where}: transition cannot be negative")
        if self.transition_style is not None:
            check_transition_style(self.transition_style, where)
        for name in ("color", "background"):
            if not _COLOR_RE.match(getattr(self, name)):
                raise ProjectError(f"{where}: invalid {name} {getattr(self, name)!r}")


@dataclass
class Project:
    clips: list[Clip] = field(default_factory=list)
    output: Output = field(default_factory=Output)
    music: Music | None = None
    chapters: str = "marked"  # marked = only clips with a chapter name, clips = every clip
    transition: float = 0.0  # default crossfade between clips, in seconds (0 = straight cut)
    transition_style: str = "fade"
    captions: Captions = field(default_factory=Captions)
    name: str | None = None
    template: str | None = None  # the look last applied in the app
    export_preset: str | None = None  # the platform last chosen in the app

    def validate(self) -> None:
        if not self.clips:
            raise ProjectError("the project has no clips")
        self.output.validate()
        if self.chapters not in CHAPTER_MODES:
            raise ProjectError(f"chapters must be one of {', '.join(CHAPTER_MODES)}")
        if self.transition < 0:
            raise ProjectError("transition cannot be negative")
        check_transition_style(self.transition_style, "project")
        self.captions.validate()
        for i, clip in enumerate(self.clips):
            clip.validate(i)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        if not isinstance(data, dict):
            raise ProjectError("project must be a JSON object")
        clips = []
        for i, raw in enumerate(data.get("clips") or []):
            if isinstance(raw, str):
                raw = {"path": raw}
            if not isinstance(raw, dict):
                raise ProjectError(f"clip {i + 1} must be an object or a path")
            raw = dict(raw)
            if "type" not in raw:
                raw["type"] = clip_type_for(raw["path"]) if raw.get("path") else "title"
            clips.append(_build(Clip, raw, f"clip {i + 1}"))
        output = _build(Output, data.get("output") or {}, "output")
        music = data.get("music")
        if isinstance(music, str):
            music = {"path": music}
        project = cls(
            clips=clips,
            output=output,
            music=_build(Music, music, "music") if music else None,
            chapters=data.get("chapters", "marked"),
        )
        try:
            project.transition = float(data.get("transition") or 0)
        except (TypeError, ValueError) as e:
            raise ProjectError(f"invalid transition {data.get('transition')!r}") from e
        project.transition_style = str(data.get("transition_style") or "fade")
        project.captions = _build(Captions, data.get("captions") or {}, "captions")
        for name in ("name", "template", "export_preset"):
            value = data.get(name)
            setattr(project, name, str(value) if value else None)
        return project

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Project":
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ProjectError(f"{path}: invalid JSON ({e})") from e
        return cls.from_dict(data)

    def save(self, path: str | os.PathLike) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
            f.write("\n")


def check_transition_style(style: str, where: str) -> None:
    if style not in TRANSITION_STYLES:
        raise ProjectError(f"{where}: unknown transition style {style!r} (choose from {', '.join(TRANSITION_STYLES)})")


def _build(cls, raw: dict, where: str):
    known = {f.name: f for f in fields(cls)}
    unknown = set(raw) - set(known)
    if unknown:
        raise ProjectError(f"{where}: unknown field(s) {', '.join(sorted(unknown))}")
    values = {}
    for name, value in raw.items():
        if value is None:
            values[name] = None
            continue
        kind = str(known[name].type)
        try:
            if kind.startswith("int"):
                value = int(value)
            elif kind.startswith("float"):
                value = float(value)
            elif kind.startswith("bool"):
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            elif kind.startswith("str"):
                value = str(value)
        except (TypeError, ValueError) as e:
            raise ProjectError(f"{where}: invalid value for {name}: {value!r}") from e
        values[name] = value
    return cls(**values)
