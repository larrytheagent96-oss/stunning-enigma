# Longform

**Put any videos together into one long video.** Phone clips, camera footage, screen recordings,
photos and music, in any format, size, frame rate or orientation, become one finished video with
transitions, captions, music and chapters. Made for YouTubers and creators.

Everything runs on your computer. Your videos are never uploaded or copied anywhere.

## Download

Get the latest app from the [Releases page](https://github.com/larrytheagent96-oss/stunning-enigma/releases).
Builds for every change are also under **Actions → Desktop app → (latest run) → Artifacts**.

| Your computer | Download | First launch |
| --- | --- | --- |
| Windows 10 / 11 | `Longform-Windows.zip` | Unzip it, open the `Longform` folder and double-click **Longform.exe**. If Windows shows "Windows protected your PC", click **More info → Run anyway** (the app isn't signed with a paid certificate yet). |
| Mac with Apple Silicon (M1 or newer) | `Longform-macOS-AppleSilicon.dmg` | Open the .dmg and drag **Longform** into Applications. The first time, macOS may say it can't check the app: open **System Settings → Privacy & Security** and click **Open Anyway**. |

FFmpeg and the speech-recognition engine are built in, so there's nothing else to install.

## How to make a video

The app walks you through four steps along the left side.

1. **Media.** Click **Add videos, photos & music**, or drag files onto the window. They stay where
   they are on your computer. Your first clips go straight onto the timeline.
2. **Arrange.** On the timeline at the bottom:
   - drag a clip to **reorder** it
   - drag a clip's **left or right edge to trim** it
   - click a clip to change it on the right: trim exactly, volume, mute, fades, chapter name, or a
     different transition
   - click the badge between two clips to change that transition
   - press **Preview** (top right) to watch everything together with transitions, music and captions
3. **Style.** Pick a one-click look:

   | Look | What it does |
   | --- | --- |
   | Vlog | Quick crossfades, music under your voice, a chapter per clip |
   | Podcast / talking head | Straight cuts, even loudness, clean captions |
   | Tutorial | Cuts with boxed captions that stay readable over screen recordings |
   | Shorts energy | Fast slide transitions and big bold captions |
   | Cinematic | Slow dips to black, fades at the very start and end |
   | Photo slideshow | Gentle dissolves, four seconds per photo, music up front |
   | Simple cuts | Just your clips, back to back |

   Then fine-tune transitions (25 styles), background music (which automatically gets quieter when
   people talk), even loudness and chapters.
4. **Captions.** Switch on **Add captions to my video** and pick a look (Clean, Bold pop or Boxed)
   and a size. Speech is turned into text on your computer. The first time, a speech model of about
   150 MB is downloaded. Click **Edit** next to a clip to fix any words, then press **Save captions**.
5. **Export.** Choose where it's going: YouTube, YouTube 4K, Shorts / TikTok / Reels (vertical),
   Instagram post, Square or Quick share (a small file). Then choose a quality and press **Export video**.
   The finished video is saved in your **Videos/Longform** folder (Movies/Longform on a Mac). You can
   change the folder. With it you also get:
   - a chapter list to paste into your YouTube description
   - a `.srt` captions file you can upload to YouTube for subtitles viewers can switch on and off

**Projects.** Click the grid button at the top left to switch projects, start a new one (optionally
from a look), duplicate a project or delete one. Everything saves automatically.

**Shortcuts:** <kbd>Space</kbd> play/pause · <kbd>Ctrl/⌘</kbd>+<kbd>Z</kbd> undo ·
<kbd>Ctrl/⌘</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> redo · <kbd>Delete</kbd> remove clip ·
<kbd>Ctrl/⌘</kbd>+<kbd>D</kbd> duplicate · <kbd>←</kbd>/<kbd>→</kbd> previous/next clip.

## Good to know

- **Long videos are fine.** Each clip is converted once and remembered. When you change one clip,
  only that clip is redone, and the final join copies the video without re-encoding it. Sound and
  picture stay in sync over hours of footage.
- **Any format.** If the preview window can't play a format directly (for example some HEVC,
  ProRes or AVI files), the app quietly makes a small preview copy. Your export always uses the original.
- **Clips with different shapes** (vertical phone video in a landscape export, for example) get black
  bars, or are cropped to fill if you choose **Crop to fill** in Export.
- **Moved a file?** The clip shows as missing. Add the file again from its new place.

## For developers

Requires Python 3.10+ and [FFmpeg](https://ffmpeg.org/download.html) on your PATH.

```bash
pip install ".[app]"     # the desktop window and the captions engine
longform app             # open the desktop app
longform ui              # or open the editor in your browser
```

### Command line

```bash
longform join ./footage -o film.mp4 --template vlog --export youtube --music song.mp3
longform join intro.mp4 ./day1 ./day2 -o trip.mp4 --crossfade 1 --transition dissolve --chapters
longform join ./talk -o podcast.mp4 --captions --caption-style boxed     # needs faster-whisper
longform init ./footage -o film.json   # write a project file to edit, then:
longform render film.json -o film.mp4 --jobs 4
longform probe ./footage               # length, size, frame rate and sound of each file
```

`--template` takes `vlog`, `podcast`, `tutorial`, `shorts`, `cinematic`, `slideshow` or `simple`.
`--export` takes `youtube`, `youtube-4k`, `shorts`, `instagram`, `square` or `small`. Individual
options like `--size`, `--crf` and `--crossfade` override them. Folders are sorted naturally
(`clip2` before `clip10`).

### Project files

```json
{
  "name": "Summer trip",
  "output": { "width": 1920, "height": 1080, "fps": "30", "fit": "contain", "crf": 20, "loudnorm": true },
  "transition": 0.5, "transition_style": "fade", "chapters": "marked",
  "music": { "path": "music/theme.mp3", "volume": 0.25, "duck": true },
  "captions": { "enabled": true, "style": "clean", "size": "medium", "model": "base" },
  "clips": [
    { "type": "title", "text": "Summer trip\n2026", "duration": 4, "chapter": "Intro" },
    { "path": "footage/a.mp4", "start": 12.5, "end": 95, "chapter": "Day 1" },
    { "path": "footage/b.mov", "volume": 0.6, "transition": 2, "transition_style": "wipeleft" },
    { "path": "footage/c.mp4", "mute": true, "captions": false },
    { "path": "photos/sunset.jpg", "duration": 6, "transition": 0 }
  ]
}
```

Paths are relative to the project file.

| Clip field | Meaning |
| --- | --- |
| `start`, `end` | Trim points in seconds. |
| `duration` | How long to show a photo or title card. For a video, it caps the length. |
| `text`, `font_size`, `color`, `background` | Title card text and style. |
| `fade_in`, `fade_out` | Fade from or to black. Only applies to an edge without a crossfade. |
| `transition`, `transition_style` | Crossfade into this clip; overrides the project default (`0` = cut). |
| `volume`, `mute`, `captions` | Sound level, silence the clip, leave it out of captions. |
| `chapter` | Starts a chapter here. |

### How rendering works

The timeline is cut into pieces: the body of each clip, plus a short piece for each crossfade, made from
the end of one clip and the start of the next. Every piece is encoded to the same size, frame rate and
audio format, with lengths snapped to whole frames, and cached. Captions are burned into the pieces
they appear in. The pieces are then joined without re-encoding the video, and only the audio (music,
loudness) is mixed once at the end.

### Building the desktop app

`.github/workflows/desktop-app.yml` does this on every push: it runs the tests (including real speech
recognition), builds the Windows and macOS apps with PyInstaller, runs each packaged app's
`--self-test --speech`, and uploads the installers. Pushing a tag like `v1.0.0` publishes them as a release.
To build locally, put `ffmpeg` and `ffprobe` in `packaging/ffmpeg/`, then run
`pyinstaller packaging/longform.spec --noconfirm`.

### Tests

```bash
python -m unittest discover -s tests -v
LONGFORM_TEST_WHISPER=1 python -m unittest discover -s tests   # also run the real speech model
```

### Licenses

Longform bundles the [Inter](https://github.com/rsms/inter) font (SIL Open Font License, see
`longform/fonts/OFL.txt`). The packaged apps include FFmpeg builds licensed under the GPL, and
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT).
