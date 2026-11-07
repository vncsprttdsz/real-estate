"""Sondagem (só imprime): como obter a descrição (andar alto, vista) nos anúncios do ZAP/Viva Real."""
import collections
import json
import re

from imoveis.common import Config
from imoveis.http import Http
from imoveis.sources import grupozap as gz

cfg = Config.load()
http = Http(0.6, 1.2)
KW = re.compile(r"andar alto|andares? altos?|vista (livre|definitiva|panor\w*|para o parque|aberta|desimpedida|eterna)|vista", re.I)
for source in ("zap", "vivareal"):
    site = gz.SITES[source]
    headers = {"x-domain": site["domain"], "Origin": site["site"], "Referer": site["site"] + "/", "Accept": "application/json"}
    base = gz._api_params(cfg, "aluguel", "Vila Nova Conceição", 0, 0, 10, None)
    for label, extra in [("includeFields description", {"includeFields": "search(result(listings(listing(id,description,title,unitFloor,floors,amenities))),totalCount)"}),
                         ("includeFields amplo", {"includeFields": "search(result(listings(listing(description))))"})]:
        r = http.get(site["api"], params={**base, **extra}, headers=headers)
        try:
            ls = [(x.get("listing") or x) for x in r.json()["search"]["result"]["listings"]]
        except Exception:
            print(f"[{source}] {label}: HTTP {r.status_code} {r.text[:200]}"); continue
        d = [l.get("description") or "" for l in ls]
        print(f"[{source}] {label}: HTTP {r.status_code}; com descrição {sum(1 for x in d if x)}/{len(d)}; menciona andar alto/vista {sum(1 for x in d if KW.search(x))}")
        for x in d:
            m = KW.search(x)
            if m:
                print("    trecho:", re.sub(r"\s+", " ", x[max(0, m.start() - 100): m.end() + 100])); break
        for l in ls[:6]:
            print("    andar:", l.get("unitFloor"), "| floors:", l.get("floors"), "| amenities:", (l.get("amenities") or [])[:12], "| title:", (l.get("title") or "")[:80])
    # página do anúncio
    r = http.get(site["api"], params=base, headers=headers)
    items = r.json()["search"]["result"]["listings"]
    parsed = [gz.parse_listing(it, source, "aluguel") for it in items]
    url = next((p.url for p in parsed if p), None)
    if url:
        p = http.get(url, headers={"Referer": site["site"] + "/"})
        m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.){20,600})', p.text)
        print(f"[{source}] página {url[:90]} HTTP {p.status_code} len={len(p.text)}; description: {m.group(1)[:300] if m else '—'}")
    am = collections.Counter(a for it in items for a in ((it.get("listing") or it).get("amenities") or []))
    print(f"[{source}] amenities mais comuns:", am.most_common(40))
