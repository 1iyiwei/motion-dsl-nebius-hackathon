"""Swarm physics: point-mass quadrotors with thrust limits following the planned tracks through wind.

The planned show lies on a vertical plane (x across, z up); the sim adds depth y, so wind can push drones
out of the plane and separation is measured in 3D. Each drone runs a PID position controller with the
planned acceleration fed forward. Thrust is limited in magnitude (thrust/weight) and tilt, so a plan that
asks for too much, or a gust that is too strong, shows up as tracking error and lost separation.
Power grows with thrust^1.5, which gives each drone's battery use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

G = 9.81


@dataclass
class Airframe:
    thrust_to_weight: float = 2.0
    max_tilt_deg: float = 40.0
    drag: float = 0.25  # 1/s: linear drag on airspeed, which is also how wind pushes a drone
    kp: float = 5.0
    kd: float = 4.0
    ki: float = 0.8
    gps_sigma: float = 0.03  # m, RTK-grade positioning noise


@dataclass
class Wind:
    speed: float = 0.0  # mean, m/s
    direction_deg: float = 90.0  # 0 = along +x (across the show), 90 = into the show plane (depth)
    gust: float = 0.35  # gust standard deviation as a fraction of the mean speed (plus a floor below)
    gust_floor: float = 0.3  # m/s of gustiness even in light wind
    tau: float = 3.0  # s, gust correlation time
    local: float = 0.25  # per-drone share of the gust (turbulence differs across a 100 m swarm)


@dataclass
class SimResult:
    times: np.ndarray  # (F,)
    pos: np.ndarray  # (F, N, 3) float32, metres [x, y depth, z]
    err: np.ndarray  # (F, N) distance from the planned point
    saturated: np.ndarray  # (F, N) thrust or tilt limit hit
    battery_used: np.ndarray  # (N,) fraction of hover endurance
    min_dist: np.ndarray  # (F,) closest pair per frame (3D)
    close: list[tuple[float, int, int, float]] = field(default_factory=list)  # (t, i, j, d) pairs below the alert distance


def reference(times: np.ndarray, plan: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Planned positions (F, N, 2) [x, z] → 3D position, velocity, acceleration (F, N, 3)."""
    dt = float(times[1] - times[0])
    p = np.zeros(plan.shape[:2] + (3,))
    p[..., 0], p[..., 2] = plan[..., 0], plan[..., 1]
    v = np.gradient(p, dt, axis=0)
    a = np.gradient(v, dt, axis=0)
    return p, v, a


class Swarm:
    """Steppable, so the live view can inject gusts (the phone's "be the wind") between frames."""

    def __init__(self, times: np.ndarray, plan: np.ndarray, endurance: float, wind: Wind | None = None,
                 airframe: Airframe | None = None, seed: int = 0, substeps: int = 2):
        self.times = times
        self.ref_p, self.ref_v, self.ref_a = reference(times, plan)
        self.n = plan.shape[1]
        self.wind = wind or Wind()
        self.af = airframe or Airframe()
        self.endurance = endurance
        self.rng = np.random.default_rng(seed)
        self.substeps = substeps
        self.dt = float(times[1] - times[0]) / substeps
        self.p = self.ref_p[0].copy()
        self.v = np.zeros((self.n, 3))
        self.i_err = np.zeros((self.n, 3))
        self.gust = np.zeros(3)
        self.local = np.zeros((self.n, 3))
        self.used = np.zeros(self.n)
        self.frame = 0
        th = math.radians(self.wind.direction_deg)
        self.w_mean = self.wind.speed * np.array([math.cos(th), math.sin(th), 0.0])

    def _wind(self, extra: np.ndarray | None) -> np.ndarray:
        w = self.wind
        sigma = w.gust * w.speed + w.gust_floor
        k = self.dt / w.tau
        s = sigma * math.sqrt(2 * k)
        self.gust += -k * self.gust + s * self.rng.standard_normal(3) * [1, 1, 0.3]
        self.local += -k * self.local + s * w.local * self.rng.standard_normal((self.n, 3)) * [1, 1, 0.3]
        out = self.w_mean + self.gust + self.local
        if extra is not None:
            out = out + extra
        return out

    def step(self, extra_wind: np.ndarray | None = None) -> np.ndarray:
        """Advance one frame; returns the saturation flags (N,)."""
        af = self.af
        k = min(self.frame, len(self.times) - 1)
        rp, rv, ra = self.ref_p[k], self.ref_v[k], self.ref_a[k]
        sat = np.zeros(self.n, bool)
        tan_tilt = math.tan(math.radians(af.max_tilt_deg))
        tmax = af.thrust_to_weight * G
        for _ in range(self.substeps):
            meas = self.p + self.rng.normal(0, af.gps_sigma, self.p.shape)
            e = rp - meas
            self.i_err = np.clip(self.i_err + e * self.dt, -2.0, 2.0)
            cmd = ra + af.kp * e + af.kd * (rv - self.v) + af.ki * self.i_err + af.drag * self.v  # cancel drag at own speed
            thrust = cmd + [0, 0, G]
            tz = np.maximum(thrust[:, 2], 0.3 * G)
            h = thrust[:, :2]
            hn = np.linalg.norm(h, axis=1)
            hmax = tz * tan_tilt
            over = hn > hmax
            h = np.where(over[:, None], h * (hmax / np.maximum(hn, 1e-9))[:, None], h)
            thrust = np.c_[h, tz]
            tn = np.linalg.norm(thrust, axis=1)
            big = tn > tmax
            thrust = np.where(big[:, None], thrust * (tmax / tn)[:, None], thrust)
            sat |= over | big
            w = self._wind(extra_wind)
            acc = thrust - [0, 0, G] - af.drag * (self.v - w)
            self.v += acc * self.dt
            self.p += self.v * self.dt
            self.used += (np.linalg.norm(thrust, axis=1) / G) ** 1.5 * self.dt / self.endurance
        self.frame += 1
        return sat


def simulate(times: np.ndarray, plan: np.ndarray, endurance: float, wind: Wind | None = None, seed: int = 0,
             alert: float = 1.0, airframe: Airframe | None = None) -> SimResult:
    sw = Swarm(times, plan, endurance, wind, airframe, seed)
    F, N = len(times), plan.shape[1]
    pos = np.zeros((F, N, 3), np.float32)
    err = np.zeros((F, N))
    sat = np.zeros((F, N), bool)
    mind = np.zeros(F)
    close = []
    for k in range(F):
        pos[k] = sw.p
        err[k] = np.linalg.norm(sw.p - sw.ref_p[k], axis=1)
        tree = cKDTree(sw.p)
        d, _ = tree.query(sw.p, k=2)
        mind[k] = d[:, 1].min()
        if mind[k] < alert:
            for i, j in tree.query_pairs(alert):
                close.append((float(times[k]), i, j, float(np.linalg.norm(sw.p[i] - sw.p[j]))))
        if k < F - 1:
            sat[k] = sw.step()
    return SimResult(times, pos, err, sat, sw.used.copy(), mind, close)
