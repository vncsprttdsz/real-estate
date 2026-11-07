"""Sondagem (só imprime): descrição dos anúncios e bairros vizinhos ao Itaim/Vila Nova Conceição."""
import collections
import json
import re

from imoveis.common import Config, norm
from imoveis.http import Http
from imoveis.sources import grupozap as gz, quintoandar as qa

cfg = Config.load()
http = Http(0.6, 1.2)
KW = re.compile(r"andar alto|andares altos|alto padr|vista (livre|definitiva|panor|para o parque|aberta|desimpedida)|vista", re.I)


def zap_page(source, business, nb):
    site = gz.SITES[source]
    headers = {"x-domain": site["domain"], "Origin": site["site"], "Referer": site["site"] + "/", "Accept": "application/json"}
    return gz._get_page(http, source, lambda rung, size, sort: gz._api_params(cfg, business, nb, 0, rung, size, sort), headers)


for nb in ["Vila Uberabinha", "Vila Nova Conceição", "Itaim Bibi"]:
    for biz in ["aluguel", "venda"]:
        try:
            data = zap_page("zap", biz, nb)
        except Exception as e:
            print(f"[zap] {nb} {biz}: ERRO {e}"); continue
        s = data.get("search") or {}
        items = (s.get("result") or {}).get("listings") or []
        nbs = collections.Counter(((it.get("listing") or it).get("address") or {}).get("neighborhood") for it in items)
        descs = [(it.get("listing") or it).get("description") or "" for it in items]
        print(f"[zap] {nb} {biz}: total={s.get('totalCount')} rung={gz._working_rung.get(('zap', nb))} bairros na pág.={dict(nbs)}")
        print(f"      com descrição: {sum(1 for d in descs if d)}/{len(descs)} · menciona andar alto/vista: {sum(1 for d in descs if KW.search(d))}")
        if items and nb == "Vila Nova Conceição" and biz == "aluguel":
            lst = items[0].get("listing") or items[0]
            print("      chaves do listing:", sorted(lst.keys()))
            print("      exemplo descrição:", (descs[0] or "")[:400].replace("\n", " "))
            for d in descs:
                m = KW.search(d)
                if m:
                    print("      trecho:", d[max(0, m.start() - 80): m.end() + 80].replace("\n", " ")); break

# QuintoAndar: campos disponíveis e bairros dentro das áreas de busca
headers = {"Origin": qa.SITE, "Referer": qa.SITE + "/", "Content-Type": "application/json"}
for nb in ["vila nova conceicao", "itaim bibi"]:
    body = qa._body(cfg, "aluguel", 0, 60, nb)
    r = http.post(qa.API, json=body, headers=headers)
    hits = [h.get("_source", h) for h in ((r.json().get("hits") or {}).get("hits") or [])] if r.status_code == 200 else []
    print(f"[qa] {nb} aluguel HTTP {r.status_code}: {len(hits)} hits · bairros: {dict(collections.Counter(h.get('neighbourhood') or h.get('neighborhood') for h in hits))}")
    if hits and nb == "vila nova conceicao":
        print("      campos (FIELDS):", sorted(hits[0].keys()))
        b2 = dict(body); b2.pop("fields", None)
        r2 = http.post(qa.API, json=b2, headers=headers)
        h2 = [h.get("_source", h) for h in ((r2.json().get("hits") or {}).get("hits") or [])] if r2.status_code == 200 else []
        print(f"      sem 'fields': HTTP {r2.status_code}; campos: {sorted(h2[0].keys()) if h2 else r2.text[:200]}")
        hid = hits[0].get("id")
        if hid:
            p = http.get(f"{qa.SITE}/imovel/{hid}")
            txt = re.sub(r"<[^>]+>", " ", p.text)
            i = txt.lower().find("descri")
            print(f"      página do imóvel HTTP {p.status_code}; trecho perto de 'descri': {re.sub(r'\\s+', ' ', txt[i:i+400]) if i >= 0 else '—'}")
            m = re.search(r'"description"\s*:\s*"([^"]{0,300})', p.text)
            print("      JSON-LD/next description:", m.group(1) if m else "—")
