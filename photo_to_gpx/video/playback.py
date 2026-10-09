"""Keep timestamp-proportional motion separate from fixed arrival holds."""
from bisect import bisect_left, bisect_right
import math


class Playback:
    def __init__(self, route, motion_seconds, *, flight_km=150, long_pause=0.2, short_pause=0.05,
                 profile=None):
        if not (motion_seconds > 0 and flight_km > 0 and long_pause >= 0 and short_pause >= 0
                and all(math.isfinite(v) for v in (motion_seconds, flight_km, long_pause, short_pause))):
            raise ValueError("invalid playback timing")
        self.route, self.scale = route, motion_seconds / route.duration
        self.profile = profile
        self.phases, self.ends, self.pauses = [], [], []
        self.hold_prefix = [0.0]
        time = 0.0
        for index, segment in enumerate(route.segments):
            duration = (profile.motion_time(route.ends[index + 1]) - profile.motion_time(route.ends[index])
                        if profile else segment[4] * self.scale)
            self.phases.append((time, time + duration, route.ends[index], route.ends[index + 1]))
            time += duration
            self.ends.append(time)
            distance_km = segment[3] / 1000
            hold = long_pause if distance_km >= flight_km else short_pause
            if hold:
                self.pauses.append({"segment": index, "arrival_seconds": time,
                                    "distance_km": distance_km, "hold_seconds": hold})
                self.phases.append((time, time + hold, route.ends[index + 1], route.ends[index + 1]))
                time += hold
                self.ends.append(time)
            self.hold_prefix.append(self.hold_prefix[-1] + hold)
        self.duration = time

    def route_time(self, seconds):
        if seconds >= self.duration:
            return self.route.duration
        index = min(len(self.phases) - 1, bisect_right(self.ends, max(0, seconds)))
        start, end, a, b = self.phases[index]
        if self.profile and a != b:
            return self.profile.route_time(self.profile.motion_time(a) + max(0, seconds - start))
        return a + (b - a) * max(0, seconds - start) / (end - start)

    def arrival_time(self, route_time):
        # A photo at a trip endpoint triggers before its arrival pause.
        completed = max(0, bisect_left(self.route.ends[1:], route_time))
        motion_time = self.profile.motion_time(route_time) if self.profile else route_time * self.scale
        return motion_time + self.hold_prefix[completed]
