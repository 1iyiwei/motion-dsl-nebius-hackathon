"""Live mode: the swarm flies the show in real time while a phone (or a slider) is the wind.

Tilt sets a steady wind (direction and strength); shaking adds a gust that decays over about two seconds.
Wind varies a little from drone to drone, as it does across a 100 m swarm, so a hard enough gust can
push neighbours together, which the live view flags as near misses.
"""

from __future__ import annotations

import math
import secrets
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from .flight import tracks_m
from .pipeline import Checked
from .sim import Swarm, Wind

FPS = 30
MAX_STEADY = 12.0  # m/s at full tilt
MAX_GUST = 16.0  # m/s for the hardest shake
GUST_DECAY = 2.0  # s
CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_code() -> str:
    return "".join(secrets.choice(CODE_CHARS) for _ in range(5))


@dataclass
class PadInput:
    steady: np.ndarray = field(default_factory=lambda: np.zeros(3))  # m/s, [x across, y depth, 0]
    gust_dir: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0]))
    seen: float = 0.0  # time of the last pad message


def tilt_to_wind(beta: float, gamma: float) -> np.ndarray:
    """Phone tilt (degrees, DeviceOrientation beta = front/back, gamma = left/right) → steady wind.
    Tilting right blows the swarm right; tilting the top away blows it away from the audience."""
    x = max(-1.0, min(1.0, gamma / 45.0))
    y = max(-1.0, min(1.0, -beta / 45.0))
    v = np.array([x, y, 0.0])
    n = float(np.linalg.norm(v))
    if n < 0.12:  # a phone held roughly flat is calm air
        return np.zeros(3)
    return v / max(n, 1.0) * MAX_STEADY * min(n, 1.0)


class LiveSession:
    def __init__(self, checked: Checked, code: str | None = None, seed: int = 0):
        self.code = code or new_code()
        self.checked = checked
        show = checked.show
        self.times, self.plan = tracks_m(checked.baked, show)
        self.endurance = show.limits.max_flight_time
        self.alert = show.limits.near_miss
        self.rng = np.random.default_rng(seed)
        self.spread = 1 + 0.6 * self.rng.standard_normal((show.drones, 1))  # per-drone share of the wind
        self.jitter = self.rng.standard_normal((show.drones, 3)) * [0.25, 0.25, 0.05]
        self.pad = PadInput()
        self.gust = 0.0
        self.gusts = 0
        self.near_total = 0
        self.max_err = 0.0
        self._restart()

    def _restart(self) -> None:
        self.swarm = Swarm(self.times, self.plan, self.endurance, Wind(speed=0.0, gust_floor=0.2), seed=int(self.rng.integers(1 << 30)))

    def set_tilt(self, beta: float, gamma: float) -> None:
        self.pad.steady = tilt_to_wind(beta, gamma)
        self.pad.seen = time.time()

    def set_wind(self, vx: float, vy: float) -> None:
        self.pad.steady = np.array([vx, vy, 0.0])
        self.pad.seen = time.time()

    def shake(self, strength: float, direction: tuple[float, float] | None = None) -> None:
        """strength 0..1 (the pad maps accelerometer jolts to it)."""
        self.gust = max(self.gust, max(0.0, min(1.0, strength)) * MAX_GUST)
        if direction is not None and any(direction):
            d = np.array([direction[0], direction[1], 0.0])
            self.pad.gust_dir = d / np.linalg.norm(d)
        elif np.linalg.norm(self.pad.steady) > 0.1:
            self.pad.gust_dir = self.pad.steady / np.linalg.norm(self.pad.steady)
        self.gusts += 1
        self.pad.seen = time.time()

    def wind_now(self) -> np.ndarray:
        """Per-drone extra wind (N, 3)."""
        base = self.pad.steady + self.gust * self.pad.gust_dir
        mag = float(np.linalg.norm(base))
        return base[None, :] * self.spread + mag * self.jitter

    def tick(self, dt: float = 1 / FPS) -> dict:
        """Advance one frame and return what the viewers draw."""
        if self.swarm.frame >= len(self.times) - 1:
            self._restart()
        self.swarm.step(self.wind_now())
        self.gust *= math.exp(-dt / GUST_DECAY)
        k = min(self.swarm.frame, len(self.times) - 1)
        p = self.swarm.p
        err = np.linalg.norm(p - self.swarm.ref_p[k], axis=1)
        tree = cKDTree(p)
        pairs = sorted(tree.query_pairs(self.alert))[:12]
        self.near_total += len(pairs)
        self.max_err = max(self.max_err, float(err.max()))
        d = tree.query(p, k=2)[0][:, 1]
        wind = self.pad.steady + self.gust * self.pad.gust_dir
        return {
            "type": "frame", "frame": k, "t": round(float(self.times[k]), 2),
            "pos": np.round(p[:, [0, 2, 1]], 2).tolist(),  # [x, z, depth] metres
            "near": [list(map(int, q)) for q in pairs],
            "min_sep": round(float(d.min()), 2), "drift": round(float(err.max()), 2),
            "wind": [round(float(wind[0]), 1), round(float(wind[1]), 1)], "gust": round(self.gust, 1),
            "pad": time.time() - self.pad.seen < 3,
        }
