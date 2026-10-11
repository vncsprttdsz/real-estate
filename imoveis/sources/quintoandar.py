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
PAGE_SIZE = 60

# Retângulo que cobre Itaim Bibi + Vila Nova Conceição (com folga).
# O filtro fino por bairro é feito depois, pelo nome do bairro de cada anúncio.
VIEWPORT = {"north": -23.565, "south": -23.605, "east": -46.655, "west": -46.700}

FIELDS = [
    "id", "coverImage", "rent", "totalCost", "salePrice", "iptuPlusCondominium",
    "area", "imageList", "address", "regionName", "city", "type", "forRent",
    "forSale", "bedrooms", "parkingSpots", "neighbourhood", "condoPrice", "iptu",
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
        price = to_float(h.get("rent") or h.get("rentPrice"))
        total = to_float(h.get("totalCost"))
        fees = to_float(h.get("iptuPlusCondominium"))
        if price is None and total and fees is not None and fees < total:
            price = total - fees  # aluguel puro = total − (condomínio + IPTU)
    else:
        price = to_float(h.get("salePrice"))
        total = None
    if price is None and total is None:
        return None

    addr = h.get("address")
    if isinstance(addr, dict):
        addr = " ".join(str(x) for x in (addr.get("street"), addr.get("number")) if x)
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
        condo=to_float(h.get("condoPrice") or h.get("iptuPlusCondominium")),
        bedrooms=h.get("bedrooms"),
        parking=h.get("parkingSpots"),
    )


def _body(cfg: Config, business: str, offset: int) -> dict:
    lat = (VIEWPORT["north"] + VIEWPORT["south"]) / 2
    lng = (VIEWPORT["east"] + VIEWPORT["west"]) / 2
    return {
        "context": {"mapShowing": True, "listShowing": True, "numPhotos": 1, "isSSR": False},
        "filters": {
            "businessContext": BUSINESS[business],
            "blocklist": [],
            "selectedHouses": [],
            "location": {
                "coordinate": {"lat": lat, "lng": lng},
                "viewport": VIEWPORT,
                "neighborhoods": [],
                "countryCode": "BR",
            },
            "priceRange": [],
            "specialConditions": [],
            "excludedSpecialConditions": [],
            "houseSpecs": {
                "area": {"range": {"min": int(cfg.min_area)}},
                "houseTypes": ["APARTMENT", "PENTHOUSE", "FLAT"],
                "amenities": [],
                "installations": [],
                "bathrooms": {"range": {}},
                "bedrooms": {"range": {}},
                "parkingSpace": {"range": {}},
                "suites": {"range": {}},
            },
            "availability": "ANY",
            "occupancy": "ANY",
            "partnerIds": [],
            "categories": [],
        },
        "sorting": {"criteria": "MOST_RECENT", "order": "DESC"},
        "pagination": {"pageSize": PAGE_SIZE, "offset": offset},
        "slug": "itaim-bibi-sao-paulo-sp-brasil",
        "fields": FIELDS,
    }


def _fetch_api(http: Http, cfg: Config, business: str) -> list[dict]:
    headers = {"Origin": SITE, "Referer": SITE + "/", "Content-Type": "application/json"}
    houses, offset = [], 0
    while offset < 1000:
        r = http.post(API, json=_body(cfg, business, offset), headers=headers)
        dump_debug(f"quintoandar-{business}-{offset}.json [HTTP {r.status_code}]", r.text)
        if r.status_code != 200:
            raise RuntimeError(f"quintoandar API HTTP {r.status_code}")
        data = r.json()
        hits = (data.get("hits") or {}).get("hits") or []
        page = [h.get("_source", h) for h in hits]
        houses.extend(page)
        total = (data.get("hits") or {}).get("total") or {}
        total = total.get("value", 0) if isinstance(total, dict) else total
        if os.environ.get("DEBUG"):
            print(f"[debug] quintoandar {business} offset={offset} page={len(page)} total={total} "
                  f"keys={list(data)[:8]}", file=sys.stderr)
        offset += PAGE_SIZE
        if not page or offset >= (total or 0):
            break
    return houses


def _fetch_html(http: Http, cfg: Config, business: str) -> list[dict]:
    """Plano B: páginas de busca por bairro, lendo o JSON do Next.js."""
    houses = []
    for nb in cfg.neighborhoods:
        url = f"{SITE}/{SITE_PATH[business]}/imovel/{slug(nb)}-{slug(cfg.city)}-{cfg.state.lower()}-brasil/apartamento"
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
