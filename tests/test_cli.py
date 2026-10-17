"""Project workflows and configuration errors, without tile downloads or encoding."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PIL.TiffImagePlugin import IFDRational as Rational

from photo_to_gpx.cli import initialize, main, read_project


class ProjectChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name) / "trip"
        self.output = io.StringIO()
        with redirect_stdout(self.output):
            initialize(self.folder)

    def configure(self, **settings):
        path = self.folder / "config.json"
        config = json.loads(path.read_text())
        config.update(settings)
        path.write_text(json.dumps(config))

    def invoke(self, *argv):
        with redirect_stdout(self.output), redirect_stderr(self.output):
            return main(list(argv))

    def test_paths_resolve_from_project_and_booleans_use_positive_names(self):
        self.configure(show_photos=False, trail=False, sleep_adjustment=False)
        args = read_project(self.folder)
        self.assertEqual(args.output, self.folder / "output/travel.mp4")
        self.assertEqual(args.config, self.folder / "vehicles.json")
        self.assertEqual(args.cache, self.folder / ".cache/photo-video")
        self.assertTrue(args.no_photos and args.no_trail and args.no_sleep_adjustment)

    def test_presentation_defaults_and_music_folder(self):
        self.assertTrue((self.folder / "music").is_dir())
        args = read_project(self.folder)
        self.assertIsNone(args.music)
        self.assertEqual(args.music_timing, "video")
        self.assertEqual(args.title, "")
        self.assertEqual(args.photo_animation_seconds, 0.4)
        self.assertFalse(args.no_timeline)

        self.configure(show_timeline=False, title="NEW YORK", subtitle="2026 FALL")
        args = read_project(self.folder)
        self.assertTrue(args.no_timeline)
        self.assertEqual(args.title, "NEW YORK")

    def test_line_color_hex_setting(self):
        self.assertEqual(read_project(self.folder).line_color, "#e84553")
        self.configure(line_color="#00a8FF")
        self.assertEqual(read_project(self.folder).line_color, "#00a8FF")
        for value in ("red", "#12345G", "#fff", "00a8FF"):
            self.configure(line_color=value)
            with self.assertRaisesRegex(ValueError, "line_color.*#RRGGBB"):
                read_project(self.folder)

    def test_music_selection_and_invalid_presentation_settings(self):
        music = self.folder / "music/song.wav"
        music.write_bytes(b"file existence is checked before decoding")
        self.configure(music="music/song.wav", music_timing="music")
        self.assertEqual(read_project(self.folder).music, music)
        self.configure(music=None)
        with self.assertRaisesRegex(ValueError, "requires a soundtrack"):
            read_project(self.folder)
        self.configure(music_timing="video")
        self.configure(music="music/song.wav", photos=None, demo=True)
        self.assertEqual(self.invoke("check", str(self.folder)), 1)
        self.assertIn("cannot decode music", self.output.getvalue())
        for key, value in (("title", None), ("show_timeline", "false"),
                           ("photo_animation_seconds", True), ("music_timing", "invalid")):
            original = (self.folder / "config.json").read_text()
            self.configure(**{key: value})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                read_project(self.folder)
            (self.folder / "config.json").write_text(original)

    def test_init_preserves_existing_files_and_raw_indices(self):
        folder = Path(self.temporary.name) / "existing"
        folder.mkdir()
        (folder / "2.jpg").write_bytes(b"existing photo")
        vehicles = '{"default_icon": "train"}'
        (folder / "vehicles.json").write_text(vehicles)
        with redirect_stdout(self.output):
            initialize(folder)
        self.assertEqual(json.loads((folder / "config.json").read_text())["photos"], ".")
        self.assertEqual((folder / "2.jpg").read_bytes(), b"existing photo")
        self.assertEqual((folder / "vehicles.json").read_text(), vehicles)
        before = (folder / "config.json").read_text()
        self.assertEqual(self.invoke("init", str(folder)), 1)
        self.assertEqual((folder / "config.json").read_text(), before)

    def test_invalid_json_settings_fail_before_rendering(self):
        original = (self.folder / "config.json").read_text()
        for settings, message in [({"fps": True}, "fps"), ({"width": 320.5}, "width"),
                                  ({"offline": "false"}, "offline"),
                                  ({"duration": float("nan")}, "duration"),
                                  ({"camera": "typo"}, "camera"),
                                  ({"widht": 320}, "widht"),
                                  ({"output": "../outside.mp4"}, "output"),
                                  ({"cache": "../cache"}, "cache"),
                                  ({"demo": True}, "exactly one source")]:
            with self.subTest(settings=settings):
                (self.folder / "config.json").write_text(original)
                self.configure(**settings)
                with self.assertRaisesRegex(ValueError, message):
                    read_project(self.folder)

    def test_missing_and_malformed_configs_have_actionable_errors(self):
        self.assertEqual(self.invoke("run", str(self.folder / "missing")), 1)
        self.assertIn("photo_to_gpx init", self.output.getvalue())
        (self.folder / "config.json").write_text("{")
        self.assertEqual(self.invoke("run", str(self.folder)), 1)
        self.assertIn("config.json", self.output.getvalue())

    def test_photo_size_bounds_are_checked_before_rendering(self):
        for fraction in (0, -0.1, 1.1):
            self.configure(photo_size=fraction)
            with self.subTest(fraction=fraction), self.assertRaises(SystemExit) as error:
                self.invoke("check", str(self.folder))
            self.assertEqual(error.exception.code, 2)
        self.assertIn("photo_size", self.output.getvalue())

    def test_check_builds_photo_index_without_rendering(self):
        for index in (1, 2):
            exif = Image.Exif()
            exif[0x8769] = {36867: f"2026:01:0{index} 12:00:00", 36881: "+00:00"}
            exif[0x8825] = {1: "N", 2: (Rational(25), Rational(0), Rational(0)),
                            3: "E", 4: (Rational(121), Rational(index), Rational(0))}
            Image.new("RGB", (16, 16), "blue").save(self.folder / "photos" / f"{index}.jpg", exif=exif)
        (self.folder / "vehicles.json").write_text(json.dumps({
            "default_icon": "car", "ranges": [{"start": 2, "end": 2, "icon": "train"}]}))
        with patch("photo_to_gpx.video.photos_to_video.render") as render:
            self.assertEqual(self.invoke("check", str(self.folder)), 0)
            render.assert_not_called()
        manifest = json.loads((self.folder / "output/travel.photo-index.json").read_text())
        self.assertEqual([entry["icon"] for entry in manifest], ["car", "train"])
        self.assertTrue(all(entry["photo_event"] for entry in manifest))
        self.assertFalse((self.folder / "output/travel.mp4").exists())

    def test_directory_shorthand_renders_and_writes_report(self):
        self.configure(photos=None, demo=True, plain_map=True, photo_size=0.1)
        report = {"frames": 3, "render_seconds": 0.1}
        (self.folder / "output").mkdir()
        with patch("photo_to_gpx.video.photos_to_video.render", return_value=report) as render:
            self.assertEqual(self.invoke(str(self.folder)), 0)
        self.assertEqual(render.call_args.args[1], self.folder / "output/travel.mp4")
        self.assertEqual(render.call_args.kwargs["photo_size"], 0.1)
        saved = json.loads((self.folder / "output/travel.report.json").read_text())
        self.assertGreater(saved["accepted_route_points"], 1)

    def test_legacy_flags_still_dispatch_to_renderer(self):
        with patch("photo_to_gpx.video.photos_to_video.main", return_value=0) as legacy:
            self.assertEqual(self.invoke("--demo", "--plain-map"), 0)
        legacy.assert_called_once_with(["--demo", "--plain-map"])
