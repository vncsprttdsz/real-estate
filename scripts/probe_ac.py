"""Sondagem (só imprime): onde os portais informam ar-condicionado (comodidades da API ou descrição)."""
import collections, json, re

from imoveis.common import Config
from imoveis.http import Http
from imoveis.sources import grupozap as gz, quintoandar as qa
from imoveis import details

cfg = Config.load()
http = Http(0.6, 1.2)
AC = re.compile(r"ar[\s-]*condicionad|ar\s+central|split|climatiza|vrf\b", re.I)

# ZAP / Viva Real: campos de comodidades da busca
for src in ("zap", "vivareal"):
    site = gz.SITES[src]
    headers = {"x-domain": site["domain"], "Origin": site["site"], "Referer": site["site"] + "/", "Accept": "application/json"}
    am, keys, n, sample = collections.Counter(), collections.Counter(), 0, []
    for off in (0, 24, 48, 72, 96):
        try:
            data = gz._get_page(http, src, lambda rung, size, sort: gz._api_params(cfg, "aluguel", "Itaim Bibi", off, rung, size, sort), headers)
        except Exception as e:
            print(f"[{src}] ERRO {e}"); break
        for it in data["search"]["result"]["listings"] or []:
            lst = it.get("listing", it); n += 1
            keys.update(lst.keys())
            for k in ("amenities", "features", "unitAmenities", "condoAmenities"):
                for a in lst.get(k) or []:
                    am[f"{k}:{a}"] += 1
            if len(sample) < 30:
                sample.append((gz.parse_listing(it, src, "aluguel"), lst))
    print(f"[{src}] {n} anúncios; chaves: {sorted(keys)}")
    print(f"[{src}] comodidades (top 80): {am.most_common(80)}")
    print(f"[{src}] com algo de AR: {[(k, v) for k, v in am.items() if re.search('AIR|COND|CLIMA', k)]}")
    if src == "zap":
        hit_api = hit_txt = got = 0
        for l, lst in sample[:20]:
            api = any(re.search("AIR_COND", str(a)) for a in (lst.get("amenities") or []))
            txt = details.fetch_description(Http(1.0, 2.0), l) if l else None
            got += bool(txt); hit_api += api; m = AC.search(txt or "")
            hit_txt += bool(m)
            print(f"  [zap] api_ac={api} desc={'sim' if txt else 'não'} trecho={(txt or '')[max(0, m.start() - 40):m.end() + 40]!r}" if m else f"  [zap] api_ac={api} desc={'sim' if txt else 'não'}")
        print(f"[zap] amostra 20: descrição obtida {got}, AR na descrição {hit_txt}, AR na API {hit_api}")

# QuintoAndar: tenta campos extras na busca
hdr = {"Origin": qa.SITE, "Referer": qa.SITE + "/", "Content-Type": "application/json"}
for extra in (["amenities"], ["installations"], ["houseAmenities"], ["features"]):
    body = qa._body(cfg, "aluguel", 0, 20, "itaim bibi")
    body["fields"] = qa.FIELDS + extra
    try:
        r = http.post(qa.API, json=body, headers=hdr)
        j = r.json()
        hits = (j.get("hits") or {}).get("hits") or []
        vals = [h.get("_source", {}).get(extra[0]) for h in hits]
        print(f"[quintoandar] campo {extra[0]}: status {r.status_code}, {len(hits)} hits, exemplos {vals[:3]}")
    except Exception as e:
        print(f"[quintoandar] campo {extra[0]}: ERRO {e}")
# Página de um imóvel do QuintoAndar
try:
    r = http.post(qa.API, json=qa._body(cfg, "aluguel", 0, 5, "itaim bibi"), headers=hdr)
    hid = r.json()["hits"]["hits"][0]["_source"]["id"]
    p = http.get(f"{qa.SITE}/imovel/{hid}", retries=1, timeout=20)
    t = p.text
    print(f"[quintoandar] página {hid}: status {p.status_code}, {len(t)} bytes; trechos AR: {[t[max(0, m.start() - 80):m.end() + 80] for m in AC.finditer(t)][:4]}")
except Exception as e:
    print(f"[quintoandar] página: ERRO {e}")
