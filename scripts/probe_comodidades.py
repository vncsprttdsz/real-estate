"""Sondagem (só imprime): nomes de comodidades de piscina/churrasqueira nos portais (compra, Itaim/VNC)."""
import collections
import re

from imoveis.common import Config
from imoveis.http import Http
from imoveis.sources import grupozap as gz, quintoandar as qa

cfg = Config.load()
http = Http(0.6, 1.2)
PAT = re.compile(r"POOL|PISCIN|BARB|CHURRAS|GOURMET|GRILL", re.I)
for nb in ("Itaim Bibi", "Vila Nova Conceição"):
    site = gz.SITES["zap"]
    headers = {"x-domain": site["domain"], "Origin": site["site"], "Referer": site["site"] + "/", "Accept": "application/json"}
    am, n = collections.Counter(), 0
    for off in (0, 24, 48, 72, 96, 120):
        try:
            data = gz._get_page(http, "zap", lambda rung, size, sort: gz._api_params(cfg, "venda", nb, off, rung, size, sort), headers)
        except Exception as e:
            print("[zap] ERRO", e); break
        for it in data["search"]["result"]["listings"] or []:
            n += 1
            am.update(set(it.get("listing", it).get("amenities") or []))
    print(f"[zap venda {nb}] {n} anúncios; relevantes: {sorted((k, v) for k, v in am.items() if PAT.search(k))}")

hdr = {"Origin": qa.SITE, "Referer": qa.SITE + "/", "Content-Type": "application/json"}
for biz in ("venda", "aluguel"):
    am, ins, n = collections.Counter(), collections.Counter(), 0
    for off in range(0, 240, 60):
        body = qa._body(cfg, biz, off, 60, "itaim bibi")
        body["fields"] = qa.FIELDS + ["installations"]
        try:
            hits = (http.post(qa.API, json=body, headers=hdr).json().get("hits") or {}).get("hits") or []
        except Exception as e:
            print("[quintoandar] ERRO", e); break
        for h in hits:
            s = h.get("_source", {}); n += 1
            am.update(set(s.get("amenities") or [])); ins.update(set(s.get("installations") or []))
    print(f"[quintoandar {biz}] {n} imóveis")
    print("  amenities relevantes:", sorted((k, v) for k, v in am.items() if PAT.search(k)))
    print("  installations relevantes:", sorted((k, v) for k, v in ins.items() if PAT.search(k)))
    print("  installations (todas):", ins.most_common(40))
