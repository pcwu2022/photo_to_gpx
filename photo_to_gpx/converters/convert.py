import os
import argparse
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

def dms_to_decimal(dms, ref):
    """Converts GPS degrees, minutes, seconds tuple to decimal degrees."""
    d = float(dms[0])
    m = float(dms[1])
    s = float(dms[2])
    deg = d + (m / 60.0) + (s / 3600.0)
    if ref in ['S', 'W']:
        deg = -deg
    return deg

def get_image_metadata(image_path):
    try:
        img = Image.open(image_path)
        exif = img.getexif()
        if not exif:
            return None, None
        
        # 1. Extract Timestamp
        timestamp_str = None
        for tag_id, val in exif.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag in ['DateTimeOriginal', 'DateTime']:
                timestamp_str = val
                break
                
        # Fallback to file modification time if EXIF timestamp is missing
        if not timestamp_str:
            mtime = os.path.getmtime(image_path)
            timestamp_str = datetime.fromtimestamp(mtime).strftime('%Y:%m:%d %H:%M:%S')

        # 2. Extract Nested GPS IFD (Tag 0x8825)
        if 0x8825 not in exif:
            return None, None
            
        gps_ifd = exif.get_ifd(0x8825)
        gps_data = {}
        for tag_id, val in gps_ifd.items():
            tag_name = GPSTAGS.get(tag_id, tag_id)
            gps_data[tag_name] = val

        if 'GPSLatitude' not in gps_data or 'GPSLongitude' not in gps_data:
            return None, None

        lat_ref = gps_data.get('GPSLatitudeRef', 'N')
        lon_ref = gps_data.get('GPSLongitudeRef', 'E')
        
        lat = dms_to_decimal(gps_data['GPSLatitude'], lat_ref)
        lon = dms_to_decimal(gps_data['GPSLongitude'], lon_ref)

        return timestamp_str, (lat, lon)
    except Exception:
        return None, None

def generate_gpx(photo_dir, output_gpx_path):
    points = []
    
    if not os.path.exists(photo_dir):
        print(f"Error: Directory '{photo_dir}' does not exist.")
        return

    print(f"Scanning '{photo_dir}' for photos...")
    valid_extensions = ('.jpg', '.jpeg', '.heic', '.png', '.tiff')
    
    for root, _, files in os.walk(photo_dir):
        for file in files:
            if file.lower().endswith(valid_extensions):
                path = os.path.join(root, file)
                timestamp_str, coords = get_image_metadata(path)
                
                if coords and timestamp_str:
                    lat, lon = coords
                    try:
                        # Clean up formatting variations if present
                        dt = datetime.strptime(timestamp_str[:19], '%Y:%m:%d %H:%M:%S')
                        points.append((dt, lat, lon, file))
                    except ValueError:
                        continue

    if not points:
        print("No photos with valid GPS data were found after parsing.")
        return

    # Sort track points chronologically by timestamp
    points.sort(key=lambda x: x[0])

    # Build GPX XML structure
    gpx_content = ['<?xml version="1.0" encoding="UTF-8"?>']
    gpx_content.append('<gpx version="1.1" creator="Fixed GPS Extractor">')
    gpx_content.append('  <trk>')
    gpx_content.append('    <name>Travel Route</name>')
    gpx_content.append('    <trkseg>')

    for dt, lat, lon, name in points:
        time_iso = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        gpx_content.append(f'      <trkpt lat="{lat}" lon="{lon}">')
        gpx_content.append(f'        <time>{time_iso}</time>')
        gpx_content.append(f'        <name>{name}</name>')
        gpx_content.append(f'      </trkpt>')

    gpx_content.append('    </trkseg>')
    gpx_content.append('  </trk>')
    gpx_content.append('</gpx>')

    with open(output_gpx_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(gpx_content))
    
    print(f"Successfully generated GPX file with {len(points)} track points at: {output_gpx_path}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Extract photo EXIF GPS data into a chronological GPX route file.")
    parser.add_argument("-i", "--input", required=True, help="Path to the directory containing your photo files.")
    parser.add_argument("-o", "--output", default="travel_route.gpx", help="Path and filename for the output GPX file.")
    
    args = parser.parse_args()
    generate_gpx(args.input, args.output)