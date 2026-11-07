"""Alerta de anúncio com R$/m² bem abaixo da mediana do mercado comparável.

Comparação: mesmo negócio (compra ou aluguel), mesmo bairro e mesma faixa de área útil — por padrão só entre os
anúncios dentro do orçamento (o "seu recorte"): no bairro inteiro, quase tudo que cabe no teto já fica abaixo da
mediana (ex.: Vila Nova Conceição pede ~R$ 31 mil/m²; 100 m² por até R$ 2,25 mi é ≤ R$ 22,5 mil/m²). Se a faixa tiver poucos
anúncios, usa o bairro inteiro; se nem o bairro tiver, não compara. Aluguel usa o valor total (aluguel + condomínio
+ IPTU) por m², como o painel. Leilão fica de fora (lance mínimo é sempre abaixo do mercado por construção).
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .common import norm

BANDS = [(0, 100, "até 100 m²"), (100, 150, "100–150 m²"), (150, 200, "150–200 m²"), (200, 300, "200–300 m²"),
         (300, float("inf"), "300 m² ou mais")]


def band(area: float | None) -> int | None:
    if not area:
        return None
    return next(i for i, (lo, hi, _) in enumerate(BANDS) if lo <= area < hi)


def ppm(d: dict, price: float | None = None) -> float | None:
    """R$/m²: venda = preço ÷ área; aluguel = valor total (ou aluguel) ÷ área."""
    biz, area = d.get("business"), d.get("area")
    if biz not in ("venda", "aluguel") or not area:
        return None
    p = price if price is not None else (d.get("price") if biz == "venda" else (d.get("total_monthly") or d.get("price")))
    return p / area if p else None


@dataclass
class Gap:
    pct: float          # R$/m² do anúncio ÷ mediana − 1
    ppm: float
    median: float
    n: int
    scope: str          # "Itaim Bibi, 100–150 m²" ou "Itaim Bibi"


class Market:
    def __init__(self, rows, min_n: int = 15, suffix: str = ""):
        self.min_n, self.suffix = min_n, suffix
        self.groups: dict = {}
        for d in rows:
            v = ppm(d)
            if v is None:
                continue
            nb = norm(d.get("neighborhood"))
            self.groups.setdefault((d["business"], nb, band(d.get("area"))), []).append(v)
            self.groups.setdefault((d["business"], nb, None), []).append(v)
        self.med = {k: (median(v), len(v)) for k, v in self.groups.items()}

    def gap(self, d: dict, price: float | None = None) -> Gap | None:
        v = ppm(d, price)
        if v is None:
            return None
        nb, b = norm(d.get("neighborhood")), band(d.get("area"))
        for key, scope in (((d["business"], nb, b), f"{d.get('neighborhood', '').strip()}, {BANDS[b][2]}" if b is not None else ""),
                           ((d["business"], nb, None), d.get("neighborhood", "").strip())):
            m = self.med.get(key)
            if m and m[1] >= self.min_n:
                return Gap(v / m[0] - 1, v, m[0], m[1], scope + self.suffix)
        return None
