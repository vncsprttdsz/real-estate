"""Gera os dados do dashboard (docs/dados.json) e o histórico diário do mercado."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from statistics import median

from .common import ROOT, Config, Listing

CATALOG = ROOT / "data" / "catalogo.json"     # último retrato conhecido de cada anúncio
HISTORY = ROOT / "data" / "historico.json"    # um retrato agregado por dia
OUTPUT = ROOT / "docs" / "dados.json"
KEEP_HISTORY_DAYS = 730


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def ppm2(d: dict) -> float | None:
    """R$/m²: preço de venda/lance, ou aluguel (sem condomínio) por m² ao mês."""
    if d.get("price") and d.get("area"):
        return round(d["price"] / d["area"], 2)
    return None


def snapshot(active: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for d in active:
        groups.setdefault(f"{d['business']}|{d['neighborhood']}", []).append(d)
    out = {}
    for key, items in groups.items():
        p = [x["ppm2"] for x in items if x.get("ppm2")]
        prices = [x["price"] for x in items if x.get("price")]
        out[key] = {
            "n": len(items),
            "ppm2": round(median(p), 2) if p else None,
            "price": round(median(prices)) if prices else None,
        }
    return out


def build(cfg: Config, state: dict, results: dict[str, list[Listing]], errors: dict[str, str]) -> dict:
    catalog: dict = _load(CATALOG, {})
    for listings in results.values():
        for l in listings:
            catalog[l.key] = l.to_dict()
    # Só guarda o que o monitor ainda lembra (mesma janela de 30 dias do estado).
    catalog = {k: v for k, v in catalog.items() if k in state["seen"]}

    last_ok = state.get("last_ok", {})
    rows = []
    for key, d in catalog.items():
        s = state["seen"][key]
        active = s["last_seen"] == last_ok.get(d["source"])
        rows.append({
            **d,
            "key": key,
            "ppm2": ppm2(d),
            "first_seen": s["first_seen"],
            "last_seen": s["last_seen"],
            "initial": bool(s.get("initial")),
            "hist": s.get("hist", []),
            "active": active,
        })

    today = datetime.now(timezone.utc).date().isoformat()
    history = [h for h in _load(HISTORY, []) if h["date"] != today]
    history.append({"date": today, "groups": snapshot([r for r in rows if r["active"]])})
    history = history[-KEEP_HISTORY_DAYS:]

    sources = {}
    for name, enabled in {"zap": 1, "vivareal": 1, "quintoandar": 1, "zuk": 1, **cfg.sources}.items():
        if not enabled:
            continue
        sources[name] = {
            "ok": name not in errors,
            "last_ok": last_ok.get(name),
            "error": errors.get(name),
            "failures": state["failures"].get(name, 0),
        }

    data = {
        "updated_at": state.get("last_run"),
        "criteria": {
            "neighborhoods": cfg.neighborhoods,
            "min_area": cfg.min_area,
            "businesses": cfg.businesses,
            "max_sale": cfg.max_sale,
            "max_rent": cfg.max_rent,
        },
        "sources": sources,
        "listings": rows,
        "history": history,
    }
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=1, sort_keys=True))
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1))
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return data
