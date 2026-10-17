# GPX and Timeline conversion utilities

These original export tools remain available for existing scripts and external Timeline visualizers. For video creation, start with the [project workflow](../README.md): the video renderer reads photo metadata directly, preserving photo paths, altitude, subsecond timestamps, and UTC offsets without an intermediate GPX export.

Run from the repository root after installing [requirements.txt](../requirements.txt):

```bash
python -m photo_to_gpx.converters.convert --help
python -m photo_to_gpx.converters.gpx_to_timeline --help
python -m photo_to_gpx.converters.photos_to_timeline --help
```

- `convert` exports photo locations to GPX.
- `gpx_to_timeline` exports GPX points as Timeline JSON.
- `photos_to_timeline` combines the two steps and can retain the intermediate GPX.

The original photo converter has older timestamp behavior: it can fall back to filesystem timestamps, miss nested EXIF capture dates, and serialize naive local times with a UTC suffix. The video metadata reader handles these cases separately; converter output should be checked before using it as a video source.

The video pipeline still reuses the converters’ DMS coordinate conversion and GPX parser, so the `converters/` package remains part of the application.
