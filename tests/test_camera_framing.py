"""Camera safety uses complete photo sprites, including their labels and overlays."""
from datetime import datetime, timedelta, timezone
import unittest

from photo_to_gpx.video.photo_markers import photo_camera_padding
from photo_to_gpx.video.presentation import CalendarTimeline, attribution_footer
from photo_to_gpx.video.speed_control import SpeedProfile
from photo_to_gpx.video.travel_route import Photo, Route
from photo_to_gpx.video.video_map import flight_camera_track, overview_pose, project_positions, map_attribution


class PhotoCameraChecks(unittest.TestCase):
    def route(self, latitude=0, longitude=0.03):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return Route([Photo(start, 0, 0, "first"),
                      Photo(start + timedelta(hours=1), latitude, longitude, "last")])

    def layout(self, size, photo_size):
        footer = attribution_footer(size, map_attribution(False, "esri"))
        timeline = CalendarTimeline(size, datetime(2026, 1, 1, tzinfo=timezone.utc),
                                    datetime(2026, 1, 2, tzinfo=timezone.utc), footer_height=footer.height)
        return timeline, photo_camera_padding(size, photo_size, size[1] - timeline.top)

    def bounds(self, point, pose, size, fraction, reference):
        x = (point[0] - pose[0]) / pose[2] * size[0] + size[0] / 2
        y = (pose[1] - point[1]) / pose[3] * size[1] + size[1] / 2
        diameter = reference * fraction / pose[2] * size[0]
        factor = diameter / max(16, round(size[0] * fraction))
        return (x - diameter / 2 - 12 * factor, y - diameter / 2 - 12 * factor,
                x + diameter / 2 + 12 * factor, y + diameter / 2 + 30 * factor)

    def assert_fits(self, points, cameras, size, fraction, timeline_top):
        reference = min(pose[2] for pose in cameras)
        for point, pose in zip(points, cameras):
            left, top, right, bottom = self.bounds(point, pose, size, fraction, reference)
            self.assertGreaterEqual(left, -0.01)
            self.assertGreaterEqual(top, -0.01)
            self.assertLessEqual(right, size[0] + 0.01)
            self.assertLessEqual(bottom, timeline_top + 0.01)

    def test_timeline_has_a_visible_gap_above_footer(self):
        for size in ((640, 360), (1920, 1080)):
            timeline, _ = self.layout(size, 0.15)
            footer = attribution_footer(size, map_attribution(False, "esri"))
            self.assertGreaterEqual(size[1] - footer.height - timeline.top - timeline.height, 10)

    def test_camera_pans_early_enough_for_photo_instead_of_only_vehicle(self):
        route = self.route()
        size, fraction = (1920, 1080), 0.15
        positions = [route.at(route.duration * i / 300) for i in range(301)]
        points = project_positions(positions)
        timeline, padding = self.layout(size, fraction)
        old = flight_camera_track(points, positions, route, size, 30, local_span_km=1)
        reference = min(pose[2] for pose in old)
        self.assertGreater(self.bounds(points[-1], old[-1], size, fraction, reference)[2], size[0])
        protected = flight_camera_track(points, positions, route, size, 30, local_span_km=1,
                                         edge_padding=padding)
        self.assert_fits(points, protected, size, fraction, timeline.top)
        self.assertNotEqual(protected[-1][:2], old[-1][:2])

    def test_framing_accounts_for_photo_size_vertical_motion_and_speed_control(self):
        for size, fraction in (((640, 360), 0.15), ((1920, 1080), 0.3)):
            for route in (self.route(latitude=0.03, longitude=0), self.route(longitude=2)):
                positions = [route.at(route.duration * i / 300) for i in range(301)]
                points = project_positions(positions)
                timeline, padding = self.layout(size, fraction)
                with self.subTest(size=size, fraction=fraction, route=route):
                    cameras = flight_camera_track(points, positions, route, size, 30,
                                                   local_span_km=1, edge_padding=padding)
                    self.assert_fits(points, cameras, size, fraction, timeline.top)
                    profile = SpeedProfile(route, 30, size, 30, local_span_km=1,
                                           edge_padding=padding)
                    self.assert_fits(points, [profile.camera_at(p.animation_time) for p in positions],
                                     size, fraction, timeline.top)
                    overview = overview_pose(points, size, padding)
                    self.assert_fits(points, [overview] * len(points), size, fraction, timeline.top)

    def test_small_movements_still_keep_the_camera_locked(self):
        route = self.route(longitude=0.0005)
        size, fraction = (1920, 1080), 0.15
        positions = [route.at(route.duration * i / 59) for i in range(60)]
        points = project_positions(positions)
        _, padding = self.layout(size, fraction)
        cameras = flight_camera_track(points, positions, route, size, 30, edge_padding=padding)
        self.assertTrue(all(pose == cameras[0] for pose in cameras))

    def test_fixed_and_follow_cameras_keep_safe_area_when_speed_limits_widen_zoom(self):
        route = self.route(longitude=0.0005)
        size, fraction = (640, 360), 0.15
        positions = [route.at(route.duration * i / 59) for i in range(60)]
        points = project_positions(positions)
        timeline, padding = self.layout(size, fraction)
        for camera in ("fixed", "follow"):
            with self.subTest(camera=camera):
                profile = SpeedProfile(route, 30, size, 30, camera=camera, local_span_km=1,
                                       max_screen_speed_px=540, edge_padding=padding)
                cameras = [profile.camera_at(p.animation_time) for p in positions]
                self.assert_fits(points, cameras, size, fraction, timeline.top)
