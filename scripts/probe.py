"""Sondagem dos sites para ajustar os coletores. Imprime trechos no log; não altera estado.

Uso: python -m scripts.probe   (roda pelo workflow 'Sondagem dos sites')
"""
import json
import re

from imoveis.common import iter_dicts, next_data
from imoveis.http import Http

http = Http(1.0, 2.0)
OUT = []


def p(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)


def get(url, **kw):
    try:
        r = http.get(url, retries=1, **kw)
        return r.status_code, r.text, str(r.url)
    except Exception as e:
        return None, str(e), url


def snippets(text, pat, n=3, width=300):
    out = []
    for m in re.finditer(pat, text):
        a = max(0, m.start() - width // 2)
        out.append(re.sub(r"\s+", " ", text[a:a + width]))
        if len(out) >= n:
            break
    return out


# ---------- ZAP / Viva Real ----------
p("=" * 30, "ZAP / VIVAREAL API")
base_params = {"business": "SALE", "categoryPage": "RESULT", "size": 5, "from": 0,
               "addressCity": "São Paulo", "addressState": "São Paulo", "addressNeighborhood": "Itaim Bibi"}
for site, api, dom in (("zap", "https://glue-api.zapimoveis.com.br", "www.zapimoveis.com.br"),
                       ("vivareal", "https://glue-api.vivareal.com", "www.vivareal.com.br")):
    for ver in ("v2", "v3", "v4"):
        for xdom in ("." + dom.split("www.")[1], dom):
            st, body, _ = get(f"{api}/{ver}/listings", params=base_params,
                              headers={"x-domain": xdom, "Origin": "https://" + dom, "Referer": "https://" + dom + "/"})
            p(f"[{site} {ver} x-domain={xdom}] HTTP {st} :: {re.sub(chr(10), ' ', body[:500])}")

p("=" * 30, "ZAP / VIVAREAL PAGES")
for site, dom in (("zap", "https://www.zapimoveis.com.br"), ("vivareal", "https://www.vivareal.com.br")):
    cands = []
    for zone in ("zona-sul", "zona-oeste", "centro"):
        cands.append(f"{dom}/venda/apartamentos/sp+sao-paulo+{zone}+itaim-bibi/")
    cands.append(f"{dom}/venda/sp/sao-paulo/zona-sul/itaim-bibi/apartamento_residencial/")
    cands.append(f"{dom}/venda/sp/sao-paulo/zona-oeste/itaim-bibi/apartamento_residencial/")
    cands.append(f"{dom}/venda/apartamentos/sp+sao-paulo/")
    for url in cands:
        st, body, final = get(url)
        info = {
            "len": len(body), "final": final,
            "next_data": bool(next_data(body)), "next_f": body.count("self.__next_f.push"),
            "usableAreas": body.count("usableAreas"), "cloudflare": "cloudflare" in body.lower()[:3000],
        }
        p(f"[{site} page] HTTP {st} {url} {json.dumps(info)}")
        if st == 200:
            for sn in snippets(body, r"glue-api[^\"' ]{0,200}", 3, 260):
                p("   api-ref:", sn)
            for sn in snippets(body, r"usableAreas", 2, 600):
                p("   areas:", sn)
            hrefs = sorted(set(re.findall(r'href="(/imovel/[^"]+)"', body)))[:5]
            p("   imovel-hrefs:", hrefs)

# ---------- QuintoAndar ----------
p("=" * 30, "QUINTOANDAR")
for bus, path in (("aluguel", "alugar"), ("venda", "comprar")):
    url = f"https://www.quintoandar.com.br/{path}/imovel/itaim-bibi-sao-paulo-sp-brasil/apartamento"
    st, body, final = get(url)
    p(f"[qa page {bus}] HTTP {st} len={len(body)} final={final}")
    data = next_data(body)
    if not data:
        p("   sem __NEXT_DATA__"); continue
    houses = [d for d in iter_dicts(data) if "id" in d and "area" in d]
    p(f"   casas encontradas: {len(houses)}")
    for h in houses[:2]:
        p("   exemplo:", json.dumps({k: h[k] for k in list(h)[:40]}, ensure_ascii=False)[:1500])
    # Onde ficam as casas e quantos resultados a busca diz ter
    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if re.search(r"total|count|hits|pagination|nextPage|offset", k, re.I) and not isinstance(v, (dict, list)):
                    p(f"   {path}.{k} = {v}")
                walk(v, f"{path}.{k}")
        elif isinstance(o, list) and o:
            walk(o[0], path + "[0]")
    walk(data.get("props", {}))
    for sn in snippets(body, r"apigw[^\"' ]{0,160}", 4, 220):
        p("   api-ref:", sn)

# ---------- Portal Zuk ----------
p("=" * 30, "PORTAL ZUK")
for url in ("https://www.portalzuk.com.br/leilao-de-imoveis/c/todos-imoveis/sp/sao-paulo/itaim-bibi",
            "https://www.portalzuk.com.br/leilao-de-imoveis/c/todos-imoveis/sp/sao-paulo/vila-nova-conceicao",
            "https://www.portalzuk.com.br/leilao-de-imoveis/u/todos-imoveis/sp/sao-paulo",
            "https://www.portalzuk.com.br/leilao-de-imoveis"):
    st, body, final = get(url)
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', body)
    interesting = sorted({h for h in hrefs if re.search(r"imovel|lote|leilao/", h)})
    p(f"[zuk] HTTP {st} {url} len={len(body)} hrefs={len(hrefs)} interessantes={len(interesting)}")
    for h in interesting[:15]:
        p("   href:", h)
    for sn in snippets(body, r"(?i)itaim|nova concei", 3, 300):
        p("   trecho:", sn)
    for sn in snippets(body, r"(?i)nenhum (im[oó]vel|resultado)", 2, 200):
        p("   vazio?:", sn)
