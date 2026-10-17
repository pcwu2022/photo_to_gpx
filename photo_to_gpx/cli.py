"""Folder-based travel video projects: init, check, and run."""
import argparse
import json
import math
from pathlib import Path
import sys

from .video import photos_to_video as video
from .video.travel_route import raw_photo_paths


# Project JSON uses positive booleans and keeps vehicle rules in their own file.
RENAMED = {"config": "vehicles", "no_photos": "show_photos",
           "no_trail": "trail", "no_sleep_adjustment": "sleep_adjustment"}
INVERTED = {"no_photos", "no_trail", "no_sleep_adjustment"}


def settings_schema():
    """Share defaults, types, choices, and supported settings with the renderer."""
    return {RENAMED.get(action.dest, action.dest): action
            for action in video.build_parser()._actions if action.dest != "help"}


def default_config():
    settings = {}
    for key, action in settings_schema().items():
        value = action.default
        if action.dest in INVERTED:
            value = not value
        settings[key] = str(value) if isinstance(value, Path) else value
    settings.update(photos="photos", vehicles="vehicles.json",
                    output="output/travel.mp4", cache=".cache/photo-video")
    return settings


def initialize(folder):
    folder = Path(folder).resolve()
    config_path = folder / "config.json"
    if config_path.exists():
        raise ValueError(f"{config_path} already exists; edit it, then use check or run")
    folder.mkdir(parents=True, exist_ok=True)
    settings = default_config()
    # Adopt existing loose photo collections without moving or renumbering files.
    if not (folder / "photos").exists() and raw_photo_paths(folder):
        settings["photos"] = "."
    else:
        (folder / "photos").mkdir(exist_ok=True)
    vehicles = folder / "vehicles.json"
    if not vehicles.exists():
        vehicles.write_text(json.dumps({"default_icon": "car", "icons": {}, "ranges": []},
                                       indent=2) + "\n", encoding="utf-8")
    config_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"Created {config_path}")
    print(f"Photos: {folder / settings['photos']}")
    print(f"Edit config.json and vehicles.json, then run: python -m photo_to_gpx run {folder}")


def read_project(folder):
    folder = Path(folder).resolve()
    config_path = folder / "config.json"
    if not config_path.is_file():
        raise ValueError(f"missing {config_path}; create a project with: python -m photo_to_gpx init {folder}")
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"{config_path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{config_path}: expected a JSON object")
    schema = settings_schema()
    unknown = data.keys() - schema.keys()
    if unknown:
        raise ValueError(f"{config_path}: unknown settings: {', '.join(sorted(unknown))}")
    settings = default_config()
    settings.update(data)
    args = argparse.Namespace()
    for key, action in schema.items():
        value = settings[key]
        if value is None:
            if key not in {"photos", "gpx", "duration", "vehicles", "icon"}:
                raise ValueError(f"{key}: cannot be null")
        elif action.type is Path:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key}: expected a nonempty path string")
            value = (folder / value).resolve()
        elif action.type in (int, float):
            allowed_types = (int,) if action.type is int else (int, float)
            if type(value) not in allowed_types or not math.isfinite(value):
                raise ValueError(f"{key}: expected a finite {action.type.__name__}")
        elif isinstance(action.default, bool):
            if type(value) is not bool:
                raise ValueError(f"{key}: expected true or false")
        elif not isinstance(value, str):
            raise ValueError(f"{key}: expected a string")
        if value is not None and action.choices and value not in action.choices:
            raise ValueError(f"{key}: choose from {', '.join(map(str, action.choices))}")
        if action.dest in INVERTED:
            value = not value
        setattr(args, action.dest, value)
    if sum(bool(value) for value in (args.photos, args.gpx, args.demo)) != 1:
        raise ValueError("choose exactly one source: photos path, gpx path, or demo: true; set unused paths to null")
    if args.photos is not None and not args.photos.is_dir():
        raise ValueError(f"photos: directory does not exist: {args.photos}")
    for key, path in (("gpx", args.gpx), ("vehicles", args.config), ("icon", args.icon)):
        if path is not None and not path.is_file():
            raise ValueError(f"{key}: file does not exist: {path}")
    if args.output.suffix.lower() != ".mp4":
        raise ValueError("output: expected an .mp4 filename")
    # Cache and outputs stay with the input, even when invoked from another cwd.
    for key, path in (("output", args.output), ("cache", args.cache)):
        if not path.is_relative_to(folder):
            raise ValueError(f"{key}: must be inside the project folder: {folder}")
    if args.icon is not None and args.icon == args.output:
        raise ValueError("output must not overwrite the icon")
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Keep existing scripts usable, including the renderer's direct module entry.
    if argv and argv[0].startswith("-") and argv[0] not in {"-h", "--help"}:
        return video.main(argv)
    parser = argparse.ArgumentParser(description=__doc__, epilog=
        "Edit settings in PROJECT/config.json and icon ranges in PROJECT/vehicles.json. "
        "All relative paths use the project folder. A bare PROJECT is shorthand for run PROJECT.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("init", "Create editable JSON files and a photos folder"),
                            ("check", "Validate inputs and write the photo index without rendering"),
                            ("run", "Render the video and reports into the project output folder")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("project", nargs="?", type=Path, default=Path.cwd(),
                             help="Project directory (default: current directory)")
    if argv and argv[0] not in {"init", "check", "run", "-h", "--help"}:
        argv.insert(0, "run")
    if not argv:
        argv = ["run"]
    command = parser.parse_args(argv)
    try:
        if command.command == "init":
            initialize(command.project)
            return 0
        args = read_project(command.project)
        return video.execute(args, parser, check=command.command == "check")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
