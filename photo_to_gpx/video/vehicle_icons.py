"""Load editable SVG vehicle assets as transparent sprites for video frames."""
import io
from pathlib import Path

import cairosvg
from PIL import Image

from .vehicle_config import BUILT_INS

ICON_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "icons"


def vehicle_icon(kind, path=None):
    """Load a north-facing built-in SVG or custom SVG/raster image."""
    if path is None:
        if kind not in BUILT_INS:
            raise ValueError(f"unknown icon: {kind!r}")
        source = ICON_DIRECTORY / f"{kind}.svg"
        size = (48, 72)
    else:
        source = Path(path)
        size = (72, 72)
    if source.suffix.lower() == ".svg":
        # Rasterize once at higher resolution; the frame loop reuses rotated sprites.
        source_image = io.BytesIO(cairosvg.svg2png(
            bytestring=source.read_bytes(), url=source.resolve().as_uri(), scale=4))
    else:
        source_image = source
    with Image.open(source_image) as image:
        icon = image.convert("RGBA")
    icon.thumbnail(size, Image.Resampling.LANCZOS)
    return icon
