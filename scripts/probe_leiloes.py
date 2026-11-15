"""Sondagem (só imprime): o que cada fonte de leilão/venda de banco entrega para São Paulo e para os bairros-alvo."""
import collections
import csv
import io
import re

from imoveis.common import Config, norm
from imoveis.http import Http
from imoveis.sources import zuk

cfg = Config.load()
http = Http(0.8, 1.5)
ALVO = {norm(n) for n in cfg.neighborhoods + ["Jardim Europa", "Jardim Paulistano", "Chácara Itaim", "Moema", "Vila Olímpia",
                                               "Jardim Paulista", "Ibirapuera", "Cidade Monções", "Brooklin"]}


def show(name, r, extra=""):
    t = r.text if hasattr(r, "text") else ""
    print(f"[{name}] HTTP {r.status_code} · {len(t)} bytes · {extra}")


# 1) Portal Zuk: listagem da capital e links por bairro
try:
    url = zuk.capital_url(cfg)
    r = http.get(url)
    links = zuk.extract_all_links(r.text)
    nbs = collections.Counter(u.split("/")[4] if len(u.split("/")) > 4 else "?" for u in links)
    show("zuk lista", r, f"{len(links)} links de imóvel; bairros: {nbs.most_common(15)}")
    print("[zuk] trechos:", re.findall(r'href="([^"]*imovel[^"]*)"', r.text)[:5], "| __NEXT_DATA__" in r.text, "api" in r.text.lower())
    for k in ("page", "pagina"):
        r2 = http.get(url + f"?{k}=2")
        print(f"[zuk] {k}=2 -> HTTP {r2.status_code}, {len(zuk.extract_all_links(r2.text))} links")
    sm = http.get(zuk.SITE + "/robots.txt")
    print("[zuk] robots/sitemaps:", re.findall(r"(?im)^sitemap:\s*(\S+)", sm.text)[:5])
except Exception as e:
    print("[zuk] ERRO", e)

# 2) Caixa: lista CSV oficial de imóveis à venda (SP)
for u in ("https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_SP.csv",):
    try:
        r = http.get(u, timeout=90)
        raw = r.content.decode("latin-1", errors="replace")
        show("caixa csv", r, f"linhas={raw.count(chr(10))}")
        lines = raw.splitlines()
        hdr_i = next((i for i, l in enumerate(lines[:10]) if "Bairro" in l or "N° do imóvel" in l or "Nº do imóvel" in l), 0)
        rows = list(csv.reader(io.StringIO("\n".join(lines[hdr_i:])), delimiter=";"))
        hdr = [h.strip() for h in rows[0]]
        print("[caixa] cabeçalho:", hdr)
        ci, bi = (next((i for i, h in enumerate(hdr) if h.lower().startswith(k)), None) for k in ("cidade", "bairro"))
        sp = [x for x in rows[1:] if ci is not None and len(x) > ci and norm(x[ci]) == "sao paulo"]
        alvo = [x for x in sp if bi is not None and norm(x[bi]) in ALVO]
        print(f"[caixa] São Paulo capital: {len(sp)} imóveis; nos bairros-alvo e vizinhos: {len(alvo)}")
        print("[caixa] bairros mais comuns na capital:", collections.Counter(x[bi].strip() for x in sp).most_common(12))
        for x in alvo[:15]:
            print("   ", " | ".join(c.strip() for c in x))
    except Exception as e:
        print("[caixa] ERRO", e)

# 3) Agregadores e leiloeiros: o que responde e se há dados estruturados
CANDIDATOS = {
    "leilaoimovel": "https://www.leilaoimovel.com.br/leilao-de-imovel/sp/sao-paulo",
    "resale": "https://www.resale.com.br/imoveis/sp/sao-paulo",
    "megaleiloes": "https://www.megaleiloes.com.br/imoveis/apartamentos/sp/sao-paulo",
    "frazao": "https://www.frazaoleiloes.com.br/",
    "sodresantoro": "https://www.sodresantoro.com.br/imoveis",
    "lancejudicial": "https://www.lancejudicial.com.br/",
    "biasi": "https://www.biasileiloes.com.br/",
    "santander": "https://www.santanderimoveis.com.br/",
    "bb": "https://www.bb.com.br/site/imoveis-a-venda/",
    "superbid": "https://www.superbid.net/categorias/imoveis",
}
for name, u in CANDIDATOS.items():
    try:
        r = http.get(u, retries=1, timeout=40)
        t = r.text
        hits = [b for b in ("Itaim", "Vila Nova Concei", "Moema", "Jardim Europa") if b in t]
        show(name, r, f"next_data={'__NEXT_DATA__' in t} json-ld={'application/ld+json' in t} "
                      f"bairros-alvo no HTML={hits} apartamento={t.lower().count('apartamento')}")
    except Exception as e:
        print(f"[{name}] ERRO", e)
