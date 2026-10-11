"""ZAP Imóveis e Viva Real (mesma plataforma do Grupo OLX, API "glue-api")."""
from __future__ import annotations

import unicodedata

from ..common import Config, Listing, dump_debug, slug, to_float
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
MAX_RESULTS = 600  # por bairro e negócio; com filtro de área/tipo raramente passa disso


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


# Do mais filtrado ao mais simples: a API recusa (HTTP 400) alguns parâmetros,
# então tentamos em ordem e guardamos o primeiro conjunto que funcionou.
PARAM_LADDER = [
    ("full", {"listingType": "USED", "unitTypes": "APARTMENT", "usableAreasMin": True,
              "addressZone": "Zona Sul", "addressLocationId": True}),
    ("area+tipo", {"listingType": "USED", "unitTypes": "APARTMENT", "usableAreasMin": True}),
    ("area", {"usableAreasMin": True}),
    ("minimo", {}),
]
_working_rung: dict[str, int] = {}
# A API limita o tamanho da página ("Size is above acceptable limit"); reduz até aceitar.
PAGE_SIZES = [24, 15, 10, 5]
_page_size: dict[str, int] = {}


def _ascii(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _api_params(cfg: Config, business: str, neighborhood: str, offset: int, rung: int, size: int) -> dict:
    params = {
        "business": BUSINESS[business],
        "categoryPage": "RESULT",
        "addressCity": cfg.city,
        "addressState": "São Paulo",
        "addressNeighborhood": neighborhood,
        "from": offset,
        "size": size,
    }
    for k, v in PARAM_LADDER[rung][1].items():
        if k == "usableAreasMin":
            params[k] = int(cfg.min_area)
        elif k == "addressLocationId":
            params[k] = f"BR>Sao Paulo>NULL>Sao Paulo>Zona Sul>{_ascii(neighborhood)}"
        else:
            params[k] = v
    return params


def _get_page(http: Http, source: str, params_for, headers) -> dict:
    site = SITES[source]
    last = None
    rung = _working_rung.get(source, 0)
    size_i = PAGE_SIZES.index(_page_size.get(source, PAGE_SIZES[0]))
    while rung < len(PARAM_LADDER):
        params = params_for(rung, PAGE_SIZES[size_i])
        r = http.get(site["api"], params=params, headers=headers)
        dump_debug(f"{source}-{params['business']}-{slug(params['addressNeighborhood'])}-{params['from']}.json "
                   f"[HTTP {r.status_code}] rung={PARAM_LADDER[rung][0]} size={params['size']}", r.text)
        if r.status_code == 200:
            _working_rung[source], _page_size[source] = rung, PAGE_SIZES[size_i]
            return r.json()
        last = f"{source} API HTTP {r.status_code} ({PARAM_LADDER[rung][0]}, size={params['size']}): {r.text[:200]}"
        if r.status_code != 400:
            break
        if "size" in r.text.lower() and size_i + 1 < len(PAGE_SIZES):
            size_i += 1          # página grande demais: tenta menor com os mesmos filtros
        else:
            rung += 1            # outro parâmetro recusado: simplifica a consulta
    raise RuntimeError(last)


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
        data = _get_page(http, source,
                         lambda rung, size: _api_params(cfg, business, neighborhood, offset, rung, size), headers)
        search = data.get("search") or {}
        page = (search.get("result") or {}).get("listings") or []
        items.extend(page)
        total = search.get("totalCount") or 0
        offset += len(page)
        if not page or offset >= total:
            break
    return items


def fetch(http: Http, cfg: Config, source: str) -> list[Listing]:
    out: dict[str, Listing] = {}
    errors = []
    for business in cfg.businesses:
        if business not in BUSINESS:
            continue
        for nb in cfg.neighborhoods:
            items = None
            for method in (_fetch_api,):
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
