"""Transit-style title cards and a quiet calendar timeline, rendered with Pillow."""
import calendar
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .vehicle_icons import vehicle_icon


FONT_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "fonts"


def font(size, *, bold=False):
    name = "InterDisplay-Bold.otf" if bold else "InterDisplay-Medium.otf"
    return ImageFont.truetype(str(FONT_DIRECTORY / name), max(8, round(size)))


def smoothstep(value):
    value = min(1, max(0, value))
    return value * value * (3 - 2 * value)


def card_opacity(seconds, duration):
    """Zero at either edge, a smooth fade, and a readable middle hold."""
    fade = min(0.8, duration / 3)
    return min(smoothstep(seconds / fade), smoothstep((duration - seconds) / fade))


def attribution_footer(size, text):
    """Build the footer before camera planning so its actual height can be reserved."""
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and measure.textlength(candidate) > size[0] - 24:
            lines.append(line)
            line = word
        else:
            line = candidate
    lines.append(line)
    footer = Image.new("RGBA", (size[0], len(lines) * 13 + 12), (0, 0, 0, 0))
    draw = ImageDraw.Draw(footer)
    for index, line in enumerate(lines):
        draw.text((12, 6 + index * 13), line, fill="white")
    return footer


class TitleCard:
    def __init__(self, size, title, subtitle=""):
        width, height = size
        self.layer = Image.new("RGBA", size)
        text_layer = Image.new("RGBA", size)
        draw = ImageDraw.Draw(text_layer)
        # Wrap long titles before fitting the complete block inside the viewport.
        title = title.strip().upper()
        subtitle = " ".join(subtitle.upper().split())
        title_size = max(16, round(min(width * 0.11, height * 0.19)))
        for title_size in range(title_size, 7, -1):
            main_font = font(title_size, bold=True)
            sub_font = font(title_size * 0.45)
            lines = []
            for paragraph in title.splitlines():
                line = ""
                for word in paragraph.split():
                    candidate = (line + " " + word).strip()
                    if line and draw.textlength(candidate, font=main_font) > width * 0.84:
                        lines.append(line)
                        line = word
                    else:
                        line = candidate
                if line:
                    lines.append(line)
            tracking = max(1, title_size * 0.075)
            subtitle_width = (sum(draw.textlength(c, font=sub_font) for c in subtitle)
                              + max(0, len(subtitle) - 1) * tracking)
            block_height = len(lines) * title_size * 1.12 + (title_size * 0.8 if subtitle else 0)
            if (all(draw.textlength(line, font=main_font) <= width * 0.86 for line in lines)
                    and subtitle_width <= width * 0.86 and block_height <= height * 0.65):
                break
        top = (height - block_height) / 2
        for line in lines:
            draw.text((width / 2, top), line, font=main_font, fill="white", anchor="mt")
            top += title_size * 1.12
        if subtitle:
            top += title_size * 0.2
            x = (width - subtitle_width) / 2
            for char in subtitle:
                draw.text((x, top), char, font=sub_font, fill="white", anchor="lt")
                x += draw.textlength(char, font=sub_font) + tracking
        # Diffused shadow: no heavy outline, and a crisp white foreground.
        alpha = text_layer.getchannel("A")
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(max(2, width / 240)))
                        .point(lambda value: round(value * 0.8)))
        self.layer.alpha_composite(shadow, (0, max(2, round(height / 180))))
        self.layer.alpha_composite(text_layer)

    def draw(self, frame, seconds, duration):
        opacity = card_opacity(seconds, duration)
        if opacity <= 0:
            return
        layer = self.layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda value: round(value * opacity)))
        frame.paste(layer, (0, 0), layer)


def next_month(value):
    year, month = (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
    return value.replace(year=year, month=month,
                         day=min(value.day, calendar.monthrange(year, month)[1]))


def timeline_ticks(start, end, timezone_name="UTC"):
    """Calendar-aligned ticks; spacing follows the complete capture-time range."""
    zone = ZoneInfo(timezone_name)
    first, last = start.astimezone(zone), end.astimezone(zone)
    elapsed = end - start
    if elapsed < timedelta(days=1):
        mode = "hours"
        current = first.replace(hour=first.hour // 2 * 2, minute=0, second=0, microsecond=0)
        # Step through real two-hour intervals across offset changes.
        advance = lambda value: (value.astimezone(timezone.utc) + timedelta(hours=2)).astimezone(zone)
    elif last < next_month(first):
        mode = "days"
        current = first.replace(hour=0, minute=0, second=0, microsecond=0)
        advance = lambda value: value + timedelta(days=1)
    else:
        mode = "months"
        current = first.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        advance = next_month
    ticks = []
    while current.astimezone(timezone.utc) <= end:
        if current.astimezone(timezone.utc) >= start:
            label = (current.strftime("%H:%M") if mode == "hours" else
                     current.strftime("%b %d") if mode == "days" else current.strftime("%b %Y"))
            ticks.append((current.astimezone(timezone.utc), label))
        current = advance(current)
    return mode, ticks


class CalendarTimeline:
    def __init__(self, size, start, end, *, footer_height=25, timezone_name="UTC"):
        self.size, self.start, self.end = size, start, end
        self.zone = ZoneInfo(timezone_name)
        self.mode, self.ticks = timeline_ticks(start, end, timezone_name)
        width, height = size
        self.height = max(60, round(height * 0.065))
        self.gap = max(10, round(height * 0.018))
        self.top = height - footer_height - self.height - self.gap
        self.left, self.right = round(width * 0.075), round(width * 0.925)
        self.y = 20
        self.base = Image.new("RGBA", (width, self.height))
        draw = ImageDraw.Draw(self.base)
        draw.rounded_rectangle((self.left - 12, 1, self.right + 12, self.height - 2),
                               radius=10, fill=(247, 249, 250, 205))
        draw.line((self.left, self.y, self.right, self.y), fill=(86, 102, 114, 120), width=1)
        self.label_font = font(1.2 * max(9, min(13, width / 100)))
        last_right = -100
        for timestamp, label in self.ticks:
            x = self.x(timestamp)
            draw.line((x, self.y + 10, x, self.y + 16), fill=(86, 102, 114, 150))
            label_width = draw.textlength(label, font=self.label_font)
            if x - label_width / 2 >= last_right + 12 and x + label_width / 2 < width - 8:
                draw.text((x, self.y + 20), label, font=self.label_font,
                          anchor="mt", fill=(55, 70, 81, 195))
                last_right = x + label_width / 2
        self.icons = {}

    def set_icons(self, icons):
        """Reuse the map's configured assets, facing forward along the timeline."""
        self.icons = {}
        for name, image in icons.items():
            icon = ImageOps.contain(image, (27, 27), Image.Resampling.LANCZOS)
            self.icons[name] = icon.rotate(-90, expand=True)

    def x(self, timestamp):
        progress = (timestamp - self.start).total_seconds() / max(1e-9, (self.end - self.start).total_seconds())
        return self.left + min(1, max(0, progress)) * (self.right - self.left)

    def draw(self, frame, timestamp, kind="train"):
        if kind not in self.icons:
            icon = ImageOps.contain(vehicle_icon(kind), (27, 27), Image.Resampling.LANCZOS)
            self.icons[kind] = icon.rotate(-90, expand=True)
        layer = self.base.copy()
        draw = ImageDraw.Draw(layer)
        x = self.x(timestamp)
        draw.line((self.left, self.y, x, self.y), fill=(55, 77, 91, 190), width=2)
        icon = self.icons[kind]
        layer.alpha_composite(icon, (round(x - icon.width / 2), self.y - icon.height // 2))
        frame.paste(layer, (0, self.top), layer)
