"""Photo sizing preserves map anchors and scales independently of viewport zoom."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from photo_to_gpx.video.photo_markers import PhotoMarkers
from photo_to_gpx.video.travel_route import demo_route


class PhotoSizeChecks(unittest.TestCase):
    def test_configured_size_changes_diameter_without_moving_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "photo.jpg"
            Image.new("RGB", (32, 32), "blue").save(path)
            photo = replace(demo_route()[0], path=str(path))
            for fraction in (0.2, 0.1, 0.005, 1):
                with self.subTest(fraction=fraction):
                    markers = PhotoMarkers([(0, photo)], 1, 1, (640, 360), photo_size=fraction)
                    markers.set_reference_span(40000)
                    screen = lambda point: (320, 180)
                    center, diameter = markers.geometry(0, screen, 0, 40000)
                    self.assertEqual(center, (320, 180))
                    self.assertAlmostEqual(diameter, 640 * fraction)
                    self.assertAlmostEqual(markers.world_diameter_m, 40000 * fraction)
                    self.assertAlmostEqual(markers.geometry(0, screen, 0, 80000)[1], diameter / 2)
                    frame = Image.new("RGB", (640, 360), "black")
                    self.assertEqual(markers.draw(frame, 1, screen, 0, span_m=40000), 1)
                    self.assertFalse(markers.issues)
                    self.assertIsNotNone(frame.getbbox())

    def test_invalid_fractions_are_rejected(self):
        for fraction in (0, -0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(fraction=fraction), self.assertRaisesRegex(ValueError, "photo_size"):
                PhotoMarkers([], 1, 1, (640, 360), photo_size=fraction)
