"""Soundtrack validation using the same bundled FFmpeg as video encoding."""
import math
from pathlib import Path
import subprocess

import imageio_ffmpeg


def audio_duration(path):
    """Decode the first audio stream to measure usable duration, including VBR files."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"music file does not exist: {path}")
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-nostats", "-i", str(path),
               "-map", "0:a:0", "-vn", "-progress", "pipe:1", "-f", "null", "-"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"cannot decode music {path}: {result.stderr.strip()}")
    values = [int(line.split("=", 1)[1]) / 1_000_000 for line in result.stdout.splitlines()
              if line.startswith("out_time_us=") and line.split("=", 1)[1].lstrip("-").isdigit()]
    seconds = max(values, default=0)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"music has no playable audio: {path}")
    return seconds


def render_clock(native_frames, fps, music_seconds=None):
    """Sample the entire native sequence, including both title fades, at output FPS."""
    frames = native_frames if music_seconds is None else max(2, round(music_seconds * fps))
    scale = (frames - 1) / (native_frames - 1)
    return [i / fps / scale for i in range(frames)], scale
