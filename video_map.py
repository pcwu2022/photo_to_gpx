"""Bounded tile atlas, adapted from mahlernim/google-timeline-visualizer.

Tile coordinate/stitching approach and smoothstep camera easing adapted from
visualizer.py at 16714b0b23b93c3ec6aeef45b7ebbe1e76329847 (MIT).
See THIRD_PARTY_LICENSE.txt. No network requests occur during rendering.
"""
import io
import math
import os
from pathlib import Path
import urllib.request
import urllib.parse

from PIL import Image, ImageDraw
from pyproj import Transformer

EXTENT = 20037508.342789244
TILE_URL = "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"
ESRI_URL = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}"
ESRI_ATTRIBUTION = ("Sources: Esri, HERE, Garmin, Intermap, increment P Corp., GEBCO, USGS, FAO, NPS, "
                    "NRCAN, GeoBase, IGN, Kadaster NL, Ordnance Survey, Esri Japan, METI, "
                    "Esri China (Hong Kong), © OpenStreetMap contributors, and the GIS User Community")
PROJECT = Transformer.from_crs(4326, 3857, always_xy=True, force_over=True)


def project_positions(positions):
    result = []
    longitude = positions[0].longitude
    for point in positions:
        longitude += (point.longitude - longitude + 180) % 360 - 180
        if abs(point.latitude) > 85.05112878:
            raise ValueError("geodesic crosses outside Web Mercator coverage")
        result.append(PROJECT.transform(longitude, point.latitude))
    return result


def tile_coordinates(x, y, zoom):
    count = 2 ** zoom
    return (math.floor((x + EXTENT) / (2 * EXTENT) * count),
            math.floor((EXTENT - y) / (2 * EXTENT) * count))


def fetch_tile(x, y, zoom, cache, offline, provider):
    namespace = "esri-topo" if provider == "esri" else "carto-light-auth"
    path = cache / namespace / str(zoom) / str(x) / f"{y}.png"
    if path.exists():
        try:
            with Image.open(path) as image:
                tile = image.convert("RGB")
                if tile.size != (256, 256):
                    raise ValueError("incorrect tile size")
                return tile
        except (OSError, ValueError):
            if offline:
                raise ValueError(f"corrupt cached tile: {path}")
    if offline:
        raise ValueError(f"missing cached tile: {path}; run once without --offline")
    if provider == "carto":
        key = os.environ.get("CARTO_BASEMAP_API_KEY", "").strip()
        if not key:
            raise ValueError("CARTO requires CARTO_BASEMAP_API_KEY; use --map-provider esri or --plain-map")
        url = TILE_URL.format(z=zoom, x=x, y=y) + "?" + urllib.parse.urlencode({"key": key})
    else:
        url = ESRI_URL.format(z=zoom, x=x, y=y)
    request = urllib.request.Request(url,
                                     headers={"User-Agent": "photo-to-video/0.1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        with Image.open(io.BytesIO(response.read())) as image:
            tile = image.convert("RGB")
    if tile.size != (256, 256):
        raise ValueError("tile provider returned incorrect dimensions")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    tile.save(temporary, format="PNG")
    temporary.replace(path)
    return tile


class MapCanvas:
    def __init__(self, points, size, *, cache, offline=False, plain=False,
                 camera="fixed", camera_seconds=0.6, fps=30, trail=True, provider="esri",
                 draw_points=None, draw_times=None):
        self.size, self.camera, self.trail = size, camera, trail
        self.camera_alpha = 1 - math.exp(-1 / (fps * camera_seconds))
        aspect = size[0] / size[1]
        xs, ys = zip(*points)
        span_y = max(max(ys) - min(ys), (max(xs) - min(xs)) / aspect, 1000) * 1.3
        self.full_span = (span_y * aspect, span_y)
        self.span = self.full_span if camera == "fixed" else (span_y * aspect * 0.55, span_y * 0.55)
        self.center = ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2) if camera == "fixed" else points[0]
        # Include all possible following viewports before fetching a single tile.
        bounds = (min(xs) - self.span[0] / 2, max(xs) + self.span[0] / 2,
                  min(ys) - self.span[1] / 2, max(ys) + self.span[1] / 2)
        if camera == "fixed":
            bounds = (self.center[0] - self.span[0] / 2, self.center[0] + self.span[0] / 2,
                      self.center[1] - self.span[1] / 2, self.center[1] + self.span[1] / 2)
        if plain:
            self.image = Image.new("RGB", size, "#e8edf1")
            self.bounds = bounds
            self.attribution = "Offline schematic • inferred geodesic route"
            self.tile_count = 0
        else:
            zoom = max(0, min(16, int(math.log2(2 * EXTENT * size[0] / (256 * self.span[0])))))
            while True:
                x0, y0 = tile_coordinates(bounds[0], bounds[3], zoom)
                x1, y1 = tile_coordinates(bounds[1], bounds[2], zoom)
                tile_count = (x1 - x0 + 1) * (y1 - y0 + 1)
                if tile_count <= 64 or zoom == 0:
                    break
                zoom -= 1
            if tile_count > 64:
                raise ValueError("route atlas exceeds tile budget")
            self.image = Image.new("RGB", ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256), "#e8edf1")
            tile_span = 2 * EXTENT / 2 ** zoom
            self.bounds = (-EXTENT + x0 * tile_span, -EXTENT + (x1 + 1) * tile_span,
                           EXTENT - (y1 + 1) * tile_span, EXTENT - y0 * tile_span)
            self.tile_count = tile_count
            print(f"Preparing {tile_count} tiles at zoom {zoom} (disk cache: {cache})", flush=True)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    if 0 <= y < 2 ** zoom:
                        tile = fetch_tile(x % (2 ** zoom), y, zoom, Path(cache), offline, provider)
                        self.image.paste(tile, ((x - x0) * 256, (y - y0) * 256))
            self.attribution = ESRI_ATTRIBUTION if provider == "esri" else "© OpenStreetMap contributors © CARTO"
        if camera == "fixed":
            self.image = self.view()
            self.bounds = bounds
        self.draw = ImageDraw.Draw(self.image)
        self.route_pixels = [self.pixel(p) for p in (draw_points or points)]
        self.draw_times, self.draw_cursor = draw_times, 0
        scale = self.image.width / size[0]
        self.line_width = max(2, round(4 * scale))
        self.last = self.route_pixels[0]

    def pixel(self, point):
        left, right, bottom, top = self.bounds
        return ((point[0] - left) / (right - left) * self.image.width,
                (top - point[1]) / (top - bottom) * self.image.height)

    def view(self):
        cx, cy = self.center
        sx, sy = self.span
        left, top = self.pixel((cx - sx / 2, cy + sy / 2))
        right, bottom = self.pixel((cx + sx / 2, cy - sy / 2))
        return self.image.transform(self.size, Image.Transform.EXTENT,
                                    (left, top, right, bottom), Image.Resampling.BILINEAR)

    def frame(self, point, route_time=None):
        pixel = self.pixel(point)
        if self.trail:
            if self.draw_times is not None:
                from bisect import bisect_right
                cursor = bisect_right(self.draw_times, route_time)
                path = [self.last, *self.route_pixels[self.draw_cursor:cursor], pixel]
                self.draw_cursor = cursor
            else:
                path = [self.last, pixel]
            self.draw.line(path, fill="#e84553", width=self.line_width)
        self.last = pixel
        if self.camera == "follow":
            self.center = tuple(c + (p - c) * self.camera_alpha for c, p in zip(self.center, point))
            # Keep the vehicle inside the middle 70% even during fast transfers.
            self.center = tuple(max(p - span * 0.35, min(p + span * 0.35, c))
                                for c, p, span in zip(self.center, point, self.span))
            frame = self.view()
        else:
            frame = self.image.copy()
        cx, cy = self.center
        sx, sy = self.span
        screen = ((point[0] - cx) / sx * self.size[0] + self.size[0] / 2,
                  (cy - point[1]) / sy * self.size[1] + self.size[1] / 2)
        return frame, screen


def vehicle_icon(kind, path=None):
    """Custom transparent icons should face up/north before rotation."""
    if path:
        with Image.open(path) as image:
            icon = image.convert("RGBA")
        icon.thumbnail((72, 72), Image.Resampling.LANCZOS)
        return icon
    icon = Image.new("RGBA", (48, 72))
    draw = ImageDraw.Draw(icon)
    if kind == "plane":
        draw.polygon([(24, 1), (29, 28), (46, 44), (46, 51), (28, 43),
                      (28, 60), (36, 68), (36, 71), (24, 67), (12, 71),
                      (12, 68), (20, 60), (20, 43), (2, 51), (2, 44), (19, 28)],
                     fill="#f4f7fa", outline="#23313d", width=2)
        draw.line((24, 12, 24, 61), fill="#e84553", width=3)
        return icon
    if kind == "car":
        for x in (3, 35):
            for y in (13, 48):
                draw.rounded_rectangle((x, y, x + 10, y + 13), radius=3, fill="#23313d")
    draw.rounded_rectangle((9, 2, 39, 69), radius=10, fill="#e84553", outline="white", width=2)
    draw.rounded_rectangle((14, 14, 34, 30), radius=3, fill="#b7e4f2")
    draw.rectangle((14, 45, 34, 53), fill="#23313d")
    if kind == "train":
        draw.line((15, 61, 33, 61), fill="white", width=3)
    return icon


def flight_camera_track(points, positions, route, size, fps, smoothing=0.6,
                        local_span_km=25, flight_km=150, city_km=5, zoom_ramp=1):
    """Endpoint-aware flight easing, with a locked viewport for nearby movements."""
    aspect = size[0] / size[1]
    local = local_span_km * 1000
    half_x, half_y = 0.5 - 40 / size[0], 0.5 - 72 / size[1]
    pose = (points[0][0], points[0][1], local * aspect, local)
    cameras, segment_id, departure_pose = [], None, pose
    alpha = 1 - math.exp(-1 / (fps * smoothing))
    for index, (point, position) in enumerate(zip(points, positions)):
        _, _, _, distance, duration = route.segments[position.segment]
        u = max(0, min(1, (position.animation_time - route.ends[position.segment]) / duration))
        if position.segment != segment_id:
            departure_pose = pose
            segment_id = position.segment
        cx, cy, sx, sy = pose
        inside = abs(point[0] - cx) <= sx * half_x and abs(point[1] - cy) <= sy * half_y
        stationary = index and position.animation_time == positions[index - 1].animation_time
        if distance <= city_km * 1000 and inside:
            # Exact lock: no smoothing drift, zoom changes, or camera anticipation.
            cameras.append(pose)
            continue
        if distance >= flight_km * 1000 and not stationary:
            eased = u * u * (3 - 2 * u)
            route_t = route.ends[position.segment] + duration * eased
            center = route.at(route_t)
            reference_lon = point[0] / 6378137 * 180 / math.pi
            lon = reference_lon + (center.longitude - reference_lon + 180) % 360 - 180
            tx, ty = PROJECT.transform(lon, center.latitude)
            start = route.segments[position.segment][0]
            start_lon = reference_lon + (start.longitude - reference_lon + 180) % 360 - 180
            ax, ay = PROJECT.transform(start_lon, start.latitude)
            cx = tx + (departure_pose[0] - ax) * (1 - eased)
            cy = ty + (departure_pose[1] - ay) * (1 - eased)
            base = math.exp(math.log(departure_pose[3]) * (1 - eased) + math.log(local) * eased)
            ramp = zoom_ramp(distance) if callable(zoom_ramp) else zoom_ramp
            sy = base + distance * 1.8 * min(1, ramp * math.sin(math.pi * u) ** 2)
        elif stationary:
            cx += (point[0] - cx) * alpha
            cy += (point[1] - cy) * alpha
            sy = math.exp(math.log(sy) + (math.log(local) - math.log(sy)) * alpha)
        elif distance <= city_km * 1000:
            # Move only when the vehicle reaches the edge; keep the current zoom.
            cx += (point[0] - cx) * alpha
            cy += (point[1] - cy) * alpha
            cx = max(point[0] - sx * half_x, min(point[0] + sx * half_x, cx))
            cy = max(point[1] - sy * half_y, min(point[1] + sy * half_y, cy))
        else:
            eased = u * u * (3 - 2 * u)
            end = route.segments[position.segment][1]
            reference_lon = point[0] / 6378137 * 180 / math.pi
            lon = reference_lon + (end.longitude - reference_lon + 180) % 360 - 180
            ex, ey = PROJECT.transform(lon, end.latitude)
            cx = departure_pose[0] + (ex - departure_pose[0]) * eased
            cy = departure_pose[1] + (ey - departure_pose[1]) * eased
            sy = math.exp(math.log(departure_pose[3]) * (1 - eased) + math.log(local) * eased)
        sy = max(sy, abs(point[1] - cy) / half_y, abs(point[0] - cx) / (half_x * aspect))
        pose = (cx, cy, sy * aspect, sy)
        cameras.append(pose)
    return cameras


def overview_pose(points, size):
    xs, ys = zip(*points)
    aspect = size[0] / size[1]
    sy = max(max(ys) - min(ys), (max(xs) - min(xs)) / aspect, 1000) * 1.4
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, sy * aspect, sy)


def append_overview(cameras, start_index, target, transition_frames):
    """Interpolate center and logarithmic zoom; finish with the exact overview pose."""
    departure = cameras[max(0, start_index - 1)]
    for i in range(start_index, len(cameras)):
        u = min(1, (i - start_index + 1) / max(1, transition_frames))
        eased = u * u * (3 - 2 * u)
        cx, cy = (a + (b - a) * eased for a, b in zip(departure[:2], target[:2]))
        sx, sy = (math.exp(math.log(a) + (math.log(b) - math.log(a)) * eased)
                  for a, b in zip(departure[2:], target[2:]))
        cameras[i] = (cx, cy, sx, sy) if u < 1 else target


class FlightMapCanvas:
    """Sparse multi-zoom tiles: prefetch once; bounded tile/viewport LRU at render time."""
    def __init__(self, points, cameras, size, *, cache, offline=False, plain=False,
                 provider="esri", trail=True, max_tiles=1200, draw_points=None, draw_times=None):
        from collections import OrderedDict
        self.size, self.cameras, self.points = size, cameras, points
        self.cache, self.provider, self.plain, self.trail = Path(cache), provider, plain, trail
        self.attribution = ("Offline schematic • inferred geodesic route" if plain else
                            ESRI_ATTRIBUTION if provider == "esri" else "© OpenStreetMap contributors © CARTO")
        self.tiles, self.atlases = OrderedDict(), OrderedDict()
        self.plans = []
        required = set()
        # Lower all zooms if the requested path exceeds the prefetch budget.
        for zoom_offset in range(17):
            required.clear()
            self.plans.clear()
            for cx, cy, sx, sy in cameras:
                zoom = max(0, min(16, int(math.log2(2 * EXTENT * size[0] / (256 * sx)))) - zoom_offset)
                x0, y0 = tile_coordinates(cx - sx / 2, cy + sy / 2, zoom)
                x1, y1 = tile_coordinates(cx + sx / 2, cy - sy / 2, zoom)
                self.plans.append((zoom, x0, y0, x1, y1))
                for x in range(x0, x1 + 1):
                    for y in range(y0, y1 + 1):
                        if 0 <= y < 2 ** zoom:
                            required.add((zoom, x % (2 ** zoom), y))
            if len(required) <= max_tiles:
                break
        self.tile_count = 0 if plain else len(required)
        if not plain:
            print(f"Prefetching {len(required)} tiles across zoom levels {sorted({z for z, _, _ in required})}", flush=True)
            from concurrent.futures import ThreadPoolExecutor
            def prepare(key):
                z, x, y = key
                fetch_tile(x, y, z, self.cache, offline, provider)
            with ThreadPoolExecutor(max_workers=4) as pool:
                for index, _ in enumerate(pool.map(prepare, sorted(required))):
                    if (index + 1) % 100 == 0:
                        print(f"Prepared {index + 1}/{len(required)} tiles", flush=True)
        # Downsample only the map drawing layer, never the trajectory or event timing.
        self.draw_points, self.draw_times = draw_points, draw_times
        if draw_points is None:
            stride = max(1, len(points) // 1500)
            self.draw_indices = list(range(0, len(points), stride))
            if self.draw_indices[-1] != len(points) - 1:
                self.draw_indices.append(len(points) - 1)
        else:
            self.draw_indices = list(range(len(draw_points)))

    def screen(self, point, index):
        cx, cy, sx, sy = self.cameras[index]
        return ((point[0] - cx) / sx * self.size[0] + self.size[0] / 2,
                (cy - point[1]) / sy * self.size[1] + self.size[1] / 2)

    def atlas(self, plan):
        if plan in self.atlases:
            self.atlases.move_to_end(plan)
            return self.atlases[plan]
        z, x0, y0, x1, y1 = plan
        image = Image.new("RGB", ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256), "#e8edf1")
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if 0 <= y < 2 ** z:
                    key = (z, x % (2 ** z), y)
                    if key not in self.tiles:
                        # offline=True is mandatory here: rendering never uses the network.
                        self.tiles[key] = fetch_tile(key[1], key[2], z, self.cache, True, self.provider)
                    self.tiles.move_to_end(key)
                    image.paste(self.tiles[key], ((x - x0) * 256, (y - y0) * 256))
                    while len(self.tiles) > 128:
                        self.tiles.popitem(last=False)
        self.atlases[plan] = image
        while len(self.atlases) > 4:
            self.atlases.popitem(last=False)
        return image

    def frame(self, point, index, route_time=None):
        if self.plain:
            frame = Image.new("RGB", self.size, "#e8edf1")
        else:
            plan = self.plans[index]
            z, x0, y0, _, _ = plan
            atlas = self.atlas(plan)
            cx, cy, sx, sy = self.cameras[index]
            meters_per_pixel = 2 * EXTENT / (2 ** z * 256)
            left = (cx - sx / 2 + EXTENT) / meters_per_pixel - x0 * 256
            top = (EXTENT - cy - sy / 2) / meters_per_pixel - y0 * 256
            frame = atlas.transform(self.size, Image.Transform.EXTENT,
                                    (left, top, left + sx / meters_per_pixel, top + sy / meters_per_pixel),
                                    Image.Resampling.BILINEAR)
        draw = ImageDraw.Draw(frame)
        path_points = self.draw_points if self.draw_points is not None else self.points
        if self.trail:
            if self.draw_times is not None:
                from bisect import bisect_right
                indices = self.draw_indices[:bisect_right(self.draw_times, route_time)]
            else:
                indices = [i for i in self.draw_indices if i <= index]
            traveled = [self.screen(path_points[i], index) for i in indices]
            traveled.append(self.screen(point, index))
            if len(traveled) >= 2:
                draw.line(traveled, fill="#e84553", width=4)
        return frame, self.screen(point, index)
