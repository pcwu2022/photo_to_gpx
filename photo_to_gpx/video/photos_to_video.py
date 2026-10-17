#!/usr/bin/env python3
"""Photos to a time-proportional H.264 journey with flying camera and photo markers."""
import argparse
from bisect import bisect_left
from dataclasses import asdict
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import imageio_ffmpeg
from PIL import Image
from tqdm import tqdm

from .travel_route import GEOD, Route, demo_route, load_gpx, scan_photos, raw_photo_paths, SLEEP_DISTANCE_KM
from .video_map import PROJECT, project_positions
from .vehicle_icons import vehicle_icon
from .video_map import (FlightMapCanvas, flight_camera_track, overview_pose, append_overview,
                        map_attribution, offset_camera, camera_tracking_area, validate_line_color)
from .photo_markers import PhotoMarkers, photo_camera_padding
from .playback import Playback, MIN_PHOTO_INTERVAL_SECONDS
from .vehicle_config import VehicleConfig, BUILT_INS
from .speed_control import SpeedProfile
from .presentation import TitleCard, CalendarTimeline, smoothstep, attribution_footer
from .soundtrack import audio_duration, render_clock


def interpolate_camera(a, b, fraction):
    return (*(x + (y - x) * fraction for x, y in zip(a[:2], b[:2])),
            *(math.exp(math.log(x) + (math.log(y) - math.log(x)) * fraction)
              for x, y in zip(a[2:], b[2:])))


def codec_options(codec):
    if codec == "libx264":
        return ["-preset", "veryfast", "-crf", "20"]
    return ["-b:v", "8M"]


def choose_encoder(executable, requested, size):
    """Probe an actual encode, since listed encoders may have no usable device."""
    candidates = ([requested, "libx264"] if requested != "auto"
                  else ["h264_nvenc", "h264_qsv", "h264_videotoolbox", "libx264"])
    failures = []
    with tempfile.TemporaryDirectory(prefix="photo-video-codec-") as directory:
        for codec in dict.fromkeys(candidates):
            command = [executable, "-v", "error", "-y", "-f", "lavfi", "-i",
                       f"color=size={size[0]}x{size[1]}:rate=30", "-frames:v", "1",
                       "-c:v", codec, *codec_options(codec), "-pix_fmt", "yuv420p",
                       str(Path(directory) / "probe.mp4")]
            try:
                result = subprocess.run(command, capture_output=True, timeout=20)
                if result.returncode == 0:
                    if failures:
                        print(f"Encoder fallback: {', '.join(failures)} unavailable; using {codec}")
                    return codec
            except subprocess.TimeoutExpired:
                pass
            failures.append(codec)
    raise RuntimeError("no usable H.264 encoder found")


def render(route, output, *, size, fps, duration, cache, offline, plain,
           camera, camera_seconds, vehicle, icon_path, trail, encoder, provider,
           popup_seconds=4, show_photos=True, local_span_km=4, flight_km=150, max_tiles=1200,
           long_pause=0.4, short_pause=0.2, city_km=5, overview_seconds=3, overview_hold=5,
           # config=None, min_speed_mps=2000, max_screen_speed_px=540, max_zoom_rate=1):
           config=None, min_speed_mps=2000, max_screen_speed_px=0, max_zoom_rate=0, photo_size=0.15,
           music=None, music_timing="video", title="", subtitle="", photo_animation_seconds=0.4,
           show_timeline=True, display_timezone="America/New_York", music_seconds=None,
           line_color="#e84553"):
    line_color = validate_line_color(line_color)
    if music_timing not in {"video", "music"}:
        raise ValueError("music_timing must be video or music")
    if music_timing == "music" and music is None:
        raise ValueError("music_timing: music requires a soundtrack")
    if music is not None and music_seconds is None:
        music_seconds = audio_duration(music)
    footer = attribution_footer(size, map_attribution(plain, provider))
    timeline = (CalendarTimeline(size, route.photos[0].timestamp, route.photos[-1].timestamp,
                                footer_height=footer.height, timezone_name=display_timezone)
                if show_timeline else None)
    has_photos = show_photos and any(Path(p.path).is_file() for _, p in route.events)
    bottom_reserved = size[1] - timeline.top if timeline else footer.height
    edge_padding = photo_camera_padding(size, photo_size, bottom_reserved) if has_photos else None
    intro_hold = overview_hold if title.strip() else 0
    intro_seconds = math.ceil((intro_hold + overview_seconds) * fps) / fps if title.strip() else 0
    video_seconds = duration or route.duration
    motion_count = max(2, round(video_seconds * fps))
    motion_seconds = (motion_count - 1) / fps
    nominal_motion_seconds = motion_seconds
    profile = (SpeedProfile(route, motion_seconds, size, fps, camera=camera,
                           camera_seconds=camera_seconds, local_span_km=local_span_km,
                           flight_km=flight_km, city_km=city_km, min_speed_mps=min_speed_mps,
                           max_screen_speed_px=max_screen_speed_px, max_zoom_rate=max_zoom_rate,
                           edge_padding=edge_padding)
               if min_speed_mps or max_screen_speed_px or max_zoom_rate else None)
    if profile:
        motion_seconds = profile.duration
        print(f"Speed limits: {nominal_motion_seconds:.2f}s baseline → {motion_seconds:.2f}s motion; "
              f"{profile.floor_conflicts} intervals limited by the screen/camera ceiling", flush=True)
    playback = Playback(route, motion_seconds, flight_km=flight_km, long_pause=long_pause,
                        short_pause=short_pause, profile=profile,
                        photo_animation_seconds=(math.ceil(photo_animation_seconds * fps) / fps
                                                 if show_photos else 0),
                        photo_interval=(math.ceil(MIN_PHOTO_INTERVAL_SECONDS * fps - 1e-8) / fps if show_photos and
                                        has_photos else 0))
    markers = PhotoMarkers(route.events, route.duration, motion_seconds, size, duration=popup_seconds,
                           arrival_time=playback.arrival_time, photo_arrival_time=playback.photo_arrival_time, fps=fps,
                           photo_size=photo_size, animation_seconds=photo_animation_seconds)
    overview_start = intro_seconds + math.ceil(playback.duration * fps) / fps
    overview_hold = max(overview_hold, popup_seconds)
    end_seconds = overview_start + overview_seconds + overview_hold
    frame_count = math.ceil(end_seconds * fps) + 1
    final_hold = max(0, end_seconds - playback.duration - intro_seconds)
    if route.timing == "compressed" and duration is not None and min(s[4] for s in route.segments) * duration / route.duration < 0.2:
        raise ValueError("requested duration gives a segment less than 0.2 seconds")
    # Keep geospatial positions, never frame images, in memory.
    positions = [route.at(playback.route_time(max(0, i / fps - intro_seconds))) for i in range(frame_count)]
    points = project_positions(positions)
    # Draw every local bend even when adjacent photo times fall within one video frame.
    draw_times = []
    for i, segment in enumerate(route.segments):
        samples = 64 if segment[3] >= flight_km * 1000 else 24
        draw_times.extend(route.ends[i] + segment[4] * j / samples for j in range(samples))
    draw_times.append(route.duration)
    draw_points = project_positions([route.at(t) for t in draw_times])
    config = config or VehicleConfig(default=vehicle)
    icons = {name: vehicle_icon(name, path) for name, path in config.icons.items()}
    for name in {config.default, *(r["icon"] for r in config.ranges)}:
        if name not in icons:
            icons[name] = vehicle_icon(name, icon_path if name == config.default else None)
    final_pose = overview_pose(draw_points, size, edge_padding)
    if timeline:
        timeline.set_icons(icons)
    if profile:
        cameras = [profile.camera_at(position.animation_time) for position in positions]
    elif camera == "fly":
        cameras = flight_camera_track(points, positions, route, size, fps, camera_seconds,
                                      local_span_km, flight_km, city_km, edge_padding=edge_padding)
    else:
        pose = overview_pose(points, size, edge_padding) if camera == "fixed" else (points[0][0], points[0][1], local_span_km * 1000 * size[0] / size[1], local_span_km * 1000)
        cameras = [pose] * frame_count
        if camera == "follow":
            alpha = 1 - math.exp(-1 / (fps * camera_seconds))
            half_x, half_y, _, _ = camera_tracking_area(size, edge_padding)
            for i, p in enumerate(points):
                pose = (pose[0] + (p[0] - pose[0]) * alpha, pose[1] + (p[1] - pose[1]) * alpha, *pose[2:])
                if edge_padding:
                    pose = (max(p[0] - pose[2] * half_x, min(p[0] + pose[2] * half_x, pose[0])),
                            max(p[1] - pose[3] * half_y, min(p[1] + pose[3] * half_y, pose[1])), *pose[2:])
                cameras[i] = offset_camera(pose, size, edge_padding) if edge_padding else pose
    speed_measurements = (profile.measure(positions, points, cameras, size, fps) if profile else {})
    append_overview(cameras, round(overview_start * fps), final_pose, round(overview_seconds * fps))
    if intro_seconds:
        departure = cameras[round(intro_seconds * fps)]
        for index in range(round(intro_seconds * fps)):
            elapsed = index / fps
            cameras[index] = interpolate_camera(final_pose, departure,
                smoothstep((elapsed - intro_hold) / overview_seconds))
    markers.set_reference_span(min(pose[2] for pose in cameras))
    native_frames = frame_count
    source_times, clock_scale = render_clock(native_frames, fps,
                                             music_seconds if music_timing == "music" else None)
    native_cameras = cameras
    cameras = []
    for seconds in source_times:
        source_index = min(len(native_cameras) - 1, seconds * fps)
        index = int(source_index)
        cameras.append(interpolate_camera(native_cameras[index],
                       native_cameras[min(index + 1, len(native_cameras) - 1)], source_index - index))
    positions = [route.at(playback.route_time(max(0, seconds - intro_seconds))) for seconds in source_times]
    points = project_positions(positions)
    frame_count = len(source_times)
    map_canvas = FlightMapCanvas(points, cameras, size, cache=cache, offline=offline, plain=plain,
                                 trail=trail, provider=provider, max_tiles=max_tiles, line_color=line_color,
                                 draw_points=draw_points, draw_times=draw_times)
    rotations = {}  # lazily cache 360 small RGBA sprites
    title_card = TitleCard(size, title, subtitle) if title.strip() else None
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    codec = choose_encoder(executable, encoder, size)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Publish only a completed encode. Preserve any previous output on failure.
    with tempfile.TemporaryDirectory(prefix="photo-video-", dir=output.parent) as directory:
        temporary = Path(directory) / "render.mp4"
        command = [executable, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "pipe:0"]
        if music is not None:
            if music_timing == "video":
                command += ["-stream_loop", "-1"]
            command += ["-i", str(music), "-map", "0:v:0", "-map", "1:a:0",
                        "-af", "apad", "-c:a", "aac", "-ar", "48000", "-b:a", "192k",
                        "-t", str(frame_count / fps)]
        else:
            command += ["-an"]
        command += ["-c:v", codec, *codec_options(codec), "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart", str(temporary)]
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log)
            started = time.perf_counter()
            display_angle = None
            heading_alpha = 1 - math.exp(-1 / (fps * 0.12))
            try:
                with tqdm(total=frame_count, desc="Rendering", unit="frame",
                          dynamic_ncols=True) as progress:
                    for index, (position, point) in enumerate(zip(positions, points)):
                        source_seconds = source_times[index]
                        journey_seconds = source_seconds - intro_seconds
                        frame, (x, y) = map_canvas.frame(point, index, position.animation_time)
                        if show_photos and journey_seconds >= 0:
                            screen = lambda p: map_canvas.screen(p, index)
                            markers.draw(frame, journey_seconds, screen, cameras[index][0], (x, y),
                                         span_m=cameras[index][2])
                        # Project the true bearing locally to account for map convergence.
                        ahead_lon, ahead_lat, _ = GEOD.fwd(position.longitude, position.latitude, position.heading, 20)
                        local_lon = point[0] / 6378137 * 180 / math.pi
                        ahead_lon = local_lon + (ahead_lon - local_lon + 180) % 360 - 180
                        ax, ay = PROJECT.transform(ahead_lon, ahead_lat)
                        target_angle = math.degrees(math.atan2(ax - point[0], ay - point[1]))
                        if display_angle is None:
                            display_angle = target_angle
                        else:
                            display_angle += ((target_angle - display_angle + 180) % 360 - 180) * heading_alpha
                        angle = round(display_angle) % 360
                        anchor = bisect_left(route.ends, position.animation_time)
                        if anchor < len(route.ends) and abs(route.ends[anchor] - position.animation_time) < 1e-8:
                            raw_index = route.photos[anchor].raw_index
                        else:
                            raw_index = route.segments[position.segment][1].raw_index
                        kind = config.for_photo(raw_index)
                        key = (kind, angle)
                        if key not in rotations:
                            rotations[key] = icons[kind].rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
                        sprite = rotations[key]
                        show_journey = (journey_seconds >= 0 and
                                        source_seconds < overview_start + overview_seconds)
                        if show_journey:
                            frame.paste(sprite, (round(x - sprite.width / 2), round(y - sprite.height / 2)), sprite)
                        if timeline and show_journey:
                            timeline.draw(frame, position.timestamp, kind)
                        if title_card:
                            if source_seconds <= intro_hold:
                                title_card.draw(frame, source_seconds, intro_hold)
                            elif source_seconds >= overview_start + overview_seconds:
                                title_card.draw(frame, source_seconds - overview_start - overview_seconds, overview_hold)
                        frame.paste(footer, (0, size[1] - footer.height), footer)
                        process.stdin.write(frame.tobytes())
                        progress.update(1)
                process.stdin.close()
                code = process.wait(timeout=120)
                if code:
                    log.seek(0)
                    raise RuntimeError(log.read().decode(errors="replace"))
            except BaseException as error:
                if process.poll() is None:
                    process.kill()
                process.wait()
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                log.seek(0)
                detail = log.read().decode(errors="replace")
                if detail:
                    raise RuntimeError(f"FFmpeg failed: {detail}") from error
                raise
        temporary.replace(output)
    elapsed = time.perf_counter() - started
    # Reports use the rendered clock, including the opening card and soundtrack fit.
    pauses = [{**pause, **{key: (value + (intro_seconds if key == "arrival_seconds" else 0)) * clock_scale
                           for key, value in pause.items() if key.endswith("_seconds")}}
              for pause in playback.pauses]
    photo_intervals = [{**interval, **{key: value * clock_scale for key, value in interval.items()
                                      if key.endswith("_seconds")}}
                       for interval in playback.photo_intervals]
    photo_display = markers.report(fps) if show_photos else []
    for entry in photo_display:
        entry["arrival_seconds"] = (entry["arrival_seconds"] + intro_seconds) * clock_scale
    speed_segments = [{**leg, "nominal_seconds": leg["nominal_seconds"] * clock_scale,
                      "movement_seconds": leg["movement_seconds"] * clock_scale,
                      "average_ground_speed_mps": leg["average_ground_speed_mps"] / clock_scale}
                     for leg in profile.legs] if profile else []
    speed_measurements = {key: value / clock_scale if value is not None else None
                          for key, value in speed_measurements.items()}
    return {"frames": frame_count, "fps": fps, "size": size, "seconds": frame_count / fps,
            "native_seconds": native_frames / fps, "video_time_scale": clock_scale,
            "music": str(music) if music else None, "music_timing": music_timing,
            "music_seconds": music_seconds, "music_looped": bool(music and music_timing == "video" and music_seconds < frame_count / fps),
            "title": title, "subtitle": subtitle, "intro_seconds": intro_seconds * clock_scale,
            "title_hold_seconds": intro_hold * clock_scale, "show_timeline": show_timeline,
            "timeline_tick_spacing": timeline.mode if timeline else None, "line_color": line_color,
            "photo_animation_seconds": photo_animation_seconds * clock_scale,
            "encoder": codec, "render_seconds": round(elapsed, 2),
            "tiles": map_canvas.tile_count, "camera": camera, "map_provider": "schematic" if plain else provider,
            "subtract_sleep": route.subtract_sleep, "sleep_adjustments": route.sleep_adjustments,
            "sleep_distance_km": route.sleep_distance_km,
            "timing": route.timing, "journey_seconds": motion_seconds * clock_scale,
            "final_photo_hold_seconds": final_hold * clock_scale,
            "nominal_journey_seconds": nominal_motion_seconds * clock_scale,
            "speed_limits": {"minimum_ground_mps": min_speed_mps,
                             "maximum_screen_px_per_second": max_screen_speed_px,
                             "maximum_zoom_octaves_per_second": max_zoom_rate,
                             "effective_local_span_km": profile.effective_local_span_km if profile else local_span_km,
                             "floor_conflict_intervals": profile.floor_conflicts if profile else 0},
            "speed_segments": speed_segments,
            "speed_limits_clock": "before_music_scaling",
            "speed_measurements": speed_measurements,
            "minimum_photo_interval_seconds": MIN_PHOTO_INTERVAL_SECONDS * clock_scale if show_photos else 0,
            "effective_minimum_photo_interval_seconds": playback.photo_interval * clock_scale,
            "photo_intervals": photo_intervals,
            "arrival_pauses": pauses, "arrival_pause_seconds": (playback.duration - motion_seconds) * clock_scale,
            "overview_start_seconds": overview_start * clock_scale,
            "overview_transition_seconds": overview_seconds * clock_scale,
            "overview_hold_seconds": overview_hold * clock_scale,
            "overview_photo_count": len(markers.entries) if show_photos else 0,
            "vehicle_ranges": config.ranges, "default_icon": config.default,
            "segment_icons": [{"destination_raw_index": s[1].raw_index,
                               "icon": config.for_photo(s[1].raw_index)} for s in route.segments],
            "photo_marker_placement": "geographic",
            "photo_reference_camera_span_m": markers.reference_span_m,
            "photo_world_diameter_m": markers.world_diameter_m,
            "photo_size": photo_size,
            "photo_camera_padding_px": edge_padding,
            "photo_closest_zoom_diameter_px": size[0] * photo_size,
            "photo_overview_diameter_px": markers.world_diameter_m / final_pose[2] * size[0],
            "photo_clusters": len(markers.groups), "overlay_issues": markers.issues,
            "photo_display": photo_display,
            "photo_events": [{"video_seconds": (playback.photo_arrival_time(t, photo) + intro_seconds) * clock_scale,
                              **asdict(photo)} for t, photo in route.events]}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--photos", type=Path)
    source.add_argument("--gpx", type=Path)
    source.add_argument("--demo", action="store_true")
    parser.add_argument("-o", "--output", type=Path, default=Path("travel.mp4"))
    parser.add_argument("--timezone", default="America/New_York", help="IANA timezone for photos without EXIF UTC offsets")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--duration", type=float, help="Baseline movement seconds before speed limits, pauses, and overview; default 90")
    parser.add_argument("--timing", choices=["time", "compressed"], default="time",
                        help="time preserves real elapsed-time proportions; compressed bounds long gaps")
    parser.add_argument("--no-sleep-adjustment", action="store_true",
                        help="Disable subtracting 7 hours from long-distance gaps crossing local 2 AM")
    parser.add_argument("--sleep-distance-km", type=float, default=SLEEP_DISTANCE_KM,
                        help="Minimum distance for sleep adjustment; default 1000 km (strictly above)")
    parser.add_argument("--compression", type=float, default=0.5)
    parser.add_argument("--time-scale", type=float, default=300, help="Real seconds per animation second before compression")
    parser.add_argument("--min-segment", type=float, default=0.8)
    parser.add_argument("--max-segment", type=float, default=6)
    parser.add_argument("--max-speed-kmh", type=float, default=1200, help="Plausibility limit, including flights")
    parser.add_argument("--gap-hours", type=float, default=6)
    parser.add_argument("--cache", type=Path, default=Path(".cache/photo-video"))
    parser.add_argument("--map-provider", choices=["esri", "carto"], default="esri",
                        help="CARTO needs CARTO_BASEMAP_API_KEY; Esri public topo is the default")
    parser.add_argument("--offline", action="store_true", help="Use only cached tiles; fail if any are missing")
    parser.add_argument("--plain-map", action="store_true", help="Offline schematic background without geographic map tiles")
    parser.add_argument("--camera", choices=["fixed", "follow", "fly"], default="fly")
    parser.add_argument("--local-span-km", type=float, default=4)
    parser.add_argument("--flight-km", type=float, default=150)
    parser.add_argument("--max-tiles", type=int, default=1200)
    parser.add_argument("--popup-seconds", type=float, default=4, help="Minimum final overview hold; photos persist after arrival")
    parser.add_argument("--long-pause", type=float, default=0.4)
    parser.add_argument("--short-pause", type=float, default=0.2)
    parser.add_argument("--city-km", type=float, default=5)
    parser.add_argument("--min-speed-mps", type=float, default=2000,
                        help="Minimum ground metres per animation second; 0 disables")
    # parser.add_argument("--max-screen-speed-px", type=float, default=540,
    parser.add_argument("--max-screen-speed-px", type=float, default=0,
                        help="Maximum pixels per animation second, accounting for zoom and camera motion; 0 disables")
    parser.add_argument("--max-zoom-rate", type=float, default=0,
                        help="Optional maximum camera zoom octaves per second; stretches travel time; default 0 (disabled)")
    parser.add_argument("--overview-seconds", type=float, default=3)
    parser.add_argument("--overview-hold", type=float, default=5)
    parser.add_argument("--config", type=Path, help="JSON icon ranges using 1-based raw photo indices")
    parser.add_argument("--turn-radius-m", type=float, default=1000, help="Local corner smoothing radius; 0 disables curves")
    parser.add_argument("--no-photos", action="store_true")
    parser.add_argument("--photo-size", type=float, default=0.15,
                        help="Photo diameter as a fraction of viewport width at closest zoom; >0 and <=1")
    parser.add_argument("--camera-seconds", type=float, default=0.6)
    parser.add_argument("--vehicle", choices=sorted(BUILT_INS), default="car")
    parser.add_argument("--icon", type=Path, help="Transparent SVG or raster image facing up/north")
    parser.add_argument("--no-trail", action="store_true")
    parser.add_argument("--line-color", type=validate_line_color, default="#e84553",
                        help="Travelled route line color in #RRGGBB hex format")
    parser.add_argument("--encoder", default="auto", help="auto, libx264, h264_nvenc, h264_qsv, etc.; software fallback")
    parser.add_argument("--music", type=Path, help="Soundtrack path; project example: music/song.mp3")
    parser.add_argument("--music-timing", choices=["video", "music"], default="video",
                        help="video keeps video timing and loops music; music scales the entire video to the track")
    parser.add_argument("--title", default="", help="Opening and closing title; empty disables title cards")
    parser.add_argument("--subtitle", default="", help="Subtitle below the title")
    parser.add_argument("--photo-animation-seconds", type=float, default=0.4,
                        help="Photo pop-in duration added to each photo arrival hold; 0 disables animation")
    parser.add_argument("--no-timeline", action="store_true", help="Hide the bottom calendar timeline")
    return parser


def execute(args, parser, *, check=False):
    """Validate and prepare the same route for project and legacy commands."""
    if args.duration is None and args.timing == "time":
        args.duration = 90
    if (args.width < 320 or args.height < 240 or args.width % 2 or args.height % 2
            or not 1 <= args.fps <= 120 or not math.isfinite(args.camera_seconds) or args.camera_seconds <= 0
            or not all(math.isfinite(v) and v > 0 for v in (args.local_span_km, args.flight_km,
                       args.popup_seconds, args.city_km, args.overview_seconds, args.overview_hold))
            or not all(math.isfinite(v) and v >= 0 for v in (args.long_pause, args.short_pause, args.turn_radius_m,
                       args.min_speed_mps, args.max_screen_speed_px, args.max_zoom_rate, args.sleep_distance_km))
            or not math.isfinite(args.photo_animation_seconds) or args.photo_animation_seconds < 0
            or not math.isfinite(args.photo_size) or not 0 < args.photo_size <= 1
            or not 1 <= args.max_tiles <= 5000
            or (args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0))):
        parser.error("require even dimensions >=320x240, FPS 1..120, positive timing/camera/popup values, "
                     "photo_size >0 and <=1, and 1..5000 tiles")
    try:
        validate_line_color(args.line_color)
        if args.music_timing == "music" and args.music is None:
            raise ValueError("music_timing: music requires a soundtrack")
        music_seconds = audio_duration(args.music) if args.music else None
        config = VehicleConfig(args.config, args.vehicle)
        if args.photos:
            photos, issues = scan_photos(args.photos, args.timezone)
        else:
            photos, issues = (load_gpx(args.gpx) if args.gpx else demo_route()), []
        for issue in issues:
            print(f"Skipped: {issue['path']}: {issue['reason']}", file=sys.stderr)
        route = Route(photos, compression=args.compression, real_seconds_per_video_second=args.time_scale,
                      min_segment=args.min_segment, max_segment=args.max_segment,
                      max_speed_kmh=args.max_speed_kmh, gap_hours=args.gap_hours, timing=args.timing,
                      turn_radius_m=args.turn_radius_m, curve_limit_km=args.flight_km,
                      subtract_sleep=not args.no_sleep_adjustment, sleep_timezone=args.timezone,
                      sleep_distance_km=args.sleep_distance_km)
        issues += route.issues
        if args.photos:
            accepted = {p.path for p in route.photos}
            included = {p.path for _, p in route.events}
            valid = {p.path for p in photos}
            chronology = {p.path: (i, p) for i, p in enumerate(photos, 1)}
            reasons = {str(Path(i["path"]).resolve()): i["reason"] for i in issues if i.get("skipped") is not False}
            manifest = [{"raw_index": i, "path": str(path.relative_to(args.photos)),
                         "chronological_index": chronology[str(path.resolve())][0] if str(path.resolve()) in chronology else None,
                         "utc_timestamp": chronology[str(path.resolve())][1].timestamp if str(path.resolve()) in chronology else None,
                         "timestamp_source": chronology[str(path.resolve())][1].timestamp_source if str(path.resolve()) in chronology else None,
                         "metadata_valid": str(path.resolve()) in valid,
                         "route_anchor": str(path.resolve()) in accepted,
                         "photo_event": str(path.resolve()) in included,
                         "icon": config.for_photo(i), "skip_reason": reasons.get(str(path.resolve()))}
                        for i, path in enumerate(raw_photo_paths(args.photos), 1)]
            args.output.parent.mkdir(parents=True, exist_ok=True)
            manifest_path = args.output.with_suffix(".photo-index.json")
            manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
        for issue in route.issues:
            print(f"{'Warning' if issue.get('skipped') is False else 'Skipped'}: {issue['path']}: {issue['reason']}", file=sys.stderr)
        print(f"Accepted {len(route.photos)} route points, {len(route.events)} photo events", flush=True)
        if check:
            print(f"Inputs valid. Video destination: {args.output}")
            if args.photos:
                print(f"Photo index: {manifest_path}")
            return 0
        report = render(route, args.output, size=(args.width, args.height), fps=args.fps,
                        duration=args.duration, cache=args.cache, offline=args.offline, plain=args.plain_map,
                        camera=args.camera, camera_seconds=args.camera_seconds,
                        vehicle=args.vehicle, icon_path=args.icon, trail=not args.no_trail,
                        line_color=args.line_color,
                        encoder=args.encoder, provider=args.map_provider, popup_seconds=args.popup_seconds,
                        show_photos=not args.no_photos, local_span_km=args.local_span_km,
                        flight_km=args.flight_km, max_tiles=args.max_tiles,
                        long_pause=args.long_pause, short_pause=args.short_pause,
                        city_km=args.city_km, overview_seconds=args.overview_seconds,
                        overview_hold=args.overview_hold, config=config,
                        min_speed_mps=args.min_speed_mps, max_screen_speed_px=args.max_screen_speed_px,
                        max_zoom_rate=args.max_zoom_rate, photo_size=args.photo_size,
                        music=args.music, music_timing=args.music_timing, music_seconds=music_seconds,
                        title=args.title, subtitle=args.subtitle, photo_animation_seconds=args.photo_animation_seconds,
                        show_timeline=not args.no_timeline, display_timezone=args.timezone)
        report.update({"input_records": len(photos), "accepted_route_points": len(route.photos),
                       "issues": issues, "timezone_for_missing_offsets": args.timezone})
        report_path = args.output.with_suffix(".report.json")
        report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
        print(f"Saved {args.output}: {report['frames']} frames in {report['render_seconds']}s; report: {report_path}")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    parser = build_parser()
    return execute(parser.parse_args(argv), parser)


if __name__ == "__main__":
    raise SystemExit(main())
