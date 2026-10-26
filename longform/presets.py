"""One-click looks (templates) and platform export settings.

A preset is a set of changes merged into a project. The app shows them as cards
and the command line accepts them with --template and --export.
"""

from __future__ import annotations

import copy

TEMPLATES = [
    {
        "id": "vlog", "name": "Vlog", "icon": "🎒",
        "description": "Quick crossfades, music tucked under your voice, a chapter for every clip.",
        "changes": {"transition": 0.5, "transition_style": "fade", "chapters": "clips",
                    "output": {"loudnorm": True}, "captions": {"enabled": False},
                    "music": {"volume": 0.18, "duck": True}},
    },
    {
        "id": "podcast", "name": "Podcast / talking head", "icon": "🎙️",
        "description": "Straight cuts, even loudness and clean captions so every word lands.",
        "changes": {"transition": 0, "chapters": "marked", "output": {"loudnorm": True},
                    "captions": {"enabled": True, "style": "clean", "size": "medium"},
                    "music": {"volume": 0.08, "duck": True}},
    },
    {
        "id": "tutorial", "name": "Tutorial", "icon": "🧑‍🏫",
        "description": "Cuts with boxed captions that stay readable over screen recordings.",
        "changes": {"transition": 0, "chapters": "marked", "output": {"loudnorm": True},
                    "captions": {"enabled": True, "style": "boxed", "size": "medium"},
                    "music": {"volume": 0.1, "duck": True}},
    },
    {
        "id": "shorts", "name": "Shorts energy", "icon": "⚡",
        "description": "Fast slide transitions and big bold captions, made for vertical video.",
        "changes": {"transition": 0.25, "transition_style": "slideleft", "chapters": "none",
                    "output": {"loudnorm": True}, "captions": {"enabled": True, "style": "bold", "size": "large"},
                    "music": {"volume": 0.15, "duck": True}},
    },
    {
        "id": "cinematic", "name": "Cinematic", "icon": "🎬",
        "description": "Slow dips to black, a fade in and out at the very start and end, music up front.",
        "changes": {"transition": 1.0, "transition_style": "fadeblack", "chapters": "marked",
                    "output": {"loudnorm": True}, "captions": {"enabled": False},
                    "music": {"volume": 0.35, "duck": True}, "edge_fade": 1.5},
    },
    {
        "id": "slideshow", "name": "Photo slideshow", "icon": "🖼️",
        "description": "Gentle dissolves, four seconds per photo and the music carrying the story.",
        "changes": {"transition": 1.0, "transition_style": "dissolve", "chapters": "none",
                    "captions": {"enabled": False}, "music": {"volume": 0.7, "duck": False},
                    "image_duration": 4},
    },
    {
        "id": "simple", "name": "Simple cuts", "icon": "✂️",
        "description": "No transitions, no captions: just your clips back to back.",
        "changes": {"transition": 0, "chapters": "marked", "captions": {"enabled": False}},
    },
]

EXPORTS = [
    {"id": "youtube", "name": "YouTube", "detail": "1080p · 16:9", "icon": "▶️",
     "changes": {"output": {"width": 1920, "height": 1080, "fit": "contain", "crf": 20, "preset": "medium"}}},
    {"id": "youtube-4k", "name": "YouTube 4K", "detail": "2160p · 16:9 · slower", "icon": "🖥️",
     "changes": {"output": {"width": 3840, "height": 2160, "fit": "contain", "crf": 20, "preset": "medium"}}},
    {"id": "shorts", "name": "Shorts · TikTok · Reels", "detail": "1080×1920 · 9:16", "icon": "📱",
     "changes": {"output": {"width": 1080, "height": 1920, "fit": "cover", "crf": 20, "preset": "medium"}}},
    {"id": "instagram", "name": "Instagram post", "detail": "1080×1350 · 4:5", "icon": "📷",
     "changes": {"output": {"width": 1080, "height": 1350, "fit": "cover", "crf": 20, "preset": "medium"}}},
    {"id": "square", "name": "Square", "detail": "1080×1080 · 1:1", "icon": "⬛",
     "changes": {"output": {"width": 1080, "height": 1080, "fit": "cover", "crf": 20, "preset": "medium"}}},
    {"id": "small", "name": "Quick share", "detail": "720p · small file · fast", "icon": "💬",
     "changes": {"output": {"width": 1280, "height": 720, "fit": "contain", "crf": 28, "preset": "veryfast"}}},
]

QUALITIES = {"good": 26, "better": 22, "best": 18}


def find(presets: list[dict], preset_id: str) -> dict:
    for preset in presets:
        if preset["id"] == preset_id:
            return preset
    raise KeyError(f"unknown preset {preset_id!r} (choose from {', '.join(p['id'] for p in presets)})")


def apply(project: dict, preset: dict) -> dict:
    """Return a copy of a project dict with a preset's changes merged in."""
    result = copy.deepcopy(project)
    for key, value in preset["changes"].items():
        if key == "music":
            if result.get("music"):  # only adjust music the user has chosen
                result["music"] = {**result["music"], **value}
        elif key == "edge_fade":
            clips = result.get("clips") or []
            if clips:
                clips[0]["fade_in"] = value
                clips[-1]["fade_out"] = value
        elif key == "image_duration":
            for clip in result.get("clips") or []:
                if clip.get("type") == "image":
                    clip["duration"] = value
        elif isinstance(value, dict):
            result[key] = {**(result.get(key) or {}), **value}
        else:
            result[key] = value
    if preset in TEMPLATES:
        result["template"] = preset["id"]
    elif preset in EXPORTS:
        result["export_preset"] = preset["id"]
    return result


def catalog() -> dict:
    """Everything the app needs to draw its style and export choices."""
    from .captions import CAPTION_STYLES, captions_available
    from .project import TRANSITION_STYLES
    return {
        "templates": TEMPLATES,
        "exports": EXPORTS,
        "qualities": QUALITIES,
        "caption_styles": [{"id": k, "name": v["label"]} for k, v in CAPTION_STYLES.items()],
        "transition_styles": list(TRANSITION_STYLES),
        "captions_available": captions_available(),
    }
