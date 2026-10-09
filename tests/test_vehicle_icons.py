"""Checks for exported SVG assets and SVG/raster sprite loading."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET

from PIL import Image

from photo_to_gpx.tools.export_icons import export_icons
from photo_to_gpx.video.vehicle_config import BUILT_INS
from photo_to_gpx.video.vehicle_icons import vehicle_icon


class VehicleIconChecks(unittest.TestCase):
    def test_exported_icons_are_vectors_and_load_with_transparency(self):
        with tempfile.TemporaryDirectory() as directory:
            with redirect_stdout(io.StringIO()):
                export_icons(directory)
            self.assertEqual({p.stem for p in Path(directory).glob('*.svg')}, BUILT_INS)
            for kind in BUILT_INS:
                path = Path(directory) / f'{kind}.svg'
                svg = ET.parse(path).getroot()
                self.assertFalse(svg.findall('.//{http://www.w3.org/2000/svg}image'))
                icon = vehicle_icon(kind, path)
                self.assertEqual(icon.mode, 'RGBA')
                self.assertEqual(icon.size, (48, 72))
                self.assertEqual(icon.getpixel((0, 0))[3], 0)
                self.assertGreater(icon.getpixel((24, 32))[3], 0)
                self.assertEqual(vehicle_icon(kind).size, (48, 72))

    def test_custom_svg_edits_are_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'custom.svg'
            template = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="48">'
                        '<rect x="4" y="4" width="16" height="40" fill="{}"/></svg>')
            path.write_text(template.format('#ff0000'))
            red = vehicle_icon('custom', path)
            path.write_text(template.format('#0000ff'))
            blue = vehicle_icon('custom', path)
            self.assertEqual(red.size, (36, 72))
            self.assertEqual(red.getpixel((18, 36)), (255, 0, 0, 255))
            self.assertEqual(blue.getpixel((18, 36)), (0, 0, 255, 255))
            self.assertEqual(blue.getpixel((0, 0))[3], 0)

    def test_custom_png_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'custom.png'
            Image.new('RGBA', (144, 72), (255, 0, 0, 128)).save(path)
            icon = vehicle_icon('custom', path)
            self.assertEqual(icon.size, (72, 36))
            self.assertEqual(icon.getpixel((36, 18)), (255, 0, 0, 128))
