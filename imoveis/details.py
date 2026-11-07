"""Detalhes de anúncio buscados só para quem vai gerar aviso: descrição e destaques (andar alto, vista livre).

As APIs de busca não trazem a descrição; a página do anúncio no ZAP traz a do anunciante (1–2 mil caracteres).
O QuintoAndar só tem um texto padrão do portal e o Viva Real bloqueia a página (403): nesses, o destaque usa só o
andar informado na busca (ZAP/Viva Real; vem em menos da metade dos anúncios).
"""
from __future__ import annotations

import json
import re

from .common import Listing
from .http import Http

PATTERNS = {
    "andar alto": re.compile(r"andar(es)?\s+alt[oa]s?|alt[oí]ssim[oa]\s+andar|últimos?\s+andar|andar\s+elevado|cobertura", re.I),
    "vista livre": re.compile(r"vista\s+(livre|definitiva|panor[aâ]mica|eterna|desimpedida|aberta|para\s+o\s+parque|do\s+parque|"
                              r"para\s+o\s+ibirapuera|indevass[aá]vel|privilegiada)|sem\s+(prédio|vizinho)s?\s+(na\s+)?frente", re.I),
}
HIGH_FLOOR = 10


def descriptions(html: str) -> list[str]:
    out = []
    for m in re.finditer(r'"description"\s*:\s*"((?:[^"\\]|\\.){20,6000})"', html):
        try:
            out.append(json.loads('"' + m.group(1) + '"'))
        except Exception:
            out.append(m.group(1))
    return sorted(set(out), key=len, reverse=True)


def fetch_description(http: Http, l: Listing) -> str | None:
    if l.source != "zap":
        return None
    try:
        r = http.get(l.url, retries=1, timeout=20)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    ds = descriptions(r.text)
    return "\n".join(ds[:3]) if ds else None   # a maior costuma ser a do anunciante; as outras são resumos do portal


def highlights(l: Listing, text: str | None) -> list[str]:
    tags = []
    if l.floor and l.floor >= HIGH_FLOOR:
        tags.append(f"andar alto ({l.floor}º)")
    for name, pat in PATTERNS.items():
        if text and pat.search(text) and not any(t.startswith(name) for t in tags):
            tags.append(name)
    return tags
