"""Sondagem (só imprime): de que bairros (rótulo do portal) vêm os anúncios dentro da área de interesse."""
import collections

from imoveis.common import Config
from imoveis.geo import inside
from imoveis.http import Http
from imoveis.sources import grupozap as gz, quintoandar as qa

cfg = Config.load()
P = cfg.area_polygon
http = Http(0.6, 1.2)
for biz in ("aluguel", "venda"):
    for nb in ["Itaim Bibi", "Vila Nova Conceição", "Vila Uberabinha", "Jardim Europa", "Jardim Paulistano", "Moema",
               "Vila Olímpia", "Ibirapuera", "Chácara Itaim", "Cidade Monções", "Jardim Luzitânia", "Indianópolis"]:
        site = gz.SITES["zap"]
        headers = {"x-domain": site["domain"], "Origin": site["site"], "Referer": site["site"] + "/", "Accept": "application/json"}
        tot, inn, n, labels = 0, 0, 0, collections.Counter()
        for off in (0, 24, 48, 72):
            try:
                data = gz._get_page(http, "zap", lambda rung, size, sort: gz._api_params(cfg, biz, nb, off, rung, size, sort), headers)
            except Exception as e:
                print(f"[zap] {biz} {nb}: ERRO {e}"); break
            tot = (data.get("search") or {}).get("totalCount") or 0
            for it in (data["search"]["result"]["listings"] or []):
                l = gz.parse_listing(it, "zap", biz)
                if l and l.lat:
                    n += 1
                    if inside(l.lat, l.lon, P):
                        inn += 1; labels[l.neighborhood] += 1
            if off + 24 >= tot:
                break
        print(f"[zap] {biz:7} {nb:20} total={tot:6}  amostra={n:3}  dentro da área={inn:3} ({inn / n:.0%})" if n else f"[zap] {biz} {nb}: sem amostra", dict(labels))
# QuintoAndar: caixa que envolve a área, rótulos dos anúncios dentro
lats, lons = [p[0] for p in P], [p[1] for p in P]
vp = {"north": max(lats), "south": min(lats), "east": max(lons), "west": min(lons)}
hdr = {"Origin": qa.SITE, "Referer": qa.SITE + "/", "Content-Type": "application/json"}
for biz in ("aluguel", "venda"):
    lab, n = collections.Counter(), 0
    for off in range(0, 600, 60):
        r = http.post(qa.API, json=qa._body(cfg, biz, off, 60, "itaim bibi", vp), headers=hdr)
        if r.status_code != 200:
            break
        hs = [h.get("_source", h) for h in (r.json().get("hits") or {}).get("hits") or []]
        for h in hs:
            l = qa.parse_house(h, biz)
            if l and l.lat and inside(l.lat, l.lon, P):
                n += 1; lab[l.neighborhood] += 1
        if len(hs) < 60:
            break
    print(f"[qa] {biz}: {n} dentro da área por rótulo:", dict(lab.most_common()))
