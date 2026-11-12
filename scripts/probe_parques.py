"""Sondagem (só imprime): contorno do Parque Ibirapuera e do Parque do Povo no OpenStreetMap (Overpass)."""
import json

from imoveis.http import Http

Q = """[out:json][timeout:60];
area["name"="São Paulo"]["admin_level"="8"]->.sp;
(way["leisure"="park"]["name"~"^Parque (Ibirapuera|do Povo)"](area.sp);
 relation["leisure"="park"]["name"~"^Parque (Ibirapuera|do Povo)"](area.sp););
out geom;"""
http = Http(0.5, 1.0)
for url in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"):
    try:
        r = http.post(url, data={"data": Q}, timeout=90)
        print(url, r.status_code)
        if r.status_code != 200:
            continue
        for el in r.json().get("elements", []):
            name = el.get("tags", {}).get("name")
            rings = []
            if el["type"] == "way":
                rings = [el.get("geometry") or []]
            else:
                rings = [m.get("geometry") or [] for m in el.get("members", []) if m.get("role") == "outer"]
            for ring in rings:
                pts = [[round(p["lat"], 5), round(p["lon"], 5)] for p in ring]
                step = max(1, len(pts) // 60)   # até ~60 pontos por contorno
                print(f"PARQUE {el['type']} {el['id']} {name!r}: {len(pts)} pontos ->", json.dumps(pts[::step]))
        break
    except Exception as e:
        print(url, "ERRO", e)
