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
from PIL import Image, ImageDraw

from travel_route import GEOD, Route, demo_route, load_gpx, scan_photos, raw_photo_paths
from video_map import PROJECT, project_positions, vehicle_icon
from video_map import FlightMapCanvas, flight_camera_track, overview_pose, append_overview
from photo_markers import PhotoMarkers
from playback import Playback
from vehicle_config import VehicleConfig
from speed_control import SpeedProfile


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
           popup_seconds=4, show_photos=True, local_span_km=25, flight_km=150, max_tiles=1200,
           long_pause=0.2, short_pause=0.05, city_km=5, overview_seconds=5, overview_hold=5,
           config=None, min_speed_mps=1000, max_screen_speed_px=180, max_zoom_rate=1):
    video_seconds = duration or route.duration
    motion_count = max(2, round(video_seconds * fps))
    motion_seconds = (motion_count - 1) / fps
    nominal_motion_seconds = motion_seconds
    profile = (SpeedProfile(route, motion_seconds, size, fps, camera=camera,
                           camera_seconds=camera_seconds, local_span_km=local_span_km,
                           flight_km=flight_km, city_km=city_km, min_speed_mps=min_speed_mps,
                           max_screen_speed_px=max_screen_speed_px, max_zoom_rate=max_zoom_rate)
               if min_speed_mps or max_screen_speed_px or max_zoom_rate else None)
    if profile:
        motion_seconds = profile.duration
        print(f"Speed limits: {nominal_motion_seconds:.2f}s baseline → {motion_seconds:.2f}s motion; "
              f"{profile.floor_conflicts} intervals limited by the screen/camera ceiling", flush=True)
    playback = Playback(route, motion_seconds, flight_km=flight_km, long_pause=long_pause,
                        short_pause=short_pause, profile=profile)
    markers = PhotoMarkers(route.events, route.duration, motion_seconds, size, duration=popup_seconds,
                           arrival_time=playback.arrival_time, fps=fps)
    overview_start = math.ceil(playback.duration * fps) / fps
    overview_hold = max(overview_hold, popup_seconds)
    end_seconds = overview_start + overview_seconds + overview_hold
    frame_count = math.ceil(end_seconds * fps) + 1
    final_hold = max(0, end_seconds - playback.duration)
    if route.timing == "compressed" and duration is not None and min(s[4] for s in route.segments) * duration / route.duration < 0.2:
        raise ValueError("requested duration gives a segment less than 0.2 seconds")
    # Keep geospatial positions, never frame images, in memory.
    positions = [route.at(playback.route_time(i / fps)) for i in range(frame_count)]
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
    final_pose = overview_pose(draw_points, size)
    if profile:
        cameras = [profile.camera_at(position.animation_time) for position in positions]
    elif camera == "fly":
        cameras = flight_camera_track(points, positions, route, size, fps, camera_seconds,
                                      local_span_km, flight_km, city_km)
    else:
        pose = overview_pose(points, size) if camera == "fixed" else (points[0][0], points[0][1], local_span_km * 1000 * size[0] / size[1], local_span_km * 1000)
        cameras = [pose] * frame_count
        if camera == "follow":
            alpha = 1 - math.exp(-1 / (fps * camera_seconds))
            for i, p in enumerate(points):
                pose = (pose[0] + (p[0] - pose[0]) * alpha, pose[1] + (p[1] - pose[1]) * alpha, *pose[2:])
                cameras[i] = pose
    speed_measurements = (profile.measure(positions, points, cameras, size, fps) if profile else {})
    append_overview(cameras, round(overview_start * fps), final_pose, round(overview_seconds * fps))
    map_canvas = FlightMapCanvas(points, cameras, size, cache=cache, offline=offline, plain=plain,
                                 trail=trail, provider=provider, max_tiles=max_tiles,
                                 draw_points=draw_points, draw_times=draw_times)
    rotations = {}  # lazily cache 360 small RGBA sprites
    # Wrap and cache attribution so it remains visible at smaller resolutions.
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines, line = [], ""
    for word in map_canvas.attribution.split():
        candidate = f"{line} {word}".strip()
        if line and measure.textlength(candidate) > size[0] - 24:
            lines.append(line)
            line = word
        else:
            line = candidate
    lines.append(line)
    footer = Image.new("RGB", (size[0], len(lines) * 13 + 12), "white")
    footer_draw = ImageDraw.Draw(footer)
    for i, line in enumerate(lines):
        footer_draw.text((12, 6 + i * 13), line, fill="#23313d")
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    codec = choose_encoder(executable, encoder, size)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Publish only a completed encode. Preserve any previous output on failure.
    with tempfile.TemporaryDirectory(prefix="photo-video-", dir=output.parent) as directory:
        temporary = Path(directory) / "render.mp4"
        command = [executable, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "pipe:0", "-an",
                   "-c:v", codec, *codec_options(codec), "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart", str(temporary)]
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log)
            started = time.perf_counter()
            display_angle = None
            heading_alpha = 1 - math.exp(-1 / (fps * 0.12))
            try:
                for index, (position, point) in enumerate(zip(positions, points)):
                    frame, (x, y) = map_canvas.frame(point, index, position.animation_time)
                    if show_photos:
                        screen = lambda p: map_canvas.screen(p, index)
                        u = max(0, min(1, (index / fps - overview_start) / overview_seconds))
                        blend = u * u * (3 - 2 * u)
                        cx, cy, sx, sy = final_pose
                        final_screen = lambda p: ((p[0] - cx) / sx * size[0] + size[0] / 2,
                                                   (cy - p[1]) / sy * size[1] + size[1] / 2)
                        markers.draw(frame, index / fps, screen, cameras[index][0], (x, y),
                                     overview=blend, overview_screen=final_screen)
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
                    frame.paste(sprite, (round(x - sprite.width / 2), round(y - sprite.height / 2)), sprite)
                    draw = ImageDraw.Draw(frame)
                    draw.rectangle((0, 0, size[0], 32), fill="#23313d")
                    draw.text((12, 9), position.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC") + " • inferred route", fill="white")
                    frame.paste(footer, (0, size[1] - footer.height))
                    process.stdin.write(frame.tobytes())
                    if index % max(1, fps * 5) == 0:
                        print(f"Rendering {index + 1}/{frame_count} frames", flush=True)
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
    return {"frames": frame_count, "fps": fps, "size": size, "seconds": frame_count / fps,
            "encoder": codec, "render_seconds": round(elapsed, 2),
            "tiles": map_canvas.tile_count, "camera": camera, "map_provider": "schematic" if plain else provider,
            "timing": route.timing, "journey_seconds": motion_seconds, "final_photo_hold_seconds": final_hold,
            "nominal_journey_seconds": nominal_motion_seconds,
            "speed_limits": {"minimum_ground_mps": min_speed_mps,
                             "maximum_screen_px_per_second": max_screen_speed_px,
                             "maximum_zoom_octaves_per_second": max_zoom_rate,
                             "effective_local_span_km": profile.effective_local_span_km if profile else local_span_km,
                             "floor_conflict_intervals": profile.floor_conflicts if profile else 0},
            "speed_segments": profile.legs if profile else [],
            "speed_measurements": speed_measurements,
            "arrival_pauses": playback.pauses, "arrival_pause_seconds": playback.duration - motion_seconds,
            "overview_start_seconds": overview_start, "overview_transition_seconds": overview_seconds,
            "overview_hold_seconds": overview_hold, "overview_photo_count": len(markers.entries) if show_photos else 0,
            "vehicle_ranges": config.ranges, "default_icon": config.default,
            "segment_icons": [{"destination_raw_index": s[1].raw_index,
                               "icon": config.for_photo(s[1].raw_index)} for s in route.segments],
            "photo_clusters": len(markers.groups), "overlay_issues": markers.issues,
            "photo_display": markers.report(fps) if show_photos else [],
            "photo_events": [{"video_seconds": playback.arrival_time(t),
                              **asdict(photo)} for t, photo in route.events]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--photos", type=Path)
    source.add_argument("--gpx", type=Path)
    source.add_argument("--demo", action="store_true")
    parser.add_argument("-o", "--output", type=Path, default=Path("travel.mp4"))
    parser.add_argument("--timezone", default="UTC", help="IANA timezone for photos without EXIF UTC offsets")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--duration", type=float, help="Baseline movement seconds before speed limits, pauses, and overview; default 180")
    parser.add_argument("--timing", choices=["time", "compressed"], default="time",
                        help="time preserves real elapsed-time proportions; compressed bounds long gaps")
    parser.add_argument("--compression", type=float, default=0.5)
    parser.add_argument("--time-scale", type=float, default=300, help="Real seconds per animation second before compression")
    parser.add_argument("--min-segment", type=float, default=0.8)
    parser.add_argument("--max-segment", type=float, default=6)
    parser.add_argument("--max-speed-kmh", type=float, default=1200, help="Plausibility limit, including flights")
    parser.add_argument("--gap-hours", type=float, default=12)
    parser.add_argument("--cache", type=Path, default=Path(".cache/photo-video"))
    parser.add_argument("--map-provider", choices=["esri", "carto"], default="esri",
                        help="CARTO needs CARTO_BASEMAP_API_KEY; Esri public topo is the default")
    parser.add_argument("--offline", action="store_true", help="Use only cached tiles; fail if any are missing")
    parser.add_argument("--plain-map", action="store_true", help="Offline schematic background without geographic map tiles")
    parser.add_argument("--camera", choices=["fixed", "follow", "fly"], default="fly")
    parser.add_argument("--local-span-km", type=float, default=25)
    parser.add_argument("--flight-km", type=float, default=150)
    parser.add_argument("--max-tiles", type=int, default=1200)
    parser.add_argument("--popup-seconds", type=float, default=4, help="Minimum final overview hold; photos persist after arrival")
    parser.add_argument("--long-pause", type=float, default=0.2)
    parser.add_argument("--short-pause", type=float, default=0.05)
    parser.add_argument("--city-km", type=float, default=5)
    parser.add_argument("--min-speed-mps", type=float, default=1000,
                        help="Minimum ground metres per animation second; 0 disables")
    parser.add_argument("--max-screen-speed-px", type=float, default=180,
                        help="Maximum pixels per animation second, accounting for zoom and camera motion; 0 disables")
    parser.add_argument("--max-zoom-rate", type=float, default=1,
                        help="Maximum camera zoom octaves per second; 0 disables")
    parser.add_argument("--overview-seconds", type=float, default=5)
    parser.add_argument("--overview-hold", type=float, default=5)
    parser.add_argument("--config", type=Path, help="JSON icon ranges using 1-based raw photo indices")
    parser.add_argument("--turn-radius-m", type=float, default=1000, help="Local corner smoothing radius; 0 disables curves")
    parser.add_argument("--no-photos", action="store_true")
    parser.add_argument("--camera-seconds", type=float, default=0.6)
    parser.add_argument("--vehicle", choices=["car", "train", "plane"], default="car")
    parser.add_argument("--icon", type=Path, help="Transparent image facing up/north")
    parser.add_argument("--no-trail", action="store_true")
    parser.add_argument("--encoder", default="auto", help="auto, libx264, h264_nvenc, h264_qsv, etc.; software fallback")
    args = parser.parse_args()
    if args.duration is None and args.timing == "time":
        args.duration = 180
    if (args.width < 320 or args.height < 240 or args.width % 2 or args.height % 2
            or not 1 <= args.fps <= 120 or not math.isfinite(args.camera_seconds) or args.camera_seconds <= 0
            or not all(math.isfinite(v) and v > 0 for v in (args.local_span_km, args.flight_km,
                       args.popup_seconds, args.city_km, args.overview_seconds, args.overview_hold))
            or not all(math.isfinite(v) and v >= 0 for v in (args.long_pause, args.short_pause, args.turn_radius_m,
                       args.min_speed_mps, args.max_screen_speed_px, args.max_zoom_rate))
            or not 1 <= args.max_tiles <= 5000
            or (args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0))):
        parser.error("require even dimensions >=320x240, FPS 1..120, positive timing/camera/popup values, and 1..5000 tiles")
    try:
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
                      turn_radius_m=args.turn_radius_m, curve_limit_km=args.flight_km)
        issues += route.issues
        if args.photos:
            accepted = {p.path for p in route.photos}
            included = {p.path for _, p in route.events}
            valid = {p.path for p in photos}
            reasons = {str(Path(i["path"]).resolve()): i["reason"] for i in issues if i.get("skipped") is not False}
            manifest = [{"raw_index": i, "path": str(path.relative_to(args.photos)),
                         "metadata_valid": str(path.resolve()) in valid,
                         "route_anchor": str(path.resolve()) in accepted,
                         "photo_event": str(path.resolve()) in included,
                         "icon": config.for_photo(i), "skip_reason": reasons.get(str(path.resolve()))}
                        for i, path in enumerate(raw_photo_paths(args.photos), 1)]
            args.output.parent.mkdir(parents=True, exist_ok=True)
            manifest_path = args.output.with_suffix(".photo-index.json")
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        for issue in route.issues:
            print(f"{'Warning' if issue.get('skipped') is False else 'Skipped'}: {issue['path']}: {issue['reason']}", file=sys.stderr)
        print(f"Accepted {len(route.photos)} route points, {len(route.events)} photo events", flush=True)
        report = render(route, args.output, size=(args.width, args.height), fps=args.fps,
                        duration=args.duration, cache=args.cache, offline=args.offline, plain=args.plain_map,
                        camera=args.camera, camera_seconds=args.camera_seconds,
                        vehicle=args.vehicle, icon_path=args.icon, trail=not args.no_trail,
                        encoder=args.encoder, provider=args.map_provider, popup_seconds=args.popup_seconds,
                        show_photos=not args.no_photos, local_span_km=args.local_span_km,
                        flight_km=args.flight_km, max_tiles=args.max_tiles,
                        long_pause=args.long_pause, short_pause=args.short_pause,
                        city_km=args.city_km, overview_seconds=args.overview_seconds,
                        overview_hold=args.overview_hold, config=config,
                        min_speed_mps=args.min_speed_mps, max_screen_speed_px=args.max_screen_speed_px,
                        max_zoom_rate=args.max_zoom_rate)
        report.update({"input_records": len(photos), "accepted_route_points": len(route.photos),
                       "issues": issues, "timezone_for_missing_offsets": args.timezone})
        report_path = args.output.with_suffix(".report.json")
        report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
        print(f"Saved {args.output}: {report['frames']} frames in {report['render_seconds']}s; report: {report_path}")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
