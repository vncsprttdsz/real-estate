"""Detalhes de anúncio buscados só para quem vai gerar aviso: descrição e destaques (andar alto, vista livre,
ar-condicionado).

Ar-condicionado e vista livre também vêm das comodidades da busca (ZAP/Viva Real: AIR_CONDITIONING;
QuintoAndar: AR_CONDICIONADO e VISTA_LIVRE), sem baixar a página. As APIs de busca não trazem a descrição; a página do anúncio no ZAP traz a do anunciante (1–2 mil caracteres).
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
# Ar-condicionado: instalado × só infraestrutura (tubulação/ponto/preparado); "sem ar" não conta.
AC = re.compile(r"ar[\s-]*condicionados?|ar\s+central|\bsplits?\b|climatizad[oa]s?|\bvrf\b", re.I)
AC_INFRA = re.compile(r"(infra[\s-]*(estrutura)?|preparad[oa]s?|pr[ée][\s-]*(instala\w*|disposi\w*)|pontos?|tubula\w*|"
                      r"previs[ãa]o|espera)\s+(completa\s+|pronta\s+)?(para|p/|de|do|com)?\s*(o\s+|os\s+)?$", re.I)
AC_NEG = re.compile(r"\b(sem|n[ãa]o\s+(tem|possui|h[áa]))\s+(o\s+)?$", re.I)


def air_conditioning(text: str | None) -> str | None:
    """'ar-condicionado' se alguma menção é de aparelho; 'infra para ar-condicionado' se só há preparação."""
    kinds, infra_end = set(), -99
    for m in AC.finditer(text or ""):
        before = text[max(0, m.start() - 45):m.start()]
        if AC_NEG.search(before):
            continue
        # "preparado para ar-condicionado split": o "split" logo depois ainda é a mesma infraestrutura
        same_phrase = m.start() - infra_end <= 12 and not re.search(r"[.;!\n]", text[infra_end:m.start()])
        if AC_INFRA.search(before) or same_phrase:
            kinds.add("infra"); infra_end = m.end()
        else:
            kinds.add("ar")
    return "ar-condicionado" if "ar" in kinds else "infra para ar-condicionado" if kinds else None


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
    if getattr(l, "free_view", False):
        tags.append("vista livre")
    for name, pat in PATTERNS.items():
        if text and pat.search(text) and not any(t.startswith(name) for t in tags):
            tags.append(name)
    ac = "ar-condicionado" if getattr(l, "air_conditioning", False) else air_conditioning(text)
    if ac:
        tags.append(ac)
    return tags
