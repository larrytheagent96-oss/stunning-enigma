import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from longform import captions, cli, ffmpeg, presets
from longform.ffmpeg import Cancelled
from longform.project import Project, ProjectError
from longform.render import RenderError, pieces_for, plan, render
from longform.server import make_server

SMALL = {"width": 160, "height": 90, "fps": "25", "preset": "ultrafast"}


def make(path: Path, *inputs: str, extra: tuple = ()) -> Path:
    args = ["ffmpeg", "-v", "error", "-y"]
    for source in inputs:
        args += ["-f", "lavfi", "-i", source]
    subprocess.run([*args, *extra, str(path)], check=True)
    return path


class MediaTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.dir = Path(cls.tmp.name)
        make(d / "clip1.mp4", "testsrc=s=320x240:r=30:d=2", "sine=f=440:d=2", extra=("-shortest",))
        make(d / "clip10.mov", "testsrc2=s=200x400:r=60:d=1.5")  # portrait, no audio
        make(d / "clip2.mkv", "color=c=red:s=640x360:r=24:d=1", "sine=f=880:d=1:sample_rate=22050",
             extra=("-shortest", "-ac", "1"))
        make(d / "still.png", "mandelbrot=s=120x80", extra=("-frames:v", "1"))
        make(d / "voice.mp3", "sine=f=300:d=1.5")
        make(d / "music.mp3", "sine=f=220:d=1")
        make(d / "talk.mp4", "testsrc=s=320x240:r=25:d=4",
             "flite=text='Hello and welcome back to the channel. Today we are testing captions.'",
             extra=("-af", "apad", "-t", "4"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def render(self, data: dict, name: str = "out.mp4", **kwargs):
        data = {**data, "output": {**SMALL, **data.get("output", {})}}
        out = self.dir / "out" / name
        result = render(Project.from_dict(data), out, base_dir=self.dir, **kwargs)
        return result, ffmpeg.probe(out)


class ProjectTests(unittest.TestCase):
    def test_paths_become_clips_with_detected_types(self):
        p = Project.from_dict({"clips": ["a.mp4", "b.JPG", {"type": "title", "text": "Hi"}]})
        self.assertEqual([c.type for c in p.clips], ["video", "image", "title"])
        p.validate()

    def test_values_are_coerced(self):
        p = Project.from_dict({"clips": [{"path": "a.mp4", "start": "1.5"}], "output": {"width": "1280", "fps": 25}})
        self.assertEqual(p.clips[0].start, 1.5)
        self.assertEqual(p.output.width, 1280)
        self.assertEqual(p.output.fps, "25")

    def test_invalid_projects_are_rejected(self):
        bad = [
            {"clips": []},
            {"clips": [{"path": "a.mp4", "speed": 2}]},
            {"clips": [{"path": "a.mp4", "start": 5, "end": 2}]},
            {"clips": [{"type": "title", "text": "  "}]},
            {"clips": ["a.mp4"], "output": {"width": 1281}},
            {"clips": ["a.mp4"], "output": {"fps": "fast"}},
            {"clips": [{"type": "title", "text": "x", "color": "red:x=1"}]},
            {"clips": ["a.mp4", "b.mp4"], "transition_style": "spin"},
            {"clips": ["a.mp4", {"path": "b.mp4", "transition": -1}]},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ProjectError):
                Project.from_dict(data).validate()

    def test_round_trip(self):
        p = Project.from_dict({"clips": ["a.mp4"], "music": "m.mp3", "chapters": "clips"})
        self.assertEqual(Project.from_dict(p.to_dict()).to_dict(), p.to_dict())


class RenderTests(MediaTestCase):
    def test_mixed_sources_join_into_one_video(self):
        result, info = self.render({
            "clips": [
                {"type": "title", "text": "Hello\nWorld", "duration": 1, "chapter": "Intro", "fade_in": 0.3},
                {"path": "clip1.mp4", "chapter": "Part 1", "fade_out": 0.5},
                {"path": "clip10.mov"},
                {"path": "clip2.mkv", "volume": 0.5},
                {"path": "still.png", "duration": 1, "chapter": "End"},
            ],
            "output": {"title": "Test film"},
        })
        self.assertAlmostEqual(result.duration, 1 + 2 + 1.5 + 1 + 1, delta=0.1)
        self.assertAlmostEqual(info.duration, result.duration, delta=0.1)
        self.assertEqual((info.width, info.height), (160, 90))
        self.assertTrue(info.has_audio)
        self.assertEqual([c.title for c in result.chapters], ["Intro", "Part 1", "End"])
        self.assertAlmostEqual(result.chapters[1].start, 1.0, delta=0.05)
        self.assertEqual(result.chapter_text().splitlines()[0], "00:00 Intro")
        chapters = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(result.output)],
            capture_output=True, text=True, check=True).stdout)["chapters"]
        self.assertEqual(len(chapters), 3)

    def test_trimming_and_cache_reuse(self):
        data = {"clips": [{"path": "clip1.mp4", "start": 0.5, "end": 1.5}, {"path": "clip2.mkv", "duration": 0.5}]}
        first, info = self.render(data, "trim.mp4")
        self.assertAlmostEqual(info.duration, 1.5, delta=0.1)
        self.assertEqual(first.segments_rendered, 2)
        data["clips"][1]["chapter"] = "renamed"  # chapter names don't need a re-encode
        second, _ = self.render(data, "trim2.mp4")
        self.assertEqual((second.segments_rendered, second.segments_cached), (0, 2))
        data["clips"][0]["end"] = 1.0
        third, info = self.render(data, "trim3.mp4")
        self.assertEqual((third.segments_rendered, third.segments_cached), (1, 1))
        self.assertAlmostEqual(info.duration, 1.0, delta=0.1)

    def test_repeated_clips_are_encoded_once(self):
        result, info = self.render({"clips": [{"path": "clip2.mkv", "fade_in": 0.1}] * 6}, "repeat.mp4", jobs=4)
        self.assertEqual(result.segments_rendered, 1)
        self.assertAlmostEqual(info.duration, 6, delta=0.15)

    def test_audio_only_music_and_loudness(self):
        result, info = self.render({
            "clips": [{"path": "voice.mp3"}, {"path": "clip10.mov"}],
            "music": {"path": "music.mp3", "volume": 0.3},  # shorter than the video, so it loops
            "output": {"loudnorm": True, "fit": "cover"},
        }, "music.mkv", keep_cache=False)
        self.assertAlmostEqual(info.duration, 3.0, delta=0.15)
        self.assertTrue(info.has_audio and info.has_video)
        self.assertFalse((self.dir / "out" / ".longform-cache").exists())

    def test_music_mix_keeps_audio_as_long_as_video(self):
        result, _ = self.render({
            "clips": [{"type": "title", "text": "Hi", "duration": 2}, {"path": "clip1.mp4"},
                      {"path": "clip10.mov"}, {"path": "still.png", "duration": 2, "fade_out": 0.5}],
            "transition": 0.4, "music": {"path": "music.mp3", "volume": 0.3},
            "output": {"preset": "medium"},  # the bug depended on how video and audio packets interleave
        }, "music-sync.mp4")
        streams = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
             str(result.output)], capture_output=True, text=True, check=True).stdout)["streams"]
        lengths = {s["codec_type"]: float(s["duration"]) for s in streams if s["codec_type"] in ("video", "audio")}
        self.assertAlmostEqual(lengths["audio"], lengths["video"], delta=0.005)

    def test_missing_file_and_bad_trim_are_reported(self):
        with self.assertRaisesRegex(RenderError, "clip 1"):
            self.render({"clips": ["nope.mp4"]})
        with self.assertRaisesRegex(RenderError, "past the end"):
            self.render({"clips": [{"path": "clip1.mp4", "start": 10}]})

    def test_cancel_stops_render(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            self.render({"clips": [{"path": "clip1.mp4", "fade_in": 0.123}]}, "cancel.mp4", cancel=cancel)
        self.assertFalse((self.dir / "out" / "cancel.mp4").exists())

    def test_progress_reaches_one(self):
        seen = []
        self.render({"clips": [{"path": "clip2.mkv", "fade_out": 0.2}]}, "progress.mp4",
                    progress=lambda f, m: seen.append(f))
        self.assertEqual(seen[-1], 1.0)
        self.assertEqual(seen, sorted(seen))


def fake_transcriber(calls: list):
    def transcribe(path, **kwargs):
        calls.append(path.name)
        return {"language": "en", "lines": [{"start": 0.5, "end": 1.5, "text": "first {line}"},
                                            {"start": 2.0, "end": 3.5, "text": "second line"}]}
    return transcribe


class PresetTests(unittest.TestCase):
    def test_every_preset_produces_a_valid_project(self):
        base = {"clips": [{"path": "a.mp4"}, {"path": "b.png", "type": "image"}], "music": {"path": "m.mp3"}}
        for preset in presets.TEMPLATES + presets.EXPORTS:
            with self.subTest(preset=preset["id"]):
                Project.from_dict(presets.apply(base, preset)).validate()

    def test_templates_change_only_what_they_describe(self):
        base = {"clips": [{"path": "a.mp4"}, {"path": "b.png", "type": "image", "duration": 9}],
                "output": {"width": 1280, "height": 720}}
        slideshow = presets.apply(base, presets.find(presets.TEMPLATES, "slideshow"))
        self.assertEqual(slideshow["clips"][1]["duration"], 4)
        self.assertNotIn("music", slideshow)  # no music chosen, so none is invented
        self.assertEqual(slideshow["output"]["width"], 1280)  # looks never change the size
        self.assertEqual(slideshow["template"], "slideshow")
        cinematic = presets.apply(base, presets.find(presets.TEMPLATES, "cinematic"))
        self.assertEqual((cinematic["clips"][0]["fade_in"], cinematic["clips"][-1]["fade_out"]), (1.5, 1.5))
        self.assertNotIn("fade_in", base["clips"][0])  # the original is untouched
        shorts = presets.apply(cinematic, presets.find(presets.EXPORTS, "shorts"))
        self.assertEqual((shorts["output"]["width"], shorts["output"]["height"], shorts["output"]["fit"]),
                         (1080, 1920, "cover"))
        self.assertEqual(shorts["transition_style"], "fadeblack")
        with self.assertRaises(KeyError):
            presets.find(presets.TEMPLATES, "nope")


class CaptionTests(MediaTestCase):
    def test_words_are_grouped_into_short_lines(self):
        words = [{"start": i * 0.3, "end": i * 0.3 + 0.25, "word": f" word{i}"} for i in range(12)]
        words[5]["word"] = " end."
        words.append({"start": 10, "end": 10.5, "word": " later"})
        lines = captions.group_words(words)
        self.assertTrue(all(len(l["text"]) <= captions.MAX_LINE_CHARS + 8 for l in lines))
        self.assertTrue(lines[0]["text"].endswith("end."))  # sentence ends start a new caption
        self.assertEqual(lines[-1]["text"], "later")  # so does a long pause

    def test_documents(self):
        lines = [{"start": 1.0, "end": 2.5, "text": "a {b} \\c\nd"}]
        ass = captions.ass_document(lines, 1920, 1080, "bold", "large")
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,A (B) /C\\ND", ass)
        self.assertIn("PlayResX: 1920", ass)
        self.assertEqual(captions.srt_document(lines).splitlines()[1], "00:00:01,000 --> 00:00:02,500")
        moved = captions.lines_in_window([{"start": 1, "end": 3, "text": "x"}, {"start": 9, "end": 10, "text": "y"}], 2, 5)
        self.assertEqual(moved, [{"start": 0.0, "end": 1, "text": "x"}])

    def test_captions_are_burned_in_and_exported_as_srt(self):
        calls = []
        data = {"clips": [{"path": "talk.mp4", "start": 0.5}, {"path": "clip10.mov"},
                          {"path": "clip1.mp4", "captions": False}, {"path": "clip2.mkv", "mute": True}],
                "transition": 0.4, "captions": {"enabled": True, "style": "boxed"}}
        result, info = self.render(data, "caps.mp4", transcriber=fake_transcriber(calls),
                                   captions_dir=self.dir / "transcripts")
        self.assertEqual(calls, ["talk.mp4"])  # silent, opted-out and muted clips are skipped
        srt = result.captions_file.read_text()
        self.assertIn("00:00:00,000 --> 00:00:01,000\nfirst {line}", srt)  # trimmed by 0.5s
        self.assertIn("00:00:01,500 --> 00:00:03,000\nsecond line", srt)

        plain, _ = self.render({**data, "captions": {"enabled": False}}, "nocaps.mp4")
        self.assertIsNone(plain.captions_file)
        # The burned-in caption changes the picture where it is shown, and only there.
        def frame(path, t):
            return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                                   "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
        self.assertNotEqual(frame(result.output, 0.5), frame(plain.output, 0.5))

        # Editing the transcript re-encodes only what shows captions, without transcribing again.
        store = captions.CaptionStore(self.dir / "transcripts")
        edited = store.load(self.dir / "talk.mp4")
        edited["lines"][0]["text"] = "fixed typo"
        store.save(self.dir / "talk.mp4", edited)
        again, _ = self.render(data, "caps2.mp4", transcriber=fake_transcriber(calls),
                               captions_dir=self.dir / "transcripts")
        self.assertEqual(calls, ["talk.mp4"])
        self.assertIn("fixed typo", again.captions_file.read_text())
        self.assertGreaterEqual(again.segments_cached, 3)

    def test_audio_is_decoded_with_ffmpeg(self):
        try:
            import numpy  # noqa: F401  (comes with the speech engine)
        except ImportError:
            self.skipTest("numpy not installed")
        audio = captions.load_audio(self.dir / "talk.mp4")
        self.assertAlmostEqual(len(audio) / captions.SPEECH_RATE, 4.0, delta=0.15)  # AAC adds padding
        self.assertGreater(float(abs(audio).max()), 0.05)
        self.assertEqual(len(captions.load_audio(self.dir / "clip10.mov")), 0)  # no sound track

    @unittest.skipUnless(os.environ.get("LONGFORM_TEST_WHISPER"), "set LONGFORM_TEST_WHISPER=1 to run the real model")
    def test_real_speech_recognition(self):
        result = captions.whisper_transcribe(self.dir / "talk.mp4", model="tiny")
        text = " ".join(l["text"] for l in result["lines"]).lower()
        self.assertIn("welcome", text)  # the 4s test clip cuts the sentence off after "testing"
        self.assertIn("channel", text)
        self.assertTrue(all(l["end"] > l["start"] for l in result["lines"]))


class TransitionTests(MediaTestCase):
    def test_crossfades_overlap_clips_and_keep_sound_in_sync(self):
        result, info = self.render({
            "clips": [
                {"type": "title", "text": "Hi", "duration": 2, "chapter": "Intro"},
                {"path": "clip1.mp4", "chapter": "Main"},                       # 2s
                {"path": "clip10.mov", "transition": 0.4, "transition_style": "wipeleft"},  # 1.5s
                {"path": "still.png", "duration": 2, "transition": 0},          # straight cut
            ],
            "transition": 1, "transition_style": "dissolve",
        }, "xfade.mp4")
        # Lengths snap to whole frames: the 1.5s clip is 37.5 frames at 25fps, so it becomes 1.52s.
        self.assertAlmostEqual(result.duration, 2 + 2 + 1.52 + 2 - 1 - 0.4, delta=0.001)
        streams = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
             str(result.output)], capture_output=True, text=True, check=True).stdout)["streams"]
        lengths = {s["codec_type"]: float(s["duration"]) for s in streams if s["codec_type"] in ("video", "audio")}
        self.assertAlmostEqual(lengths["video"], lengths["audio"], delta=0.005)
        # A chapter starts where its clip begins to fade in.
        self.assertEqual([(c.title, round(c.start, 2)) for c in result.chapters], [("Intro", 0), ("Main", 1.0)])

    def test_overlap_is_limited_to_half_of_each_clip(self):
        project = Project.from_dict({
            "clips": [{"path": "clip2.mkv"}, {"path": "clip1.mp4"}, {"path": "still.png", "duration": 0.6}],
            "transition": 5, "output": SMALL,
        })
        plans = plan(project, self.dir)
        self.assertEqual([p.transition_in for p in plans], [0, 0.48, 0.28])  # whole frames at 25fps
        pieces = pieces_for(project, plans, self.dir)
        self.assertEqual([p.kind for p in pieces], ["body", "transition", "body", "transition", "body"])
        self.assertAlmostEqual(sum(p.length for p in pieces), 1 + 2 + 0.6 - 0.48 - 0.28, places=6)

    def test_clip_fades_only_apply_to_edges_without_a_crossfade(self):
        project = Project.from_dict({
            "clips": [{"path": "clip1.mp4", "fade_in": 0.5, "fade_out": 0.5},
                      {"path": "clip2.mkv", "fade_in": 0.3, "fade_out": 0.3}],
            "transition": 0.4, "output": SMALL,
        })
        bodies = [p for p in pieces_for(project, plan(project, self.dir), self.dir) if p.kind == "body"]
        self.assertEqual([(b.fade_in, b.fade_out) for b in bodies], [(0.5, 0.0), (0.0, 0.3)])

    def test_changing_a_transition_only_re_encodes_that_transition(self):
        data = {"clips": [{"path": "clip1.mp4"}, {"path": "clip2.mkv"}, {"path": "clip10.mov"}],
                "transition": 0.4}
        first, _ = self.render(data, "xf1.mp4")
        self.assertEqual(first.segments_rendered, 5)
        data["clips"][2]["transition_style"] = "slideup"
        second, _ = self.render(data, "xf2.mp4")
        self.assertEqual((second.segments_rendered, second.segments_cached), (1, 4))


class CliTests(MediaTestCase):
    def run_cli(self, *args) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([str(a) for a in args])
        return code, out.getvalue() + err.getvalue()

    def test_folders_are_sorted_naturally(self):
        names = [p.name for p in cli.expand_inputs([str(self.dir)])]
        self.assertEqual(names, ["clip1.mp4", "clip2.mkv", "clip10.mov", "still.png", "talk.mp4"])

    def test_join(self):
        out = self.dir / "cli" / "joined.mp4"
        code, text = self.run_cli("join", self.dir / "clip1.mp4", self.dir / "clip2.mkv", "-o", out,
                                  "--size", "160x90", "--preset", "ultrafast", "--chapters",
                                  "--title", "My film", "--fade", "0.2", "-q")
        self.assertEqual(code, 0, text)
        self.assertAlmostEqual(ffmpeg.probe(out).duration, 4 + 2 + 1, delta=0.15)
        self.assertIn("00:04 clip1", (self.dir / "cli" / "joined.chapters.txt").read_text())

    def test_init_then_render(self):
        project_file = self.dir / "proj.json"
        code, text = self.run_cli("init", self.dir / "clip2.mkv", self.dir / "still.png", "-o", project_file,
                                  "--image-duration", "1", "--force")
        self.assertEqual(code, 0, text)
        data = json.loads(project_file.read_text())
        self.assertEqual([c["path"] for c in data["clips"]], ["clip2.mkv", "still.png"])
        code, text = self.run_cli("render", project_file, "-o", self.dir / "cli" / "proj.mp4",
                                  "--size", "160x90", "--preset", "ultrafast", "-q")
        self.assertEqual(code, 0, text)
        self.assertAlmostEqual(ffmpeg.probe(self.dir / "cli" / "proj.mp4").duration, 2, delta=0.1)

    def test_join_with_crossfade(self):
        out = self.dir / "cli" / "xfade.mp4"
        code, text = self.run_cli("join", self.dir / "clip1.mp4", self.dir / "clip2.mkv", "-o", out,
                                  "--size", "160x90", "--fps", "25", "--preset", "ultrafast",
                                  "--crossfade", "0.5", "--transition", "circleopen", "-q")
        self.assertEqual(code, 0, text)
        self.assertAlmostEqual(ffmpeg.probe(out).duration, 2 + 1 - 0.5, delta=0.05)

    def test_errors_are_friendly(self):
        code, text = self.run_cli("join", self.dir / "missing.mp4", "-o", self.dir / "x.mp4")
        self.assertEqual(code, 1)
        self.assertIn("not found", text)


class ServerTests(MediaTestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(dir=self.dir))
        self.exports = self.ws / "exports"
        self.server = make_server(self.ws, port=0, export_dir=self.exports)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, method: str, path: str, body=None, raw: bytes | None = None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def wait(self, job: dict) -> dict:
        for _ in range(300):
            if job["status"] != "running":
                return job
            time.sleep(0.1)
            job = self.call("GET", f"/api/jobs/{job['id']}")[1]
        self.fail("job did not finish")

    def test_library_links_files_without_copying(self):
        status, added = self.call("POST", "/api/library", {"paths": [str(self.dir / "clip1.mp4"), str(self.dir / "music.mp3")]})
        self.assertEqual(status, 200, added)
        self.assertEqual(added[0]["path"], str((self.dir / "clip1.mp4").resolve()))
        self.assertTrue(added[1]["is_audio"])
        self.assertEqual(list((self.ws / "media").iterdir()), [])  # nothing copied
        mid = added[0]["id"]
        for kind in ("thumb", "filmstrip"):
            with urllib.request.urlopen(f"{self.base}/api/{kind}/{mid}") as res:
                self.assertEqual(res.headers["Content-Type"], "image/jpeg")
        req = urllib.request.Request(f"{self.base}/api/media/{mid}", headers={"Range": "bytes=0-9"})
        with urllib.request.urlopen(req) as res:
            self.assertEqual((res.status, len(res.read())), (206, 10))
        self.assertEqual(self.call("DELETE", f"/api/library/{mid}")[0], 200)
        self.assertTrue((self.dir / "clip1.mp4").exists())  # removing a linked file never deletes it
        self.assertEqual(len(self.call("GET", "/api/library")[1]), 1)

    def test_upload_projects_preview_and_export(self):
        status, info = self.call("PUT", "/api/upload/my%20clip.mp4", raw=(self.dir / "clip2.mkv").read_bytes())
        self.assertEqual(status, 200, info)
        self.assertEqual(self.call("PUT", "/api/upload/again.mp4", raw=b"x")[1]["error"] is not None, True)

        status, project = self.call("POST", "/api/projects", {"name": "Trip", "template_id": "cinematic"})
        self.assertEqual(project["transition_style"], "fadeblack")
        self.assertEqual(self.call("POST", "/api/projects", {"name": "Trip"})[1]["name"], "Trip 2")
        project["clips"] = [{"type": "title", "text": "Hi", "duration": 1}, {"path": info["path"]}]
        project["output"].update(SMALL)
        status, saved = self.call("PUT", f"/api/projects/{project['id']}", project)
        self.assertEqual((status, len(saved["clips"])), (200, 2))
        names = [p["name"] for p in self.call("GET", "/api/state")[1]["projects"]]
        self.assertEqual(sorted(names), ["Trip", "Trip 2"])

        preview = self.wait(self.call("POST", "/api/preview", saved)[1])
        self.assertEqual(preview["status"], "done", preview)
        with urllib.request.urlopen(f"{self.base}/api/jobs/{preview['id']}/file") as res:
            self.assertGreater(len(res.read()), 1000)

        export = self.wait(self.call("POST", "/api/export", {**saved, "filename": "Final cut"})[1])
        self.assertEqual(export["status"], "done", export)
        self.assertEqual(Path(export["result"]["file"]), self.exports / "Final cut.mp4")
        self.assertAlmostEqual(ffmpeg.probe(self.exports / "Final cut.mp4").duration, 1.52, delta=0.05)  # overlap capped at half a clip
        again = self.wait(self.call("POST", "/api/export", {**saved, "filename": "Final cut"})[1])
        self.assertEqual(Path(again["result"]["file"]).name, "Final cut (2).mp4")  # never overwrites

        dup = self.call("POST", f"/api/projects/{project['id']}/duplicate")[1]
        self.assertEqual(dup["name"], "Trip copy")
        self.assertEqual(self.call("DELETE", f"/api/projects/{dup['id']}")[0], 200)
        self.assertEqual(self.call("GET", f"/api/projects/{dup['id']}")[0], 404)

    def test_caption_editing(self):
        mid = self.call("POST", "/api/library", {"paths": [str(self.dir / "talk.mp4")]})[1][0]["id"]
        self.assertEqual(self.call("GET", f"/api/captions/{mid}")[0], 404)
        lines = [{"start": 0.2, "end": 1.0, "text": "hello"}, {"start": 1.0, "end": 0.5, "text": "bad"}]
        status, saved = self.call("PUT", f"/api/captions/{mid}", {"lines": lines})
        self.assertEqual((status, saved["lines"], saved["edited"]),
                         (200, [{"start": 0.2, "end": 1.0, "text": "hello"}], True))
        self.assertTrue(self.call("GET", "/api/library")[1][0]["has_captions"])

    def test_rejects_files_outside_the_library(self):
        project = self.call("POST", "/api/projects", {})[1]
        project["clips"] = [{"path": str(self.dir / "clip1.mp4")}]
        status, err = self.call("POST", "/api/export", project)
        self.assertEqual(status, 400)
        self.assertIn("not in the library", err["error"])
        self.assertEqual(self.call("POST", "/api/library", {"paths": ["/etc/hostname"]})[0], 400)
        self.assertEqual(self.call("GET", "/api/projects/..%2f..%2fsettings")[0], 404)
        self.assertEqual(self.call("GET", "/api/media/0000000000000000")[0], 404)


if __name__ == "__main__":
    unittest.main()
