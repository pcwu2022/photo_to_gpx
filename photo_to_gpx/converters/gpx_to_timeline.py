import argparse
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_gpx_time(value):
    """Parse common GPX timestamp forms into a timezone-aware UTC datetime."""
    if not value:
        return None

    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def datetime_to_timestamp_ms(value):
    return str(int(value.timestamp() * 1000))


def decimal_to_e7(value):
    return int(round(float(value) * 10_000_000))


def find_child_text(element, child_name):
    for child in element:
        if child.tag.split("}")[-1] == child_name:
            return child.text
    return None


def read_gpx_points(gpx_path):
    tree = ET.parse(gpx_path)
    root = tree.getroot()
    points = []

    for trkpt in root.iter():
        if trkpt.tag.split("}")[-1] != "trkpt":
            continue

        lat = trkpt.get("lat")
        lon = trkpt.get("lon")
        time_text = find_child_text(trkpt, "time")

        if lat is None or lon is None or time_text is None:
            continue

        timestamp = parse_gpx_time(time_text)
        if timestamp is None:
            continue

        points.append(
            {
                "lat": float(lat),
                "lon": float(lon),
                "time": timestamp,
                "name": find_child_text(trkpt, "name"),
            }
        )

    points.sort(key=lambda point: point["time"])
    return points


def build_records_timeline(points, accuracy_metres):
    return {
        "locations": [
            {
                "timestampMs": datetime_to_timestamp_ms(point["time"]),
                "latitudeE7": decimal_to_e7(point["lat"]),
                "longitudeE7": decimal_to_e7(point["lon"]),
                "accuracy": accuracy_metres,
            }
            for point in points
        ]
    }


def build_semantic_timeline(points, activity_type):
    timeline_objects = []

    for start, end in zip(points, points[1:]):
        timeline_objects.append(
            {
                "activitySegment": {
                    "startLocation": {
                        "latitudeE7": decimal_to_e7(start["lat"]),
                        "longitudeE7": decimal_to_e7(start["lon"]),
                    },
                    "endLocation": {
                        "latitudeE7": decimal_to_e7(end["lat"]),
                        "longitudeE7": decimal_to_e7(end["lon"]),
                    },
                    "duration": {
                        "startTimestamp": start["time"].isoformat().replace("+00:00", "Z"),
                        "endTimestamp": end["time"].isoformat().replace("+00:00", "Z"),
                    },
                    "activityType": activity_type,
                    "confidence": "HIGH",
                }
            }
        )

    return {"timelineObjects": timeline_objects}


def format_timeline_time(value):
    return value.isoformat(timespec="milliseconds")


def format_lat_lng(lat, lon):
    return f"{lat:.7f}\N{DEGREE SIGN}, {lon:.7f}\N{DEGREE SIGN}"


def build_timeline_path_segments(points, timezone_name, segment_hours, segment_offset_hours):
    if segment_hours <= 0:
        raise ValueError("--segment-hours must be greater than zero.")

    try:
        target_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown timezone: {timezone_name}") from error
    segment_seconds = segment_hours * 60 * 60
    segments = []
    current_points = []
    current_segment_start = None

    for point in points:
        local_time = point["time"].astimezone(target_timezone)
        segment_start = get_segment_start(
            local_time,
            segment_seconds,
            segment_offset_hours,
        )

        if current_segment_start is not None and segment_start != current_segment_start:
            segments.append(
                build_timeline_path_segment(
                    current_points,
                    current_segment_start,
                    segment_seconds,
                )
            )
            current_points = []

        current_segment_start = segment_start
        current_points.append({**point, "local_time": local_time})

    if current_points:
        segments.append(
            build_timeline_path_segment(
                current_points,
                current_segment_start,
                segment_seconds,
            )
        )

    return {"semanticSegments": segments}


def get_segment_start(local_time, segment_seconds, segment_offset_hours):
    midnight = datetime.combine(local_time.date(), time.min, tzinfo=local_time.tzinfo)
    seconds_since_midnight = int((local_time - midnight).total_seconds())
    offset_seconds = segment_offset_hours * 60 * 60
    segment_start_seconds = (
        (seconds_since_midnight - offset_seconds) // segment_seconds
    ) * segment_seconds + offset_seconds
    return midnight + timedelta(seconds=segment_start_seconds)


def build_timeline_path_segment(points, segment_start, segment_seconds):
    segment_end = datetime.fromtimestamp(
        segment_start.timestamp() + segment_seconds,
        tz=segment_start.tzinfo,
    )

    return {
        "startTime": format_timeline_time(segment_start),
        "endTime": format_timeline_time(segment_end),
        "timelinePath": [
            {
                "point": format_lat_lng(point["lat"], point["lon"]),
                "time": format_timeline_time(point["local_time"]),
            }
            for point in points
        ],
    }


def convert_gpx_to_timeline(
    input_gpx_path,
    output_json_path,
    output_format,
    accuracy_metres,
    activity_type,
    timezone_name,
    segment_hours,
    segment_offset_hours,
):
    if not os.path.exists(input_gpx_path):
        raise FileNotFoundError(f"Input GPX file does not exist: {input_gpx_path}")

    points = read_gpx_points(input_gpx_path)
    if not points:
        raise ValueError("No GPX track points with timestamps were found.")

    if output_format == "timeline":
        timeline = build_timeline_path_segments(
            points,
            timezone_name,
            segment_hours,
            segment_offset_hours,
        )
    elif output_format == "records":
        timeline = build_records_timeline(points, accuracy_metres)
    else:
        timeline = build_semantic_timeline(points, activity_type)

    with open(output_json_path, "w", encoding="utf-8") as output_file:
        json.dump(timeline, output_file, indent=2, ensure_ascii=False)
        output_file.write("\n")

    print(
        f"Successfully converted {len(points)} GPX points to {output_format} "
        f"Google Timeline JSON at: {output_json_path}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert a GPX track file into Google Timeline-compatible JSON."
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help="Path to the generated GPX file.",
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

    args = parser.parse_args()
    convert_gpx_to_timeline(
        args.input,
        args.output,
        args.format,
        args.accuracy,
        args.activity_type,
        args.timezone,
        args.segment_hours,
        args.segment_offset_hours,
    )
