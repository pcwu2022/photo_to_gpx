"""Keep timestamp-proportional motion separate from arrival and photo holds."""
from bisect import bisect_left, bisect_right
import math

from .travel_route import GEOD

# Minimum rendered time between consecutive photo appearances.
MIN_PHOTO_INTERVAL_SECONDS = 0.5


class Playback:
    def __init__(self, route, motion_seconds, *, flight_km=150, long_pause=0.2, short_pause=0.05,
                 profile=None, photo_interval=0):
        if not (motion_seconds > 0 and flight_km > 0 and long_pause >= 0 and short_pause >= 0
                and photo_interval >= 0
                and all(math.isfinite(v) for v in (motion_seconds, flight_km, long_pause, short_pause,
                                                   photo_interval))):
            raise ValueError("invalid playback timing")
        self.route, self.scale = route, motion_seconds / route.duration
        self.profile, self.photo_interval = profile, photo_interval
        self.phases, self.ends, self.pauses = [], [], []
        self.photo_arrivals, self.photo_intervals = {}, []
        groups = {t: [] for t in route.ends}
        for t, photo in route.events:
            groups[t].append(photo)
        motion = lambda t: profile.motion_time(t) if profile else t * self.scale
        durations = [motion(b) - motion(a) for a, b in zip(route.ends, route.ends[1:])]
        base_holds = [0.0] + [long_pause if s[3] / 1000 >= flight_km else short_pause
                              for s in route.segments]
        self.hold_prefix = [0.0]
        time = 0.0
        for anchor, route_t in enumerate(route.ends):
            if anchor:
                duration = durations[anchor - 1]
                self.phases.append((time, time + duration, route.ends[anchor - 1], route_t))
                time += duration
                self.ends.append(time)
            arrival = time
            for j, photo in enumerate(groups[route_t]):
                self.photo_arrivals[self.photo_key(photo)] = arrival + j * photo_interval
            burst_hold = max(0, len(groups[route_t]) - 1) * photo_interval
            # The next trip's actual movement time and the existing arrival wait
            # already count toward spacing. Only add the remaining time needed.
            extra = (max(0.0, photo_interval - durations[anchor] - base_holds[anchor])
                     if anchor < len(durations) else 0.0)
            hold = burst_hold + base_holds[anchor] + extra
            if hold:
                distance = route.segments[anchor - 1][3] / 1000 if anchor else 0.0
                self.pauses.append({"segment": anchor - 1, "arrival_seconds": arrival,
                                    "distance_km": distance, "hold_seconds": hold,
                                    "photo_delay_seconds": burst_hold + extra})
                self.phases.append((time, time + hold, route_t, route_t))
                time += hold
                self.ends.append(time)
            if anchor == 0:
                self.hold_prefix[0] = hold
            else:
                self.hold_prefix.append(self.hold_prefix[-1] + hold)
        self.duration = time
        for (a_t, a), (b_t, b) in zip(route.events, route.events[1:]):
            leg = bisect_left(route.ends, b_t) - 1
            distance = GEOD.inv(a.longitude, a.latitude, b.longitude, b.latitude)[2]
            if b_t > a_t and route.curves[leg] is not None:
                distance = route.curves[leg].lengths[-1]
            base_gap = motion(b_t) - motion(a_t) + (base_holds[leg] if b_t > a_t else 0)
            gap = self.photo_arrival_time(b_t, b) - self.photo_arrival_time(a_t, a)
            self.photo_intervals.append({"from_raw_index": a.raw_index, "to_raw_index": b.raw_index,
                                         "effective_distance_m": distance,
                                         "movement_seconds": motion(b_t) - motion(a_t),
                                         "original_gap_seconds": base_gap,
                                         "rendered_gap_seconds": gap,
                                         "added_delay_seconds": max(0.0, gap - base_gap)})

    def route_time(self, seconds):
        if seconds >= self.duration:
            return self.route.duration
        index = min(len(self.phases) - 1, bisect_right(self.ends, max(0, seconds)))
        start, end, a, b = self.phases[index]
        if self.profile and a != b:
            return self.profile.route_time(self.profile.motion_time(a) + max(0, seconds - start))
        return a + (b - a) * max(0, seconds - start) / (end - start)

    def arrival_time(self, route_time):
        # The first photo triggers before any initial photo spacing hold.
        if route_time <= 0:
            return 0.0
        completed = max(0, bisect_left(self.route.ends[1:], route_time))
        motion_time = self.profile.motion_time(route_time) if self.profile else route_time * self.scale
        return motion_time + self.hold_prefix[completed]

    @staticmethod
    def photo_key(photo):
        # Remain stable when Route normalizes a caller's timezone-aware record.
        return photo.path, photo.raw_index, photo.timestamp.timestamp()

    def photo_arrival_time(self, route_time, photo):
        return self.photo_arrivals.get(self.photo_key(photo), self.arrival_time(route_time))
