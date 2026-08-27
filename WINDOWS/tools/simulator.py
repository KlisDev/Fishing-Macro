"""Minigame simulator built from the constants measured in the video.

Used to validate the controller without the game running. All units are track
widths; the tick rate matches the game's 60 fps.

Model (see docs/MECHANICS.md):
  zone:  z'' = +a while held, -a while released, |z'| clamped to v_max
  fish:  constant speed, instantaneous random reversals
  progress: starts at 0.5, +rate while the fish is inside the zone,
            -rate*loss_ratio while it is outside
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class SimParams:
    accel: float = 1.92           # track/s^2   (measured 0.94 px/frame^2 @ 4K)
    v_max: float = 0.545          # track/s     (measured 16 px/frame)
    zone_w: float = 0.307         # measured 541/1762
    fish_w: float = 0.087         # measured 153/1762
    fish_speed: float = 0.060     # measured 0.041 .. 0.080 track/s
    # Direction runs measured at 20..164 frames.
    flip_min: float = 0.33
    flip_max: float = 2.7
    fill_rate: float = 0.093      # 0.5 progress over ~5.4 s
    # Measured across the alarm flash at frames 2994-3010: progress stalls and
    # bleeds at about half the fill rate while the fish is out.
    loss_ratio: float = 0.5
    dt: float = 1.0 / 60.0


class Minigame:
    def __init__(self, p: SimParams, rng: random.Random | None = None) -> None:
        self.p = p
        self.rng = rng or random.Random()
        self.t = 0.0
        self.z = 0.5
        self.vz = 0.0
        self.f = 0.5
        self.fdir = self.rng.choice((-1.0, 1.0))
        self._next_flip = self._flip_time()
        self.progress = 0.5
        self.done = False
        self.won = False
        self.time_outside = 0.0

    def _flip_time(self) -> float:
        return self.t + self.rng.uniform(self.p.flip_min, self.p.flip_max)

    @property
    def inside(self) -> bool:
        return abs(self.f - self.z) <= (self.p.zone_w - self.p.fish_w) / 2

    def tick(self, hold: bool) -> None:
        if self.done:
            return
        p, dt = self.p, self.p.dt

        acc = p.accel if hold else -p.accel
        self.vz = max(-p.v_max, min(p.v_max, self.vz + acc * dt))
        self.z += self.vz * dt
        lo, hi = p.zone_w / 2, 1.0 - p.zone_w / 2
        if self.z <= lo:
            self.z, self.vz = lo, max(0.0, self.vz)
        elif self.z >= hi:
            self.z, self.vz = hi, min(0.0, self.vz)

        if self.t >= self._next_flip:
            self.fdir *= -1.0
            self._next_flip = self._flip_time()
        self.f += self.fdir * p.fish_speed * dt
        flo, fhi = p.fish_w / 2, 1.0 - p.fish_w / 2
        if self.f <= flo:
            self.f, self.fdir = flo, 1.0
        elif self.f >= fhi:
            self.f, self.fdir = fhi, -1.0

        if self.inside:
            self.progress += p.fill_rate * dt
        else:
            self.progress -= p.fill_rate * p.loss_ratio * dt
            self.time_outside += dt

        self.t += dt
        if self.progress >= 1.0:
            self.done, self.won = True, True
        elif self.progress <= 0.0:
            self.done, self.won = True, False


class RecordedFish:
    """Replays a fish trajectory captured from a real minigame.

    Same interface as `Minigame`, but the fish follows exactly what the game
    actually did rather than a synthetic random walk. This is the closest thing
    to an offline test against the real thing.
    """

    def __init__(self, p: SimParams, track: list[float], fps: float = 60.0,
                 start: float | None = None) -> None:
        self.p = p
        self.track = track
        self.fps = fps
        self.t = 0.0
        self.f = track[0]
        self.z = track[0] if start is None else start
        self.vz = 0.0
        self.progress = 0.5
        self.done = False
        self.won = False
        self.time_outside = 0.0
        self.worst_error = 0.0

    @property
    def inside(self) -> bool:
        return abs(self.f - self.z) <= (self.p.zone_w - self.p.fish_w) / 2

    def tick(self, hold: bool) -> None:
        if self.done:
            return
        p, dt = self.p, self.p.dt

        acc = p.accel if hold else -p.accel
        self.vz = max(-p.v_max, min(p.v_max, self.vz + acc * dt))
        self.z += self.vz * dt
        lo, hi = p.zone_w / 2, 1.0 - p.zone_w / 2
        if self.z <= lo:
            self.z, self.vz = lo, max(0.0, self.vz)
        elif self.z >= hi:
            self.z, self.vz = hi, min(0.0, self.vz)

        idx = int(self.t * self.fps)
        if idx >= len(self.track) - 1:
            self.done = True
            self.won = self.progress > 0.0
            return
        self.f = self.track[idx]

        self.worst_error = max(self.worst_error, abs(self.f - self.z))
        if self.inside:
            self.progress = min(1.0, self.progress + p.fill_rate * dt)
        else:
            self.progress -= p.fill_rate * p.loss_ratio * dt
            self.time_outside += dt

        self.t += dt
        if self.progress <= 0.0:
            self.done, self.won = True, False
