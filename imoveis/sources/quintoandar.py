"""QuintoAndar (aluguel e venda)."""
from __future__ import annotations

import os
import sys

from ..common import Config, Listing, dump_debug, iter_dicts, next_data, norm, slug, to_float
from ..http import Http

API = "https://apigw.prod.quintoandar.com.br/house-listing-search/v2/search/list"
SITE = "https://www.quintoandar.com.br"
BUSINESS = {"aluguel": "RENT", "venda": "SALE"}
SITE_PATH = {"aluguel": "alugar", "venda": "comprar"}
APARTMENT_TYPES = {"apartamento", "cobertura", "flat", "kitnet", "studio", "apartment"}
PAGE_SIZE = 36

# Um retângulo justo por bairro: a API não pagina além de ~1.000 resultados e devolve
# também imóveis vizinhos ("Outra localização"); o filtro fino é pelo nome do bairro.
VIEWPORTS = {
    "itaim bibi": {"north": -23.574, "south": -23.593, "east": -46.667, "west": -46.692},
    "vila nova conceicao": {"north": -23.583, "south": -23.598, "east": -46.660, "west": -46.678},
}
DEFAULT_VIEWPORT = {"north": -23.565, "south": -23.605, "east": -46.655, "west": -46.700}

FIELDS = [
    "id", "area", "rentPrice", "totalCost", "salePrice", "condoIptu", "address", "regionName",
    "type", "forRent", "forSale", "bedrooms", "parkingSpaces", "neighbourhood",
]


def parse_house(h: dict, business: str) -> Listing | None:
    hid = h.get("id")
    if hid is None or h.get("area") is None:
        return None
    htype = norm(h.get("type"))
    if htype and htype not in APARTMENT_TYPES:
        return None
    if business == "aluguel" and h.get("forRent") is False:
        return None
    if business == "venda" and h.get("forSale") is False:
        return None

    if business == "aluguel":
        price = to_float(h.get("rentPrice") or h.get("rent"))
        total = to_float(h.get("totalCost"))
        fees = to_float(h.get("condoIptu") or h.get("iptuPlusCondominium"))
        if price is None and total and fees is not None and fees < total:
            price = total - fees  # aluguel puro = total − (condomínio + IPTU)
    else:
        price = to_float(h.get("salePrice")) or None  # 0 = não está à venda
        total = None
    if price is None and total is None:
        return None

    addr = h.get("address")
    if isinstance(addr, dict):
        addr = addr.get("address") or " ".join(str(x) for x in (addr.get("street"), addr.get("number")) if x)
    neighborhood = h.get("neighbourhood") or h.get("neighborhood") or h.get("regionName") or ""

    path = f"/imovel/{hid}" + ("/comprar" if business == "venda" else "")
    return Listing(
        source="quintoandar",
        id=str(hid),
        url=SITE + path,
        business=business,
        title=f"{(h.get('type') or 'Apartamento').title()} em {neighborhood}",
        neighborhood=neighborhood,
        address=addr or "",
        area=to_float(h.get("area")),
        price=price,
        total_monthly=total,
        condo=to_float(h.get("condoPrice") or h.get("condoIptu") or h.get("iptuPlusCondominium")),
        bedrooms=h.get("bedrooms"),
        parking=h.get("parkingSpots") or h.get("parkingSpaces"),
    )


def _body(cfg: Config, business: str, offset: int, page_size: int, neighborhood: str) -> dict:
    """Corpo enxuto: a API recusa ("Invalid parameter type") alguns filtros opcionais vazios."""
    vp = VIEWPORTS.get(norm(neighborhood), DEFAULT_VIEWPORT)
    lat = (vp["north"] + vp["south"]) / 2
    lng = (vp["east"] + vp["west"]) / 2
    return {
        "context": {"mapShowing": True, "listShowing": True, "numPhotos": 1, "isSSR": False},
        "filters": {
            "businessContext": BUSINESS[business],
            "location": {"coordinate": {"lat": lat, "lng": lng}, "viewport": vp,
                         "neighborhoods": [], "countryCode": "BR"},
            "houseSpecs": {"area": {"range": {"min": int(cfg.min_area)}}, "houseTypes": ["APARTMENT"]},
        },
        "sorting": {"criteria": "MOST_RECENT", "order": "DESC"},
        "pagination": {"pageSize": page_size, "offset": offset},
        "slug": f"{slug(neighborhood)}-{slug(cfg.city)}-{cfg.state.lower()}-brasil",
        "fields": FIELDS,
    }


def _fetch_api(http: Http, cfg: Config, business: str) -> list[dict]:
    headers = {"Origin": SITE, "Referer": SITE + "/", "Content-Type": "application/json"}
    houses = []
    for nb in cfg.neighborhoods:
        offset = 0
        while offset < 1500:
            r = http.post(API, json=_body(cfg, business, offset, PAGE_SIZE, nb), headers=headers)
            dump_debug(f"quintoandar-{business}-{slug(nb)}-{offset}.json [HTTP {r.status_code}]", r.text)
            if r.status_code != 200:
                if offset and "pagination" in r.text.lower():
                    break  # limite de profundidade da API: fica com o que já veio (mais recentes primeiro)
                raise RuntimeError(f"quintoandar API HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            hits = (data.get("hits") or {}).get("hits") or []
            page = [h.get("_source", h) for h in hits]
            houses.extend(page)
            total = (data.get("hits") or {}).get("total") or {}
            total = total.get("value", 0) if isinstance(total, dict) else total
            if os.environ.get("DEBUG"):
                print(f"[debug] quintoandar {business} {nb} offset={offset} page={len(page)} total={total}",
                      file=sys.stderr)
            offset += PAGE_SIZE
            if not page or offset >= (total or 0):
                break
    return houses


def _fetch_html(http: Http, cfg: Config, business: str) -> list[dict]:
    """Plano B: páginas de busca por bairro, lendo o JSON do Next.js."""
    houses = []
    for nb in cfg.neighborhoods:
        url = (f"{SITE}/{SITE_PATH[business]}/imovel/{slug(nb)}-{slug(cfg.city)}-{cfg.state.lower()}-brasil"
               f"/apartamento/de-{int(cfg.min_area)}-a-1000-m2")
        r = http.get(url)
        dump_debug(f"quintoandar-{business}-{slug(nb)}.html", r.text)
        if r.status_code != 200:
            raise RuntimeError(f"quintoandar página HTTP {r.status_code}")
        data = next_data(r.text)
        if data is None:
            raise RuntimeError("quintoandar: JSON da página não encontrado")
        houses += [d for d in iter_dicts(data) if "id" in d and "area" in d]
    return houses


def fetch(http: Http, cfg: Config) -> list[Listing]:
    out: dict[str, Listing] = {}
    errors = []
    for business in cfg.businesses:
        if business not in BUSINESS:
            continue
        houses = None
        for method in (_fetch_api, _fetch_html):
            try:
                houses = method(http, cfg, business)
                break
            except Exception as e:
                errors.append(f"{method.__name__}({business}): {e}")
        for h in houses or []:
            l = parse_house(h, business)
            if l:
                out[l.key] = l
    if not out and errors:
        raise RuntimeError("; ".join(errors))
    return list(out.values())
