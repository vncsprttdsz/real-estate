"""Detalhes de anúncio buscados só para quem vai gerar aviso: descrição e destaques (andar alto, vista livre,
perto de parque). O ar-condicionado é lido (air_conditioning) e aparece no painel, mas não é destaque de aviso.

Ar-condicionado e vista livre também vêm das comodidades da busca (ZAP/Viva Real: AIR_CONDITIONING;
QuintoAndar: AR_CONDICIONADO e VISTA_LIVRE), sem baixar a página. As APIs de busca não trazem a descrição; a página do anúncio no ZAP traz a do anunciante (1–2 mil caracteres).
O QuintoAndar só tem um texto padrão do portal e o Viva Real bloqueia a página (403): nesses, o destaque usa só o
andar informado na busca (ZAP/Viva Real; vem em menos da metade dos anúncios).
"""
from __future__ import annotations

import json
import re

from . import geo
from .common import Listing
from .http import Http

PATTERNS = {
    "andar alto": re.compile(r"andar(es)?\s+alt[oa]s?|alt[oí]ssim[oa]\s+andar|últimos?\s+andar|andar\s+elevado|cobertura", re.I),
    "vista livre": re.compile(r"vista\s+(livre|definitiva|panor[aâ]mica|eterna|desimpedida|aberta|para\s+o\s+parque|do\s+parque|"
                              r"para\s+o\s+ibirapuera|indevass[aá]vel|privilegiada)|sem\s+(prédio|vizinho)s?\s+(na\s+)?frente", re.I),
}
HIGH_FLOOR = 10
# Lazer. Do apartamento (conta como destaque): piscina privativa, churrasqueira na varanda. Do prédio (só ⭐ extra
# em quem já tem destaque): qualquer outra menção. "sem piscina" não conta.
LEISURE_APTO = {
    "piscina": re.compile(r"piscinas?\s+(privativas?|exclusivas?|pr[óo]prias?|particular)|piscinas?\s+(na|no|da|do)\s+"
                          r"(cobertura|terra[çc]o|varanda|apartamento|unidade|sacada)", re.I),
    "churrasqueira": re.compile(r"churrasqueiras?\s+(na|no|da|do)\s+(varanda|terra[çc]o|sacada|apartamento|unidade|cobertura)|"
                                r"(varanda|sacada|terra[çc]o)\s+gourmet\s+(com|e)\s+churrasqueira|"
                                r"churrasqueiras?\s+(privativas?|pr[óo]prias?|exclusivas?)", re.I),
}
LEISURE_ANY = {
    "piscina": re.compile(r"\bpiscinas?\b", re.I),
    "churrasqueira": re.compile(r"\bchurrasqueiras?\b", re.I),
}
LEISURE_TAG = {("piscina", "apto"): "piscina privativa", ("churrasqueira", "apto"): "churrasqueira na varanda",
               ("piscina", "predio"): "piscina no prédio", ("churrasqueira", "predio"): "churrasqueira no prédio"}


def mentions(text: str | None, pat: re.Pattern) -> bool:
    """Alguma menção que não seja negada logo antes ("sem piscina", "não possui churrasqueira")."""
    return any(not AC_NEG.search(text[max(0, m.start() - 25):m.start()]) for m in pat.finditer(text or ""))
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


def leisure(l: Listing, text: str | None) -> dict:
    """{"piscina"|"churrasqueira": "apto"|"predio"}: dados da busca e, se houver, a descrição (o do apartamento prevalece)."""
    out = {}
    for name, flag in (("piscina", "pool"), ("churrasqueira", "bbq")):
        api = getattr(l, flag, None)
        if api == "apto" or mentions(text, LEISURE_APTO[name]):
            out[name] = "apto"
        elif api == "predio" or mentions(text, LEISURE_ANY[name]):
            out[name] = "predio"
    return out


def extras(l: Listing, text: str | None) -> list[str]:
    """Marcas que não contam como destaque (lazer do prédio): só aparecem junto de um destaque."""
    return [LEISURE_TAG[n, v] for n, v in leisure(l, text).items() if v == "predio"]


def highlights(l: Listing, text: str | None, park_max_m: float = 0) -> list[str]:
    """Destaques que contam para os avisos: andar alto, vista livre, piscina, churrasqueira e perto de parque
    (ar-condicionado não conta)."""
    tags = []
    if l.floor and l.floor >= HIGH_FLOOR:
        tags.append(f"andar alto ({l.floor}º)")
    if getattr(l, "free_view", False):
        tags.append("vista livre")
    for name, pat in PATTERNS.items():
        if text and pat.search(text) and not any(t.startswith(name) for t in tags):
            tags.append(name)
    for name, level in leisure(l, text).items():
        if level == "apto":
            tags.append(LEISURE_TAG[name, level])
    park = geo.nearest_park(l.lat, l.lon, park_max_m)
    if park:
        tags.append(f"perto do {park[0]} ({round(park[1] / 50) * 50:.0f} m)" if park[1] else f"no {park[0]}")
    return tags
