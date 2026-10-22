import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from longform import cli, ffmpeg
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
        self.assertEqual(names, ["clip1.mp4", "clip2.mkv", "clip10.mov", "still.png"])

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
        self.server = make_server(self.ws, port=0)
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

    def test_upload_edit_and_render(self):
        status, info = self.call("PUT", "/api/upload/clip1.mp4", raw=(self.dir / "clip1.mp4").read_bytes())
        self.assertEqual(status, 200)
        self.assertEqual(info["path"], "media/clip1.mp4")
        status, again = self.call("PUT", "/api/upload/clip1.mp4", raw=b"x")
        self.assertEqual(again["name"], "clip1 (1).mp4")  # never overwrites
        self.assertTrue(again["error"])  # and reports files ffprobe cannot read
        self.assertEqual(len(self.call("GET", "/api/media")[1]), 2)

        project = {"clips": [{"type": "title", "text": "Hi", "duration": 1}, {"path": "media/clip1.mp4"}],
                   "output": SMALL, "filename": "web.mp4"}
        status, job = self.call("POST", "/api/render", project)
        self.assertEqual(status, 200, job)
        for _ in range(200):
            job = self.call("GET", "/api/render")[1]
            if job["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["output"], "renders/web.mp4")
        self.assertAlmostEqual(ffmpeg.probe(self.ws / "renders" / "web.mp4").duration, 3, delta=0.1)
        self.assertEqual(self.call("GET", "/api/project")[1]["clips"][1]["path"], "media/clip1.mp4")

        req = urllib.request.Request(self.base + "/renders/web.mp4", headers={"Range": "bytes=0-9"})
        with urllib.request.urlopen(req) as res:
            self.assertEqual((res.status, len(res.read())), (206, 10))

    def test_rejects_paths_outside_workspace(self):
        status, err = self.call("POST", "/api/render", {"clips": [str(self.dir / "clip1.mp4")], "output": SMALL})
        self.assertEqual(status, 400)
        self.assertIn("outside", err["error"])
        self.assertEqual(self.call("GET", "/media/..%2fproject.json")[0], 400)
        self.assertEqual(self.call("PUT", "/api/upload/evil.sh", raw=b"x")[0], 400)


if __name__ == "__main__":
    unittest.main()
