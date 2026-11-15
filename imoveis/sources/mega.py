"""Mega Leilões: apartamentos em leilão (judicial e extrajudicial) na capital.

Listagem paginada (?pagina=N; ~60 apartamentos em SP na sondagem de 15/11). O link de cada lote já traz área e
bairro no texto (…/apartamento-124-m2-03-vagas-brooklin-paulista-sao-paulo-sp-x131536), então só os lotes dos
bairros procurados têm a página aberta para ler praças, lances, avaliação e ocupação.
"""
from __future__ import annotations

import html as htmllib
import re
from datetime import datetime

from ..common import Config, Listing, dump_debug, slug, to_float
from ..http import Http

SITE = "https://www.megaleiloes.com.br"
LIST_URL = SITE + "/imoveis/apartamentos/{uf}/{city}"
LINK_RE = re.compile(r'href="(https://www\.megaleiloes\.com\.br/imoveis/apartamentos/[a-z]{2}/[a-z0-9-]+/([a-z0-9-]+-([xj]\d{5,})))["?]', re.I)
MAX_PAGES = 10
TAG = re.compile(r"<[^>]+>")


def page_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", htmllib.unescape(TAG.sub(" ", html)))


def wanted_slugs(cfg: Config) -> dict[str, str]:
    return {slug(n): n for n in list(cfg.neighborhoods) + list(cfg.area_extra_neighborhoods)}


def lot_links(html: str) -> dict[str, tuple[str, str]]:
    """{código: (url sem utm, slug do lote)}"""
    out = {}
    for m in LINK_RE.finditer(html):
        out[m.group(3).upper()] = (m.group(1), m.group(2))
    return out


def neighborhood_of(lot_slug: str, wanted: dict[str, str]) -> str | None:
    for s, name in sorted(wanted.items(), key=lambda kv: -len(kv[0])):
        if f"-{s}-" in f"-{lot_slug}-":
            return name
    return None


def _date(d: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


ROUND_RE = re.compile(r"(1|2)[ªaº]\s*Pra[çc]a:?\s*(\d{2}/\d{2}/\d{2,4})[^R]{0,30}R\$\s*([\d.]+,\d{2})", re.I)
SINGLE_RE = re.compile(r"(?:P\.?\s*[ÚU]nica|Pra[çc]a [úu]nica)[^D]{0,40}Data:?\s*(\d{2}/\d{2}/\d{2,4})[^R]{0,30}R\$\s*([\d.]+,\d{2})", re.I)
APPRAISAL_RE = re.compile(r"(?:valor\s+de\s+)?avalia[çc][ãa]o[^R\d]{0,40}R\$\s*([\d.]+,\d{2})", re.I)
AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m²", re.I)


def parse_detail(html: str) -> dict:
    t = page_text(html)
    rounds = [{"n": int(n), "date": _date(d), "bid": to_float(v)} for n, d, v in ROUND_RE.findall(t)]
    if not rounds:
        m = SINGLE_RE.search(t)
        if m:
            rounds = [{"n": 1, "date": _date(m.group(1)), "bid": to_float(m.group(2))}]
    seen, uniq = set(), []
    for r in rounds:
        if r["n"] not in seen:
            seen.add(r["n"])
            uniq.append(r)
    ap = APPRAISAL_RE.search(t)
    occ = None
    if re.search(r"im[óo]vel\s+desocupado", t, re.I):
        occ = False
    elif re.search(r"im[óo]vel\s+ocupado|\bocupado\s+por\b|encontra-se\s+ocupado", t, re.I):
        occ = True
    kind = "judicial" if re.search(r"\bjudicial\b", t, re.I) and not re.search(r"\bextrajudicial\b", t, re.I) else (
        "banco" if re.search(r"extrajudicial|aliena[çc][ãa]o fiduci", t, re.I) else None)
    addr = re.search(r"((?:Rua|R\.|Avenida|Av\.|Alameda|Al\.|Praça)\s[^,\n]{3,60},\s*\d{1,5})", t)
    qts = re.search(r"(\d+)\s*(?:dormit[óo]rios?|quartos?|su[íi]tes?)", t, re.I)
    title = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    return {
        "rounds": uniq, "appraisal": to_float(ap.group(1)) if ap else None, "occupied": occ, "kind": kind,
        "address": addr.group(1) if addr else "", "bedrooms": int(qts.group(1)) if qts else None,
        "financing": True if re.search(r"aceita\s+financiamento|financiamento\s+banc[áa]rio", t, re.I) else None,
        "title": htmllib.unescape(TAG.sub("", title.group(1))).strip() if title else "",
    }


def fetch(http: Http, cfg: Config, known_ids: set[str], rejected: set[str], today: str | None = None) -> list[Listing]:
    from ..leilao import next_round
    today = today or datetime.now().strftime("%Y-%m-%d")
    wanted = wanted_slugs(cfg)
    base = LIST_URL.format(uf=cfg.state.lower(), city=slug(cfg.city))
    lots: dict[str, tuple[str, str]] = {}
    for page in range(1, MAX_PAGES + 1):
        r = http.get(base + (f"?pagina={page}" if page > 1 else ""))
        dump_debug(f"mega-lista-{page}.html [HTTP {r.status_code}]", r.text)
        if r.status_code != 200:
            if page == 1:
                raise RuntimeError(f"mega: HTTP {r.status_code}")
            break
        found = lot_links(r.text)
        if not set(found) - set(lots):
            break
        lots.update(found)
    out = []
    for code, (url, lot_slug) in lots.items():
        nb = neighborhood_of(lot_slug, wanted)
        if not nb or code in rejected:
            continue
        am = re.search(r"-(\d+)-m2-", f"-{lot_slug}-")
        area = float(am.group(1)) if am else None
        key = f"mega:leilao:{code}"
        try:
            r = http.get(url)
            dump_debug(f"mega-lote-{code}.html", r.text)
            if r.status_code != 200:
                continue
            d = parse_detail(r.text)
        except Exception:
            if key in known_ids:   # mantém o lote ativo mesmo sem conseguir reler a página
                out.append(Listing(source="mega", id=code, url=url, business="leilao", neighborhood=nb, area=area))
            continue
        nr = next_round(d["rounds"], today)
        out.append(Listing(
            source="mega", id=code, url=url, business="leilao", title=d["title"] or lot_slug.replace("-", " "),
            neighborhood=nb, address=d["address"], area=area, price=nr["bid"] if nr else None, bedrooms=d["bedrooms"],
            appraisal=d["appraisal"], rounds=d["rounds"] or None, auction_kind=d["kind"], occupied=d["occupied"],
            financing=d["financing"], extra=" · ".join(f"{x['n']}º leilão {x['date'] or ''}" for x in d["rounds"]),
        ))
    return out
