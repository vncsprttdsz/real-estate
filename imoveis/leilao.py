"""Leilões: custo total estimado, descontos (avaliação, anúncios do bairro, ITBI do prédio) e textos dos avisos.

Custo total = lance + comissão do leiloeiro (5%; 0 em venda direta) + ITBI (3%) + registro/escritura (~1%).
Dívidas (IPTU, condomínio) e desocupação dependem do edital: não entram na conta, só no alerta.
"""
from __future__ import annotations

import json
import re
from datetime import date
import unicodedata
from statistics import median

from .common import ROOT

TRANSACOES = ROOT / "docs" / "transacoes.json"

# Mesma normalização de endereço de docs/itbi.js (streetKey/addrKey)
TYPES = {"R", "RUA", "AV", "AVENIDA", "AL", "ALAMEDA", "PC", "PCA", "PRACA", "TV", "TRAVESSA", "LGO", "LARGO", "VL", "VIELA",
         "PQ", "PARQUE", "EST", "ESTRADA", "PSG", "PASSAGEM"}
ABBR = {"DOUTOR": "DR", "DOUTORA": "DRA", "PROFESSOR": "PROF", "PROFESSORA": "PROFA", "SENADOR": "SEN", "GENERAL": "GEN",
        "PADRE": "PE", "SANTO": "STO", "SANTA": "STA", "SAO": "S", "CAPITAO": "CAP", "CORONEL": "CEL", "DEPUTADO": "DEP",
        "ENGENHEIRO": "ENG", "BRIGADEIRO": "BRIG", "PRESIDENTE": "PRES", "MINISTRO": "MIN", "GOVERNADOR": "GOV", "MARECHAL": "MAL",
        "COMENDADOR": "COM", "DESEMBARGADOR": "DES", "MAESTRO": "MTO", "JORNALISTA": "JORN", "VEREADOR": "VER", "CARDEAL": "CARD",
        "DONA": "D", "DOM": "D", "EMBAIXADOR": "EMB", "ALMIRANTE": "ALM", "PREFEITO": "PREF", "TENENTE": "TEN", "ARQUITETO": "ARQ",
        "MONSENHOR": "MONS", "CONSELHEIRO": "CONS", "MAJOR": "MAJ", "JUNIOR": "JR", "FILHO": "FO",
        "DO": "", "DA": "", "DE": "", "DOS": "", "DAS": "", "E": ""}


def plain(t) -> str:
    t = unicodedata.normalize("NFD", str(t or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", t)).strip()


def street_key(name) -> str:
    w = [ABBR.get(t, t) for t in plain(name).split(" ") if t]
    if len(w) > 1 and w[0] in TYPES:
        w = w[1:]
    return " ".join(x for x in w if x)


def addr_key(street, number) -> str:
    n = "" if number in (None, "") else str(int(re.sub(r"\D", "", str(number)) or 0))
    return street_key(street) + "|" + n


def parse_address(text: str) -> str | None:
    """'Rua Joaquim Floriano, 100, apto 51' → chave 'JOAQUIM FLORIANO|100' (None sem número)."""
    m = re.match(r"^(.*?)[\s,]+(?:n[ºo°.]?\s*)?(\d{1,5})\b", str(text or "").strip(), re.I)
    return addr_key(m.group(1), m.group(2)) if m else None


class ItbiIndex:
    """R$/m² (área útil equivalente) das transações de cada prédio nos últimos meses publicados no painel."""

    def __init__(self, path=TRANSACOES):
        self.by_addr: dict[str, list[float]] = {}
        self.period = ""
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        f = {c: i for i, c in enumerate(d.get("campos", []))}
        fator = (d.get("parametros") or {}).get("fator_area_util") or 0.72
        p = d.get("parametros") or {}
        self.period = f"{p.get('mes_inicio', '')[5:7]}/{p.get('mes_inicio', '')[:4]}–{p.get('mes_fim', '')[5:7]}/{p.get('mes_fim', '')[:4]}"
        for x in d.get("linhas", []):
            if re.search(r"[ol]", x[f["flags"]] or ""):   # sem outliers nem compras em bloco
                continue
            ac, v = x[f["area_construida"]], x[f["valor"]]
            if ac and v:
                self.by_addr.setdefault(addr_key(x[f["logradouro"]], x[f["numero"]]), []).append(v / (ac * fator))

    def building(self, address: str, min_n: int = 3) -> tuple[int, float] | None:
        k = parse_address(address)
        vals = self.by_addr.get(k or "", [])
        return (len(vals), median(vals)) if k and len(vals) >= min_n else None


def total_cost(bid: float | None, kind: str | None, costs: dict) -> float | None:
    if not bid:
        return None
    pct = (0 if kind == "venda direta" else costs.get("comissao_pct", 5)) + costs.get("itbi_pct", 3) + costs.get("registro_pct", 1)
    return bid * (1 + pct / 100)


def next_round(rounds: list | None, today: str) -> dict | None:
    """Leilão que define o preço do lote: o de menor lance entre os que ainda vão acontecer (se o 1º não tiver
    lance, o 2º sai mais barato); se todos passaram, o último."""
    rs = sorted([r for r in rounds or [] if r.get("bid")], key=lambda r: r.get("date") or "9999")
    future = [r for r in rs if (r.get("date") or "9999") >= today]
    return min(future, key=lambda r: r["bid"]) if future else (rs[-1] if rs else None)


def assess(d: dict, costs: dict, market=None, itbi: ItbiIndex | None = None) -> dict:
    """Números do lote para o aviso: custo total, desconto vs avaliação, vs anúncios e vs ITBI do prédio."""
    bid, area = d.get("price"), d.get("area")
    out = {"cost": total_cost(bid, d.get("auction_kind"), costs)}
    if bid and d.get("appraisal"):
        out["disc_appraisal"] = bid / d["appraisal"] - 1
    if market and bid and area:
        g = market.gap({**d, "business": "venda"})
        if g:
            out["vs_market"] = (g.pct, g.median, g.scope)
    if itbi and bid and area and d.get("address"):
        b = itbi.building(d["address"])
        if b:
            out["vs_itbi"] = (bid / area / b[1] - 1, b[1], b[0], itbi.period)
    return out


def brl(v) -> str:
    return "R$ " + f"{v:,.0f}".replace(",", ".") if v is not None else "?"


def pct(v) -> str:
    return f"{v * 100:+.0f}%".replace("-", "−")


def describe(x: dict) -> str:
    """Bloco do resumo diário para um lote (x = item do resumo, com o dict 'auction')."""
    a = x.get("auction") or {}
    head = " · ".join(p for p in (x.get("nb"), f"{x['area']:.0f} m²" if x.get("area") else "",
                                  f"{x['bedrooms']} qts" if x.get("bedrooms") else "", a.get("kind_label")) if p)
    lines = [head]
    today = date.today().isoformat()
    for r in a.get("rounds") or []:
        d = r.get("date") or ""
        lines.append(f"{r.get('n', '')}º leilão{(' ' + d[8:10] + '/' + d[5:7]) if d else ''}: {brl(r.get('bid'))}"
                     + (" (já passou)" if d and d < today else ""))
    if not a.get("rounds") and x.get("price"):
        lines.append(f"Lance/preço: {brl(x['price'])}")
    bits = []
    if a.get("appraisal"):
        bits.append(f"avaliação {brl(a['appraisal'])}" + (f" ({pct(a['disc_appraisal'])})" if a.get("disc_appraisal") is not None else ""))
    if a.get("cost"):
        bits.append(f"custo total ≈ {brl(a['cost'])}")
    if bits:
        lines.append(" · ".join(bits))
    cmp_ = []
    if a.get("vs_market"):
        cmp_.append(f"{pct(a['vs_market'][0])} vs. anúncios")
    if a.get("vs_itbi"):
        v = a["vs_itbi"]
        cmp_.append(f"{pct(v[0])} vs. {v[2]} vendas no prédio (ITBI)")
    if cmp_:
        lines.append(" · ".join(cmp_))
    flags = []
    if a.get("occupied") is True:
        flags.append("⚠️ ocupado: desocupação por conta do comprador")
    elif a.get("occupied") is False:
        flags.append("desocupado")
    if a.get("financing") is True:
        flags.append("aceita financiamento")
    if flags:
        lines.append(" · ".join(flags))
    if x.get("addr"):
        lines.append(x["addr"])
    lines.append(x["url"])
    return "\n".join(lines)
