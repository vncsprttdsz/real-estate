"""Sondagem dos sites para ajustar os coletores. Imprime trechos no log; não altera estado.

Uso: python -m scripts.probe   (roda pelo workflow 'Sondagem dos sites')
"""
import json
import re

from imoveis.common import iter_dicts, next_data
from imoveis.http import Http

http = Http(0.8, 1.5)


def p(*a):
    print(" ".join(str(x) for x in a), flush=True)


def get(url, **kw):
    try:
        r = http.get(url, retries=1, timeout=25, **kw)
        return r.status_code, r.text
    except Exception as e:
        return None, str(e)


# ---------- Portal Zuk: descobrir o formato de URL por bairro ----------
p("=" * 30, "ZUK")
st, body = get("https://www.portalzuk.com.br/leilao-de-imoveis")
links = sorted({h for h in re.findall(r'href=["\']([^"\']+)["\']', body) if "leilao-de-imoveis/" in h})
p(f"[zuk raiz] HTTP {st} links de busca: {len(links)}")
for h in links[:40]:
    p("   busca:", h)
for sn in re.findall(r"(?i)[^\n]{0,120}(?:bairro|regiao)[^\n]{0,160}", body)[:8]:
    p("   bairro-js:", re.sub(r"\s+", " ", sn)[:280])
for url in (
    "https://www.portalzuk.com.br/leilao-de-imoveis/c/todos-imoveis/sp",
    "https://www.portalzuk.com.br/leilao-de-imoveis/c/todos-imoveis/sp/sao-paulo/zona-sul",
    "https://www.portalzuk.com.br/leilao-de-imoveis/c/todos-imoveis/sp/sao-paulo/zona-sul/itaim-bibi",
    "https://www.portalzuk.com.br/leilao-de-imoveis/c/apartamento/sp/sao-paulo",
    "https://www.portalzuk.com.br/leilao-de-imoveis/c/todos-imoveis/sp/sao-paulo?bairro=itaim-bibi",
):
    st, body = get(url)
    imv = sorted(set(re.findall(r"/imovel/sp/sao-paulo/([a-z0-9-]+)/", body)))
    total = re.findall(r"(?i)(\d+)\s*(?:im[oó]veis|resultados?)\s*encontrad", body)[:2]
    p(f"[zuk] HTTP {st} {url} imoveis-sp={len(re.findall('/imovel/sp/', body))} bairros={imv[:15]} total={total}")
    more = re.findall(r'(?i)(?:data-[a-z-]*url|action|fetch\(|ajax)[^>]{0,200}', body)[:4]
    for m in more:
        p("   ajax:", re.sub(r"\s+", " ", m)[:220])

# ---------- QuintoAndar: API e paginação ----------
p("=" * 30, "QUINTOANDAR")
API = "https://apigw.prod.quintoandar.com.br/house-listing-search/v2/search/list"
hdr = {"Origin": "https://www.quintoandar.com.br", "Referer": "https://www.quintoandar.com.br/",
       "Content-Type": "application/json"}
loc = {"coordinate": {"lat": -23.585, "lng": -46.677},
       "viewport": {"north": -23.565, "south": -23.605, "east": -46.655, "west": -46.700},
       "neighborhoods": [], "countryCode": "BR"}
bodies = {
    "minimo": {"context": {"mapShowing": True, "listShowing": True, "numPhotos": 1, "isSSR": False},
               "filters": {"businessContext": "RENT", "location": loc},
               "pagination": {"pageSize": 12, "offset": 0}, "slug": "itaim-bibi-sao-paulo-sp-brasil",
               "fields": ["id", "area", "rentPrice", "totalCost", "neighbourhood"]},
    "com_area": {"context": {"mapShowing": True, "listShowing": True, "numPhotos": 1, "isSSR": False},
                 "filters": {"businessContext": "RENT", "location": loc,
                             "houseSpecs": {"area": {"range": {"min": 100}}, "houseTypes": ["APARTMENT"]}},
                 "sorting": {"criteria": "MOST_RECENT", "order": "DESC"},
                 "pagination": {"pageSize": 12, "offset": 0}, "slug": "itaim-bibi-sao-paulo-sp-brasil",
                 "fields": ["id", "area", "rentPrice", "totalCost", "neighbourhood"]},
}
for name, b in bodies.items():
    try:
        r = http.post(API, json=b, headers=hdr, retries=1)
        p(f"[qa api {name}] HTTP {r.status_code} :: {r.text[:400]}")
    except Exception as e:
        p(f"[qa api {name}] erro {e}")

for url in (
    "https://www.quintoandar.com.br/alugar/imovel/itaim-bibi-sao-paulo-sp-brasil/apartamento",
    "https://www.quintoandar.com.br/alugar/imovel/itaim-bibi-sao-paulo-sp-brasil/apartamento/de-100-a-1000-m2",
    "https://www.quintoandar.com.br/alugar/imovel/itaim-bibi-sao-paulo-sp-brasil/apartamento/100-m2-mais",
    "https://www.quintoandar.com.br/alugar/imovel/itaim-bibi-sao-paulo-sp-brasil/apartamento?pagina=2",
    "https://www.quintoandar.com.br/alugar/imovel/itaim-bibi-sao-paulo-sp-brasil/apartamento?ordem=mais-recente",
):
    st, body = get(url)
    data = next_data(body) or {}
    houses = [d for d in iter_dicts(data) if "id" in d and "area" in d]
    areas = sorted(int(h["area"]) for h in houses if h.get("area"))
    total = None
    for d in iter_dicts(data):
        if isinstance(d.get("visibleHouses"), dict):
            total = d["visibleHouses"].get("total")
    p(f"[qa page] HTTP {st} {url} casas={len(houses)} total={total} >=100m2={sum(a >= 100 for a in areas)} "
      f"ids={[h['id'] for h in houses[:4]]}")
