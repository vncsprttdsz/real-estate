"""Gera os dados do dashboard (docs/dados.json) e o histórico diário do mercado."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from statistics import median

from .common import ROOT, Config, Listing, norm
from . import dedupe

CATALOG = ROOT / "data" / "catalogo.json"     # último retrato conhecido de cada anúncio
HISTORY = ROOT / "data" / "historico.json"    # um retrato agregado por dia
OUTPUT = ROOT / "docs" / "dados.json"
KEEP_HISTORY_DAYS = 730
GONE_AFTER_SECONDS = 24 * 3600
# ZAP/Viva Real: só lemos as novidades de uma busca com milhares de resultados, então
# ausência não prova que saiu do ar; espera mais antes de marcar.
GONE_AFTER_BY_SOURCE = {"zap": 7 * 86400, "vivareal": 7 * 86400}


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# Cobertura de cada fonte (o que o monitor efetivamente lê) — exibido na aba de metodologia.
SOURCE_INFO = {
    "zap": "API do portal; por bairro e negócio, os 1.200 anúncios mais recentes (≥ área mínima, apartamentos).",
    "vivareal": "Mesma base do ZAP (Grupo OLX); lê os 240 mais recentes e descarta ids já vistos no ZAP.",
    "quintoandar": "API do portal; todos os resultados de cada bairro (≥ área mínima), mais recentes primeiro.",
    "zuk": "Listagem de leilões da capital; só a 1ª página (30 leilões) — o site não expõe paginação.",
}

# Mudanças de cobertura/metodologia que afetam séries e contagens (data ISO, texto).
METHOD_NOTES = [
    ("2026-10-11", "Cobertura ampliada ao longo do dia: QuintoAndar passou a ler todos os resultados, "
                   "ZAP/Viva Real passaram a ler os mais recentes primeiro. Contagens e medianas de 11/10 "
                   "não são comparáveis com dias anteriores; anúncios já existentes foram marcados como carga inicial."),
    ("2026-11-03", "R$/m² do aluguel padronizado pelo valor total (aluguel + condomínio + IPTU) em todo o painel, inclusive "
                   "no indicador e na evolução. A série desde 11/10 foi recalculada nessa base a partir do histórico de preço "
                   "de cada anúncio; passa a incluir os anúncios do QuintoAndar que não separam o aluguel do condomínio."),
    ("2026-11-03", "Anúncios duplicados agrupados: a mesma unidade (rua, área, preço e negócio iguais) em mais de um portal "
                   "ou republicada no mesmo portal conta uma vez em contagens, medianas e novos. Contagens a partir desta data "
                   "ficam menores que as anteriores."),
    ("2026-11-07", "Jardim Paulista retirado do monitoramento de anúncios: sai das contagens, medianas, listas e alertas. "
                   "Somas que combinam bairros a partir desta data cobrem só Itaim Bibi e Vila Nova Conceição. "
                   "Na aba Transações (ITBI) o bairro continua."),
    ("2026-11-03", "Inclusão do bairro Jardim Paulista. Os anúncios dele entram como carga inicial (não contam como novos) "
                   "e as somas que combinam bairros (ex.: anúncios monitorados) passam a incluí-lo a partir desta data."),
]


def ppm2(d: dict) -> float | None:
    """R$/m²: preço de venda/lance, ou valor total do aluguel (aluguel + condomínio + IPTU) por m² ao mês."""
    p = (d.get("total_monthly") or d.get("price")) if d.get("business") == "aluguel" else d.get("price")
    if p and d.get("area"):
        return round(p / d["area"], 2)
    return None


def _price_at(hist: list, end_ts: float) -> float | None:
    before = [p for t, p in hist if _ts(t) <= end_ts]
    return before[-1] if before else (hist[0][1] if hist else None)


def rebase_rent_history(history: list[dict], catalog: dict, state: dict) -> list[dict]:
    """Recalcula, para dias antigos, a mediana de R$/m² do aluguel na base total (a série era registrada
    sem condomínio). Usa o histórico de preço de cada anúncio ainda na memória do monitor; anúncios
    vistos no dia (com a mesma tolerância de 'saiu do ar' do painel) entram na mediana do dia."""
    seen = state.get("seen", {})
    rents = []
    for key, d in catalog.items():
        s = seen.get(key)
        if d.get("business") != "aluguel" or not s or not d.get("area"):
            continue
        rents.append((d, s, GONE_AFTER_BY_SOURCE.get(d["source"], GONE_AFTER_SECONDS)))
    for h in history:
        if h.get("rent_basis") == "total":
            continue
        end = datetime.fromisoformat(h["date"] + "T23:59:59+00:00").timestamp()
        by_nb: dict[str, list[float]] = {}
        for d, s, limit in rents:
            if _ts(s["first_seen"]) > end or _ts(s["last_seen"]) < end - 86400 - limit:
                continue
            p = _price_at(s.get("hist") or [], end) or d.get("total_monthly") or d.get("price")
            if p:
                by_nb.setdefault((d.get("neighborhood") or "").strip(), []).append(p / d["area"])
        for k, g in h["groups"].items():
            biz, nb = k.split("|", 1)
            if biz == "aluguel":
                vals = by_nb.get(nb)
                g["ppm2"] = round(median(vals), 2) if vals else None
        h["rent_basis"] = "total"
    return history


def snapshot(active: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for d in active:
        groups.setdefault(f"{d['business']}|{d['neighborhood'].strip()}", []).append(d)
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


def group(rows: list[dict]) -> tuple[list[dict], dict]:
    """Uma linha por unidade. O representante é o anúncio ativo visto primeiro (ou o mais antigo, se
    nenhum está ativo); a unidade herda a 1ª data em que qualquer cópia apareceu, para que uma
    republicação não conte como novidade. As demais cópias ficam em `also`."""
    groups: dict[str, list[dict]] = {}
    out = []
    for r in rows:
        k = dedupe.key(r)
        if k:
            groups.setdefault(k, []).append(r)
        else:
            out.append(r)
    merged = {"grupos": 0, "copias": 0, "por_fonte": {}}
    for k, items in groups.items():
        items.sort(key=lambda r: (not r["active"], r["first_seen"]))
        rep, rest = items[0], items[1:]
        if rest:
            first = min(items, key=lambda r: r["first_seen"])
            rep = {**rep, "first_seen": first["first_seen"], "initial": first["initial"], "active": any(r["active"] for r in items),
                   "also": [{"source": r["source"], "url": r["url"], "active": r["active"], "first_seen": r["first_seen"]} for r in rest]}
            if rep["active"]:
                merged["grupos"] += 1
                for r in rest:
                    if r["active"]:
                        merged["copias"] += 1
                        merged["por_fonte"][r["source"]] = merged["por_fonte"].get(r["source"], 0) + 1
        out.append(rep)
    return out, merged


def build(cfg: Config, state: dict, results: dict[str, list[Listing]], errors: dict[str, str], macro: dict | None = None) -> dict:
    catalog: dict = _load(CATALOG, {})
    for listings in results.values():
        for l in listings:
            catalog[l.key] = l.to_dict()
    # Só guarda o que o monitor ainda lembra (mesma janela de 30 dias do estado).
    # Só bairros configurados: um bairro retirado do config.yaml some do painel na próxima coleta.
    nbs = cfg.neighborhoods_norm
    catalog = {k: v for k, v in catalog.items() if k in state["seen"] and norm(v.get("neighborhood")) in nbs}

    last_ok = state.get("last_ok", {})
    rows = []
    for key, d in catalog.items():
        s = state["seen"][key]
        # Ausente numa coleta isolada não significa que saiu do ar: tolera 24 h.
        ok = last_ok.get(d["source"])
        limit = GONE_AFTER_BY_SOURCE.get(d["source"], GONE_AFTER_SECONDS)
        active = bool(ok) and _ts(ok) - _ts(s["last_seen"]) <= limit
        d = {**d, "neighborhood": (d.get("neighborhood") or "").strip()}
        rows.append({
            **d,
            "key": key,
            "ppm2": ppm2(d),
            "first_seen": s["first_seen"],
            "last_seen": s["last_seen"],
            "initial": bool(s.get("initial")),
            "hist": s.get("hist", []),
            "active": active,
            "in_budget": cfg.within_cap(
                d["business"], (d.get("total_monthly") or d.get("price")) if d["business"] == "aluguel" else d.get("price")
            ),
        })

    rows, merged = group(rows)

    today = datetime.now(timezone.utc).date().isoformat()
    history = rebase_rent_history([h for h in _load(HISTORY, []) if h["date"] != today], catalog, state)
    history.append({"date": today, "rent_basis": "total", "groups": snapshot([r for r in rows if r["active"]])})
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
        "prev_run": state.get("prev_run"),
        "macro": macro if macro is not None else _load(ROOT / "data" / "macro.json", None),
        "source_info": SOURCE_INFO,
        "notes": [{"date": a, "text": b} for a, b in METHOD_NOTES],
        "criteria": {
            "neighborhoods": cfg.neighborhoods,
            "min_area": cfg.min_area,
            "businesses": cfg.businesses,
            "max_sale": cfg.max_sale,
            "max_rent": cfg.max_rent,
            "area_polygon": cfg.area_polygon,
        },
        "sources": sources,
        "dedupe": merged,
        "listings": rows,
        "history": history,
    }
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=1, sort_keys=True))
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1))
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return data
