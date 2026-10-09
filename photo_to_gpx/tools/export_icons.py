"""Export north-facing vehicle artwork as editable SVG files."""
import argparse
from pathlib import Path
from xml.etree import ElementTree as ET

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "icons"


def build_icon(kind):
    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg", width="48", height="72",
                     viewBox="0 0 48 72")
    ET.SubElement(svg, "title").text = f"North-facing {kind}"

    def shape(tag, **attributes):
        return ET.SubElement(svg, tag, {key.replace('_', '-'): str(value)
                                       for key, value in attributes.items()})

    def rect(x, y, width, height, fill, radius=0, **attributes):
        shape("rect", x=x, y=y, width=width, height=height, rx=radius, fill=fill, **attributes)

    if kind == "boat":
        for x1, x2 in ((10, 7), (24, 24), (38, 41)):
            shape("line", x1=x1, y1=66, x2=x2, y2=71, stroke="#b7e4f2", stroke_width=3)
        shape("polygon", points="24,2 38,20 42,51 36,64 12,64 6,51 10,20",
              fill="#e84553", stroke="white", stroke_width=2)
        shape("polygon", points="24,10 32,24 35,53 13,53 16,24", fill="#f4f7fa")
        rect(17, 28, 14, 18, "#23313d", 3)
        rect(19, 30, 10, 6, "#b7e4f2", 2)
        rect(18, 55, 12, 4, "#23313d")
    elif kind == "plane":
        shape("polygon", points="24,1 29,28 46,44 46,51 28,43 28,60 36,68 36,71 "
              "24,67 12,71 12,68 20,60 20,43 2,51 2,44 19,28",
              fill="#f4f7fa", stroke="#23313d", stroke_width=2)
        shape("line", x1=24, y1=12, x2=24, y2=61, stroke="#e84553", stroke_width=3)
    elif kind in ("car", "train"):
        if kind == "car":
            for x in (3, 35):
                for y in (13, 48):
                    rect(x, y, 10, 13, "#23313d", 3)
        rect(9, 2, 30, 67, "#e84553", 10, stroke="white", stroke_width=2)
        rect(14, 14, 20, 16, "#b7e4f2", 3)
        rect(14, 45, 20, 8, "#23313d")
        if kind == "train":
            shape("line", x1=15, y1=61, x2=33, y2=61, stroke="white", stroke_width=3)
    else:
        raise ValueError(f"Unknown icon: {kind}")
    ET.indent(svg)
    return ET.tostring(svg, encoding="unicode", xml_declaration=True) + "\n"


def export_icons(output=DEFAULT_OUTPUT):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for kind in ("car", "train", "plane", "boat"):
        path = output / f"{kind}.svg"
        path.write_text(build_icon(kind), encoding="utf-8")
        print(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT,
                        help="Destination directory; defaults to the renderer's built-in icon directory")
    args = parser.parse_args()
    export_icons(args.output_dir)


if __name__ == "__main__":
    main()
