# Photo Travel Video

Create a complete animated travel video from geotagged photos: a moving vehicle follows your journey on a map, photos appear at their capture locations, and optional music, opening and closing titles, and a date timeline turn it into a shareable H.264 MP4.

Everything renders locally in Python. The repository and Python package retain the original `photo_to_gpx` name; video creation is now the primary workflow. GPX input and the original conversion utilities remain available.

## Get started

Run these commands from the repository root with Python 3.10 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m photo_to_gpx init projects/my-trip
```

Copy your photos into `projects/my-trip/photos/`, then edit `config.json` and `vehicles.json` in that project:

```bash
python -m photo_to_gpx check projects/my-trip
python -m photo_to_gpx run projects/my-trip
```

The result is `projects/my-trip/output/travel.mp4`, alongside a rendering report and photo index. `check` validates settings, metadata, the route, and soundtrack decoding, and writes the photo index so you can assign vehicles before rendering. Map tiles and video encoders are checked during `run`. Repeated runs replace generated outputs; a failed encode preserves the previous video.

Photos need usable GPS coordinates and capture timestamps. Set `timezone` to the capture timezone for photos without EXIF offsets. Unsupported images and invalid metadata are reported as skipped. HEIC requires a separately installed and registered Pillow decoder.

Dependencies include Pillow, PyProj, imageio-ffmpeg, tqdm, and CairoSVG. Supported imageio-ffmpeg wheels supply FFmpeg; `IMAGEIO_FFMPEG_EXE` can select a system binary. On Linux, CairoSVG also needs the system Cairo library, typically `libcairo2`.

## Shape your video

Each trip has its own folder:

```text
projects/my-trip/
├── config.json          # Inputs, titles, music, timing, camera, and encoding
├── vehicles.json        # Vehicle choices by photo index
├── photos/              # Source photos, optionally in subfolders
├── music/               # Optional audio files
├── icons/               # Optional custom vehicle artwork
├── .cache/photo-video/  # Map tiles, created as needed
└── output/              # MP4, report, and photo index
```

`init` creates the config, starter vehicle rules, and photo/music folders. Add custom icons if needed. It refuses to overwrite an existing config and preserves existing photos and vehicle rules. If it finds a photo collection without a `photos/` subfolder, it adopts that collection in place.

The generated config includes every supported setting. For example, change these fields to add a soundtrack and title:

```json
{
  "title": "NEW YORK",
  "subtitle": "FALL 2026",
  "music": "music/song.mp3",
  "music_timing": "music",
  "timezone": "America/New_York",
  "width": 1920,
  "height": 1080,
  "fps": 30,
  "camera": "fly",
  "photo_size": 0.15,
  "show_timeline": true
}
```

Paths resolve relative to the project folder. `music_timing: "music"` fits the complete video to the song; `"video"` keeps the normal video timing and loops or trims the audio. With no soundtrack, the video is silent. A nonempty title adds opening and closing cards.

The default output is 1080p at 30 FPS with a flying camera, rounded local turns, persistent circular photo markers, arrival pauses, and a final overview. The default 90-second duration is a movement baseline: speed controls, photo animations, pauses, and titles can change the final runtime.

Choose built-in `car`, `train`, `bullet_train`, `subway`, `plane`, `boat`, or `bus` artwork, or supply custom icons. Use the generated `travel.photo-index.json` to assign inclusive, 1-based photo ranges in `vehicles.json`. Indices follow natural filename order before metadata filtering; each leg uses its destination photo’s vehicle.

See the [configuration reference](docs/configuration.md) for all fields, defaults, timing interactions, GPX input, and offline settings. The [rendering reference](docs/rendering.md) explains route validation, interpolation, cameras, photo markers, caching, and vehicle rules.

## Try the included demo

The demo includes photos and music and uses a schematic map without downloads:

```bash
python -m photo_to_gpx check examples/demo
python -m photo_to_gpx run examples/demo
```

Its video is written to `examples/demo/output/travel.mp4`. For normal map rendering, tiles download before encoding and are cached locally. `offline` requires cached tiles; `plain_map` uses a schematic background. Photos stay local; map downloads contact the configured provider.

## Repository layout

```text
README.md                   # Video creation guide
requirements.txt            # Video application dependencies
docs/
├── configuration.md        # Project settings reference
├── rendering.md            # Pipeline and rendering behavior
├── converters.md           # Original GPX/Timeline export utilities
├── desktop-roadmap.md      # Proposed desktop app, not implemented
└── archive/                # Historical design prompt
photo_to_gpx/
├── __main__.py             # python -m photo_to_gpx
├── cli.py                  # Project init/check/run
├── video/                  # Route, camera, photos, audio, and encoding
├── assets/                 # Editable SVG vehicles and bundled fonts
└── converters/             # GPX/Timeline tools and shared helpers
examples/demo/              # Reproducible video project
tests/                      # Automated checks
projects/                   # Your local projects (ignored by Git)
```

Local projects, caches, generated videos, and reports are ignored by Git. Keep personal source media in `projects/`. `requirements-video.txt` remains a compatibility entry point for existing setups.

## Development and advanced commands

```bash
python -m unittest discover -s tests -v
python -m photo_to_gpx --help
python -m photo_to_gpx.video.photos_to_video --help
```

The original flag-based video CLI also works:

```bash
python -m photo_to_gpx --photos /path/to/photos \
  --timezone America/New_York --duration 180 -o travel.mp4
python -m photo_to_gpx --gpx trip.gpx -o trip.mp4
python -m photo_to_gpx --demo --plain-map -o demo.mp4
```

The [converter guide](docs/converters.md) covers the original exports. The [desktop roadmap](docs/desktop-roadmap.md) describes proposed UI work. Third-party attribution is recorded in [THIRD_PARTY_LICENSE.txt](THIRD_PARTY_LICENSE.txt), with bundled font licenses under `photo_to_gpx/assets/fonts/`.
