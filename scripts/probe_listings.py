"""Sondagem (só imprime): descrição completa na página do anúncio (ZAP e QuintoAndar) e andar informado."""
import collections
import json
import re

from imoveis.common import Config
from imoveis.http import Http
from imoveis.sources import grupozap as gz, quintoandar as qa

cfg = Config.load()
http = Http(0.8, 1.5)
KW = re.compile(r"andar alto|andares? altos?|alt[oí]ssimo|vista (livre|definitiva|panor\w*|para o parque|aberta|desimpedida|eterna|linda|maravilhosa)|vista", re.I)


def descs(html):
    out = []
    for m in re.finditer(r'"description"\s*:\s*"((?:[^"\\]|\\.){20,5000})"', html):
        try:
            out.append(json.loads('"' + m.group(1) + '"'))
        except Exception:
            out.append(m.group(1))
    return sorted(set(out), key=len, reverse=True)


site = gz.SITES["zap"]
headers = {"x-domain": site["domain"], "Origin": site["site"], "Referer": site["site"] + "/", "Accept": "application/json"}
r = http.get(site["api"], params=gz._api_params(cfg, "aluguel", "Vila Nova Conceição", 0, 0, 24, None), headers=headers)
items = r.json()["search"]["result"]["listings"]
floors = collections.Counter(str((it.get("listing") or it).get("unitFloor")) for it in items)
print("[zap] unitFloor nas 24:", dict(floors))
hits = 0
for it in items[:8]:
    p = gz.parse_listing(it, "zap", "aluguel")
    if not p:
        continue
    page = http.get(p.url, headers={"Referer": site["site"] + "/"})
    ds = descs(page.text)
    best = ds[0] if ds else ""
    hits += bool(KW.search(best))
    print(f"[zap] {page.status_code} n_desc={len(ds)} len_maior={len(best)} kw={bool(KW.search(best))} :: {re.sub(r'\\s+', ' ', best)[:220]}")
print("[zap] com andar alto/vista:", hits)

hdr = {"Origin": qa.SITE, "Referer": qa.SITE + "/", "Content-Type": "application/json"}
r = http.post(qa.API, json=qa._body(cfg, "aluguel", 0, 8, "vila nova conceicao"), headers=hdr)
hs = [h.get("_source", h) for h in r.json()["hits"]["hits"]]
hits = 0
for h in hs:
    page = http.get(f"{qa.SITE}/imovel/{h['id']}")
    ds = descs(page.text)
    best = ds[0] if ds else ""
    hits += bool(KW.search(best))
    print(f"[qa] {page.status_code} n_desc={len(ds)} len_maior={len(best)} kw={bool(KW.search(best))} :: {re.sub(r'\\s+', ' ', best)[:220]}")
print("[qa] com andar alto/vista:", hits)
