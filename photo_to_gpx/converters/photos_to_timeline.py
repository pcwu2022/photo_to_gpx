import argparse
import os
import sys
import tempfile

from .convert import generate_gpx
from .gpx_to_timeline import convert_gpx_to_timeline


def photos_to_timeline(
    photo_dir,
    output_json_path,
    output_format,
    accuracy_metres,
    activity_type,
    timezone_name,
    segment_hours,
    segment_offset_hours,
    keep_gpx,
    gpx_output_path,
):
    if not os.path.isdir(photo_dir):
        raise NotADirectoryError(f"Input photo directory does not exist: {photo_dir}")

    if keep_gpx:
        intermediate_gpx_path = gpx_output_path or default_gpx_path(output_json_path)
        generate_gpx(photo_dir, intermediate_gpx_path)
        ensure_gpx_was_created(intermediate_gpx_path)
        convert_gpx_to_timeline(
            intermediate_gpx_path,
            output_json_path,
            output_format,
            accuracy_metres,
            activity_type,
            timezone_name,
            segment_hours,
            segment_offset_hours,
        )
        return

    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as temp_gpx:
        intermediate_gpx_path = temp_gpx.name

    try:
        generate_gpx(photo_dir, intermediate_gpx_path)
        ensure_gpx_was_created(intermediate_gpx_path)
        convert_gpx_to_timeline(
            intermediate_gpx_path,
            output_json_path,
            output_format,
            accuracy_metres,
            activity_type,
            timezone_name,
            segment_hours,
            segment_offset_hours,
        )
    finally:
        if os.path.exists(intermediate_gpx_path):
            os.remove(intermediate_gpx_path)


def default_gpx_path(output_json_path):
    root, _ = os.path.splitext(output_json_path)
    return f"{root}.gpx"


def ensure_gpx_was_created(gpx_path):
    if not os.path.exists(gpx_path) or os.path.getsize(gpx_path) == 0:
        raise ValueError("No photos with valid GPS data were found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Extract photo EXIF GPS data and write Google Timeline-compatible JSON "
            "in one command."
        )
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help="Path to the directory containing your photo files.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="timeline.json",
        help="Path and filename for the output JSON file.",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["timeline", "records", "semantic"],
        default="timeline",
        help=(
            "Output format. 'timeline' writes semanticSegments with timelinePath. "
            "'records' writes the deprecated raw locations array. "
            "'semantic' writes timelineObjects activity segments."
        ),
    )
    parser.add_argument(
        "--accuracy",
        type=int,
        default=10,
        help="Accuracy value in metres for records output.",
    )
    parser.add_argument(
        "--activity-type",
        default="UNKNOWN_ACTIVITY_TYPE",
        help="Activity type label for semantic output.",
    )
    parser.add_argument(
        "--timezone",
        default="America/New_York",
        help="IANA timezone used for timeline-format timestamps.",
    )
    parser.add_argument(
        "--segment-hours",
        type=int,
        default=2,
        help="Hour window size for timelinePath semanticSegments.",
    )
    parser.add_argument(
        "--segment-offset-hours",
        type=int,
        default=1,
        help="Hour offset for timelinePath segment boundaries.",
    )
    parser.add_argument(
        "--keep-gpx",
        action="store_true",
        help="Keep the intermediate GPX file instead of deleting it.",
    )
    parser.add_argument(
        "--gpx-output",
        help=(
            "Path for the intermediate GPX file. Implies --keep-gpx. "
            "Defaults to the JSON output path with a .gpx extension."
        ),
    )

    args = parser.parse_args()
    try:
        photos_to_timeline(
            args.input,
            args.output,
            args.format,
            args.accuracy,
            args.activity_type,
            args.timezone,
            args.segment_hours,
            args.segment_offset_hours,
            args.keep_gpx or args.gpx_output is not None,
            args.gpx_output,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
