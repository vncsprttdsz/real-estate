"""Portal Zuk (leilões de imóveis).

1. Lista os apartamentos da capital: a página traz 30 lotes e o resto vem pelo botão "Carregar mais", que chama a
   rota "carrega.mais" (POST com o token da página). Sondagem de 15/11: ?page=N devolve sempre os mesmos 30.
2. Mantém só os links cujo caminho contém um dos bairros procurados ou extras da área de interesse
   (as URLs seguem /imovel/sp/sao-paulo/<bairro>/<endereço>/<id>).
3. Abre a página de cada lote para ler tipo, área, lances e datas de 1º/2º leilão, ocupação e se é judicial.
"""
from __future__ import annotations

import html as htmllib
import re
from datetime import datetime

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
ROUND_RE = re.compile(r"([12])º\s*Leil[ãa]o:?\s*(\d{2}/\d{2}/\d{2,4})[^R]{0,40}R\$\s*([\d.]+,\d{2})", re.I)
APPRAISAL_RE = re.compile(r"(?:valor\s+de\s+)?avalia[çc][ãa]o[^R\d]{0,40}R\$\s*([\d.]+,\d{2})", re.I)


def _iso(d: str) -> str | None:
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None




def capital_url(cfg: Config, kind: str = "todos-imoveis") -> str:
    """Listagem da capital (o site não filtra por bairro na URL); o bairro sai do link de cada imóvel."""
    return f"{SITE}/leilao-de-imoveis/c/{kind}/{cfg.state.lower()}/capital/{slug(cfg.city)}"


MAX_MORE = 40   # rodadas de "Carregar mais" (20 lotes cada)


def load_all(http: Http, url: str) -> tuple[str, int]:
    """HTML da listagem com todos os lotes (página + respostas do "Carregar mais") e quantas rodadas vieram."""
    r = http.get(url)
    if r.status_code != 200:
        raise RuntimeError(f"zuk: HTTP {r.status_code} em {url}")
    html = r.text
    token = re.search(r'name="_token"\s+value="([^"]+)"', html) or re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    uri = re.search(r'"carrega\.mais"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
    order = re.search(r'<select[^>]*id="ordenar".*?<option[^>]*value="([^"]*)"[^>]*selected', html, re.S)
    if not token:
        return html, 0
    post_url = f"{SITE}/{uri.group(1).lstrip('/')}" if uri else f"{SITE}/carrega-mais"
    seen = extract_all_links(html)
    rounds = 0
    for _ in range(MAX_MORE):
        cards = len(re.findall(r'class="[^"]*card_lotes_div', html))
        zuk_cards = len(re.findall(r'data-parceiro="0"', html))
        try:
            rr = http.post(post_url, data={"limit": cards, "count_imovel_zuk": zuk_cards, "path": url,
                                           "order": order.group(1) if order else "", "div_parceiro_count": 0,
                                           "_token": token.group(1)},
                           headers={"X-Requested-With": "XMLHttpRequest", "X-CSRF-TOKEN": token.group(1), "Referer": url})
        except Exception:
            break
        found = extract_all_links(rr.text) if rr.status_code == 200 else set()
        dump_debug(f"zuk-mais-{rounds}.html [HTTP {rr.status_code}] links={len(found)}", rr.text)
        if not found - seen:
            break
        seen |= found
        html += rr.text
        rounds += 1
    return html, rounds


def extract_links(text: str, cfg: Config) -> dict[str, str]:
    """Devolve {id: url} dos imóveis nos bairros procurados."""
    wanted = {slug(n) for n in list(cfg.neighborhoods) + list(cfg.area_extra_neighborhoods)}
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

    rounds = []
    for n, d, v in ROUND_RE.findall(text):
        if int(n) not in {r["n"] for r in rounds}:
            rounds.append({"n": int(n), "date": _iso(d), "bid": to_float(v)})
    from ..leilao import next_round
    nr = next_round(rounds, datetime.now().strftime("%Y-%m-%d"))
    if nr:
        price = nr["bid"]
    ap = APPRAISAL_RE.search(text)
    occ = None
    if re.search(r"im[óo]vel\s+desocupado|situa[çc][ãa]o:?\s*desocupado", text, re.I):
        occ = False
    elif re.search(r"im[óo]vel\s+ocupado|situa[çc][ãa]o:?\s*ocupado|encontra-se\s+ocupado", text, re.I):
        occ = True
    kind = "judicial" if re.search(r"Processo:?\s*\d{7}-\d{2}", text) else (
        "banco" if re.search(r"aliena[çc][ãa]o fiduci|extrajudicial|banco|caixa econ", text[:6000], re.I) else None)
    addr = re.search(r"((?:Rua|Avenida|Av\.|Alameda|Al\.|Praça)\s[^,]{3,60},\s*\d{1,5})", text)
    qts = re.search(r"(\d+)\s*(?:dormit[óo]rios?|quartos?|su[íi]tes?)", text, re.I)
    dates = DATE_RE.findall(text)
    extra = (" · ".join(f"{r['n']}º leilão {r['date'] or ''}" for r in rounds) if rounds else
             ("Datas: " + ", ".join(" ".join(x for x in d if x) for d in dates[:2]) if dates else ""))

    nb_name = next((n for n in list(cfg.neighborhoods) + list(cfg.area_extra_neighborhoods) if slug(n) == neighborhood_slug),
                   neighborhood_slug)
    return Listing(
        source="zuk",
        id=lid,
        url=url,
        business="leilao",
        title=title or "Imóvel em leilão",
        neighborhood=nb_name,
        address=addr.group(1) if addr else "",
        area=area,
        price=price,
        bedrooms=int(qts.group(1)) if qts else None,
        appraisal=to_float(ap.group(1)) if ap else None,
        rounds=rounds or None,
        auction_kind=kind,
        occupied=occ,
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
    ok_pages = 0
    errors = []
    base = capital_url(cfg)

    try:
        apts = capital_url(cfg, "apartamentos")
        html, more = load_all(http, apts)
        ok_pages += 1
        candidates.update(extract_links(html, cfg))
        print(f"zuk: {len(extract_all_links(html))} lotes de apartamento na capital ({more} rodadas de 'Carregar mais')")
    except Exception as e:
        errors.append(f"{base}: {e}")

    # Sitemap (inclusive índices de sitemap), limitado para não demorar demais.
    queue, seen_maps = _sitemaps(http), set()
    while queue and len(seen_maps) < 30:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        try:
            r = http.get(sm, retries=1)
            dump_debug(f"zuk-sitemap-{slug(sm[-40:])}.xml [HTTP {r.status_code}] links={len(LINK_RE.findall(r.text))}", r.text)
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
        if lid in rejected:
            continue
        nb_slug = url.split("/")[6] if len(url.split("/")) > 6 else ""
        # Relê sempre: poucos lotes nos bairros procurados, e lance/datas mudam (1º → 2º leilão).
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
