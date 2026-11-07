"""Sondagem (só imprime): coordenadas dos anúncios (ZAP/Viva Real e QuintoAndar) e sua precisão."""
import collections
import json

from imoveis.common import Config
from imoveis.http import Http
from imoveis.sources import grupozap as gz, quintoandar as qa

cfg = Config.load()
http = Http(0.6, 1.2)
for source in ("zap", "vivareal"):
    site = gz.SITES[source]
    headers = {"x-domain": site["domain"], "Origin": site["site"], "Referer": site["site"] + "/", "Accept": "application/json"}
    for nb in ("Itaim Bibi", "Vila Nova Conceição"):
        r = http.get(site["api"], params=gz._api_params(cfg, "aluguel", nb, 0, 0, 24, None), headers=headers)
        items = r.json()["search"]["result"]["listings"]
        addrs = [((it.get("listing") or it).get("address") or {}) for it in items]
        print(f"[{source}] {nb}: exemplo address = {json.dumps(addrs[0], ensure_ascii=False)[:700]}")
        prec = collections.Counter((a.get("point") or {}).get("source") or (a.get("point") or {}).get("precision") or ("com point" if a.get("point") else "sem point") for a in addrs)
        disp = collections.Counter((it.get("listing") or it).get("displayAddressType") for it in items)
        print(f"   point: {dict(prec)} · displayAddressType: {dict(disp)}")
        pts = [(a.get("point") or {}) for a in addrs]
        print("   pontos:", [(p.get("lat"), p.get("lon"), p.get("approximateLat"), p.get("approximateLon")) for p in pts[:6]])
hdr = {"Origin": qa.SITE, "Referer": qa.SITE + "/", "Content-Type": "application/json"}
for extra in (["location"], ["location", "coordinates", "lat", "lng"]):
    body = qa._body(cfg, "aluguel", 0, 10, "itaim bibi")
    body["fields"] = list(body["fields"]) + extra
    r = http.post(qa.API, json=body, headers=hdr)
    hs = [h.get("_source", h) for h in ((r.json().get("hits") or {}).get("hits") or [])] if r.status_code == 200 else []
    print(f"[qa] fields+{extra}: HTTP {r.status_code}; chaves: {sorted(hs[0].keys()) if hs else r.text[:200]}")
    if hs:
        print("   exemplo:", {k: hs[0].get(k) for k in ("address", "location", "coordinates", "lat", "lng")})
