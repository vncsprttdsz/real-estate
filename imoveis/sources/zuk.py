"""Portal Zuk (leilões de imóveis).

Estratégia em camadas, porque o site não tem API pública:
1. Coleta links de imóveis em páginas de listagem (cidade e bairros) e no sitemap.
2. Mantém só os links cujo caminho contém um dos bairros procurados
   (as URLs seguem /imovel/sp/sao-paulo/<bairro>/<endereço>/<id>).
3. Abre a página de cada imóvel novo para ler tipo, área e lance.
"""
from __future__ import annotations

import html as htmllib
import re

from ..common import Config, Listing, dump_debug, norm, slug, to_float
from ..http import Http

SITE = "https://www.portalzuk.com.br"
LINK_RE = re.compile(
    r'(?:https?://www\.portalzuk\.com\.br)?(/imovel/([a-z]{2})/([a-z0-9-]+)/([a-z0-9-]+)/[^"\'\s<>#?]+)'
)
APARTMENT_RE = re.compile(r"\b(apartamento|cobertura|flat|duplex|triplex|studio|apto)\b", re.I)
NOT_APARTMENT_RE = re.compile(r"\b(casa|terreno|galp[aã]o|loja|sala comercial|pr[eé]dio|vaga de garagem|lote)\b", re.I)
AREA_LABEL_RE = re.compile(
    r"[ÁáAa]rea\s*(?:[úu]til|privativa|constru[ií]da|total)?[^0-9]{0,40}?([\d.]+(?:,\d+)?)\s*m(?:²|2)", re.I
)
AREA_ANY_RE = re.compile(r"([\d.]+(?:,\d+)?)\s*m(?:²|2)\b", re.I)
PRICE_RE = re.compile(r"(?:lance\s*m[ií]nimo|valor|lance)[^R]{0,60}R\$\s*([\d.]+(?:,\d{2})?)", re.I)
DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s*(?:às|-)?\s*(\d{1,2}[:h]\d{2}))?")
TAG_RE = re.compile(r"<[^>]+>")


def listing_pages(cfg: Config) -> list[str]:
    """Listagem da capital (o site não filtra por bairro na URL); o bairro sai do link de cada imóvel."""
    st = cfg.state.lower()
    base = f"{SITE}/leilao-de-imoveis/c/todos-imoveis/{st}/capital/{slug(cfg.city)}"
    return [base] + [f"{base}?pagina={n}" for n in range(2, 8)]


def extract_links(text: str, cfg: Config) -> dict[str, str]:
    """Devolve {id: url} dos imóveis nos bairros procurados."""
    wanted = {slug(n) for n in cfg.neighborhoods}
    city = slug(cfg.city)
    found = {}
    for m in LINK_RE.finditer(text):
        path, _st, c, nb = m.group(1), m.group(2), m.group(3), m.group(4)
        if c != city or nb not in wanted:
            continue
        path = path.rstrip("/")
        lid = path.rsplit("/", 1)[-1]
        found[lid] = SITE + path
    return found


def extract_all_links(text: str) -> set[str]:
    return {m.group(1) for m in LINK_RE.finditer(text)}


def page_text(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", htmllib.unescape(TAG_RE.sub(" ", html)))


def parse_detail(html: str, url: str, lid: str, neighborhood_slug: str, cfg: Config) -> Listing | None:
    text = page_text(html)
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html) or re.search(
        r"<h1[^>]*>(.*?)</h1>", html, re.S
    ) or re.search(r"<title>(.*?)</title>", html, re.S)
    title = htmllib.unescape(TAG_RE.sub("", m.group(1))).strip() if m else ""

    head = f"{title} {text[:3000]}"
    if NOT_APARTMENT_RE.search(title) and not APARTMENT_RE.search(title):
        return None
    if "apartamento" in cfg.types and not APARTMENT_RE.search(head) and NOT_APARTMENT_RE.search(head):
        return None

    areas = [to_float(a) for a in AREA_LABEL_RE.findall(text)]
    if not areas:
        areas = [to_float(a) for a in AREA_ANY_RE.findall(text)]
    areas = [a for a in areas if a and 15 <= a <= 5000]
    # Área útil/privativa costuma ser a menor das áreas listadas.
    area = min(areas) if areas else None

    prices = [to_float(p) for p in PRICE_RE.findall(text)]
    prices = [p for p in prices if p and p > 10000]
    price = min(prices) if prices else None

    dates = DATE_RE.findall(text)
    extra = "Datas: " + ", ".join(" ".join(x for x in d if x) for d in dates[:2]) if dates else ""

    nb_name = next((n for n in cfg.neighborhoods if slug(n) == neighborhood_slug), neighborhood_slug)
    return Listing(
        source="zuk",
        id=lid,
        url=url,
        business="leilao",
        title=title or "Imóvel em leilão",
        neighborhood=nb_name,
        area=area,
        price=price,
        extra=extra,
    )


def _sitemaps(http: Http) -> list[str]:
    urls = []
    try:
        r = http.get(f"{SITE}/robots.txt", retries=1)
        urls = re.findall(r"(?im)^sitemap:\s*(\S+)", r.text)
    except Exception:
        pass
    return urls or [f"{SITE}/sitemap.xml"]


def fetch(http: Http, cfg: Config, known_ids: set[str], rejected: set[str]) -> list[Listing]:
    """`rejected` é atualizado com ids descartados (ex.: casas), para não reabrir sempre."""
    candidates: dict[str, str] = {}
    seen_links: set[str] = set()
    new_on_last_page = True
    ok_pages = 0
    errors = []
    for url in listing_pages(cfg):
        try:
            if "pagina=" in url and not new_on_last_page:
                break
            r = http.get(url)
            dump_debug(f"zuk-{slug(url[-60:])}.html [HTTP {r.status_code}] links={len(LINK_RE.findall(r.text))}", r.text)
            if r.status_code == 200:
                ok_pages += 1
                found = extract_all_links(r.text)
                new_on_last_page = bool(found - seen_links)
                seen_links |= found
                candidates.update(extract_links(r.text, cfg))
            elif r.status_code != 404:
                errors.append(f"{url}: HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"{url}: {e}")

    # Sitemap (inclusive índices de sitemap), limitado para não demorar demais.
    queue, seen_maps = _sitemaps(http), set()
    while queue and len(seen_maps) < 30:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        try:
            r = http.get(sm, retries=1)
            if r.status_code != 200:
                continue
            ok_pages += 1
            body = r.text
            candidates.update(extract_links(body, cfg))
            for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", body):
                if loc.endswith(".xml") or "sitemap" in loc.lower() and "/imovel/" not in loc:
                    queue.append(loc)
        except Exception:
            continue

    if ok_pages == 0:
        raise RuntimeError("zuk: nenhuma página acessível; " + "; ".join(errors))

    results = []
    for lid, url in candidates.items():
        key = f"zuk:leilao:{lid}"
        if lid in rejected:
            continue
        nb_slug = url.split("/")[6] if len(url.split("/")) > 6 else ""
        if key in known_ids:
            # Já analisado antes: não precisa abrir de novo.
            results.append(Listing(source="zuk", id=lid, url=url, business="leilao",
                                   neighborhood=next((n for n in cfg.neighborhoods if slug(n) == nb_slug), nb_slug)))
            continue
        try:
            r = http.get(url)
            dump_debug(f"zuk-detail-{lid}.html", r.text)
            if r.status_code != 200:
                continue
            l = parse_detail(r.text, url, lid, nb_slug, cfg)
            if l:
                results.append(l)
            else:
                rejected.add(lid)
        except Exception:
            continue
    return results
