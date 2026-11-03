"""Projeções do Boletim Focus (Banco Central) para o simulador comprar × alugar.

Fonte: API pública de expectativas de mercado do BCB (Olinda), série anual, mediana.
Guarda o último retrato em data/macro.json; se a API falhar, mantém o anterior e sinaliza.
"""
from __future__ import annotations

import json
import sys
from urllib.parse import quote
from datetime import datetime, timezone

from .common import ROOT
from .http import Http

FOCUS = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais"
MACRO = ROOT / "data" / "macro.json"
INDICADORES = {"selic": "Selic", "igpm": "IGP-M", "ipca": "IPCA"}


def parse_focus(rows: list[dict]) -> dict:
    """Do retrato mais recente: {ano: mediana} em % a.a. (Selic = taxa de fim de ano)."""
    rows = [r for r in rows if r.get("Mediana") is not None and r.get("Data")]
    if not rows:
        raise ValueError("Focus sem dados")
    latest = max(r["Data"] for r in rows)
    anos = {}
    for r in rows:
        if r["Data"] == latest and str(r.get("DataReferencia", "")).isdigit():
            anos[int(r["DataReferencia"])] = float(r["Mediana"])
    if not anos:
        raise ValueError("Focus sem projeções anuais no retrato mais recente")
    resp = [r.get("numeroRespondentes") for r in rows if r["Data"] == latest and r.get("numeroRespondentes")]
    return {"data": latest, "anos": dict(sorted(anos.items())), "respondentes": max(resp) if resp else None}


def odata_url(params: dict) -> str:
    """A API OData do BCB exige espaços como %20 (rejeita o '+' que a codificação de formulário usa)."""
    return FOCUS + "?" + "&".join(f"{k}={quote(str(v), safe="'(),")}" for k, v in params.items())


def fetch_indicador(http: Http, nome: str) -> dict:
    base = {"$orderby": "Data desc", "$top": "80", "$format": "json",
            "$select": "Indicador,Data,DataReferencia,Mediana,numeroRespondentes,baseCalculo"}
    # baseCalculo 0 = estatística com as projeções dos últimos 30 dias (a referência do boletim)
    for filtro in (f"Indicador eq '{nome}' and baseCalculo eq 0", f"Indicador eq '{nome}'"):
        r = http.get(odata_url({**base, "$filter": filtro}), retries=2)
        if r.status_code == 200:
            rows = r.json().get("value") or []
            rows = [x for x in rows if x.get("baseCalculo", 0) == 0] or rows
            if rows:
                return parse_focus(rows)
    raise RuntimeError(f"Focus {nome}: HTTP {r.status_code}")


def update(http: Http | None = None) -> dict:
    """Atualiza data/macro.json; em caso de falha, devolve o último retrato com o erro anotado."""
    try:
        prev = json.loads(MACRO.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        prev = {}
    http = http or Http(0.5, 1.0)
    out = {"fonte": "Boletim Focus (BCB), mediana das projeções anuais", "atualizado": prev.get("atualizado"), "erro": None}
    for key, nome in INDICADORES.items():
        try:
            out[key] = fetch_indicador(http, nome)
        except Exception as e:  # mantém o último valor conhecido
            out[key] = prev.get(key)
            out["erro"] = f"{nome}: {e}"
            print(f"[aviso] Focus {nome}: {e}", file=sys.stderr)
    if not out["erro"]:
        out["atualizado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    MACRO.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out
