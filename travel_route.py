"""Photo records and a compressed, WGS84 geodesic animation timeline."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image
from pyproj import Geod, Transformer, CRS

from convert import dms_to_decimal
from gpx_to_timeline import read_gpx_points

GEOD = Geod(ellps="WGS84")
EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic"}


@dataclass(frozen=True)
class Photo:
    timestamp: datetime
    latitude: float
    longitude: float
    path: str
    altitude: float | None = None
    raw_index: int | None = None


def validate_point(photo):
    if not (math.isfinite(photo.latitude) and math.isfinite(photo.longitude)
            and -90 <= photo.latitude <= 90 and -180 <= photo.longitude <= 180):
        raise ValueError("invalid GPS coordinates")
    if abs(photo.latitude) > 85.05112878:
        raise ValueError("latitude outside raster Web Mercator coverage")
    if photo.altitude is not None and not math.isfinite(photo.altitude):
        raise ValueError("invalid altitude")
    return photo


def text(value):
    return value.decode("ascii").strip("\0 ") if isinstance(value, bytes) else str(value).strip("\0 ")


def gps_coordinate(dms, ref):
    if len(dms) != 3:
        raise ValueError("invalid GPS DMS tuple")
    degrees, minutes, seconds = map(float, dms)
    if not (all(math.isfinite(v) for v in (degrees, minutes, seconds))
            and degrees >= 0 and 0 <= minutes < 60 and 0 <= seconds < 60):
        raise ValueError("invalid GPS degrees/minutes/seconds")
    return dms_to_decimal(dms, ref)


def extract_photo(path, default_timezone, raw_index=None):
    with Image.open(path) as image:
        exif = image.getexif()
        original = exif.get_ifd(0x8769)  # DateTimeOriginal usually lives in ExifIFD.
        stamp = original.get(36867, exif.get(36867))
        if not stamp:
            raise ValueError("missing DateTimeOriginal")
        local = datetime.strptime(text(stamp), "%Y:%m:%d %H:%M:%S")
        subsecond = original.get(37521, exif.get(37521))
        if subsecond:
            digits = text(subsecond)
            if not digits.isdigit():
                raise ValueError("invalid SubSecTimeOriginal")
            local = local.replace(microsecond=int(digits[:6].ljust(6, "0")))
        offset = original.get(36881, exif.get(36881))
        if offset:
            local = datetime.fromisoformat(local.isoformat() + text(offset))
        else:
            # Refuse ambiguous/nonexistent DST wall times instead of silently guessing.
            candidates = []
            for fold in (0, 1):
                aware = local.replace(tzinfo=default_timezone, fold=fold)
                utc = aware.astimezone(timezone.utc)
                if utc.astimezone(default_timezone).replace(tzinfo=None) == local:
                    if utc not in candidates:
                        candidates.append(utc)
            if len(candidates) != 1:
                raise ValueError("ambiguous/nonexistent local time; EXIF UTC offset required")
            local = candidates[0]
        gps = exif.get_ifd(0x8825)
        if not all(key in gps for key in (1, 2, 3, 4)):
            raise ValueError("missing GPS coordinates or hemisphere references")
        lat_ref, lon_ref = text(gps[1]), text(gps[3])
        if lat_ref not in ("N", "S") or lon_ref not in ("E", "W"):
            raise ValueError("invalid GPS hemisphere references")
        altitude = float(gps[6]) if 6 in gps else None
        if altitude is not None and gps.get(5) in (1, b"\x01"):
            altitude = -altitude
        return validate_point(Photo(
            local.astimezone(timezone.utc),
            gps_coordinate(gps[2], lat_ref), gps_coordinate(gps[4], lon_ref),
            str(path.resolve()), altitude, raw_index,
        ))


def raw_photo_paths(folder):
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"photo directory does not exist: {folder}")
    return sorted((p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS),
                  key=lambda p: p.relative_to(folder).as_posix())


def scan_photos(folder, timezone_name="UTC"):
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"photo directory does not exist: {folder}")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"unknown timezone: {timezone_name}") from error
    photos, issues = [], []
    for index, path in enumerate(raw_photo_paths(folder), start=1):
        try:
            photos.append(extract_photo(path, zone, index))
        except Exception as error:
            issues.append({"path": str(path), "raw_index": index, "reason": str(error)})
    return sorted(photos, key=lambda p: (p.timestamp, p.path)), issues


def load_gpx(path):
    return [validate_point(Photo(p["time"], p["lat"], p["lon"], p["name"] or ""))
            for p in read_gpx_points(path)]


def demo_route():
    start = datetime(2026, 7, 26, 9, tzinfo=timezone.utc)
    coords = [(25.0146, 121.5347), (25.0156, 121.5371), (25.0176, 121.5347),
              (25.0207, 121.5319), (25.0271, 121.5291), (25.0329, 121.5185)]
    return [Photo(start + timedelta(minutes=i * i * 4), lat, lon, "synthetic")
            for i, (lat, lon) in enumerate(coords)]


@dataclass(frozen=True)
class Position:
    animation_time: float
    timestamp: datetime
    latitude: float
    longitude: float
    heading: float  # clockwise from true north
    segment: int


class Route:
    def __init__(self, photos, *, compression=0.5, real_seconds_per_video_second=300,
                 min_segment=0.8, max_segment=6, max_speed_kmh=350, gap_hours=12,
                 timing="time", turn_radius_m=0, curve_limit_km=150):
        if timing not in ("time", "compressed"):
            raise ValueError("timing must be time or compressed")
        self.timing = timing
        if not (all(math.isfinite(v) for v in (compression, real_seconds_per_video_second,
                                             min_segment, max_segment, max_speed_kmh, gap_hours))
                and 0 < compression <= 1 and real_seconds_per_video_second > 0
                and 0 < min_segment <= max_segment and max_speed_kmh > 0 and gap_hours > 0):
            raise ValueError("invalid time scaling, speed, or gap configuration")
        self.photos, self.issues = [], []
        self.events = []  # retained photo events, including colocated timestamp bursts
        self.segments, self.ends = [], [0.0]
        for photo in sorted(photos, key=lambda p: (p.timestamp, p.path)):
            validate_point(photo)
            if not self.photos:
                self.photos.append(photo)
                self.events.append((0.0, photo))
                continue
            previous = self.photos[-1]
            heading, _, distance = GEOD.inv(previous.longitude, previous.latitude,
                                             photo.longitude, photo.latitude)
            seconds = (photo.timestamp - previous.timestamp).total_seconds()
            if seconds == 0:
                if distance <= 50:
                    self.events.append((self.ends[-1], photo))
                else:
                    self.issues.append({"path": photo.path, "reason": "conflicting GPS at duplicate timestamp"})
                continue
            speed = distance / seconds * 3.6
            if speed > max_speed_kmh:
                self.issues.append({"path": photo.path, "reason": f"GPS jump: {speed:.0f} km/h from previous accepted photo"})
                continue
            duration = seconds / real_seconds_per_video_second if timing == "time" else min(
                max_segment, max(min_segment, (seconds / real_seconds_per_video_second) ** compression))
            self.segments.append((previous, photo, heading, distance, duration))
            self.ends.append(self.ends[-1] + duration)
            self.photos.append(photo)
            self.events.append((self.ends[-1], photo))
            if seconds > gap_hours * 3600:
                self.issues.append({"path": photo.path, "reason": "long gap: geodesic transfer is inferred", "skipped": False})
        if len(self.photos) < 2:
            details = "; ".join(f"{i['path']}: {i['reason']}" for i in self.issues)
            raise ValueError("need at least two accepted photos with distinct timestamps"
                             + (f"; {details}" if details else ""))
        self.duration = self.ends[-1]
        if not math.isfinite(turn_radius_m) or turn_radius_m < 0:
            raise ValueError("turn radius must be finite and nonnegative")
        self.curves = [None] * len(self.segments)
        if turn_radius_m:
            self._build_curves(turn_radius_m, curve_limit_km * 1000)

    def _build_curves(self, radius, limit):
        def bearings(segment):
            a, b = segment[:2]
            heading, back, _ = GEOD.inv(a.longitude, a.latitude, b.longitude, b.latitude)
            return heading, (back + 180) % 360
        def average(a, b):
            return a + ((b - a + 180) % 360 - 180) / 2
        for i, (a, b, heading, distance, _) in enumerate(self.segments):
            if not 1 < distance < limit:
                continue  # Keep long transfers as WGS84 geodesics.
            departure, arrival = bearings(self.segments[i])
            start_length = end_length = min(radius, distance * 0.3)
            if i and 1 < self.segments[i - 1][3] < limit:
                departure = average(bearings(self.segments[i - 1])[1], departure)
                start_length = min(start_length, self.segments[i - 1][3] * 0.3)
            if i + 1 < len(self.segments) and 1 < self.segments[i + 1][3] < limit:
                arrival = average(arrival, bearings(self.segments[i + 1])[0])
                end_length = min(end_length, self.segments[i + 1][3] * 0.3)
            self.curves[i] = RoundedSegment(a, b, departure, arrival, start_length, end_length)

    def at(self, animation_time):
        from bisect import bisect_right
        animation_time = max(0.0, min(self.duration, animation_time))
        index = min(len(self.segments) - 1, bisect_right(self.ends, animation_time) - 1)
        start, end, heading, distance, duration = self.segments[index]
        fraction = (animation_time - self.ends[index]) / duration
        if self.curves[index] is not None:
            lon, lat, bearing = self.curves[index].at(fraction)
        else:
            lon, lat, back_azimuth = GEOD.fwd(start.longitude, start.latitude, heading, distance * fraction)
            bearing = (back_azimuth + 180) % 360 if distance > 0.01 else heading % 360
        return Position(animation_time,
            start.timestamp + (end.timestamp - start.timestamp) * fraction,
            lat, lon, bearing, index)


class RoundedSegment:
    """Local metric cubic Bezier, reparameterized by arc length; anchors stay exact."""
    def __init__(self, a, b, departure, arrival, start_length, end_length):
        local = CRS.from_proj4(f"+proj=aeqd +lat_0={a.latitude} +lon_0={a.longitude} +datum=WGS84 +units=m")
        self.forward = Transformer.from_crs(4326, local, always_xy=True)
        self.inverse = Transformer.from_crs(local, 4326, always_xy=True)
        c1 = GEOD.fwd(a.longitude, a.latitude, departure, start_length)[:2]
        c2 = GEOD.fwd(b.longitude, b.latitude, arrival + 180, end_length)[:2]
        self.controls = [self.forward.transform(*p) for p in
                         ((a.longitude, a.latitude), c1, c2, (b.longitude, b.latitude))]
        self.lengths = [0.0]
        for i in range(1, 129):
            self.lengths.append(self.lengths[-1] + self.arc_length((i - 1) / 128, i / 128))

    def derivative(self, u):
        p = self.controls
        return tuple(sum(3 * w * (p[j + 1][axis] - p[j][axis])
                         for j, w in enumerate(((1 - u) ** 2, 2 * u * (1 - u), u * u)))
                     for axis in (0, 1))

    def arc_length(self, a, b):
        # Simpson integration also resolves endpoint tangents much shorter than a bin.
        step = (b - a) / 8
        return step / 3 * sum((1 if j in (0, 8) else 4 if j % 2 else 2)
                              * math.hypot(*self.derivative(a + step * j)) for j in range(9))

    def evaluate(self, u):
        weights = ((1 - u) ** 3, 3 * u * (1 - u) ** 2, 3 * u * u * (1 - u), u ** 3)
        return tuple(sum(w * p[axis] for w, p in zip(weights, self.controls)) for axis in (0, 1))

    def at(self, fraction):
        from bisect import bisect_right
        target = max(0, min(1, fraction)) * self.lengths[-1]
        i = min(127, max(0, bisect_right(self.lengths, target) - 1))
        low, high = i / 128, (i + 1) / 128
        local_target = target - self.lengths[i]
        u = low + (high - low) * local_target / (self.lengths[i + 1] - self.lengths[i])
        origin = low
        for _ in range(8):
            error = self.arc_length(origin, u) - local_target
            if abs(error) < 1e-7:
                break
            if error > 0:
                high = u
            else:
                low = u
            speed = math.hypot(*self.derivative(u))
            candidate = u - error / speed if speed > 1e-12 else (low + high) / 2
            u = candidate if low < candidate < high else (low + high) / 2
        x, y = self.evaluate(u)
        dx, dy = self.derivative(u)
        norm = math.hypot(dx, dy)
        lon, lat = self.inverse.transform(x, y)
        ahead_lon, ahead_lat = self.inverse.transform(x + dx / norm, y + dy / norm)
        bearing = GEOD.inv(lon, lat, ahead_lon, ahead_lat)[0] % 360
        return lon, lat, bearing
