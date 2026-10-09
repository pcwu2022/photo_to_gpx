"""Persistent circular photos with fixed geographic centers and map-scaled sizes."""
from bisect import bisect_right
from collections import OrderedDict
from pathlib import Path
import math

from PIL import Image, ImageDraw, ImageOps, ImageFilter
from travel_route import GEOD
from video_map import PROJECT


class PhotoMarkers:
    FADE_IN = 0.18

    def __init__(self, events, route_duration, motion_seconds, size, *, duration=4,
                 cluster_seconds=30, cluster_meters=50, arrival_time=None, fps=30):
        self.duration, self.size = duration, size
        self.cache, self.scaled_cache, self.issues = OrderedDict(), OrderedDict(), []
        self.groups, self.entries = [], []
        convert = arrival_time or (lambda t: t / route_duration * motion_seconds)
        for t, photo in events:
            if not Path(photo.path).is_file():
                continue
            # Even sub-frame arrivals get an entire minimum visibility window.
            start = math.ceil(convert(t) * fps - 1e-8) / fps
            event = (start, photo)
            if self.groups:
                group = self.groups[-1]
                first, previous = group[0][1], group[-1][1]
                _, _, distance = GEOD.inv(first.longitude, first.latitude, photo.longitude, photo.latitude)
                if ((photo.timestamp - previous.timestamp).total_seconds() <= cluster_seconds
                        and (photo.timestamp - first.timestamp).total_seconds() <= 120
                        and distance <= cluster_meters):
                    group.append(event)
                else:
                    self.groups.append([event])
            else:
                self.groups.append([event])
            self.entries.append((start, math.inf, photo))
        self.starts = [e[0] for e in self.entries]
        self.peak = max(1, len(self.entries))
        self.diameter = max(1, round(size[0] / 10))
        self.set_reference_span(size[0])
        self.visible_frames = [0] * len(self.entries)
        self.full_frames = [0] * len(self.entries)

    def active(self, seconds):
        end = bisect_right(self.starts, seconds)
        return list(range(end))

    def thumbnail(self, photo, diameter=None):
        d = diameter or self.diameter
        key = (photo.path, d)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        pad = 12
        try:
            with Image.open(photo.path) as image:
                image.draft("RGB", (d * 2, d * 2))
                thumb = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGB"),
                                     (d - 12, d - 12), Image.Resampling.LANCZOS)
            sprite = Image.new("RGBA", (d + pad * 2, d + pad * 2 + 18))
            shadow = Image.new("RGBA", sprite.size)
            ImageDraw.Draw(shadow).ellipse((pad + 2, pad + 5, pad + d + 2, pad + d + 5),
                                           fill=(0, 0, 0, 105))
            sprite.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(5)))
            draw = ImageDraw.Draw(sprite)
            draw.ellipse((pad, pad, pad + d - 1, pad + d - 1), fill="white", outline="#b7c2c9", width=1)
            mask = Image.new("L", (thumb.width * 2, thumb.height * 2))
            ImageDraw.Draw(mask).ellipse((0, 0, mask.width - 1, mask.height - 1), fill=255)
            mask = mask.resize(thumb.size, Image.Resampling.LANCZOS)
            sprite.paste(thumb, (pad + 6, pad + 6), mask)
            label = f"{photo.timestamp:%m-%d %H:%M}"
            label_width = math.ceil(draw.textlength(label)) + 12
            label_x = (sprite.width - label_width) // 2
            draw.rounded_rectangle((label_x, pad + d + 2, label_x + label_width, pad + d + 16),
                                   radius=5, fill="white")
            draw.text((label_x + 6, pad + d + 3), label, fill="#23313d")
        except (OSError, ValueError) as error:
            self.issues.append({"path": photo.path, "reason": f"photo overlay: {error}"})
            sprite = None
        self.cache[key] = sprite
        while len(self.cache) > max(128, self.peak * 2):
            self.cache.popitem(last=False)
        return sprite

    def anchor(self, index, reference_x):
        photo = self.entries[index][2]
        reference_lon = reference_x / 6378137 * 180 / math.pi
        lon = reference_lon + (photo.longitude - reference_lon + 180) % 360 - 180
        return PROJECT.transform(lon, photo.latitude)

    def set_reference_span(self, span_m):
        if not math.isfinite(span_m) or span_m <= 0:
            raise ValueError("photo reference camera span must be finite and positive")
        self.reference_span_m = span_m
        self.world_diameter_m = span_m / 10

    def geometry(self, index, screen, reference_x, span_m=None):
        center = screen(self.anchor(index, reference_x))
        diameter = self.world_diameter_m / (span_m or self.reference_span_m) * self.size[0]
        return center, diameter

    def scaled_thumbnail(self, photo, scale):
        base = self.thumbnail(photo)
        if base is None:
            return None
        dimensions = tuple(max(1, round(v * scale)) for v in base.size)
        key = (photo.path, dimensions)
        if key not in self.scaled_cache:
            self.scaled_cache[key] = (base if dimensions == base.size else
                                      base.resize(dimensions, Image.Resampling.LANCZOS))
        self.scaled_cache.move_to_end(key)
        while len(self.scaled_cache) > max(128, self.peak * 3):
            self.scaled_cache.popitem(last=False)
        return self.scaled_cache[key]

    def draw(self, frame, seconds, screen, reference_x, vehicle_screen=None, *, span_m=None):
        scale = self.reference_span_m / (span_m or self.reference_span_m)
        count = 0
        for i in self.active(seconds):
            start, _, photo = self.entries[i]
            (x, y), diameter = self.geometry(i, screen, reference_x, span_m)
            radius = diameter / 2
            if x + radius < 0 or x - radius > self.size[0] or y + radius < 32 or y - radius > self.size[1] - 26:
                continue
            opacity = min(1, max(0, (seconds - start) / self.FADE_IN))
            if opacity <= 0:
                continue
            sprite = self.scaled_thumbnail(photo, scale)
            if sprite is None:
                continue
            if opacity < 1:
                sprite = sprite.copy()
                sprite.putalpha(sprite.getchannel("A").point(lambda value: round(value * opacity)))
            # The circle's center is the EXIF coordinate. Padding, shadow, and label
            # scale with it; they never introduce an independent screen-space offset.
            factor_x = sprite.width / (self.diameter + 24)
            factor_y = sprite.height / (self.diameter + 42)
            left = round(x - (12 + self.diameter / 2) * factor_x)
            top = round(y - (12 + self.diameter / 2) * factor_y)
            frame.paste(sprite, (left, top), sprite)
            self.visible_frames[i] += 1
            if opacity >= 1 - 1e-8:
                self.full_frames[i] += 1
            count += 1
        return count

    def report(self, fps):
        return [{"path": photo.path, "arrival_seconds": start,
                 "expires_seconds": None, "persistent": True, "raw_index": photo.raw_index,
                 "latitude": photo.latitude, "longitude": photo.longitude,
                 "world_diameter_m": self.world_diameter_m,
                 "visible_seconds": count / fps,
                 "full_visibility_seconds": full / fps}
                for (start, expiry, photo), count, full in
                zip(self.entries, self.visible_frames, self.full_frames)]
