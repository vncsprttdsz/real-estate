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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .common import ROOT, Config, Listing, norm
from .http import Http
from .notify import Notifier, brl, describe
from . import bargain, dashboard, dedupe, details, geo, macro
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


SP = ZoneInfo("America/Sao_Paulo")


def now_sp() -> datetime:
    return datetime.now(SP)


def digest_add(state: dict, day: str, kind: str, l: Listing, gap=None, old: float | None = None) -> None:
    """Guarda um evento de compra para o resumo do dia (compacto; uma entrada por imóvel e tipo)."""
    items = state.setdefault("digest", {}).setdefault(day, [])
    if any(x["key"] == l.key and x["kind"] == kind for x in items):
        return
    items.append({"kind": kind, "key": l.key, "business": l.business, "nb": l.neighborhood.strip(), "area": l.area,
                  "price": l.ref_price, "old": old, "url": l.url, "addr": l.address or l.title, "bedrooms": l.bedrooms,
                  "source": l.source, "pct": round(gap.pct, 4) if gap else None, "scope": gap.scope if gap else None,
                  "median": round(gap.median) if gap else None})


def digest_messages(day: str, items: list[dict], cfg: Config) -> tuple[str, list[tuple[str, str]]]:
    """Resumo diário de compra: contagem por bairro, quedas e oportunidades."""
    d = f"{day[8:10]}/{day[5:7]}"
    new = [x for x in items if x["kind"] == "novo"]
    drops = [x for x in items if x["kind"] == "queda"]
    lim = -(cfg.bargain_pct or 0) / 100
    opp = sorted([x for x in items if x["pct"] is not None and cfg.bargain_pct and x["pct"] <= lim], key=lambda x: x["pct"])
    by_nb: dict = {}
    for x in new:
        by_nb[x["nb"]] = by_nb.get(x["nb"], 0) + 1
    cap = f", até {brl(cfg.max_sale)}" if cfg.max_sale else ""
    head = (f"{len(new)} anúncio{'s' if len(new) != 1 else ''} novo{'s' if len(new) != 1 else ''} de compra "
            f"({cfg.alert_min_area:.0f} m² ou mais{cap})" + (": " + " · ".join(f"{k} {v}" for k, v in sorted(by_nb.items(), key=lambda kv: -kv[1])) if new else "."))
    ppms = sorted(x["price"] / x["area"] for x in new if x["price"] and x["area"])
    if ppms:
        head += f"\nR$/m² mediano dos novos: {brl(ppms[len(ppms) // 2])}."
    if drops:
        head += f"\nQuedas de preço: {len(drops)}."
    out = [(head, head)]

    def line(x):
        pct = f"{x['pct'] * 100:+.0f}%".replace("-", "−") if x["pct"] is not None else ""
        ppm = brl(x["price"] / x["area"]) + "/m²" if x["price"] and x["area"] else ""
        parts = [p for p in (pct, x["nb"], f"{x['area']:.0f} m²" if x["area"] else "", f"{x['bedrooms']} qts" if x["bedrooms"] else "",
                             brl(x["price"]), ppm) if p]
        t = " · ".join(parts)
        if x["kind"] == "queda" and x.get("old"):
            t += f"\nbaixou de {brl(x['old'])}"
        if x.get("addr"):
            t += "\n" + x["addr"]
        return t + "\n" + x["url"]

    if opp:
        txt = f"💎 Possíveis oportunidades ({cfg.bargain_pct:.0f}%+ abaixo da mediana do seu recorte):\n\n" + "\n\n".join(line(x) for x in opp[:5])
        if len(opp) > 5:
            txt += f"\n\n… e mais {len(opp) - 5}."
        out.append((txt, txt))
    elif new:
        best = sorted([x for x in new if x["pct"] is not None], key=lambda x: x["pct"])[:3]
        if best:
            txt = "Nenhum bem abaixo da mediana hoje. Os de menor R$/m² vs. a mediana do seu recorte:\n\n" + "\n\n".join(line(x) for x in best)
            out.append((txt, txt))
    return f"🏢 Compra — resumo de {d}", out


def send_digests(state: dict, cfg: Config, notifier, now: datetime, dry_run: bool = False) -> list[str]:
    """Envia o resumo do dia a partir de digest_hour; dias anteriores pendentes (coleta perdida) vão na próxima."""
    if not cfg.digest:
        return []
    today, sent = now.strftime("%Y-%m-%d"), []
    pending = state.setdefault("digest", {})
    days = sorted(d for d in pending if d < today or (d == today and now.hour >= cfg.digest_hour))
    if now.hour >= cfg.digest_hour and today not in days and state.get("digest_last") != today:
        days.append(today)                                   # dia sem novidade: avisa mesmo assim (sinal de vida)
    for day in days:
        subject, items = digest_messages(day, pending.get(day, []), cfg)
        if cfg.dashboard_url:
            items[-1] = (items[-1][0] + f"\n\nPainel: {cfg.dashboard_url}#anuncios", items[-1][1])
        if dry_run:
            print(subject); [print(t) for t, _ in items]
        else:
            notifier.send(subject, items)
        pending.pop(day, None)
        state["digest_last"] = max(state.get("digest_last") or "", day)
        sent.append(day)
    return sent


def market_rows(results: dict, catalog: dict, state: dict, ts: str, days: int = 7) -> list[dict]:
    """Mercado para as medianas: anúncios desta coleta + os do catálogo vistos nos últimos dias; uma vez por imóvel."""
    cutoff = datetime.fromisoformat(ts).timestamp() - days * 86400
    rows = {}
    for k, d in catalog.items():
        s = state["seen"].get(k)
        if s and datetime.fromisoformat(s["last_seen"]).timestamp() >= cutoff:
            rows[k] = d
    for ls in results.values():
        for l in ls:
            rows[l.key] = l.to_dict()
    out, units = [], set()
    for k, d in rows.items():
        u = dedupe.key(d) or k
        if u not in units:
            units.add(u)
            out.append(d)
    return out


def run(dry_run: bool = False) -> int:
    cfg = Config.load()
    state = load_state()
    results, errors = collect(cfg, state)
    notifier = Notifier()
    ts = now()

    # Carga inicial por fonte E por bairro: um bairro incluído depois não gera enxurrada de "novos".
    if "initialized_nbs" not in state:
        known = {norm(v.get("neighborhood", "")) for v in dashboard._load(dashboard.CATALOG, {}).values()}
        state["initialized_nbs"] = {s: sorted(known & cfg.neighborhoods_norm) for s in state["initialized"]}

    # Bairro retirado do config.yaml: esquece os anúncios dele e a carga inicial (se voltar, entra de novo como carga inicial).
    cat_prev = dashboard._load(dashboard.CATALOG, {})
    for k in [k for k in state["seen"] if k in cat_prev and norm(cat_prev[k].get("neighborhood")) not in cfg.neighborhoods_norm]:
        del state["seen"][k]
    state["initialized_nbs"] = {s: [n for n in v if n in cfg.neighborhoods_norm] for s, v in state["initialized_nbs"].items()}

    new_items, drops, first_load = [], [], {}
    # Unidades já conhecidas (qualquer fonte): republicação ou cópia em outro portal não gera alerta.
    catalog = dashboard._load(dashboard.CATALOG, {})
    known_units = {dedupe.key(d) for k, d in catalog.items() if k in state["seen"]} - {None}
    dup_skipped = 0
    new_outside, price_cuts = [], []
    for source, listings in results.items():
        src_new = source not in state["initialized"]
        nbs_done = set(state["initialized_nbs"].get(source, []))
        for l in listings:
            prev = state["seen"].get(l.key)
            if prev is None:
                unit = dedupe.key(l.to_dict())
                initializing = src_new or norm(l.neighborhood) not in nbs_done
                if initializing:
                    label = source if src_new else f"{source} ({l.neighborhood})"
                    first_load[label] = first_load.get(label, 0) + 1
                elif unit and unit in known_units:
                    if cfg.in_budget(l):
                        dup_skipped += 1
                elif cfg.in_budget(l):
                    new_items.append(l)
                elif cfg.bargain_pct and not cfg.bargain_budget_only:
                    new_outside.append(l)
                if unit:
                    known_units.add(unit)
                state["seen"][l.key] = {
                    "first_seen": ts, "last_seen": ts, "price": l.ref_price,
                    "hist": [[ts, l.ref_price]] if l.ref_price else [],
                    **({"initial": True} if initializing else {}),
                }
                continue
            old_price = prev.get("price")
            if old_price and l.ref_price and l.ref_price < old_price:
                price_cuts.append((l, old_price))
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
        if src_new:
            state["initialized"].append(source)
        # Marca como inicializados os bairros que trouxeram anúncios (ou todos, no Zuk, onde vazio é normal).
        seen_nbs = {norm(l.neighborhood) for l in listings}
        done = nbs_done | (cfg.neighborhoods_norm if source == "zuk" else seen_nbs & cfg.neighborhoods_norm)
        state["initialized_nbs"][source] = sorted(done)

    # Abaixo da mediana: anúncios novos, ou que baixaram e cruzaram o limite.
    bargains = []
    if cfg.bargain_pct:
        rows = market_rows(results, catalog, state, ts)
        if cfg.bargain_budget_only:   # compara com o seu recorte: anúncios dentro do orçamento
            rows = [d for d in rows if cfg.within_cap(d.get("business"), (d.get("total_monthly") or d.get("price")) if d.get("business") == "aluguel" else d.get("price"))]
        market = bargain.Market(rows, cfg.bargain_min_n, " no seu orçamento" if cfg.bargain_budget_only else "")
        lim = -cfg.bargain_pct / 100
        for l in new_items + new_outside:
            g = market.gap(l.to_dict())
            if g and g.pct <= lim:
                bargains.append((l, g, None))
        for l, old in price_cuts:
            if cfg.bargain_budget_only and not cfg.in_budget(l):
                continue
            d = l.to_dict()
            g, g_old = market.gap(d), market.gap(d, old)
            if g and g.pct <= lim and (g_old is None or g_old.pct > lim):
                bargains.append((l, g, old))
        seen_b, uniq_b = set(), []
        for item in bargains:
            k = dedupe.key(item[0].to_dict()) or item[0].key
            if k not in seen_b:
                seen_b.add(k)
                uniq_b.append(item)
        bargains = sorted(uniq_b, key=lambda x: x[1].pct)
        shown = {b[0].key for b in bargains}
        new_items = [l for l in new_items if l.key not in shown]   # não repete no aviso de "novos"
        drops = [x for x in drops if x[0].key not in shown]

    # Mesma unidade em vários anúncios: um aviso de queda por unidade.
    seen_drop, uniq = set(), []
    for item in drops:
        k = dedupe.key(item[0].to_dict()) or item[0].key
        if k not in seen_drop:
            seen_drop.add(k)
            uniq.append(item)
    drops = uniq

    # Avisos: só imóveis com a área mínima de alerta; negócios do resumo diário vão para o resumo.
    in_area = lambda l: (not cfg.area_polygon or (geo.inside(l.lat, l.lon, cfg.area_polygon) if l.lat and l.lon else cfg.area_keep_unknown))
    big = lambda l: ((not cfg.alert_min_area or (l.area is not None and l.area >= cfg.alert_min_area))
                     and (not cfg.alert_min_bedrooms or l.bedrooms is None or l.bedrooms >= cfg.alert_min_bedrooms)
                     and in_area(l))
    # Resumo do dia D cobre de digest_hour de D−1 a digest_hour de D: o que aparece depois do envio vai para o de amanhã.
    when = datetime.fromisoformat(ts).astimezone(SP)
    day = (when + timedelta(days=1) if when.hour >= cfg.digest_hour else when).strftime("%Y-%m-%d")
    if cfg.digest:
        gap_of = {l.key: g for l, g, _ in bargains}
        for l in new_items:
            if l.business in cfg.digest and big(l):
                g = gap_of.get(l.key) or (market.gap(l.to_dict()) if cfg.bargain_pct else None)
                digest_add(state, day, "novo", l, g)
        for l, g, old in bargains:
            if l.business in cfg.digest and big(l):
                digest_add(state, day, "novo" if old is None else "queda", l, g, old)
        for l, old, _ in drops:
            if l.business in cfg.digest and big(l):
                digest_add(state, day, "queda", l, market.gap(l.to_dict()) if cfg.bargain_pct else None, old)
    now_imm = lambda l: l.business in cfg.immediate and big(l)
    new_items = [l for l in new_items if now_imm(l)]
    drops = [x for x in drops if now_imm(x[0])]
    bargains = [x for x in bargains if now_imm(x[0])]

    # Destaques (⭐ andar alto / vista livre): descrição só dos poucos anúncios que vão gerar aviso agora.
    stars: dict[str, list[str]] = {}
    if cfg.highlight and not dry_run:
        cand = {l.key: l for l in new_items + [x[0] for x in drops] + [x[0] for x in bargains]}
        http_d = Http(1.0, 2.0)
        for l in list(cand.values())[:12]:
            tags = details.highlights(l, details.fetch_description(http_d, l))
            if tags:
                stars[l.key] = tags
        star_first = lambda key: 0 if key in stars else 1
        new_items.sort(key=lambda l: star_first(l.key))
        bargains.sort(key=lambda x: (star_first(x[0].key), x[1].pct))
    star_note = lambda l: ("⭐ " + " · ".join(stars[l.key]).capitalize()) if l.key in stars else ""

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
    state["prev_run"] = state.get("last_run")
    state["last_run"] = ts
    state["last_errors"] = errors

    if dry_run:
        for l in new_items:
            print("NOVO:", describe(l)[0])
        for l, g, old in bargains:
            print(f"ABAIXO DA MEDIANA ({g.pct:+.0%} vs. {g.scope}):", describe(l)[0])
        send_digests(json.loads(json.dumps(state)), cfg, notifier, now_sp(), dry_run=True)
        print(f"[dry-run] abaixo_mediana={len(bargains)} novos={len(new_items)} duplicados={dup_skipped} quedas={len(drops)} carga_inicial={first_load} erros={list(errors)}")
        return 0

    send_digests(state, cfg, notifier, now_sp())
    if bargains:
        n = len(bargains)
        items = []
        for l, g, old in bargains:
            unit = "/m²·mês" if l.business == "aluguel" else "/m²"
            note = (f"{g.pct * 100:+.0f}% vs. mediana ({g.scope}): {brl(g.ppm)} vs. {brl(g.median)}{unit}".replace("-", "−")
                    + (f" · baixou de {brl(old)} para {brl(l.ref_price)}" if old else ""))
            items.append(describe(l, " · ".join(x for x in (star_note(l), note) if x)))
        notifier.send(f"💎 {n} imóve{'is' if n > 1 else 'l'} {cfg.bargain_pct:.0f}% ou mais abaixo da mediana", items)
    if new_items:
        n = len(new_items)
        what = ("aluguel" if n == 1 else "aluguéis") if all(l.business == "aluguel" for l in new_items) else ("anúncio" if n == 1 else "anúncios")
        subject = f"🏠 {n} {what} novo{'s' if n > 1 else ''} nos seus critérios"
        if stars:
            subject += f" · ⭐ {sum(1 for l in new_items if l.key in stars)} com destaque"
        notifier.send(subject, [describe(l, star_note(l)) for l in new_items])
    if drops:
        items = [describe(l, " · ".join(x for x in (star_note(l), f"{why}: {brl(old)} → {brl(l.ref_price)}") if x)) for l, old, why in drops]
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
    try:
        macro_data = macro.update()
    except Exception as e:   # o painel segue sem o simulador atualizado
        print(f"[aviso] Focus: {e}", file=sys.stderr)
        macro_data = None
    dashboard.build(cfg, state, results, errors, macro_data)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))
    print(f"abaixo_mediana={len(bargains)} novos={len(new_items)} duplicados={dup_skipped} quedas={len(drops)} carga_inicial={first_load} erros={list(errors)}")
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
