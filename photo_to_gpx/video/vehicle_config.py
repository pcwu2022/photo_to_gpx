"""Inclusive icon ranges indexed by every image file, before metadata filtering."""
import json
from pathlib import Path

BUILT_INS = {"car", "train", "plane", "boat", "bus", "bullet_train", "subway"}


class VehicleConfig:
    def __init__(self, path=None, default="car"):
        self.icons, self.ranges = {}, []
        self.default = default
        if path is None:
            return
        path = Path(path)
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("vehicle config must be a JSON object")
        unknown = set(data) - {"default_icon", "icons", "ranges"}
        if unknown:
            raise ValueError(f"unknown vehicle config fields: {sorted(unknown)}")
        self.default = data.get("default_icon", default)
        icons, ranges = data.get("icons", {}), data.get("ranges", [])
        if not isinstance(icons, dict) or not isinstance(ranges, list):
            raise ValueError("icons must be an object and ranges must be an array")
        for name, filename in icons.items():
            if not isinstance(name, str) or name in BUILT_INS or not isinstance(filename, str):
                raise ValueError("custom icons need a unique name and an image path")
            icon_path = (path.parent / filename).resolve()
            if not icon_path.is_file():
                raise ValueError(f"custom icon file does not exist: {icon_path}")
            self.icons[name] = str(icon_path)
        for rule in ranges:
            if not isinstance(rule, dict) or set(rule) != {"start", "end", "icon"}:
                raise ValueError("each range must contain start, end, and icon")
            if (type(rule["start"]) is not int or type(rule["end"]) is not int
                    or not 1 <= rule["start"] <= rule["end"]):
                raise ValueError("range indices must be positive integers, with start <= end")
            self.validate_icon(rule["icon"])
            self.ranges.append(rule)
        self.validate_icon(self.default)

    def validate_icon(self, name):
        if not isinstance(name, str) or name not in BUILT_INS | self.icons.keys():
            raise ValueError(f"unknown icon: {name!r}")

    def for_photo(self, raw_index):
        selected = self.default
        if raw_index is not None:
            for rule in self.ranges:
                if rule["start"] <= raw_index <= rule["end"]:
                    selected = rule["icon"]
        return selected
