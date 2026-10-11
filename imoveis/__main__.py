"""Executa um ciclo de monitoramento: coleta, compara com o estado salvo e notifica.

Uso:
    python -m imoveis              # ciclo normal
    python -m imoveis --dry-run    # não notifica nem grava estado
    DEBUG=1 python -m imoveis      # salva respostas brutas em debug/
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone

from .common import ROOT, Config, Listing
from .http import Http
from .notify import Notifier, brl, describe
from . import dashboard
from .sources import grupozap, quintoandar, zuk

STATE = ROOT / "data" / "state.json"
CURRENT = ROOT / "data" / "anuncios.json"
REPORT = ROOT / "ANUNCIOS.md"
FAIL_ALERT_AFTER = 3   # ciclos seguidos com erro antes de avisar
REMOVE_AFTER_DAYS = 30


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    try:
        st = json.loads(STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    st.setdefault("seen", {})          # key -> {first_seen, last_seen, price}
    st.setdefault("failures", {})      # fonte -> ciclos seguidos com erro
    st.setdefault("zuk_rejected", [])
    st.setdefault("initialized", [])   # fontes que já fizeram a carga inicial
    return st


def collect(cfg: Config, state: dict) -> tuple[dict[str, list[Listing]], dict[str, str]]:
    http = Http()
    results, errors = {}, {}
    known = set(state["seen"])
    rejected = set(state["zuk_rejected"])
    jobs = {
        "zap": lambda: grupozap.fetch(http, cfg, "zap"),
        "vivareal": lambda: grupozap.fetch(http, cfg, "vivareal"),
        "quintoandar": lambda: quintoandar.fetch(http, cfg),
        "zuk": lambda: zuk.fetch(http, cfg, known, rejected),
    }
    for name, job in jobs.items():
        if not cfg.sources.get(name, True):
            continue
        try:
            found = job()
            results[name] = [l for l in found if cfg.matches(l)]
            if name == "zuk":  # não reabrir leilões fora dos critérios a cada ciclo
                rejected.update(l.id for l in found if not cfg.matches(l))
            print(f"{name}: {len(found)} coletados, {len(results[name])} dentro dos critérios")
        except Exception as e:
            errors[name] = str(e)[:500]
            print(f"{name}: ERRO {e}", file=sys.stderr)
            traceback.print_exc()
    # ZAP e Viva Real usam o mesmo estoque (mesmos ids): mantém um só aviso por imóvel.
    if "zap" in results and "vivareal" in results:
        zap_ids = {l.id for l in results["zap"]}
        results["vivareal"] = [l for l in results["vivareal"] if l.id not in zap_ids]
    state["zuk_rejected"] = sorted(rejected)[-2000:]
    return results, errors


def write_report(listings: list[Listing], cfg: Config) -> None:
    order = {"leilao": 0, "venda": 1, "aluguel": 2}
    listings = sorted(listings, key=lambda l: (order.get(l.business, 9), l.ref_price or 0))
    CURRENT.write_text(json.dumps([l.to_dict() for l in listings], ensure_ascii=False, indent=1))
    lines = [
        "# Anúncios atuais dentro dos critérios",
        "",
        f"Atualizado em {datetime.now(timezone.utc):%d/%m/%Y %H:%M} UTC · {len(listings)} anúncios. "
        f"Orçamento: compra até {brl(cfg.max_sale) if cfg.max_sale else 'sem limite'}, "
        f"aluguel até {brl(cfg.max_rent) + '/mês' if cfg.max_rent else 'sem limite'}. "
        "Critérios em [`config.yaml`](config.yaml).",
        "",
    ]
    titles = {"leilao": "Leilões", "venda": "Venda", "aluguel": "Aluguel"}
    for b, title in titles.items():
        group = [l for l in listings if l.business == b]
        if not group:
            continue
        lines += [f"## {title} ({len(group)})", "", "| Bairro | Área | Preço | Quartos | Fonte | Link |", "|---|---|---|---|---|---|"]
        for l in group:
            price = brl(l.total_monthly or l.price) + ("/mês" if b == "aluguel" else "")
            lines.append(
                f"| {l.neighborhood} | {f'{l.area:.0f} m²' if l.area else '?'} | {price} | "
                f"{l.bedrooms or ''} | {l.source} | [abrir]({l.url}) |"
            )
        lines.append("")
    REPORT.write_text("\n".join(lines))


def run(dry_run: bool = False) -> int:
    cfg = Config.load()
    state = load_state()
    results, errors = collect(cfg, state)
    notifier = Notifier()
    ts = now()

    new_items, drops, first_load = [], [], {}
    for source, listings in results.items():
        initializing = source not in state["initialized"]
        for l in listings:
            prev = state["seen"].get(l.key)
            if prev is None:
                if initializing:
                    first_load.setdefault(source, 0)
                    first_load[source] += 1
                elif cfg.in_budget(l):
                    new_items.append(l)
                state["seen"][l.key] = {
                    "first_seen": ts, "last_seen": ts, "price": l.ref_price,
                    "hist": [[ts, l.ref_price]] if l.ref_price else [],
                    **({"initial": True} if initializing else {}),
                }
                continue
            old_price = prev.get("price")
            if cfg.in_budget(l) and old_price and l.ref_price and l.ref_price < old_price:
                if not cfg.within_cap(l.business, old_price):
                    drops.append((l, old_price, "Baixou e entrou no seu orçamento"))
                elif cfg.price_drop_pct and l.ref_price <= old_price * (1 - cfg.price_drop_pct / 100):
                    drops.append((l, old_price, "Queda de preço"))
            prev["last_seen"] = ts
            if l.ref_price and l.ref_price != old_price:
                prev["price"] = l.ref_price
                prev.setdefault("hist", []).append([ts, l.ref_price])
                prev["hist"] = prev["hist"][-30:]
        if initializing:
            state["initialized"].append(source)

    # Esquece anúncios sumidos há muito tempo (só de fontes que funcionaram neste ciclo).
    cutoff = datetime.now(timezone.utc).timestamp() - REMOVE_AFTER_DAYS * 86400
    for key in list(state["seen"]):
        src = key.split(":", 1)[0]
        if src in results and datetime.fromisoformat(state["seen"][key]["last_seen"]).timestamp() < cutoff:
            del state["seen"][key]

    # Alertas de fontes quebradas (site mudou ou bloqueou).
    broken = []
    for name in ("zap", "vivareal", "quintoandar", "zuk"):
        if name in errors:
            state["failures"][name] = state["failures"].get(name, 0) + 1
            if state["failures"][name] == FAIL_ALERT_AFTER:
                broken.append(name)
        elif name in results:
            state["failures"][name] = 0
            state.setdefault("last_ok", {})[name] = ts
    state["last_run"] = ts
    state["last_errors"] = errors

    if dry_run:
        for l in new_items:
            print("NOVO:", describe(l)[0])
        print(f"[dry-run] novos={len(new_items)} quedas={len(drops)} carga_inicial={first_load} erros={list(errors)}")
        return 0

    if new_items:
        n = len(new_items)
        subject = f"🏠 {n} anúncio{'s' if n > 1 else ''} novo{'s' if n > 1 else ''} nos seus critérios"
        notifier.send(subject, [describe(l) for l in new_items])
    if drops:
        items = [describe(l, f"{why}: {brl(old)} → {brl(l.ref_price)}") for l, old, why in drops]
        notifier.send(f"📉 {len(drops)} anúncio(s) baixaram de preço", items)
    if first_load:
        summary = ", ".join(f"{k}: {v}" for k, v in first_load.items())
        notifier.send(
            "✅ Monitor de imóveis ativado",
            [(f"Carga inicial ({summary}). A partir de agora você recebe só os anúncios novos. "
              "A lista completa atual está em ANUNCIOS.md no repositório.", "")],
        )
    if broken:
        notifier.send(
            "⚠️ Monitor de imóveis: fonte com erro",
            [(f"{name} falhou {FAIL_ALERT_AFTER} ciclos seguidos: {errors[name]}", "") for name in broken],
        )

    all_current = [l for ls in results.values() for l in ls]
    # Mantém no relatório os anúncios das fontes que falharam neste ciclo.
    if CURRENT.exists():
        try:
            old = [Listing(**d) for d in json.loads(CURRENT.read_text())]
            all_current += [l for l in old if l.source in errors]
        except Exception:
            pass
    write_report([l for l in all_current if cfg.in_budget(l)], cfg)
    dashboard.build(cfg, state, results, errors)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))
    print(f"novos={len(new_items)} quedas={len(drops)} carga_inicial={first_load} erros={list(errors)}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-notify", action="store_true", help="envia uma mensagem de teste e sai")
    args = ap.parse_args()
    if args.test_notify:
        Notifier().send("🔔 Teste do monitor de imóveis", [("Se você recebeu isto, os alertas estão funcionando.", "Se você recebeu isto, os alertas estão funcionando.")])
        return
    sys.exit(run(args.dry_run))


if __name__ == "__main__":
    main()
