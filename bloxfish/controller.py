"""Reel controller.

The zone is a double integrator driven by a binary input:

    z'' = +a   while LMB is held      (measured +0.95 px/frame^2)
    z'' = -a   while LMB is up        (measured -0.93 px/frame^2)

with |z'| saturating around 0.545 track widths/s. The fish moves at a constant
speed and reverses at random.

Time-optimal tracking of a moving target for that plant is bang-bang on the
switching curve:

    e  = x_fish - z
    ev = v_fish - v_zone
    s  = e + 0.5 * ev * |ev| / a
    hold if s > 0 else release

`s > 0` reads as "even if I started braking now I would still land short of the
fish", so keep accelerating right. Near s = 0 the law chatters at the loop rate;
that is desirable — a ~50 % duty cycle averages to zero net acceleration and the
zone coasts alongside the fish.

Everything is in *track widths* so the same numbers work at any resolution.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


class VelocityEstimator:
    """Least-squares slope over a short sliding window of (t, x) samples.

    A plain finite difference is unusable here: positions are quantised to ~2 px
    and the loop is jittery, so a 2-sample difference is mostly noise. A 5-sample
    fit still reacts within ~40 ms, which is well inside a fish direction run
    (measured 0.33-2.7 s).
    """

    def __init__(self, window: int = 5) -> None:
        self.window = window
        self._t: deque[float] = deque(maxlen=window)
        self._x: deque[float] = deque(maxlen=window)

    def reset(self) -> None:
        self._t.clear()
        self._x.clear()

    def push(self, t: float, x: float) -> None:
        # A large jump means the target teleported (or a detection glitch);
        # start over rather than emitting a huge bogus velocity.
        if self._x and abs(x - self._x[-1]) > 0.25:
            self.reset()
        self._t.append(t)
        self._x.append(x)

    @property
    def value(self) -> float:
        if len(self._t) < 3:
            return 0.0
        t = np.fromiter(self._t, float)
        x = np.fromiter(self._x, float)
        t = t - t[0]
        denom = float(((t - t.mean()) ** 2).sum())
        if denom < 1e-12:
            return 0.0
        return float(((t - t.mean()) * (x - x.mean())).sum() / denom)

    @property
    def ready(self) -> bool:
        return len(self._t) >= 3


@dataclass
class Decision:
    hold: bool
    error: float          # track widths, fish - zone
    s: float              # switching function
    v_zone: float
    v_fish: float


class ReelController:
    """Stateful bang-bang controller. All positions in track widths (0..1)."""

    def __init__(self, physics, control, timing) -> None:
        self.p = physics
        self.c = control
        self.t = timing
        self.v_zone = VelocityEstimator(control.vel_window)
        self.v_fish = VelocityEstimator(control.vel_window)
        self._accel = physics.accel
        self._last_hold = False
        self._last_t: float | None = None
        self._last_vz: float | None = None
        self._pwm = False

    # -- live acceleration identification ---------------------------------
    def _adapt(self, t: float, vz: float) -> None:
        """Blend the observed |dv/dt| into the acceleration estimate.

        The fitted 1.92 track/s^2 is for the rod in the reference recording.
        Different rods/levels move at different rates, so the controller
        measures its own plant while it drives it.
        """
        if not self.p.adapt_accel or self._last_t is None or self._last_vz is None:
            self._last_t, self._last_vz = t, vz
            return
        dt = t - self._last_t
        if 0.004 < dt < 0.08:
            observed = (vz - self._last_vz) / dt
            expected_sign = 1.0 if self._last_hold else -1.0
            # Only learn from samples where the plant clearly responded in the
            # commanded direction and is not saturated.
            if np.sign(observed) == expected_sign and abs(vz) < self.p.v_max * 0.85:
                mag = abs(observed)
                if 0.2 < mag < 12.0:
                    r = self.p.accel_adapt_rate
                    self._accel = (1 - r) * self._accel + r * mag
        self._last_t, self._last_vz = t, vz

    @property
    def accel(self) -> float:
        return self._accel

    def retarget(self) -> None:
        """Forget the target's velocity history.

        Called when the aim point switches between the fish and a chest: the
        two are different objects, and carrying the fish's velocity across the
        jump would have the controller lead a target that is not moving.
        """
        self.v_fish.reset()

    def reset(self) -> None:
        self.v_zone.reset()
        self.v_fish.reset()
        self._accel = self.p.accel
        self._last_t = self._last_vz = None
        self._last_hold = False

    # -- main step ---------------------------------------------------------
    def step(self, t: float, zone_c: float, fish_c: float,
             zone_half: float, fish_half: float) -> Decision:
        """One control decision.

        zone_c / fish_c : centres, in track widths
        zone_half / fish_half : half-widths, in track widths
        """
        self.v_zone.push(t, zone_c)
        self.v_fish.push(t, fish_c)
        vz = self.v_zone.value
        vf = self.v_fish.value
        self._adapt(t, vz)

        a = max(0.2, self._accel)
        lat = self.t.latency

        # Roll both bodies forward by the capture+input latency so the switch
        # lands on the right game frame rather than the right capture frame.
        acc = a if self._last_hold else -a
        z_pred = zone_c + vz * lat + 0.5 * acc * lat * lat
        vz_pred = float(np.clip(vz + acc * lat, -self.p.v_max, self.p.v_max))
        f_pred = fish_c + vf * lat

        target = f_pred + self.c.aim_bias
        # Never chase the fish into a wall we cannot follow it into.
        limit_lo = zone_half + self.c.edge_margin
        limit_hi = 1.0 - zone_half - self.c.edge_margin
        target = min(max(target, limit_lo), limit_hi)

        e = target - z_pred
        ev = vf - vz_pred
        s = e + 0.5 * ev * abs(ev) / a

        if abs(s) < self.c.deadband:
            # Inside the deadband, alternate every tick: mean accel ~ 0, so the
            # zone holds the fish's velocity instead of sawing around it.
            self._pwm = not self._pwm
            hold = self._pwm
        else:
            hold = s > 0

        # Do not fight the speed clamp: pushing further into saturation only
        # delays the eventual reversal.
        if hold and vz_pred >= self.p.v_max:
            hold = s > self.c.deadband
        elif not hold and vz_pred <= -self.p.v_max:
            hold = s > -self.c.deadband

        self._last_hold = hold
        return Decision(hold=hold, error=e, s=s, v_zone=vz, v_fish=vf)
