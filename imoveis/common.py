"""Modelos, configuração e utilitários compartilhados."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEBUG_DIR = ROOT / "debug"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def norm(text: str | None) -> str:
    """Minúsculas, sem acentos e espaços colapsados — para comparar bairros."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")


def to_float(value) -> float | None:
    """Converte '1.234,56', '1234.5', 'R$ 2.500.000' ou números em float."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d,.]", "", str(value))
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1 or re.search(r"\.\d{3}$", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class Listing:
    source: str          # zap | vivareal | quintoandar | zuk
    id: str
    url: str
    business: str        # venda | aluguel | leilao
    title: str = ""
    neighborhood: str = ""
    address: str = ""
    area: float | None = None
    price: float | None = None       # venda/lance ou aluguel mensal
    total_monthly: float | None = None  # aluguel + condomínio + IPTU
    condo: float | None = None
    bedrooms: int | None = None
    parking: int | None = None
    extra: str = ""      # informação livre (ex.: data do leilão)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.business}:{self.id}"

    @property
    def ref_price(self) -> float | None:
        return self.total_monthly or self.price

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Config:
    neighborhoods: list[str]
    city: str = "São Paulo"
    state: str = "SP"
    min_area: float = 100
    types: list[str] = field(default_factory=lambda: ["apartamento"])
    businesses: list[str] = field(default_factory=lambda: ["venda", "aluguel"])
    max_sale: float | None = None
    max_rent: float | None = None
    sources: dict = field(default_factory=dict)
    price_drop_pct: float = 3

    @property
    def neighborhoods_norm(self) -> set[str]:
        return {norm(n) for n in self.neighborhoods}

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        raw = yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8"))
        return cls(
            neighborhoods=raw["bairros"],
            city=raw.get("cidade", "São Paulo"),
            state=raw.get("estado", "SP"),
            min_area=float(raw.get("area_minima_m2", 100)),
            types=[norm(t) for t in raw.get("tipos", ["apartamento"])],
            businesses=[norm(b) for b in raw.get("negocios", ["venda", "aluguel"])],
            max_sale=raw.get("preco_max_venda"),
            max_rent=raw.get("preco_max_aluguel"),
            sources=raw.get("fontes", {}),
            price_drop_pct=float(raw.get("alerta_queda_preco_pct") or 0),
        )

    def matches(self, l: Listing) -> bool:
        """Filtro final aplicado a todas as fontes (as APIs nem sempre respeitam os filtros)."""
        if norm(l.neighborhood) not in self.neighborhoods_norm:
            return False
        # Leilões frequentemente não informam área na listagem: só descarta se souber que é menor.
        if l.area is not None and l.area < self.min_area:
            return False
        if l.area is None and l.business != "leilao":
            return False
        if l.business == "venda" and self.max_sale and l.price and l.price > self.max_sale:
            return False
        if l.business == "aluguel" and self.max_rent and l.ref_price and l.ref_price > self.max_rent:
            return False
        return True


def dump_debug(name: str, content: str | bytes) -> None:
    """Salva respostas brutas quando DEBUG=1 (útil para ajustar coletores se um site mudar)."""
    if not os.environ.get("DEBUG"):
        return
    DEBUG_DIR.mkdir(exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(DEBUG_DIR / name, mode) as f:
        f.write(content)


def iter_dicts(obj):
    """Percorre recursivamente um JSON e devolve todos os dicts."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_dicts(v)


NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)


def next_data(html: str) -> dict | None:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
