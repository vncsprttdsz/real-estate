"""Histórico de transações: guias de ITBI pagas (Prefeitura de São Paulo, Lei 18.161/2024).

    python -m imoveis.itbi            # atualiza data/itbi/ e docs/transacoes.json
    python -m imoveis.itbi --rebuild  # só refaz docs/transacoes.json a partir do cache

Fluxo:
  1. Raspa a página da Secretaria da Fazenda atrás dos .xlsx (o nome do arquivo do ano corrente muda a cada
     atualização, então nada de URL montada à mão).
  2. Baixa só o que é novo: anos fechados ficam em cache (data/itbi/transacoes_AAAA.json); o ano corrente é
     baixado de novo quando aparece um link novo ou o conteúdo muda (sha256). Cada download fica registrado em
     data/itbi/arquivos.json (URL, bytes, sha256, data).
  3. Lê as abas mensais (o ano vem do nome das abas, não do arquivo), filtra apartamento em condomínio, compra e
     venda, 100% transmitido, CEP da região e bairro (Correios, pelo CEP) entre os configurados.
  4. Proteção: se o download falhar, vier vazio ou trouxer menos da metade das linhas que o cache daquele ano já
     tinha, mantém o cache e não grava.
  5. Monta docs/transacoes.json: janela de N meses, sem duplicatas, outliers e sinalizações marcados, checagens.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import yaml

from .common import ROOT, norm
from .http import Http

PAGE = "https://prefeitura.sp.gov.br/web/fazenda/w/acesso_a_informacao/31501"
DIR = ROOT / "data" / "itbi"
REGISTRY = DIR / "arquivos.json"
CEPS = DIR / "cep_bairro.json"
OUTPUT = ROOT / "docs" / "transacoes.json"
YEARS_BACK = 2                  # ano corrente + 2 anteriores cobrem a janela de 24 meses
MIN_KEEP_RATIO = 0.5            # anti-sobrescrita: novo arquivo do mesmo ano com < 50% das linhas do cache é rejeitado
CEP_BUDGET_S = 8 * 60

# Colunas canônicas → padrão do nome normalizado (validado nos arquivos 2024–2026; ver diagnostico/itbi/RELATORIO.md)
COLUMNS = {
    "sql": r"CADASTRO",
    "logradouro": r"LOGRADOURO",
    "numero": r"^NUMERO",
    "complemento": r"^COMPLEMENTO",
    "cep": r"^CEP",
    "natureza": r"NATUREZA",
    "valor": r"^VALOR DE TRANSACAO",
    "data": r"^DATA DE TRANSACAO",
    "venal": r"^VALOR VENAL DE REFERENCIA$",
    "proporcao": r"^PROPORCAO TRANSMITIDA",
    "financiamento": r"^TIPO DE FINANCIAMENTO",
    "area": r"^AREA CONSTRUIDA",
    "uso": r"^USO \(IPTU\)",
    "padrao": r"^PADRAO \(IPTU\)",
    "acc": r"^ACC",
}
REQUIRED = {"sql", "logradouro", "numero", "cep", "natureza", "valor", "data", "proporcao", "area", "uso"}
USO_APARTAMENTO = 20
PADRAO_LETRA = {21: "A", 22: "B", 23: "C", 24: "D", 25: "E"}
MONTHS = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12}


def log(*a):
    print("[itbi]", *a, flush=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_settings(path=None) -> dict:
    raw = yaml.safe_load((path or ROOT / "config.yaml").read_text(encoding="utf-8")).get("transacoes") or {}
    bairros = raw.get("bairros") or {}
    alias = {norm(c): nome for nome, correios in bairros.items() for c in (correios or [nome])}
    return {
        "bairros": list(bairros),
        "alias": alias,                                   # bairro Correios normalizado → nome exibido
        "prefixos": tuple(raw.get("prefixos_cep") or ("014", "045")),
        "janela": int(raw.get("janela_meses", 24)),
        "fator": float(raw.get("fator_area_util", 0.72)),
        "pct": tuple(raw.get("outliers_percentis") or (1, 99)),
        "n_min": int(raw.get("n_minimo", 5)),
        "novo_anos": int(raw.get("predio_novo_anos", 2)),
        "lote": int(raw.get("lote_minimo", 5)),
    }


# ---------------------------------------------------------------- página e arquivos
def scrape_links(html: str) -> list[str]:
    out = []
    for href in re.findall(r'href="([^"]+)"', html, re.I):
        href = href.replace("&amp;", "&")
        if re.search(r"\.xlsx?($|\?)", href, re.I) and re.search(r"itbi", href, re.I):
            u = urljoin(PAGE, href)
            if u not in out:
                out.append(u)
    return out


def sheet_year(names) -> int | None:
    ys = [int(m.group(1)) for n in names for m in [re.search(r"-(20\d\d)\s*$", str(n))] if m]
    return max(set(ys), key=ys.count) if ys else None


def map_columns(cols) -> dict:
    out = {}
    for key, pat in COLUMNS.items():
        for c in cols:
            if re.search(pat, norm(c).upper()):
                out[key] = c
                break
    return out


def fix_header(df, ref_cols):
    """A aba OUT-2024 não tem cabeçalho: usa a linha que contém 'Cadastro' ou o cabeçalho da aba de referência."""
    import pandas as pd
    if any("CADASTRO" in norm(c).upper() for c in df.columns):
        return df
    for i in range(min(15, len(df))):
        if any("CADASTRO" in norm(v).upper() for v in df.iloc[i].tolist()):
            out = df.iloc[i + 1:].copy()
            out.columns = df.iloc[i].tolist()
            return out
    if ref_cols and df.shape[1] > len(ref_cols):
        extra = df.iloc[:, len(ref_cols):]
        if extra.isna().mean().min() >= 0.99 if extra.shape[1] else True:   # colunas vazias no fim: descarta
            df = df.iloc[:, :len(ref_cols)]
    if ref_cols and len(ref_cols) >= df.shape[1]:
        body = df.copy()
        body.columns = range(df.shape[1])
        first = pd.DataFrame([list(df.columns)], columns=range(df.shape[1]))
        out = pd.concat([first, body], ignore_index=True)
        out.columns = list(ref_cols)[:df.shape[1]]
        return out
    raise ValueError(f"aba sem cabeçalho reconhecível ({df.shape[1]} colunas; referência: {len(ref_cols) if ref_cols else 'nenhuma'})")


def read_book(content: bytes) -> dict:
    import pandas as pd
    try:
        return pd.read_excel(io.BytesIO(content), sheet_name=None, engine="calamine")
    except Exception:
        return pd.read_excel(io.BytesIO(content), sheet_name=None)


def cep8(v) -> str:
    d = re.sub(r"\D", "", str(v).split(".")[0]) if v is not None else ""
    return d.zfill(8) if d else ""


def extract(book: dict, st: dict) -> tuple[int | None, list[dict], dict]:
    """Linhas no escopo (apartamento, compra e venda, 100%, CEP da região) de todas as abas mensais.
    O bairro ainda não é aplicado (depende do mapa de CEPs)."""
    import pandas as pd
    year = sheet_year(book)
    rows, stats = [], {"abas": {}, "linhas_total": 0}
    monthly = {n: d for n, d in book.items() if re.search(r"-20\d\d\s*$", str(n))}
    # cabeçalho de referência: o de qualquer aba que tenha cabeçalho (a aba sem cabeçalho pode vir antes)
    ref = next((list(d.columns) for d in monthly.values() if any("CADASTRO" in norm(c).upper() for c in d.columns)), None)
    for name, df in monthly.items():
        try:
            df = fix_header(df.dropna(how="all"), ref)
        except ValueError as e:
            raise ValueError(f"aba {name}: {e}") from None
        cols = map_columns(df.columns)
        missing = REQUIRED - set(cols)
        if missing:
            raise ValueError(f"aba {name}: colunas ausentes {sorted(missing)}")
        stats["abas"][str(name)] = len(df)
        stats["linhas_total"] += len(df)
        c = {k: df[v] for k, v in cols.items()}
        cep = c["cep"].map(cep8)
        uso = pd.to_numeric(c["uso"], errors="coerce")
        nat = c["natureza"].astype(str).str.strip().str.split(".").str[0]
        prop = pd.to_numeric(c["proporcao"], errors="coerce")
        keep = (uso == USO_APARTAMENTO) & (nat == "1") & (prop == 100) & cep.str[:3].isin(st["prefixos"])
        for i in df.index[keep]:
            dt = pd.to_datetime(c["data"][i], errors="coerce")
            area = pd.to_numeric(c["area"][i], errors="coerce")
            valor = pd.to_numeric(c["valor"][i], errors="coerce")
            if pd.isna(dt) or not area or pd.isna(area) or area <= 0 or not valor or pd.isna(valor) or valor <= 0:
                continue
            g = lambda k: c[k][i] if k in c else None
            num = pd.to_numeric(g("numero"), errors="coerce")
            pad = pd.to_numeric(g("padrao"), errors="coerce")
            acc = pd.to_numeric(g("acc"), errors="coerce")
            venal = pd.to_numeric(g("venal"), errors="coerce")
            fin = g("financiamento")
            rows.append({
                "sql": re.sub(r"\D", "", str(c["sql"][i]).split(".")[0]),
                "data": dt.date().isoformat(), "aba": str(name),
                "logradouro": re.sub(r"\s+", " ", str(c["logradouro"][i])).strip(),
                "numero": int(num) if not pd.isna(num) else None,
                "complemento": (re.sub(r"\s+", " ", str(g("complemento"))).strip() if g("complemento") is not None and not pd.isna(g("complemento")) else ""),
                "cep": cep[i], "area": float(area), "valor": round(float(valor), 2),
                "venal": round(float(venal), 2) if venal is not None and not pd.isna(venal) else None,
                "financiado": bool(fin is not None and not pd.isna(fin) and str(fin).strip()),
                "acc": int(acc) if acc is not None and not pd.isna(acc) and acc > 1800 else None,
                "padrao": PADRAO_LETRA.get(int(pad)) if pad is not None and not pd.isna(pad) else None,
            })
    stats["linhas_escopo"] = len(rows)
    return year, rows, stats


# ---------------------------------------------------------------- bairro pelo CEP
def lookup_ceps(ceps: set[str], cache: dict, http: Http | None, budget_s: float = CEP_BUDGET_S) -> dict:
    """Consulta o bairro dos Correios (ViaCEP; BrasilAPI de reserva) só para CEPs que ainda não estão no cache."""
    todo = sorted(c for c in ceps if c not in cache)
    if not todo or http is None:
        return cache
    t0 = time.time()
    for c in todo:
        if time.time() - t0 > budget_s:
            log(f"CEPs: limite de tempo; {len(todo)} pendentes ficam para a próxima atualização")
            break
        for url in (f"https://viacep.com.br/ws/{c}/json/", f"https://brasilapi.com.br/api/cep/v1/{c}"):
            try:
                r = http.get(url, retries=1, timeout=8)
                if r.status_code == 200:
                    j = r.json()
                    if not j.get("erro") and (j.get("bairro") or j.get("neighborhood")):
                        cache[c] = j.get("bairro") or j.get("neighborhood")
                        break
            except Exception:
                pass
    return cache


def bairro_of(cep: str, cache: dict, cep5: dict, st: dict) -> str | None:
    """Bairro exibido: Correios pelo CEP; sem resposta, o bairro majoritário do CEP de 5 dígitos."""
    b = cache.get(cep) or cep5.get(cep[:5])
    return st["alias"].get(norm(b)) if b else None


def cep5_majority(cache: dict) -> dict:
    cnt: dict = {}
    for c, b in cache.items():
        if b:
            cnt.setdefault(c[:5], {}).setdefault(b, 0)
            cnt[c[:5]][b] += 1
    return {k: max(v, key=v.get) for k, v in cnt.items()}


# ---------------------------------------------------------------- saída
def _q(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _month(d: str) -> str:
    return d[:7]


def _add_months(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) + k
    while m <= 0:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


def build_output(rows: list[dict], st: dict, registry: dict, cache: dict) -> dict:
    cep5 = cep5_majority(cache)
    mapped, unmapped_region = [], 0
    for r in rows:
        b = bairro_of(r["cep"], cache, cep5, st)
        if b:
            mapped.append({**r, "bairro": b})
        elif not (cache.get(r["cep"]) or cep5.get(r["cep"][:5])):
            unmapped_region += 1
    # duplicatas: mesmo SQL, data e valor (a mesma DTI pode aparecer em mais de uma aba)
    seen, uniq, dup = set(), [], 0
    for r in sorted(mapped, key=lambda r: (r["data"], r["sql"])):
        k = (r["sql"], r["data"], r["valor"])
        if k in seen:
            dup += 1
            continue
        seen.add(k)
        uniq.append(r)
    if not uniq:
        raise RuntimeError("nenhuma transação nos bairros configurados")
    # fim da janela = último mês de pagamento publicado (aba mais recente); transações com data posterior
    # (ITBI pago antes da escritura) entram, mas não empurram a janela para um mês quase vazio
    pay = [f"{int(m.group(2))}-{MONTHS[m.group(1)]:02d}" for r in uniq for m in [re.match(r"([A-Z]{3})-(20\d\d)", r.get("aba", ""))] if m and m.group(1) in MONTHS]
    last = max(pay) if pay else max(_month(r["data"]) for r in uniq)
    first = _add_months(last, -(st["janela"] - 1))
    win = [r for r in uniq if first <= _month(r["data"])]
    # outliers de R$/m² por bairro (percentis configuráveis) e sinalizações
    lo_p, hi_p = st["pct"][0] / 100, st["pct"][1] / 100
    limits = {}
    for b in st["bairros"]:
        v = sorted(r["valor"] / r["area"] for r in win if r["bairro"] == b)
        if v:
            limits[b] = (_q(v, lo_p), _q(v, hi_p))
    # compras em bloco: N+ unidades do mesmo prédio na mesma data (fundo, incorporadora) não são varejo
    same: dict = {}
    for r in win:
        k = (r["logradouro"], r["numero"], r["data"])
        same[k] = same.get(k, 0) + 1
    out_rows, n_out, n_lote = [], 0, 0
    for r in win:
        ppm = r["valor"] / r["area"]
        lo, hi = limits.get(r["bairro"], (0, float("inf")))
        flags = ""
        if ppm < lo or ppm > hi:
            flags += "o"; n_out += 1
        if same[(r["logradouro"], r["numero"], r["data"])] >= st["lote"]:
            flags += "l"; n_lote += 1
        if r["venal"]:
            if abs(r["valor"] - r["venal"]) < 1:
                flags += "e"
            elif r["valor"] < r["venal"]:
                flags += "v"
        if r["acc"] and int(r["data"][:4]) - r["acc"] <= st["novo_anos"]:
            flags += "n"
        out_rows.append([r["data"], st["bairros"].index(r["bairro"]), r["logradouro"], r["numero"], r["complemento"], r["cep"],
                         r["area"], r["valor"], r["venal"], int(r["financiado"]), r["acc"], r["padrao"], flags])
    out_rows.sort(key=lambda x: x[0], reverse=True)
    # checagens de sanidade
    por_mes: dict = {}
    for x in out_rows:
        por_mes.setdefault(_month(x[0]), {}).setdefault(st["bairros"][x[1]], 0)
        por_mes[_month(x[0])][st["bairros"][x[1]]] += 1
    checks = []
    months = sorted(por_mes)
    tot = {m: sum(por_mes[m].values()) for m in months}
    med = sorted(tot.values())[len(tot) // 2] if tot else 0
    for m in months[:-1]:
        if tot[m] < 0.3 * med:
            checks.append(f"{m}: só {tot[m]} transações (mediana mensal {med})")
    region_ceps = {r["cep"] for r in rows}
    cov = sum(1 for c in region_ceps if cache.get(c) or cep5.get(c[:5])) / max(1, len(region_ceps))
    if cov < 0.95:
        checks.append(f"cobertura do mapa de CEPs {cov:.1%} (< 95%)")
    for b, (lo, hi) in limits.items():
        if not 1_000 <= (lo + hi) / 2 <= 100_000:
            checks.append(f"{b}: faixa de R$/m² implausível ({lo:.0f}–{hi:.0f})")
    files = sorted(({k: v[k] for k in ("ano", "url", "sha256", "bytes", "baixado_em", "linhas_total", "linhas_escopo") if k in v}
                    for v in registry.values() if v.get("ativo")), key=lambda v: -v.get("ano", 0))
    return {
        "fonte": "Guias de ITBI pagas — Secretaria Municipal da Fazenda de São Paulo (Lei 18.161/2024)",
        "pagina": PAGE,
        "atualizado": now(),
        "arquivos": files,
        "bairros": st["bairros"],
        "campos": ["data", "bairro", "logradouro", "numero", "complemento", "cep", "area_construida", "valor", "venal_ref",
                   "financiado", "acc", "padrao", "flags"],
        "flags": {"o": "R$/m² fora da faixa de outliers do bairro (excluído das métricas)",
                  "l": f"compra em bloco: {st['lote']}+ unidades do mesmo prédio na mesma data (excluída das métricas)",
                  "e": "valor declarado igual ao venal de referência", "v": "valor declarado abaixo do venal de referência",
                  "n": f"prédio novo (ACC até {st['novo_anos']} anos antes da transação; preço pode ter sido fechado na planta)"},
        "parametros": {"lote_minimo": st["lote"], "janela_meses": st["janela"], "mes_inicio": first, "mes_fim": last, "fator_area_util": st["fator"],
                       "outliers_percentis": list(st["pct"]), "n_minimo": st["n_min"], "prefixos_cep": list(st["prefixos"])},
        "filtros": ["Uso IPTU 20 (apartamento em condomínio)", "Natureza 1 (compra e venda)", "Proporção transmitida 100%",
                    "Bairro dos Correios pelo CEP entre os configurados", "Sem duplicatas (SQL + data + valor)"],
        "qualidade": {"por_mes": por_mes, "faixa_ppm": {b: [round(lo), round(hi)] for b, (lo, hi) in limits.items()},
                      "cobertura_cep": round(cov, 4), "duplicatas": dup, "outliers": n_out, "lotes": n_lote,
                      "sem_bairro_na_regiao": unmapped_region, "alertas": checks,
                      "ultimo_mes_incompleto": last},
        "linhas": out_rows,
    }


# ---------------------------------------------------------------- orquestração
def year_file(y: int):
    return DIR / f"transacoes_{y}.json"


def prune_year_files(registry: dict, cache: dict, st: dict) -> None:
    """Guarda só as transações dos bairros configurados (as de CEP ainda sem bairro ficam até serem resolvidas).
    Incluir um bairro depois exige rodar com --force para reler os arquivos."""
    cep5 = cep5_majority(cache)
    for y in {v["ano"] for v in registry.values() if v.get("ativo") and v.get("ano")}:
        data = _load(year_file(y), None)
        if not data:
            continue
        keep = [r for r in data["linhas"] if bairro_of(r["cep"], cache, cep5, st) or not (cache.get(r["cep"]) or cep5.get(r["cep"][:5]))]
        if len(keep) != len(data["linhas"]):
            data["linhas"] = keep
            year_file(y).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def update(http: Http | None = None, rebuild_only: bool = False, force: bool = False) -> dict:
    st = load_settings()
    DIR.mkdir(parents=True, exist_ok=True)
    registry = _load(REGISTRY, {})
    cache = _load(CEPS, {})
    http = http or Http(0.3, 0.8)
    if not rebuild_only:
        r = http.get(PAGE, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"página da Prefeitura: HTTP {r.status_code}")
        links = scrape_links(r.text)
        if not links:
            raise RuntimeError("nenhum .xlsx encontrado na página (layout mudou?)")
        min_year = datetime.now().year - YEARS_BACK
        known_sha = {v.get("sha256"): k for k, v in registry.items()}
        for url in links:                              # a página lista do mais novo para o mais antigo
            info = registry.get(url)
            if info and info.get("ano") is not None and info["ano"] < min_year:
                break
            if not force and info and info.get("ano") and year_file(info["ano"]).exists() and info.get("ativo"):
                continue                               # ano já em cache por este mesmo link
            log("baixando", url)
            try:
                resp = http.get(url, timeout=900, retries=2)
                content = resp.content if resp.status_code == 200 else b""
            except Exception as e:
                log("download falhou:", e); content = b""
            if len(content) < 100_000:
                log(f"arquivo vazio ou inválido ({len(content)} bytes): mantém os dados anteriores")
                continue
            sha = hashlib.sha256(content).hexdigest()
            meta = {"url": url, "bytes": len(content), "sha256": sha, "baixado_em": now()}
            if not force and sha in known_sha and registry[known_sha[sha]].get("ano") and year_file(registry[known_sha[sha]]["ano"]).exists():
                registry[url] = {**registry[known_sha[sha]], **meta}
                continue
            try:
                year, rows, stats = extract(read_book(content), st)
            except Exception as e:
                log("leitura falhou:", e)
                registry[url] = {**registry.get(url, {}), "ultima_tentativa": {**meta, "erro": str(e)[:300]}}
                continue
            meta.update(ano=year, **{k: stats[k] for k in ("linhas_total", "linhas_escopo")})
            if year is None or year < min_year:
                registry[url] = {**registry.get(url, {}), **meta}
                if year is not None and year < min_year:
                    break
                continue
            prev = _load(year_file(year), None)
            if prev is not None and len(rows) < MIN_KEEP_RATIO * len(prev["linhas"]):
                log(f"{year}: {len(rows)} linhas no escopo contra {len(prev['linhas'])} no cache — mantém o cache")
                registry[url] = {**registry.get(url, {}), "ultima_tentativa": {**meta, "erro": "rejeitado: queda de linhas"}}
                continue
            year_file(year).write_text(json.dumps({"ano": year, "url": url, "sha256": sha, "abas": stats["abas"], "linhas": rows},
                                                  ensure_ascii=False, separators=(",", ":")))
            for k, v in registry.items():              # um só arquivo ativo por ano
                if v.get("ano") == year:
                    v["ativo"] = False
            registry[url] = {**meta, "ativo": True}
            log(f"{year}: {stats['linhas_total']} linhas, {len(rows)} no escopo")
        REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1))
    rows = []
    for y in sorted({v["ano"] for v in registry.values() if v.get("ativo") and v.get("ano")}):
        rows += _load(year_file(y), {"linhas": []})["linhas"]
    if not rows:
        raise RuntimeError("cache de transações vazio")
    if not rebuild_only:
        cache = lookup_ceps({r["cep"] for r in rows}, cache, Http(0.05, 0.2))
        CEPS.write_text(json.dumps(dict(sorted(cache.items())), ensure_ascii=False, indent=0))
    prune_year_files(registry, cache, st)
    out = build_output(rows, st, registry, cache)
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    q = out["qualidade"]
    log(f"{len(out['linhas'])} transações {out['parametros']['mes_inicio']}–{out['parametros']['mes_fim']}; "
        f"cobertura CEP {q['cobertura_cep']:.1%}; duplicatas {q['duplicatas']}; outliers {q['outliers']}")
    for m in sorted(q["por_mes"]):
        log(" ", m, q["por_mes"][m])
    for a in q["alertas"]:
        log("ALERTA:", a)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="só refaz docs/transacoes.json a partir do cache")
    ap.add_argument("--force", action="store_true", help="baixa de novo os arquivos já em cache")
    a = ap.parse_args()
    try:
        update(rebuild_only=a.rebuild, force=a.force)
    except Exception as e:
        log("ERRO:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
