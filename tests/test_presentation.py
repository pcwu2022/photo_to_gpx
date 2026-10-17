"""Calendar, title, photo holds, and actual video/audio integration checks."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import wave

import imageio_ffmpeg
from PIL import Image, ImageChops

from photo_to_gpx.video.photo_markers import PhotoMarkers
from photo_to_gpx.video.photos_to_video import render
from photo_to_gpx.video.playback import Playback
from photo_to_gpx.video.presentation import TitleCard, CalendarTimeline, card_opacity, timeline_ticks
from photo_to_gpx.video.soundtrack import audio_duration, render_clock
from photo_to_gpx.video.travel_route import Route, demo_route


def make_music(path, seconds):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(b"".join(struct.pack("<h", round(8000 * math.sin(2 * math.pi * 440 * i / 8000)))
                                    for i in range(round(seconds * 8000))))


class PresentationChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        photos = []
        samples = demo_route()
        # Two photos at one timestamp exercise burst holds as well as normal arrival.
        for index, sample in enumerate((samples[0], samples[0], samples[1]), 1):
            path = self.folder / f"{index}.jpg"
            Image.new("RGB", (64, 64), (50 * index, 100, 160)).save(path)
            photos.append(replace(sample, path=str(path), raw_index=index))
        self.route = Route(photos)

    def render_video(self, filename, **options):
        defaults = dict(size=(320, 240), fps=10, duration=1.5, cache=self.folder / "cache",
                        offline=True, plain=True, camera="fly", camera_seconds=0.6, vehicle="car",
                        icon_path=None, trail=True, encoder="libx264", provider="esri", min_speed_mps=0,
                        overview_seconds=0.3, overview_hold=0.8, popup_seconds=0.8,
                        photo_animation_seconds=0.4)
        defaults.update(options)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return render(self.route, self.folder / filename, **defaults)

    def test_title_fades_and_readable_centered_typography(self):
        self.assertEqual(card_opacity(0, 3), 0)
        self.assertEqual(card_opacity(3, 3), 0)
        self.assertEqual(card_opacity(1.5, 3), 1)
        self.assertAlmostEqual(card_opacity(0.2, 3), card_opacity(2.8, 3))
        card = TitleCard((640, 360), "New York", "2026 Fall")
        frame = Image.new("RGB", (640, 360), "#e8edf1")
        before = frame.copy()
        card.draw(frame, 0, 3)
        self.assertIsNone(ImageChops.difference(before, frame).getbbox())
        card.draw(frame, 1.5, 3)
        box = ImageChops.difference(before, frame).getbbox()
        self.assertTrue(box and box[0] > 20 and box[2] < 620)

    def test_tick_spacing_and_local_calendar_boundaries(self):
        start = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
        for delta, expected in ((timedelta(hours=10), "hours"),
                                (timedelta(days=10), "days"), (timedelta(days=90), "months")):
            mode, ticks = timeline_ticks(start, start + delta, "America/New_York")
            self.assertEqual(mode, expected)
            self.assertTrue(all(start <= tick <= start + delta for tick, _ in ticks))
            self.assertTrue(all(a[0] < b[0] for a, b in zip(ticks, ticks[1:])))
        mode, ticks = timeline_ticks(start, start + timedelta(hours=10))
        self.assertTrue(all((b[0] - a[0]).total_seconds() == 7200 for a, b in zip(ticks, ticks[1:])))
        # Days stay local-midnight aligned through a daylight-saving transition.
        start = datetime(2026, 3, 7, tzinfo=timezone.utc)
        _, ticks = timeline_ticks(start, start + timedelta(days=4), "America/New_York")
        self.assertIn(23 * 3600, [(b[0] - a[0]).total_seconds() for a, b in zip(ticks, ticks[1:])])
        timeline = CalendarTimeline((640, 360), start, start + timedelta(days=4))
        self.assertEqual(timeline.x(start), timeline.left)
        self.assertEqual(timeline.x(start + timedelta(days=4)), timeline.right)

    def test_pop_duration_is_added_for_every_photo_including_bursts(self):
        playback = Playback(self.route, 2, short_pause=0.1, long_pause=0.2,
                            photo_interval=0, photo_animation_seconds=0.4)
        self.assertAlmostEqual(playback.pauses[0]["hold_seconds"], 1)
        self.assertAlmostEqual(playback.photo_arrival_time(0, self.route.events[1][1]), 0.5)
        self.assertAlmostEqual(playback.pauses[-1]["hold_seconds"], 0.5)
        self.assertEqual(playback.route_time(0.9), 0)
        self.assertAlmostEqual(playback.duration, 3.5)
        disabled = Playback(self.route, 2, short_pause=0.1, photo_animation_seconds=0)
        self.assertAlmostEqual(disabled.duration, 2.1)

    def test_pop_grows_without_moving_anchor_and_zero_is_immediate(self):
        photo = self.route.events[0][1]
        markers = PhotoMarkers([(0, photo)], 1, 1, (320, 240), animation_seconds=0.4)
        frames = []
        for seconds in (0.05, 0.4):
            frame = Image.new("RGB", (320, 240))
            markers.draw(frame, seconds, lambda point: (160, 120), 0)
            frames.append(frame.getbbox())
        self.assertLess(frames[0][2] - frames[0][0], frames[1][2] - frames[1][0])
        markers = PhotoMarkers([(0, photo)], 1, 1, (320, 240), animation_seconds=0)
        frame = Image.new("RGB", (320, 240))
        self.assertEqual(markers.draw(frame, 0, lambda point: (160, 120), 0), 1)

    def test_fit_music_renders_all_titles_events_and_audio(self):
        music = self.folder / "song.wav"
        make_music(music, 3)
        self.assertAlmostEqual(audio_duration(music), 3, places=2)
        report = self.render_video("fit.mp4", music=music, music_timing="music", title="TAIPEI", subtitle="SUMMER")
        self.assertEqual(report["frames"], 30)
        self.assertEqual(report["seconds"], 3)
        self.assertGreater(report["intro_seconds"], 0)
        self.assertEqual(report["timeline_tick_spacing"], "hours")
        self.assertTrue(all(event["video_seconds"] >= report["intro_seconds"] for event in report["photo_events"]))
        self.assertTrue(all(item["visible_seconds"] > 0 for item in report["photo_display"]))
        self.assertAlmostEqual(audio_duration(self.folder / "fit.mp4"), 3, delta=0.06)
        for seconds in (0.5, 4, 10):
            times, scale = render_clock(100, 30, seconds)
            self.assertEqual(len(times), round(seconds * 30))
            self.assertAlmostEqual(times[-1], 99 / 30)
            self.assertEqual(times[0], 0)
        decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(self.folder / "fit.mp4"),
                                  "-map", "0:v:0", "-progress", "pipe:1", "-f", "null", "-"],
                                 capture_output=True, text=True, check=True).stdout
        counts = [int(line.split("=", 1)[1]) for line in decoded.splitlines() if line.startswith("frame=")]
        self.assertEqual(counts[-1], report["frames"])

    def test_preserve_video_loops_audio_and_can_hide_timeline(self):
        music = self.folder / "song.wav"
        make_music(music, 0.5)
        report = self.render_video("loop.mp4", music=music, music_timing="video", show_timeline=False)
        self.assertTrue(report["music_looped"])
        self.assertEqual(report["intro_seconds"], 0)
        self.assertFalse(report["show_timeline"])
        output = self.folder / "loop.mp4"
        decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(output),
                                  "-map", "0:a:0", "-f", "s16le", "-ar", "8000", "-ac", "1", "pipe:1"],
                                 capture_output=True, check=True).stdout
        samples = struct.unpack(f"<{len(decoded) // 2}h", decoded)
        self.assertGreater(max(abs(value) for value in samples[-1000:]), 1000)
        # The old dark timestamp banner is absent even when the timeline is hidden.
        decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(output),
                                  "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                                 capture_output=True, check=True).stdout
        self.assertGreater(min(decoded[:3]), 200)

    def test_invalid_music_preserves_existing_video(self):
        output = self.folder / "existing.mp4"
        output.write_bytes(b"previous video")
        music = self.folder / "bad.wav"
        music.write_text("not audio")
        with self.assertRaisesRegex(ValueError, "cannot decode music"):
            self.render_video(output.name, music=music)
        self.assertEqual(output.read_bytes(), b"previous video")
