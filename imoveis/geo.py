"""Área de interesse (polígono desenhado em docs/area.html e salvo no config.yaml) e distância até os parques."""
from __future__ import annotations

import json
import math
from pathlib import Path


def inside(lat: float, lon: float, poly: list) -> bool:
    """Ponto dentro do polígono (ray casting; poly = [[lat, lon], ...], fechado ou não)."""
    n, ok = len(poly), False
    if n < 3:
        return False
    for i in range(n):
        y1, x1 = poly[i]
        y2, x2 = poly[(i + 1) % n]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            ok = not ok
    return ok


def distance_m(lat: float, lon: float, poly: list) -> float:
    """Distância em metros do ponto até a borda do polígono (0 se estiver dentro). Projeção local, boa para < 10 km."""
    if inside(lat, lon, poly):
        return 0.0
    kx, ky = 111320 * math.cos(math.radians(lat)), 110540
    px, py = 0.0, 0.0
    best = float("inf")
    n = len(poly)
    for i in range(n):
        (y1, x1), (y2, x2) = poly[i], poly[(i + 1) % n]
        ax, ay = (x1 - lon) * kx, (y1 - lat) * ky
        bx, by = (x2 - lon) * kx, (y2 - lat) * ky
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(ax + t * dx - px, ay + t * dy - py))
    return best


_PARKS = None


def parks() -> dict:
    """Contornos dos parques de referência (imoveis/parques.json, do OpenStreetMap)."""
    global _PARKS
    if _PARKS is None:
        p = Path(__file__).with_name("parques.json")
        _PARKS = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _PARKS


def nearest_park(lat, lon, max_m: float) -> tuple[str, float] | None:
    """(nome, distância em m) do parque mais próximo até max_m, ou None."""
    if not (lat and lon and max_m):
        return None
    best = min(((name, distance_m(lat, lon, poly)) for name, poly in parks().items()), key=lambda x: x[1], default=None)
    return best if best and best[1] <= max_m else None
