# longform

Put any videos together into one long-form video.

Drop in clips from anywhere: phone videos, screen recordings, camera footage, GIFs,
photos, voice memos. They can have different resolutions, frame rates, orientations,
codecs, or no sound at all. longform turns them into one clean video with title
cards, crossfade transitions, fades, background music and chapters. You can use it from the command line
or from a drag-and-drop editor in your browser.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/download.html) (`ffmpeg` and `ffprobe` on your PATH)

No other dependencies.

```bash
pip install .          # installs the `longform` command
# or run it without installing:
python -m longform --help
```

## The editor

```bash
longform ui
```

This opens `http://localhost:8765` with three panels:

- **Library**: drop files to upload them. Each file shows a thumbnail, length, size and whether it has sound.
- **Timeline**: clips play top to bottom. Drag them, or use ↑/↓, to reorder. Click a clip to trim it,
  add fades, change the volume, mute it, give it a chapter name, or set its own transition from the
  previous clip. Use **+ Title card** to add text screens.
  The running total length is shown at the top.
- **Output**: choose the size (4K, 1080p, 720p, vertical, square or custom), frame rate, how to handle
  clips with a different shape (black bars, crop or stretch), quality, crossfade transitions between
  clips (length and style), background music and loudness
  normalisation. Then click **Render video**. You get progress with an estimated time left, a preview,
  a download button, and a chapter list you can paste into a YouTube description.

Everything is stored in a workspace folder (`./longform-workspace` by default; change it with
`--workspace`): `media/` holds your files, `renders/` holds finished videos, and `project.json` holds the timeline.
The project saves automatically.

## Command line

Join files or whole folders in one go. Folders are sorted by name, so `clip2` comes before `clip10`:

```bash
longform join intro.mp4 ./day1 ./day2 outro.mov -o vlog.mp4
longform join ./footage -o film.mp4 --title "Summer 2026" --fade 0.5 --chapters \
    --music soundtrack.mp3 --size 1080p --fps 30
longform join ./footage -o film.mp4 --crossfade 1 --transition dissolve
```

`--crossfade SECONDS` blends every clip into the next. `--transition` picks the style: `fade` (default),
`dissolve`, `fadeblack`, `fadewhite`, `wipeleft`/`right`/`up`/`down`, `slideleft`/`right`/`up`/`down`,
`smoothleft`/`right`/`up`/`down`, `circleopen`, `circleclose`, `circlecrop`, `rectcrop`, `radial`,
`pixelize`, `distance`, `hblur` or `zoomin`.

For more control, write a project file, edit it, then render it:

```bash
longform init ./footage -o film.json
longform render film.json -o film.mp4 --jobs 4
```

Other commands:

```bash
longform probe ./footage        # length, size, frame rate and audio of each file, plus the total
longform ui --port 9000 --workspace ~/Videos/project
```

### Project file

```json
{
  "output": { "width": 1920, "height": 1080, "fps": "30", "fit": "contain",
              "crf": 20, "preset": "medium", "loudnorm": true, "title": "My film" },
  "chapters": "marked",
  "transition": 1,
  "transition_style": "fade",
  "music": { "path": "music/theme.mp3", "volume": 0.25, "duck": true, "fade_out": 3 },
  "clips": [
    { "type": "title", "text": "My film\nPart one", "duration": 4, "fade_in": 1, "chapter": "Intro" },
    { "path": "footage/a.mp4", "start": 12.5, "end": 95, "fade_out": 0.5, "chapter": "The trip" },
    { "path": "footage/b.mov", "volume": 0.6, "transition": 2, "transition_style": "wipeleft" },
    { "path": "footage/c.mp4", "mute": true },
    { "path": "photos/sunset.jpg", "duration": 6, "transition": 0 },
    "footage/d.mkv"
  ]
}
```

Paths are relative to the project file. A clip can be just a path string.

| Clip field | Meaning |
| --- | --- |
| `type` | `video`, `image` or `title`. Detected from the file extension when left out. |
| `start`, `end` | Trim points in seconds (video). |
| `duration` | How long to show an image or title card. For a video, it caps the length. |
| `text`, `font_size`, `color`, `background` | Title card text and style. |
| `fade_in`, `fade_out` | Fade from or to black and silence, in seconds. Only applies to an edge that isn't crossfading. |
| `transition`, `transition_style` | Crossfade from the previous clip into this one. Overrides the project default; `0` makes a straight cut. |
| `volume`, `mute` | Clip audio level. |
| `chapter` | Starts a chapter here. |

| Setting | Meaning |
| --- | --- |
| `output.fit` | `contain` adds black bars, `cover` crops to fill, `stretch` distorts to fit. |
| `output.crf` / `preset` | x264 quality (lower is better) and speed. |
| `output.loudnorm` | Normalise loudness to −16 LUFS, the usual level for online video. |
| `transition` / `transition_style` | Default crossfade between clips, in seconds (`0` = straight cuts), and its style. |
| `chapters` | `marked` adds chapters only for clips with a `chapter` name, `clips` adds one per clip, `none` adds none. |
| `music.duck` | Lower the music automatically while the clips have sound. |

## How it handles long videos

1. Each clip is converted once into a standard segment: same size, frame rate, pixel format and
   48 kHz stereo audio. Clips without sound get silence, so audio and video stay in sync.
2. Segments are cached in `.longform-cache` (next to the output; the editor keeps them in its
   workspace). If you change one clip in a two-hour project, only that clip is re-encoded.
   Clips used more than once are encoded once. Use `--jobs` to encode several clips in parallel.
   A crossfade becomes its own short segment, made from the end of one clip and the start of the
   next. Each clip's own segment is shortened by the overlap, so changing a transition only re-encodes
   that transition. A clip gives at most half its length to each neighbour, so short clips get
   shorter transitions.
3. All lengths are snapped to whole video frames, so every segment's audio is exactly as long as its
   video. Across hundreds of clips and transitions the sound stays in sync.
4. The segments are joined without re-encoding the video. Only the audio is mixed (with music and
   loudness, if set) and encoded once, so the final step is fast and audio doesn't drift over long runs.
5. Chapters start where each clip begins to appear. They are written into the MP4/MKV and also saved as `<output>.chapters.txt` with YouTube-style timestamps.

The cache can take a lot of disk space for long projects. `longform render --no-cache` deletes it
afterwards. `longform join` deletes it by default; pass `--keep-cache` to keep it.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests generate their own media with FFmpeg and cover rendering, trimming, crossfades, caching,
music, cancellation, the CLI and the editor's HTTP API.
