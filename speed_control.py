"""Retiming against ground distance and a preplanned, zoom-dependent screen budget."""
from dataclasses import replace
from bisect import bisect_right
import math

from travel_route import GEOD
from video_map import project_positions, flight_camera_track, overview_pose


class SpeedProfile:
    def __init__(self, route, motion_seconds, size, fps, *, camera="fly", camera_seconds=0.6,
                 local_span_km=25, flight_km=150, city_km=5, min_speed_mps=1000,
                 max_screen_speed_px=540, max_zoom_rate=1):
        values = (min_speed_mps, max_screen_speed_px, max_zoom_rate)
        if not all(math.isfinite(v) and v >= 0 for v in values):
            raise ValueError("animation speed limits must be finite and nonnegative")
        self.route, self.times, self.move_ends = route, [], [0.0]
        self.legs, self.floor_conflicts = [], 0
        self.min_speed_mps, self.max_screen_speed_px = min_speed_mps, max_screen_speed_px
        planning_floor = min_speed_mps * 1.01  # Allow for projection/curve numerical error.
        positions, self.leg_indices = [], []
        for i, s in enumerate(route.segments):
            # Spatial samples include exact anchors regardless of their timestamp spacing.
            samples = 256 if s[3] > city_km * 1000 else 64
            for j in range(samples + 1):
                # Extra resolution where departure/arrival zoom changes most quickly.
                u = (1 - math.cos(math.pi * j / samples)) / 2 if s[3] > city_km * 1000 else j / samples
                t = route.ends[i] + s[4] * u
                positions.append(replace(route.at(t), segment=i))
                self.times.append(t)
                self.leg_indices.append(i)
        self.points = project_positions(positions)
        # Widen local framing if the requested floor cannot fit the screen budget.
        if min_speed_mps and max_screen_speed_px:
            distortion = max(1 / math.cos(math.radians(p.latitude)) for p in positions)
            local_span_km = max(local_span_km, planning_floor * distortion * size[1]
                                / (max_screen_speed_px * 0.35) / 1000)
        self.effective_local_span_km = local_span_km
        if camera == "fly":
            def ramp(distance):
                if not min_speed_mps or not max_zoom_rate:
                    return 64
                # Keep the zoom envelope feasible at the absolute ground-speed floor.
                limit = (math.log(2) * max_zoom_rate / (math.pi * planning_floor)) ** 2
                return max(1, min(64, 0.6 * limit * local_span_km * 1000 * distance / 1.8))
            self.cameras = flight_camera_track(self.points, positions, route, size, fps,
                                              camera_seconds, local_span_km, city_km + 0.001, city_km,
                                              zoom_ramp=ramp)
        else:
            pose = (overview_pose(self.points, size) if camera == "fixed" else
                    (*self.points[0], local_span_km * 1000 * size[0] / size[1], local_span_km * 1000))
            if min_speed_mps and max_screen_speed_px:
                needed = local_span_km * 1000
                factor = max(1, needed / pose[3])
                pose = (*pose[:2], pose[2] * factor, pose[3] * factor)
            self.cameras = []
            for point in self.points:
                if camera == "follow":
                    # Spatial look-ahead avoids following a vehicle after it leaves the frame.
                    pose = (*point, *pose[2:])
                self.cameras.append(pose)
        nominal_scale = motion_seconds / route.duration
        for k in range(1, len(self.times)):
            if self.times[k] == self.times[k - 1]:
                self.cameras[k] = self.cameras[k - 1]
        totals = [0.0] * len(route.segments)
        ground_totals = [0.0] * len(route.segments)
        for k in range(1, len(self.times)):
            dt = self.times[k] - self.times[k - 1]
            if dt <= 0:  # Shared anchor between legs, not an extra movement interval.
                self.move_ends.append(self.move_ends[-1])
                continue
            leg = self.leg_indices[k]
            a, b = positions[k - 1], positions[k]
            distance = GEOD.inv(a.longitude, a.latitude, b.longitude, b.latitude)[2]
            duration = dt * nominal_scale
            upper = distance / planning_floor if min_speed_mps and distance > 1e-5 else math.inf
            if distance > 1e-5:
                duration = min(duration, upper)
            else:
                # No physical movement: do not leave the vehicle idling for a long EXIF gap.
                duration = min(duration, 1 / (fps * 64))
            ca, cb = self.cameras[k - 1], self.cameras[k]
            sx, sy = min(ca[2], cb[2]), min(ca[3], cb[3])
            scale = (size[0] / sx, size[1] / sy)
            vector_pixels = lambda v: math.hypot(v[0] * scale[0], v[1] * scale[1])
            displacement = vector_pixels(tuple(q - p for p, q in zip(self.points[k - 1], self.points[k])))
            pan = vector_pixels((cb[0] - ca[0], cb[1] - ca[1]))
            zoom = abs(math.log(cb[3] / ca[3]))
            offset = max(vector_pixels((p[0] - c[0], p[1] - c[1]))
                         for p, c in ((self.points[k - 1], ca), (self.points[k], cb)))
            # Triangle bound covers vehicle motion, camera translation, and changing zoom.
            required = ((displacement + pan + offset * zoom) * 1.02 / max_screen_speed_px
                        if max_screen_speed_px else 0)
            if max_zoom_rate:
                required = max(required, zoom / (math.log(2) * max_zoom_rate))
            duration = max(duration, required)
            if upper < duration - 1e-8:
                self.floor_conflicts += 1  # Screen/camera safety wins for incompatible settings.
            duration = max(duration, 1e-9)
            self.move_ends.append(self.move_ends[-1] + duration)
            totals[leg] += duration
            ground_totals[leg] += distance
        self.duration = self.move_ends[-1]
        for i, s in enumerate(route.segments):
            self.legs.append({"segment": i, "nominal_seconds": s[4] * nominal_scale,
                              "movement_seconds": totals[i], "ground_distance_m": ground_totals[i],
                              "average_ground_speed_mps": ground_totals[i] / totals[i]})

    def motion_time(self, route_time):
        i = min(len(self.times) - 2, max(0, bisect_right(self.times, route_time) - 1))
        a, b = self.times[i:i + 2]
        u = max(0, min(1, (route_time - a) / (b - a))) if b > a else 1
        return self.move_ends[i] + (self.move_ends[i + 1] - self.move_ends[i]) * u

    def route_time(self, seconds):
        i = min(len(self.move_ends) - 2, max(0, bisect_right(self.move_ends, seconds) - 1))
        a, b = self.move_ends[i:i + 2]
        u = max(0, min(1, (seconds - a) / (b - a))) if b > a else 1
        return self.times[i] + (self.times[i + 1] - self.times[i]) * u

    def camera_at(self, route_time):
        i = min(len(self.times) - 2, max(0, bisect_right(self.times, route_time) - 1))
        a, b = self.times[i:i + 2]
        u = max(0, min(1, (route_time - a) / (b - a))) if b > a else 1
        ca, cb = self.cameras[i:i + 2]
        if ca == cb:
            return ca
        return (ca[0] + (cb[0] - ca[0]) * u, ca[1] + (cb[1] - ca[1]) * u,
                math.exp(math.log(ca[2]) * (1 - u) + math.log(cb[2]) * u),
                math.exp(math.log(ca[3]) * (1 - u) + math.log(cb[3]) * u))

    def measure(self, positions, points, cameras, size, fps):
        maximum_world = maximum_screen = maximum_zoom = maximum_ground = 0.0
        minimum_ground = math.inf
        for i in range(1, len(points)):
            a, b = points[i - 1:i + 1]
            ca, cb = cameras[i - 1:i + 1]
            world = math.hypot((b[0] - a[0]) / ca[2] * size[0], (b[1] - a[1]) / ca[3] * size[1]) * fps
            screens = [((p[0] - c[0]) / c[2] * size[0], (p[1] - c[1]) / c[3] * size[1])
                       for p, c in ((a, ca), (b, cb))]
            maximum_world = max(maximum_world, world)
            maximum_screen = max(maximum_screen, math.dist(*screens) * fps)
            maximum_zoom = max(maximum_zoom, abs(math.log2(cb[3] / ca[3])) * fps)
            pa, pb = positions[i - 1:i + 1]
            active_seconds = self.motion_time(pb.animation_time) - self.motion_time(pa.animation_time)
            ground = GEOD.inv(pa.longitude, pa.latitude, pb.longitude, pb.latitude)[2]
            if pa.segment == pb.segment and self.route.curves[pa.segment] is not None:
                # A chord cuts across a rounded turn, understating the distance traveled.
                curve = self.route.curves[pa.segment]
                fraction = (pb.animation_time - pa.animation_time) / self.route.segments[pa.segment][4]
                ground = curve.lengths[-1] * fraction
            if active_seconds > 1e-8 and ground > 1e-5 and pa.segment == pb.segment:
                speed = ground / active_seconds
                minimum_ground = min(minimum_ground, speed)
                maximum_ground = max(maximum_ground, speed)
        return {"maximum_world_screen_px_per_second": maximum_world,
                "maximum_vehicle_screen_px_per_second": maximum_screen,
                "maximum_zoom_octaves_per_second": maximum_zoom,
                "minimum_moving_ground_mps": minimum_ground if math.isfinite(minimum_ground) else None,
                "maximum_moving_ground_mps": maximum_ground}
