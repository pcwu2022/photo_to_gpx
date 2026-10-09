"""Focused checks for metadata, time scaling, and geodesic edge cases."""
import tempfile
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from PIL.TiffImagePlugin import IFDRational as Rational

from photo_to_gpx.video.travel_route import GEOD, Photo, Route, demo_route, scan_photos
from photo_to_gpx.video.video_map import MapCanvas, project_positions, flight_camera_track, FlightMapCanvas
from photo_to_gpx.video.photo_markers import PhotoMarkers
from photo_to_gpx.video.playback import Playback
from photo_to_gpx.video.vehicle_config import VehicleConfig
from photo_to_gpx.video.speed_control import SpeedProfile


def write_test_photo(path, timestamp, offset="+08:00"):
    exif = Image.Exif()
    original = {36867: timestamp, 37521: "125"}
    if offset:
        original[36881] = offset
    exif[0x8769] = original
    exif[0x8825] = {1: "N", 2: (Rational(25), Rational(1), Rational(0)),
                    3: "E", 4: (Rational(121), Rational(32), Rational(0)),
                    5: b"\x01", 6: Rational(12)}
    Image.new("RGB", (32, 32), "blue").save(path, exif=exif)


class VideoChecks(unittest.TestCase):
    def test_overnight_sleep_adjustment_and_disable_flag(self):
        start = datetime(2026, 1, 1, 20, tzinfo=timezone.utc)
        photos = [Photo(start, 0, 0, "a"),
                  Photo(start + timedelta(hours=12), 0, .01, "b"),
                  Photo(start + timedelta(hours=14), 0, .02, "c")]
        route = Route(photos, real_seconds_per_video_second=3600)
        self.assertEqual([s[4] for s in route.segments], [5, 2])
        self.assertEqual(route.at(route.ends[1]).timestamp, photos[1].timestamp)
        self.assertEqual(route.at(route.duration).timestamp, photos[-1].timestamp)
        self.assertEqual(route.sleep_adjustments[0]["sleep_seconds"], 7 * 3600)
        original = Route(photos, real_seconds_per_video_second=3600, subtract_sleep=False)
        self.assertEqual([s[4] for s in original.segments], [12, 2])
        self.assertEqual(original.sleep_adjustments, [])
        compressed = Route(photos, real_seconds_per_video_second=3600, timing="compressed",
                           compression=1, min_segment=.01, max_segment=24)
        self.assertEqual([s[4] for s in compressed.segments], [5, 2])

    def test_sleep_uses_exif_local_dates_instead_of_utc_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("a.jpg", "b.jpg")]
            # Both timestamps are January 1 UTC, but capture-local dates cross midnight.
            write_test_photo(paths[0], "2026:01:01 20:00:00", "+08:00")
            write_test_photo(paths[1], "2026:01:02 07:00:00", "+08:00")
            photos, issues = scan_photos(directory)
            self.assertFalse(issues)
            self.assertEqual(photos[0].timestamp.date(), photos[1].timestamp.date())
            route = Route(photos, real_seconds_per_video_second=3600)
            self.assertEqual(route.duration, 4)
            # With no EXIF offset, the supplied IANA zone has the same local dates.
            for path, stamp in zip(paths, ("2026:01:01 20:00:00", "2026:01:02 07:00:00")):
                write_test_photo(path, stamp, offset=None)
            photos, issues = scan_photos(directory, "Asia/Taipei")
            self.assertFalse(issues)
            self.assertEqual(Route(photos, real_seconds_per_video_second=3600).duration, 4)

    def test_sleep_leaves_daytime_and_short_midnight_gaps_alone(self):
        for hour, gap in ((8, 12), (23, 2), (20, 7)):
            start = datetime(2026, 1, 1, hour, tzinfo=timezone.utc)
            route = Route([Photo(start, 0, 0, "a"),
                           Photo(start + timedelta(hours=gap), 0, .01, "b")],
                          real_seconds_per_video_second=3600)
            self.assertEqual(route.duration, gap)
            self.assertFalse(route.sleep_adjustments)
        start = datetime(2026, 1, 1, 20, tzinfo=timezone.utc)
        route = Route([Photo(start, 0, 0, "a"),
                       Photo(start + timedelta(hours=7, microseconds=1), 0, .01, "b")])
        self.assertGreater(route.duration, 0)

    def test_sleep_adjustment_does_not_change_gps_plausibility(self):
        start = datetime(2026, 1, 1, 20, tzinfo=timezone.utc)
        # About 89 km/h over ten real hours; after sleep removal it exceeds 100.
        route = Route([Photo(start, 0, 0, "a"),
                       Photo(start + timedelta(hours=10), 0, 8, "b")], max_speed_kmh=100,
                      real_seconds_per_video_second=3600)
        self.assertEqual(len(route.photos), 2)
        self.assertEqual(route.duration, 3)

    def test_slow_leg_speed_floor_and_retimed_photo_arrival(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 0, 0, "a"),
                       Photo(start + timedelta(hours=1), 0, .01, "b"),
                       Photo(start + timedelta(hours=2), 0, .02, "c")])
        profile = SpeedProfile(route, 30, (640, 360), 30)
        self.assertLessEqual(profile.duration, sum(s[3] for s in route.segments) / 2000)
        self.assertTrue(all(s["average_ground_speed_mps"] >= 2000 for s in profile.legs))
        self.assertEqual(profile.floor_conflicts, 0)
        playback = Playback(route, profile.duration, profile=profile)
        arrival = playback.arrival_time(route.ends[1])
        self.assertAlmostEqual(arrival, profile.motion_time(route.ends[1]))
        self.assertAlmostEqual(playback.route_time(arrival + .025), route.ends[1])
        self.assertAlmostEqual(playback.arrival_time(route.duration), profile.duration + .05)

    def test_speed_ceiling_allows_faster_ground_motion_when_zoomed_out(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 0, 0, "a"),
                       Photo(start + timedelta(hours=1), 0, .04, "b")])
        near = SpeedProfile(route, .001, (640, 360), 30, camera="fixed", local_span_km=25,
                            min_speed_mps=1, max_zoom_rate=0, max_screen_speed_px=540)
        far = SpeedProfile(route, .001, (640, 360), 30, camera="fixed", local_span_km=250,
                           min_speed_mps=1, max_zoom_rate=0, max_screen_speed_px=540)
        self.assertAlmostEqual(near.duration / far.duration, 10, places=4)

    def test_flight_speed_and_zoom_ceilings_at_frame_resolution(self):
        import math
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 25, 121, "a"),
                       Photo(start + timedelta(hours=20), 50, 0, "b")], max_speed_kmh=1200)
        profile = SpeedProfile(route, 2, (640, 360), 30, max_screen_speed_px=540, max_zoom_rate=1)
        self.assertEqual(profile.floor_conflicts, 0)
        playback = Playback(route, profile.duration, profile=profile)
        positions = [route.at(playback.route_time(i / 30)) for i in range(math.ceil(playback.duration * 30) + 1)]
        points = project_positions(positions)
        cameras = [profile.camera_at(p.animation_time) for p in positions]
        measured = profile.measure(positions, points, cameras, (640, 360), 30)
        self.assertLessEqual(measured["maximum_world_screen_px_per_second"], 540)
        self.assertLessEqual(measured["maximum_vehicle_screen_px_per_second"], 540)
        self.assertLessEqual(measured["maximum_zoom_octaves_per_second"], 1.001)
        self.assertGreaterEqual(measured["minimum_moving_ground_mps"], 999)

    def test_default_flight_moves_while_zooming_without_retiming_departure(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 25, 121, "a"),
                       Photo(start + timedelta(hours=20), 50, 0, "b")], max_speed_kmh=1200)
        profile = SpeedProfile(route, 6, (1920, 1080), 30)
        self.assertAlmostEqual(profile.duration, 6)
        for fraction in (.05, .1, .25, .5, .75, .9, .95):
            self.assertAlmostEqual(profile.route_time(6 * fraction), route.duration * fraction)
        departure = profile.camera_at(0)
        early = profile.camera_at(route.duration * .1)
        middle = profile.camera_at(route.duration * .5)
        late = profile.camera_at(route.duration * .9)
        arrival = profile.camera_at(route.duration)
        self.assertGreater(early[3], departure[3])
        self.assertGreater(middle[3], early[3] * 2)
        self.assertLess(late[3], middle[3])
        self.assertLess(arrival[3], late[3])
        self.assertNotEqual(departure[:2], early[:2])

    def test_stationary_photo_gap_does_not_create_long_idle_motion(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 0, 0, "a"),
                       Photo(start + timedelta(days=1), 0, 0, "b")])
        profile = SpeedProfile(route, 30, (640, 360), 30)
        self.assertAlmostEqual(profile.duration, 1 / 30)
        playback = Playback(route, profile.duration, profile=profile)
        self.assertEqual(playback.route_time(playback.duration), route.duration)

    def test_speed_control_keeps_city_camera_exactly_locked(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 25, 121, "a"),
                       Photo(start + timedelta(hours=1), 25.01, 121.01, "b")])
        profile = SpeedProfile(route, 30, (640, 360), 30)
        cameras = [profile.camera_at(route.duration * i / 59) for i in range(60)]
        self.assertTrue(all(c == cameras[0] for c in cameras))

    def test_curve_arc_length_handles_very_short_endpoint_tangent(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 0, 0, "a"),
                       Photo(start + timedelta(hours=1), 0, .00001, "b"),
                       Photo(start + timedelta(hours=2), .02, .02, "c")], turn_radius_m=1000)
        curve = route.curves[1]
        a, b = curve.at(.0001), curve.at(.000101)
        actual = GEOD.inv(a[0], a[1], b[0], b[1])[2]
        expected = .000001 * curve.lengths[-1]
        self.assertAlmostEqual(actual / expected, 1, delta=.002)

    def test_nested_exif_offset_altitude_and_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write_test_photo(path / "photo.jpg", "2026:07:26 09:00:00")
            Image.new("RGB", (32, 32)).save(path / "missing.jpg")
            photos, issues = scan_photos(path, "America/New_York")
            self.assertEqual(len(photos), 1)
            self.assertEqual(photos[0].timestamp.hour, 1)
            self.assertEqual(photos[0].timestamp.microsecond, 125000)
            self.assertEqual(photos[0].altitude, -12)
            self.assertIn("missing DateTimeOriginal", issues[0]["reason"])

    def test_dst_ambiguity_and_missing_timezone_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write_test_photo(path / "ambiguous.jpg", "2026:11:01 01:30:00", None)
            write_test_photo(path / "normal.jpg", "2026:07:26 09:00:00", None)
            photos, issues = scan_photos(path, "America/New_York")
            self.assertEqual(photos[0].timestamp.hour, 13)
            self.assertIn("ambiguous", issues[0]["reason"])

    def test_duplicates_and_jumps(self):
        photos = demo_route()
        first = photos[0]
        duplicate = Photo(first.timestamp, first.latitude, first.longitude, "burst")
        conflict = Photo(first.timestamp, 40, 40, "conflict")
        jump = Photo(first.timestamp + timedelta(seconds=1), 50, 50, "jump")
        route = Route([*photos, duplicate, conflict, jump])
        self.assertEqual(len(route.photos), len(photos))
        self.assertEqual(len(route.events), len(photos) + 1)
        self.assertEqual(len(route.issues), 2)

    def test_dateline_geodesic_and_projection(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 10, 179, "a"),
                       Photo(start + timedelta(hours=3), 10, -179, "b")])
        midpoint = route.at(route.duration / 2)
        self.assertAlmostEqual(abs(midpoint.longitude), 180, places=4)
        positions = [route.at(route.duration * i / 100) for i in range(101)]
        points = project_positions(positions)
        self.assertLess(max(x for x, _ in points) - min(x for x, _ in points), 250000)
        _, _, total = GEOD.inv(179, 10, -179, 10)
        _, _, half = GEOD.inv(179, 10, midpoint.longitude, midpoint.latitude)
        self.assertAlmostEqual(half, total / 2, places=5)

    def test_gap_duration_and_endpoint(self):
        first = demo_route()[0]
        end = Photo(first.timestamp + timedelta(days=30), 26, 122, "end")
        route = Route([first, end], max_segment=4, timing="compressed")
        self.assertEqual(route.duration, 4)
        self.assertFalse(route.issues[0]["skipped"])
        self.assertEqual(route.at(4).timestamp, end.timestamp)
        self.assertAlmostEqual(route.at(4).latitude, end.latitude)

    def test_plain_follow_view_stays_on_atlas(self):
        route = Route(demo_route())
        points = project_positions([route.at(route.duration * i / 99) for i in range(100)])
        canvas = MapCanvas(points, (640, 360), cache="unused", plain=True, camera="follow")
        for point in points:
            frame, (x, y) = canvas.frame(point)
            self.assertEqual(frame.size, (640, 360))
            self.assertTrue(0 <= x <= 640 and 0 <= y <= 360)

    def test_time_proportions_ignore_distance(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        photos = [Photo(start, 10, 10, "a"),
                  Photo(start + timedelta(hours=1), 10, 10.01, "b"),
                  Photo(start + timedelta(hours=4), 10, 11, "c")]
        route = Route(photos)
        self.assertAlmostEqual(route.segments[1][4] / route.segments[0][4], 3)
        position = route.at(route.ends[1] * 0.25)
        self.assertEqual(position.timestamp, start + timedelta(minutes=15))
        _, _, distance = GEOD.inv(10, 10, 10.01, 10)
        _, _, traveled = GEOD.inv(10, 10, position.longitude, position.latitude)
        self.assertAlmostEqual(traveled / distance, 0.25, places=6)

    def test_flight_camera_zooms_and_keeps_vehicle_visible(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 25, 121, "a"),
                       Photo(start + timedelta(hours=14), 50, 0, "b")], max_speed_kmh=1200)
        positions = [route.at(route.duration * i / 299) for i in range(300)]
        points = project_positions(positions)
        cameras = flight_camera_track(points, positions, route, (640, 360), 30)
        self.assertGreater(cameras[150][3], cameras[0][3] * 4)
        self.assertGreater(cameras[150][3], cameras[-1][3] * 4)
        canvas = FlightMapCanvas(points, cameras, (640, 360), cache="unused", plain=True)
        for i, p in enumerate(points):
            x, y = canvas.screen(p, i)
            self.assertTrue(0 <= x <= 640 and 0 <= y <= 360)

    def test_photo_marker_trigger_burst_and_fade(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{i}.jpg" for i in range(2)]
            for path in paths:
                write_test_photo(path, "2026:07:26 09:00:00")
            photos, _ = scan_photos(directory)
            markers = PhotoMarkers([(1, photos[0]), (1.01, photos[1])], 2, 4, (640, 360))
            self.assertEqual(len(markers.groups), 1)
            self.assertEqual(markers.active(1.9), [])
            self.assertEqual(markers.active(2.1), [0, 1])
            self.assertEqual(markers.active(700), [0, 1])
            frame = Image.new("RGB", (640, 360), "black")
            self.assertEqual(markers.draw(frame, 2.5, lambda p: (320, 180), 0, (320, 180)), 2)
            sprite = markers.thumbnail(photos[0])
            self.assertEqual(sprite.getpixel((0, 0))[3], 0)
            self.assertEqual(sprite.getpixel((sprite.width // 2, sprite.height // 2))[:3], (0, 0, 254))
            markers.full_frames = [0, 0]
            for i in range(210):
                markers.draw(Image.new("RGB", (640, 360)), i / 30,
                             lambda p: (320, 180), 0, (320, 180))
            self.assertTrue(all(v >= 120 for v in markers.full_frames))

    def test_arrival_pauses_freeze_and_preserve_motion_ratios(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 0, 0, "a"),
                       Photo(start + timedelta(hours=1), 0, 1, "b"),
                       Photo(start + timedelta(hours=3), 0, 3, "c")])
        playback = Playback(route, 9, flight_km=150)
        first, second = playback.pauses
        self.assertEqual(first["hold_seconds"], 0.05)
        self.assertEqual(second["hold_seconds"], 0.2)
        self.assertAlmostEqual(playback.route_time(3 + first["hold_seconds"] / 2), route.ends[1])
        self.assertAlmostEqual(playback.arrival_time(route.ends[1]), 3)
        self.assertAlmostEqual(playback.arrival_time(route.ends[2]), 9 + first["hold_seconds"])
        self.assertAlmostEqual(playback.route_time(3 + first["hold_seconds"] + 3),
                               route.ends[1] + route.segments[1][4] / 2)

    def test_curves_keep_anchors_and_match_corner_headings(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        photos = [Photo(start, 10, 10, "a"),
                  Photo(start + timedelta(hours=1), 10, 10.02, "b"),
                  Photo(start + timedelta(hours=2), 10.02, 10.02, "c")]
        route = Route(photos, turn_radius_m=500)
        for t, photo in zip(route.ends, photos):
            position = route.at(t)
            self.assertLess(GEOD.inv(photo.longitude, photo.latitude, position.longitude, position.latitude)[2], 0.001)
        before = route.at(route.ends[1] - 0.0001).heading
        after = route.at(route.ends[1] + 0.0001).heading
        self.assertLess(abs((after - before + 180) % 360 - 180), 1)
        straight = Route(photos).at(route.ends[1] * 0.75)
        curved = route.at(route.ends[1] * 0.75)
        self.assertGreater(GEOD.inv(straight.longitude, straight.latitude, curved.longitude, curved.latitude)[2], 10)

    def test_raw_index_counts_missing_gps_and_range_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (32, 32)).save(folder / "01-no-metadata.jpg")
            write_test_photo(folder / "02-valid.jpg", "2026:07:26 09:00:00")
            write_test_photo(folder / "03-valid.jpg", "2026:07:26 10:00:00")
            photos, issues = scan_photos(folder)
            self.assertEqual([p.raw_index for p in photos], [2, 3])
            self.assertEqual(issues[0]["raw_index"], 1)
            config_path = folder / "vehicles.json"
            config_path.write_text(json.dumps({"default_icon": "car", "ranges": [
                {"start": 2, "end": 3, "icon": "train"},
                {"start": 3, "end": 5, "icon": "plane"}]}))
            config = VehicleConfig(config_path)
            self.assertEqual(config.for_photo(1), "car")
            self.assertEqual(config.for_photo(2), "train")
            self.assertEqual(config.for_photo(3), "plane")
            self.assertEqual(config.for_photo(5), "plane")
            self.assertEqual(config.for_photo(6), "car")
            self.assertEqual(config.for_photo(None), "car")

    def test_city_camera_is_exactly_locked(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        route = Route([Photo(start, 25, 121, "a"),
                       Photo(start + timedelta(hours=1), 25.01, 121.01, "b")])
        positions = [route.at(route.duration * i / 59) for i in range(60)]
        points = project_positions(positions)
        cameras = flight_camera_track(points, positions, route, (640, 360), 30, city_km=5)
        self.assertTrue(all(c == cameras[0] for c in cameras))

    def test_future_trail_hidden_and_marker_fixed_at_geographic_center(self):
        points = [(0, 0), (1000, 0), (2000, 0)]
        canvas = MapCanvas(points, (640, 360), cache="unused", plain=True,
                           draw_points=points, draw_times=[0, 1, 2])
        frame, _ = canvas.frame(points[0], route_time=0)
        future_pixel = tuple(round(v) for v in canvas.pixel(points[-1]))
        self.assertEqual(frame.getpixel(future_pixel), (232, 237, 241))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write_test_photo(path / "photo.jpg", "2026:07:26 09:00:00")
            photos, _ = scan_photos(path)
            markers = PhotoMarkers([(0, photos[0])], 1, 1, (640, 360))
            first = Image.new("RGB", (640, 360))
            markers.draw(first, 1, lambda p: (320, 180), 0, (100, 100))
            self.assertEqual(markers.geometry(0, lambda p: (320, 180), 0)[0], (320, 180))
            self.assertEqual(first.getpixel((320, 180)), (0, 0, 254))
            markers.draw(Image.new("RGB", (640, 360)), 20,
                         lambda p: (420, 180), 0, (100, 100))
            self.assertEqual(markers.geometry(0, lambda p: (420, 180), 0)[0], (420, 180))
            count = markers.draw(Image.new("RGB", (640, 360)), 30,
                                 lambda p: (320, 180), 0, (100, 100), span_m=6400)
            self.assertEqual(count, 1)

    def test_photo_geometry_scales_with_map_and_preserves_projected_location(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write_test_photo(path / "photo.jpg", "2026:07:26 09:00:00")
            photos, _ = scan_photos(path)
            markers = PhotoMarkers([(0, photos[0])], 1, 1, (1920, 1080))
            markers.set_reference_span(40000)
            ax, ay = markers.anchor(0, 0)
            for span in (40000, 80000, 160000, 40000000):
                cx, cy = ax - 4000, ay + 2000
                screen = lambda p: ((p[0] - cx) / span * 1920 + 960,
                                    (cy - p[1]) / (span * 1080 / 1920) * 1080 + 540)
                center, diameter = markers.geometry(0, screen, cx, span)
                recovered = (cx + (center[0] - 960) / 1920 * span,
                             cy - (center[1] - 540) / 1920 * span)
                self.assertAlmostEqual(recovered[0], ax)
                self.assertAlmostEqual(recovered[1], ay)
                self.assertAlmostEqual(diameter * span / 1920, 4000)
                self.assertEqual(markers.draw(Image.new("RGB", (1920, 1080)), 10,
                                              screen, cx, span_m=span), 1)
            self.assertEqual(markers.geometry(0, lambda p: (960, 540), ax, 40000)[1], 192)


if __name__ == "__main__":
    unittest.main()
