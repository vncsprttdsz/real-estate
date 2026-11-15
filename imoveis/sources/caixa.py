"""Caixa: imóveis retomados à venda (leilão, licitação, venda direta). Lista oficial em CSV, por estado.

Colunas (sondagem de 15/11): N° do imóvel; UF; Cidade; Bairro; Endereço; Preço; Valor de avaliação; Desconto;
Financiamento; Descrição; Modalidade de venda; Link de acesso. Na capital de SP, o estoque fica quase todo na
periferia (643 imóveis, 1 no entorno de Itaim/VNC); custa uma requisição por coleta.
"""
from __future__ import annotations

import csv
import io
import re

from ..common import Config, Listing, dump_debug, norm, to_float
from ..http import Http

CSV_URL = "https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_{uf}.csv"
APTO = re.compile(r"^\s*(apartamento|cobertura|flat|duplex|studio)", re.I)


def kind_of(modalidade: str) -> str:
    m = norm(modalidade)
    if "venda direta" in m or "venda online" in m:
        return "venda direta"
    return "banco"   # Leilão SFI / Licitação Aberta: venda de imóvel retomado pelo banco


def parse_rows(raw: str, cfg: Config) -> list[Listing]:
    lines = raw.splitlines()
    start = next((i for i, l in enumerate(lines[:15]) if "Bairro" in l and "Endere" in l), 0)
    rows = list(csv.reader(io.StringIO("\n".join(lines[start:])), delimiter=";"))
    if not rows:
        return []
    col = {norm(h).strip(): i for i, h in enumerate(rows[0])}

    def get(x, *names):
        for n in names:
            i = col.get(norm(n))
            if i is not None and i < len(x):
                return x[i].strip()
        return ""

    out = []
    for x in rows[1:]:
        if norm(get(x, "Cidade")) != norm(cfg.city):
            continue
        desc = get(x, "Descrição", "Descricao")
        if not APTO.search(desc):
            continue
        priv = re.search(r"([\d.,]+)\s*de\s*[áa]rea\s*privativa", desc, re.I)
        total = re.search(r"([\d.,]+)\s*de\s*[áa]rea\s*total", desc, re.I)
        area = to_float(priv.group(1).replace(".", ",") if priv else None) or to_float(total.group(1).replace(".", ",") if total else None)
        qts = re.search(r"(\d+)\s*(?:qto|quarto|dorm)", desc, re.I)
        lid = re.sub(r"\D", "", get(x, "N° do imóvel", "Nº do imóvel", "N do imovel"))
        if not lid:
            continue
        mod = get(x, "Modalidade de venda")
        out.append(Listing(
            source="caixa", id=lid, url=get(x, "Link de acesso") or f"https://venda-imoveis.caixa.gov.br/sistema/detalhe-imovel.asp?hdnimovel={lid}",
            business="leilao", title=f"{desc.split(',')[0].strip().title()} · Caixa · {mod}",
            neighborhood=get(x, "Bairro").title(), address=get(x, "Endereço", "Endereco").title(),
            area=area if area and area > 0 else None, price=to_float(get(x, "Preço", "Preco")),
            bedrooms=int(qts.group(1)) if qts else None,
            appraisal=to_float(get(x, "Valor de avaliação", "Valor de avaliacao")),
            auction_kind=kind_of(mod), financing=norm(get(x, "Financiamento")).startswith("sim"),
            extra=mod,
        ))
    return out


def fetch(http: Http, cfg: Config) -> list[Listing]:
    r = http.get(CSV_URL.format(uf=cfg.state.upper()), timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"caixa: HTTP {r.status_code}")
    raw = r.content.decode("latin-1", errors="replace")
    dump_debug("caixa-lista.csv", raw)
    return parse_rows(raw, cfg)
