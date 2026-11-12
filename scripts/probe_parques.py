"""Sondagem (só imprime): contorno do Parque Ibirapuera e do Parque do Povo no OpenStreetMap (Overpass ou Nominatim)."""
import json
import urllib.parse
import urllib.request

UA = {"User-Agent": "radar-apartamentos/1.0 (sondagem de contorno de parques)", "Accept": "application/json"}
Q = """[out:json][timeout:60];
(way["leisure"="park"]["name"~"^Parque (Ibirapuera|do Povo)"](-23.62,-46.71,-23.56,-46.64);
 relation["leisure"="park"]["name"~"^Parque (Ibirapuera|do Povo)"](-23.62,-46.71,-23.56,-46.64););
out geom;"""


def get(url, data=None):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def show(name, pts, src):
    step = max(1, len(pts) // 80)
    print(f"PARQUE {src} {name!r}: {len(pts)} pontos ->", json.dumps([[round(a, 5), round(b, 5)] for a, b in pts[::step]]))


done = False
for url in ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"):
    try:
        j = get(url, urllib.parse.urlencode({"data": Q}).encode())
        for el in j.get("elements", []):
            name = el.get("tags", {}).get("name")
            rings = [el.get("geometry") or []] if el["type"] == "way" else \
                [m.get("geometry") or [] for m in el.get("members", []) if m.get("role") == "outer"]
            for ring in rings:
                show(name, [(p["lat"], p["lon"]) for p in ring], f"overpass {el['type']} {el['id']}")
                done = True
        print(url, "ok", len(j.get("elements", [])), "elementos")
        if done:
            break
    except Exception as e:
        print(url, "ERRO", e)

for q in ("Parque Ibirapuera, São Paulo", "Parque do Povo, Itaim Bibi, São Paulo"):
    try:
        j = get("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"q": q, "format": "json", "polygon_geojson": 1, "limit": 3}))
        for r in j:
            g = r.get("geojson") or {}
            print(f"nominatim {q!r}: {r.get('display_name')!r} tipo={r.get('type')} geom={g.get('type')}")
            if g.get("type") == "Polygon":
                show(r.get("display_name", q).split(",")[0], [(b, a) for a, b in g["coordinates"][0]], "nominatim")
            elif g.get("type") == "MultiPolygon":
                big = max(g["coordinates"], key=lambda p: len(p[0]))
                show(r.get("display_name", q).split(",")[0], [(b, a) for a, b in big[0]], "nominatim")
    except Exception as e:
        print("nominatim", q, "ERRO", e)
