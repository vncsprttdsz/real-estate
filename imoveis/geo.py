"""Área de interesse: polígono (lat, lon) desenhado em docs/area.html e salvo no config.yaml."""
from __future__ import annotations


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
