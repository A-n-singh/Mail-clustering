"""Deterministic 3D positions for the Groups hologram: each group sits on a sphere, its mails fill a ball around it.
Same input -> same picture every time, so the view does not jump between refreshes. No clustering, nothing is stored."""
import math
import random
import zlib


def _sphere(n: int) -> list[tuple[float, float, float]]:
    if n == 1:
        return [(0.0, 0.0, 0.0)]
    golden = math.pi * (3 - math.sqrt(5))
    out = []
    for i in range(n):
        y = 1 - 2 * (i + 0.5) / n
        r = math.sqrt(max(0.0, 1 - y * y))
        out.append((r * math.cos(golden * i), y, r * math.sin(golden * i)))
    return out


def layout(groups: list[tuple[str, list[str]]]) -> dict[str, tuple[float, float, float]]:
    """groups: (key, message ids). Returns message id -> (x, y, z). Bigger groups get bigger balls."""
    groups = sorted((g for g in groups if g[1]), key=lambda g: (-len(g[1]), g[0]))
    if not groups:
        return {}
    biggest = len(groups[0][1])
    spread = min(0.4, 1.1 / math.sqrt(len(groups)))
    out: dict[str, tuple[float, float, float]] = {}
    for (key, ids), (cx, cy, cz) in zip(groups, _sphere(len(groups))):
        radius = spread * (0.45 + 0.55 * math.sqrt(len(ids) / biggest))
        rnd = random.Random(zlib.crc32(key.encode()))
        for mid in ids:
            while True:
                x, y, z = rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1)
                if x * x + y * y + z * z <= 1:
                    break
            out[mid] = (cx + x * radius, cy + y * radius, cz + z * radius)
    return out
