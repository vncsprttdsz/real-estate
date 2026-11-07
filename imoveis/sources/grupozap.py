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
# Janela lida por bairro e negócio (mais recentes primeiro). O Viva Real repete o estoque do ZAP,
# então basta olhar as novidades dele.
MAX_RESULTS = {"zap": 1200, "vivareal": 240}


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
        neighborhood=(addr.get("neighborhood") or "").strip(),
        address=street,
        area=area,
        price=price,
        total_monthly=total,
        condo=condo,
        bedrooms=first_int(lst.get("bedrooms")),
        parking=first_int(lst.get("parkingSpaces")),
        floor=(first_int(lst.get("unitFloor")) or None),
        **_coords(addr),
    )


def _coords(addr: dict) -> dict:
    """address.point: lat/lon exatos (Google) ou approximateLat/Lon quando o anunciante esconde o endereço."""
    p = addr.get("point") or {}
    lat, lon = to_float(p.get("lat")), to_float(p.get("lon"))
    if lat and lon:
        return {"lat": lat, "lon": lon, "geo_approx": bool(p.get("aproximated"))}
    lat, lon = to_float(p.get("approximateLat")), to_float(p.get("approximateLon"))
    return {"lat": lat, "lon": lon, "geo_approx": True} if lat and lon else {}


# Do mais filtrado ao mais simples: a API recusa (HTTP 400) alguns parâmetros,
# então tentamos em ordem e guardamos o primeiro conjunto que funcionou.
PARAM_LADDER = [
    ("full", {"listingType": "USED", "unitTypes": "APARTMENT", "usableAreasMin": True,
              "addressZone": "Zona Sul", "addressLocationId": True}),
    ("area+tipo", {"listingType": "USED", "unitTypes": "APARTMENT", "usableAreasMin": True}),
    ("area", {"usableAreasMin": True}),
    ("minimo", {}),
]
_working_rung: dict[tuple, int] = {}   # (fonte, bairro) -> degrau que funcionou
# A API limita o tamanho da página ("Size is above acceptable limit"); reduz até aceitar.
PAGE_SIZES = [24, 15, 10, 5]
_page_size: dict[str, int] = {}
# Mais recentes primeiro: a busca devolve milhares de resultados e só lemos uma janela,
# então a ordem precisa ser estável para a janela conter sempre as novidades.
SORTS = ["createdAt DESC", "updatedAt DESC", None]
_sort: dict[str, int] = {}


def _ascii(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _api_params(cfg: Config, business: str, neighborhood: str, offset: int, rung: int, size: int,
                sort: str | None = None) -> dict:
    params = {
        "business": BUSINESS[business],
        "categoryPage": "RESULT",
        "addressCity": cfg.city,
        "addressState": "São Paulo",
        "addressNeighborhood": neighborhood,
        "from": offset,
        "size": size,
    }
    if sort:
        params["sort"] = sort
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
    nb = params_for(0, PAGE_SIZES[0], None)["addressNeighborhood"]
    key = (source, nb)
    rung = _working_rung.get(key, 0)
    size_i = PAGE_SIZES.index(_page_size.get(source, PAGE_SIZES[0]))
    sort_i = _sort.get(source, 0)
    while rung < len(PARAM_LADDER):
        params = params_for(rung, PAGE_SIZES[size_i], SORTS[sort_i])
        r = http.get(site["api"], params=params, headers=headers)
        dump_debug(f"{source}-{params['business']}-{slug(params['addressNeighborhood'])}-{params['from']}.json "
                   f"[HTTP {r.status_code}] rung={PARAM_LADDER[rung][0]} size={params['size']} "
                   f"sort={params.get('sort')}", r.text)
        if r.status_code == 200:
            data = r.json()
            total = (data.get("search") or {}).get("totalCount") or 0
            # Zona/locationId fixos podem não bater com a classificação do portal para o bairro:
            # 0 resultados na 1ª página com esses filtros -> tenta sem eles antes de aceitar o vazio.
            if not total and params["from"] == 0 and "addressZone" in params and rung + 1 < len(PARAM_LADDER):
                rung += 1
                continue
            _working_rung[key], _page_size[source], _sort[source] = rung, PAGE_SIZES[size_i], sort_i
            return data
        last = f"{source} API HTTP {r.status_code} ({PARAM_LADDER[rung][0]}, size={params['size']}): {r.text[:200]}"
        if r.status_code != 400:
            break
        if "size" in r.text.lower() and size_i + 1 < len(PAGE_SIZES):
            size_i += 1          # página grande demais: tenta menor com os mesmos filtros
        elif sort_i + 1 < len(SORTS):
            sort_i += 1          # ordenação recusada: tenta a próxima (ou nenhuma)
        else:
            rung, sort_i = rung + 1, 0   # outro parâmetro recusado: simplifica a consulta
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
    while offset < MAX_RESULTS.get(source, 600):
        data = _get_page(http, source,
                         lambda rung, size, sort: _api_params(cfg, business, neighborhood, offset, rung, size, sort),
                         headers)
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
        for nb in cfg.query_neighborhoods:
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
