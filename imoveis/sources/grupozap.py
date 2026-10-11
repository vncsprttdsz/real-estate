"""ZAP Imóveis e Viva Real (mesma plataforma do Grupo OLX, API "glue-api")."""
from __future__ import annotations

import json

from ..common import Config, Listing, dump_debug, iter_dicts, next_data, norm, slug, to_float
from ..http import Http

SITES = {
    "zap": {
        "api": "https://glue-api.zapimoveis.com.br/v2/listings",
        "site": "https://www.zapimoveis.com.br",
        "domain": ".zapimoveis.com.br",
        "portal": "ZAP",
    },
    "vivareal": {
        "api": "https://glue-api.vivareal.com/v2/listings",
        "site": "https://www.vivareal.com.br",
        "domain": ".vivareal.com.br",
        "portal": "VIVAREAL",
    },
}

BUSINESS = {"venda": "SALE", "aluguel": "RENTAL"}
SITE_PATH = {"venda": "venda", "aluguel": "aluguel"}
APARTMENT_TYPES = {"APARTMENT", "PENTHOUSE", "FLAT", "DUPLEX", "TRIPLEX", "LOFT", "STUDIO"}
PAGE_SIZE = 100
MAX_RESULTS = 1000  # a API não pagina além disso


def parse_listing(item: dict, source: str, business: str) -> Listing | None:
    """Converte um item de `search.result.listings[]` em Listing."""
    lst = item.get("listing", item)
    lid = lst.get("id") or lst.get("externalId")
    if not lid:
        return None

    unit_types = set(lst.get("unitTypes") or [])
    if unit_types and not unit_types & APARTMENT_TYPES:
        return None

    areas = [to_float(a) for a in (lst.get("usableAreas") or []) if to_float(a)]
    if not areas:
        areas = [to_float(a) for a in (lst.get("totalAreas") or []) if to_float(a)]
    area = max(areas) if areas else None

    want = BUSINESS[business]
    pricing = next(
        (p for p in lst.get("pricingInfos") or [] if p.get("businessType") == want), None
    )
    if pricing is None:
        return None  # anúncio não está disponível para este tipo de negócio
    price = to_float(pricing.get("price"))
    condo = to_float(pricing.get("monthlyCondoFee"))
    iptu = to_float(pricing.get("yearlyIptu"))
    total = None
    if business == "aluguel":
        rental = pricing.get("rentalInfo") or {}
        total = to_float(rental.get("monthlyRentalTotalPrice"))
        if not total and price:
            total = price + (condo or 0) + ((iptu or 0) / 12)

    addr = lst.get("address") or {}
    street = " ".join(str(x) for x in (addr.get("street"), addr.get("streetNumber")) if x)

    link = item.get("link") if isinstance(item.get("link"), dict) else lst.get("link")
    href = link.get("href") if isinstance(link, dict) else None
    base = SITES[source]["site"]
    if href:
        url = href if href.startswith("http") else base + href
    else:
        url = f"{base}/imovel/id-{lid}/"

    def first_int(v):
        if isinstance(v, list):
            v = v[0] if v else None
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return Listing(
        source=source,
        id=str(lid),
        url=url,
        business=business,
        title=lst.get("title") or "",
        neighborhood=addr.get("neighborhood") or "",
        address=street,
        area=area,
        price=price,
        total_monthly=total,
        condo=condo,
        bedrooms=first_int(lst.get("bedrooms")),
        parking=first_int(lst.get("parkingSpaces")),
    )


def _api_params(cfg: Config, business: str, neighborhood: str, offset: int) -> dict:
    return {
        "business": BUSINESS[business],
        "categoryPage": "RESULT",
        "listingType": "USED",
        "unitTypes": "APARTMENT",
        "unitTypesV3": "APARTMENT",
        "usageTypes": "RESIDENTIAL",
        "usableAreasMin": int(cfg.min_area),
        "addressCountry": "Brasil",
        "addressState": "São Paulo",
        "addressCity": cfg.city,
        "addressNeighborhood": neighborhood,
        "addressType": "neighborhood",
        "size": PAGE_SIZE,
        "from": offset,
        "sort": "updatedAt DESC",
        "includeFields": (
            "search(result(listings(listing(id,title,unitTypes,usableAreas,totalAreas,"
            "pricingInfos,address,bedrooms,parkingSpaces,createdAt,updatedAt),link)),totalCount)"
        ),
    }


def _fetch_api(http: Http, cfg: Config, source: str, business: str, neighborhood: str) -> list[dict]:
    site = SITES[source]
    headers = {
        "x-domain": site["domain"],
        "Origin": site["site"],
        "Referer": site["site"] + "/",
        "Accept": "application/json",
    }
    items, offset = [], 0
    while offset < MAX_RESULTS:
        r = http.get(site["api"], params=_api_params(cfg, business, neighborhood, offset), headers=headers)
        dump_debug(f"{source}-{business}-{slug(neighborhood)}-{offset}.json", r.text)
        if r.status_code != 200:
            raise RuntimeError(f"{source} API HTTP {r.status_code}")
        data = r.json()
        result = (data.get("search") or {}).get("result") or {}
        page = result.get("listings") or []
        items.extend(page)
        total = (data.get("search") or {}).get("totalCount") or 0
        offset += PAGE_SIZE
        if not page or offset >= total:
            break
    return items


def _fetch_html(http: Http, cfg: Config, source: str, business: str, neighborhood: str) -> list[dict]:
    """Plano B: página de resultados do site, extraindo o JSON embutido (Next.js)."""
    site = SITES[source]
    url = (
        f"{site['site']}/{SITE_PATH[business]}/apartamentos/"
        f"{cfg.state.lower()}+{slug(cfg.city)}++{slug(neighborhood)}/"
    )
    r = http.get(url, params={"areaMinima": int(cfg.min_area)})
    dump_debug(f"{source}-{business}-{slug(neighborhood)}.html", r.text)
    if r.status_code != 200:
        raise RuntimeError(f"{source} página HTTP {r.status_code}")
    data = next_data(r.text)
    if data is None:
        raise RuntimeError(f"{source}: JSON da página não encontrado")
    return [d for d in iter_dicts(data) if isinstance(d.get("listing"), dict)]


def fetch(http: Http, cfg: Config, source: str) -> list[Listing]:
    out: dict[str, Listing] = {}
    errors = []
    for business in cfg.businesses:
        if business not in BUSINESS:
            continue
        for nb in cfg.neighborhoods:
            items = None
            for method in (_fetch_api, _fetch_html):
                try:
                    items = method(http, cfg, source, business, nb)
                    break
                except Exception as e:
                    errors.append(f"{method.__name__}({business}, {nb}): {e}")
            for it in items or []:
                l = parse_listing(it, source, business)
                if l:
                    out[l.key] = l
    if not out and errors:
        raise RuntimeError("; ".join(errors))
    return list(out.values())
