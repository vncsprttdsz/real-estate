"""Gera um dados.json FICTÍCIO para visualizar o dashboard antes dos dados reais existirem.

Uso: python scripts/demo_data.py docs/demo.json   e abra docs/index.html?dados=demo.json
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone

random.seed(7)
NOW = datetime.now(timezone.utc).replace(minute=17, second=0, microsecond=0)
NB = {"Vila Nova Conceição": (24500, 92), "Itaim Bibi": (19800, 78)}  # R$/m² venda, R$/m² aluguel/mês*... aprox.
SRC = ["zap", "vivareal", "quintoandar"]
STREETS = {"Vila Nova Conceição": ["Rua Afonso Braz", "Rua Domingos Leme", "Rua Diogo Jácome", "Rua Lourenço de Almeida"],
           "Itaim Bibi": ["Rua Tabapuã", "Rua Joaquim Floriano", "Rua Pedroso Alvarenga", "Rua Jerônimo da Veiga"]}


def iso(d):
    return d.isoformat(timespec="seconds")


rows = []
for i in range(150):
    nb = random.choice(list(NB))
    business = random.choices(["venda", "aluguel", "leilao"], [0.58, 0.38, 0.04])[0]
    area = round(random.lognormvariate(5.0, 0.35))
    area = max(100, min(area, 520))
    sale_m2, rent_m2 = NB[nb]
    if business == "aluguel":
        rent_m2 = sale_m2 * 0.0042
        price = round(area * rent_m2 * random.uniform(0.75, 1.3), -2)
        condo = round(area * random.uniform(18, 30), -1)
        total = price + condo + round(area * 6, -1)
    else:
        price = round(area * sale_m2 * random.uniform(0.72, 1.35), -4)
        condo, total = round(area * random.uniform(18, 30), -1), None
        if business == "leilao":
            price = round(price * random.uniform(0.5, 0.75), -3)
    age = random.expovariate(1 / 40)
    first = NOW - timedelta(days=min(age, 60), hours=random.randint(0, 20))
    initial = (NOW - first).days > 40
    active = random.random() > 0.12
    last = NOW if active else NOW - timedelta(days=random.randint(1, 20))
    hist = [[iso(first), total or price]]
    if random.random() < 0.15:
        drop = (total or price) * random.uniform(0.9, 0.96)
        hist.append([iso(first + (last - first) * 0.6), round(drop, -2)])
    p_final = hist[-1][1]
    if business == "aluguel":
        total = p_final
        price = round(total - condo - area * 6, -2)
    else:
        price = p_final
    src = "zuk" if business == "leilao" else random.choice(SRC)
    rows.append({
        "source": src, "id": f"demo{i}", "key": f"{src}:{business}:demo{i}", "url": "#",
        "business": business, "title": f"Apartamento {random.choice([2,3,3,4,4])} dormitórios (EXEMPLO)",
        "neighborhood": nb, "address": f"{random.choice(STREETS[nb])}, {random.randint(10, 999)}",
        "area": area, "price": price, "total_monthly": total, "condo": condo,
        "bedrooms": random.choice([2, 3, 3, 4, 4, 5]), "parking": random.choice([1, 2, 2, 3, 4]),
        "extra": f"Datas: {(NOW + timedelta(days=random.randint(5, 40))):%d/%m/%Y} 10:00" if business == "leilao" else "",
        "ppm2": round(price / area, 2), "first_seen": iso(first), "last_seen": iso(last),
        "initial": initial, "hist": hist, "active": active,
    })

history = []
walk = [0.0, 0.0]
for d in range(60, -1, -1):
    day = (NOW - timedelta(days=d)).date()
    drift = 1 - d * 0.0006
    groups = {}
    for j, (nb, (sale_m2, _)) in enumerate(NB.items()):
        walk[j] += random.uniform(-1.2, 1.2)
        groups[f"venda|{nb}"] = {"n": round(44 + walk[j] - j * 6), "ppm2": round(sale_m2 * drift * random.uniform(0.99, 1.01)), "price": None}
        groups[f"aluguel|{nb}"] = {"n": round(28 + walk[j] * 0.6 - j * 3), "ppm2": round(sale_m2 * 0.0042 * drift * random.uniform(0.985, 1.015), 1), "price": None}
        groups[f"leilao|{nb}"] = {"n": random.randint(0, 3), "ppm2": None, "price": None}
    history.append({"date": day.isoformat(), "groups": groups})

data = {
    "demo": True,
    "updated_at": iso(NOW),
    "criteria": {"neighborhoods": list(NB), "min_area": 100, "businesses": ["venda", "aluguel"], "max_sale": None, "max_rent": None},
    "sources": {
        "zap": {"ok": True, "last_ok": iso(NOW), "error": None, "failures": 0},
        "vivareal": {"ok": True, "last_ok": iso(NOW), "error": None, "failures": 0},
        "quintoandar": {"ok": True, "last_ok": iso(NOW), "error": None, "failures": 0},
        "zuk": {"ok": False, "last_ok": iso(NOW - timedelta(hours=6)), "error": "HTTP 403", "failures": 3},
    },
    "listings": rows,
    "history": history,
}
out = sys.argv[1] if len(sys.argv) > 1 else "docs/demo.json"
with open(out, "w") as f:
    json.dump(data, f, ensure_ascii=False)
print(f"{len(rows)} anúncios fictícios -> {out}")
