"""Persistent circular photos anchored to the map, plus a packed final overview."""
from bisect import bisect_right
from collections import OrderedDict
from pathlib import Path
import math

from PIL import Image, ImageDraw, ImageOps, ImageFilter
from travel_route import GEOD
from video_map import PROJECT


class PhotoMarkers:
    FADE_IN = 0.18
    FADE_OUT = 0.25

    def __init__(self, events, route_duration, motion_seconds, size, *, duration=4,
                 cluster_seconds=30, cluster_meters=50, arrival_time=None, fps=30):
        self.duration, self.size = duration, size
        self.cache, self.issues, self.offsets = OrderedDict(), [], {}
        self.overview_centers = None
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
        self.last_expiry = self.starts[-1] + self.FADE_IN + duration if self.starts else motion_seconds
        self.peak = max(1, len(self.entries))
        self.diameter = max(40, min(110, round(size[1] * 0.11)))
        columns = max(1, math.ceil(math.sqrt(self.peak * size[0] / max(1, size[1] - 104))))
        rows = max(1, math.ceil(self.peak / columns))
        self.overview_diameter = max(20, int(min(80, (size[0] - 40) / columns - 12,
                                                 (size[1] - 104) / rows - 24)))
        self.overview_slots = [((c + 0.5) * size[0] / columns,
                                44 + (r + 0.5) * (size[1] - 104) / rows)
                               for r in range(rows) for c in range(columns)]
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

    def plan_overview(self, screen, reference_x):
        """Pack all images once around their map points; leaders identify each location."""
        available = set(range(len(self.overview_slots)))
        self.overview_centers = {}
        for i in range(len(self.entries)):
            x, y = screen(self.anchor(i, reference_x))
            slot = min(available, key=lambda j: (math.dist(self.overview_slots[j], (x, y)), j))
            self.overview_centers[i] = self.overview_slots[slot]
            available.remove(slot)

    def draw(self, frame, seconds, screen, reference_x, vehicle_screen, future_screens=(),
             overview=0, overview_screen=None):
        active = self.active(seconds)
        if overview > 0 and self.overview_centers is None:
            self.plan_overview(overview_screen or screen, reference_x)
        sprites, occupied = [], []
        draw = ImageDraw.Draw(frame)
        diameter = round(self.diameter + (self.overview_diameter - self.diameter) * overview)
        for i in active:
            start, _, photo = self.entries[i]
            x, y = screen(self.anchor(i, reference_x))
            # Outside this viewport, the geographic marker remains registered for later views.
            if not overview and not (-diameter <= x <= self.size[0] + diameter
                                      and 20 - diameter <= y <= self.size[1] + diameter):
                continue
            if i not in self.offsets:
                candidates = [(0, -diameter / 2 - 20)]
                for ring in range(1, 4):
                    for direction in range(8):
                        angle = direction * math.pi / 4
                        candidates.append((math.cos(angle) * ring * (diameter + 14),
                                           math.sin(angle) * ring * (diameter + 14)))
                def score(offset):
                    cx, cy = x + offset[0], y + offset[1]
                    overlap = sum(math.dist((cx, cy), c) < diameter + 10 for c in occupied)
                    car_overlap = math.dist((cx, cy), vehicle_screen) < diameter / 2 + 40
                    return overlap + car_overlap, math.hypot(*offset)
                self.offsets[i] = min(candidates, key=score)
            dx, dy = self.offsets[i]
            normal = (x + dx, y + dy)
            if overview:
                final = self.overview_centers[i]
                cx, cy = tuple(a + (b - a) * overview for a, b in zip(normal, final))
            else:
                cx, cy = normal
            occupied.append((cx, cy))
            if (cx + diameter < 0 or cx - diameter > self.size[0]
                    or cy + diameter < 32 or cy - diameter > self.size[1] - 26):
                continue
            sprite = self.thumbnail(photo, self.overview_diameter if overview == 1 else None)
            if sprite is None:
                continue
            circle_y_offset = 9
            if diameter != self.diameter and overview != 1:
                factor = diameter / self.diameter
                sprite = sprite.resize((round(sprite.width * factor), round(sprite.height * factor)),
                                       Image.Resampling.BILINEAR)
                circle_y_offset *= factor
            opacity = min(1, max(0, (seconds - start) / self.FADE_IN))
            if opacity <= 0:
                continue
            self.visible_frames[i] += 1
            if opacity >= 1 - 1e-8:
                self.full_frames[i] += 1
            dx, dy = x - cx, y - cy
            length = math.hypot(dx, dy)
            ratio = min(1, (diameter / 2 + 3) / max(length, 1))
            endpoint = (cx + dx * ratio, cy + dy * ratio)
            ax = max(-10, min(self.size[0] + 10, x))
            ay = max(32, min(self.size[1] - 26, y))
            draw.line((ax, ay, *endpoint), fill="#c56c73", width=1)
            if 0 <= x <= self.size[0] and 32 <= y <= self.size[1] - 26:
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="#e84553")
            sprites.append((sprite, cx, cy, circle_y_offset, opacity))
        for sprite, cx, cy, circle_y_offset, opacity in sprites:
            if opacity < 1:
                sprite = sprite.copy()
                sprite.putalpha(sprite.getchannel("A").point(lambda value: round(value * opacity)))
            frame.paste(sprite, (round(cx - sprite.width / 2),
                                 round(cy - sprite.height / 2 + circle_y_offset)), sprite)
        return len(sprites)

    def report(self, fps):
        return [{"path": photo.path, "arrival_seconds": start,
                 "expires_seconds": None, "persistent": True, "raw_index": photo.raw_index,
                 "visible_seconds": count / fps,
                 "full_visibility_seconds": full / fps}
                for (start, expiry, photo), count, full in
                zip(self.entries, self.visible_frames, self.full_frames)]
