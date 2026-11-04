"""Diagnóstico da base de ITBI da Prefeitura de SP (etapa 1; não é o pipeline de produção).

Roda no GitHub Actions (o ambiente de desenvolvimento não alcança prefeitura.sp.gov.br):
    python -m scripts.itbi_probe

Saída em diagnostico/itbi/:
  links.json        links .xlsx encontrados na página (raspados, não construídos)
  arquivos.json     arquivo, URL, bytes, sha256, data do download
  perfil.json       por arquivo/aba: colunas exatas, tipos, % nulos; distintos de uso, natureza, bairro
  amostra_AAAA.csv.gz  linhas da região (CEP 014xx/044xx/045xx/040xx ou bairro parecido), todas as colunas
  cep_correios.json bairro dos Correios (ViaCEP) para cada CEP da amostra — referência para o mapeamento
  iptu.csv.gz       cadastro do IPTU (dados abertos) para os SQLs da amostra, se encontrado
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import sys
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests

PAGE = "https://prefeitura.sp.gov.br/web/fazenda/w/acesso_a_informacao/31501"
OUT = Path("diagnostico/itbi")
RAW = Path("/tmp/itbi_raw")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}
FULL_YEARS = 3            # perfil completo e amostra para os anos mais recentes
CEP_PREFIXES = ("014", "044", "045", "040")
BAIRRO_RE = re.compile(r"ITAIM|CONCEIC|JARDIM PAULISTA|JD\.? ?PAULISTA|J\.? PAULISTA|JARDINS|CERQUEIRA|PAULISTA")


def norm(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip().upper()


def log(*a):
    print(*a, flush=True)


def scrape_links() -> list[dict]:
    r = requests.get(PAGE, headers=UA, timeout=60)
    r.raise_for_status()
    (OUT / "pagina.html").write_text(r.text)
    links = []
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S | re.I):
        href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        text = re.sub(r"\s+", " ", text).strip()
        if re.search(r"\.xlsx?($|\?)|xlsx|/documents/", href, re.I) and re.search(r"itbi|guia|20[12]\d|xls", href + " " + text, re.I):
            links.append({"href": urljoin(PAGE, href.replace("&amp;", "&")), "texto": text})
    seen, out = set(), []
    for l in links:
        if l["href"] not in seen:
            seen.add(l["href"]); out.append(l)
    return out


def year_of(link: dict) -> int | None:
    m = re.findall(r"(20[12]\d)", link["texto"] + " " + link["href"])
    return int(m[-1]) if m else None


def download(link: dict) -> tuple[Path, dict]:
    RAW.mkdir(parents=True, exist_ok=True)
    r = requests.get(link["href"], headers=UA, timeout=600)
    r.raise_for_status()
    name = re.sub(r"[^\w.-]+", "_", link["href"].rsplit("/", 1)[-1].split("?")[0]) or "arquivo"
    if not name.lower().endswith((".xlsx", ".xls")):
        name += ".xlsx"
    path = RAW / name
    path.write_bytes(r.content)
    meta = {"url": link["href"], "texto": link["texto"], "arquivo": name, "bytes": len(r.content),
            "sha256": hashlib.sha256(r.content).hexdigest(), "content_type": r.headers.get("content-type"),
            "baixado_em": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return path, meta


def find_col(cols, *pats):
    for c in cols:
        n = norm(c)
        if all(re.search(p, n) for p in pats):
            return c
    return None


def profile_sheet(df: pd.DataFrame) -> dict:
    cols = list(df.columns)
    prof = {"linhas": len(df), "colunas": []}
    for c in cols:
        s = df[c]
        prof["colunas"].append({"nome": str(c), "tipo": str(s.dtype), "nulos_pct": round(float(s.isna().mean() * 100), 2),
                                "exemplo": [str(v) for v in s.dropna().head(3).tolist()]})
    keys = {"uso": find_col(cols, "USO") or find_col(cols, "DESCRI", "USO"), "natureza": find_col(cols, "NATUREZA"),
            "bairro": find_col(cols, "BAIRRO"), "financiamento": find_col(cols, "FINANC"), "padrao": find_col(cols, "PADR")}
    for k, c in keys.items():
        if c is not None:
            vc = df[c].astype(str).map(norm).value_counts()
            prof[f"distintos_{k}"] = {"coluna": str(c), "n_distintos": int(vc.size), "top": vc.head(400 if k == "bairro" else 80).to_dict()}
    return prof


def region_mask(df: pd.DataFrame) -> pd.Series:
    cols = list(df.columns)
    cep_c, bai_c = find_col(cols, "CEP"), find_col(cols, "BAIRRO")
    m = pd.Series(False, index=df.index)
    if cep_c is not None:
        cep = df[cep_c].astype(str).str.replace(r"\D", "", regex=True).str.zfill(8)
        m |= cep.str[:3].isin(CEP_PREFIXES)
    if bai_c is not None:
        m |= df[bai_c].astype(str).map(norm).str.contains(BAIRRO_RE)
    return m


def read_book(path: Path, nrows=None) -> dict[str, pd.DataFrame]:
    try:
        return pd.read_excel(path, sheet_name=None, engine="calamine", nrows=nrows)
    except Exception as e:
        log("calamine falhou, tentando openpyxl:", e)
        return pd.read_excel(path, sheet_name=None, nrows=nrows)


def viacep(cep: str) -> dict:
    for url in (f"https://viacep.com.br/ws/{cep}/json/", f"https://brasilapi.com.br/api/cep/v1/{cep}"):
        for _ in range(3):
            try:
                r = requests.get(url, headers=UA, timeout=20)
                if r.status_code == 200:
                    j = r.json()
                    if not j.get("erro"):
                        return {"bairro": j.get("bairro") or j.get("neighborhood"), "logradouro": j.get("logradouro") or j.get("street"), "fonte": url.split("/")[2]}
                    break
                if r.status_code in (404, 400):
                    break
            except Exception:
                pass
            time.sleep(1)
    return {"bairro": None}


def iptu_cadastro(sqls: set[str]) -> pd.DataFrame | None:
    """Procura o cadastro do IPTU (dados abertos, CKAN) e filtra pelos SQLs da amostra."""
    api = "https://dados.prefeitura.sp.gov.br/api/3/action/package_search"
    try:
        r = requests.get(api, params={"q": "IPTU cadastro imoveis", "rows": 20}, headers=UA, timeout=60).json()
    except Exception as e:
        log("CKAN indisponível:", e); return None
    res = []
    for pkg in r.get("result", {}).get("results", []):
        for rs in pkg.get("resources", []):
            u, nm = rs.get("url", ""), (rs.get("name") or "") + " " + u
            y = re.findall(r"(20[12]\d)", nm)
            if re.search(r"\.(zip|csv)", u, re.I) and y:
                res.append((int(y[-1]), u, pkg.get("name")))
    log("recursos IPTU:", res[:15])
    (OUT / "iptu_recursos.json").write_text(json.dumps(res, indent=1))
    if not res:
        return None
    year, url, _ = max(res)
    log("baixando IPTU", year, url)
    raw = requests.get(url, headers=UA, timeout=1800).content
    log("IPTU bytes", len(raw))
    if url.lower().endswith(".zip") or raw[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(raw)); name = max(z.namelist(), key=lambda n: z.getinfo(n).file_size); fh = z.open(name)
    else:
        fh = io.BytesIO(raw)
    keep = []
    for chunk in pd.read_csv(fh, sep=";", encoding="latin-1", dtype=str, chunksize=300_000, on_bad_lines="skip"):
        c = find_col(chunk.columns, "CONTRIBUINTE") or chunk.columns[0]
        sq = chunk[c].astype(str).str.replace(r"\D", "", regex=True)
        part = chunk[sq.str[:10].isin({s[:10] for s in sqls})]   # setor-quadra-lote (sem dígito)
        if len(part):
            keep.append(part)
    df = pd.concat(keep) if keep else pd.DataFrame()
    df.attrs["fonte"] = url
    return df


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    links = scrape_links()
    (OUT / "links.json").write_text(json.dumps(links, ensure_ascii=False, indent=1))
    log(f"{len(links)} links"); [log(" ", l) for l in links]
    files = [l for l in links if re.search(r"xls", l["href"], re.I)] or links
    by_year = sorted(((year_of(l), l) for l in files if year_of(l)), key=lambda x: x[0], reverse=True)
    metas, perfil, amostras = [], {}, {}
    for i, (year, link) in enumerate(by_year):
        try:
            path, meta = download(link)
        except Exception as e:
            log("download falhou", link, e); metas.append({"url": link["href"], "erro": str(e)}); continue
        meta["ano"] = year; metas.append(meta); log("baixado", meta)
        full = i < FULL_YEARS
        try:
            book = read_book(path, None if full else 50)
        except Exception as e:
            log("leitura falhou", e); meta["erro_leitura"] = str(e); continue
        perfil[meta["arquivo"]] = {"ano": year, "abas": list(book.keys()), "perfil_completo": full, "por_aba": {}}
        frames = []
        for sh, df in book.items():
            df = df.dropna(how="all")
            p = profile_sheet(df) if full else {"linhas_lidas": len(df), "colunas": [str(c) for c in df.columns]}
            perfil[meta["arquivo"]]["por_aba"][sh] = p
            if full and len(df):
                reg = df[region_mask(df)].copy(); reg.insert(0, "_aba", sh); frames.append(reg)
        if frames:
            amostras[year] = pd.concat(frames, ignore_index=True)
            with gzip.open(OUT / f"amostra_{year}.csv.gz", "wt", encoding="utf-8") as f:
                amostras[year].to_csv(f, index=False)
            log(year, "amostra", len(amostras[year]))
        (OUT / "perfil.json").write_text(json.dumps(perfil, ensure_ascii=False, indent=1, default=str))
        (OUT / "arquivos.json").write_text(json.dumps(metas, ensure_ascii=False, indent=1))
    # Correios (ViaCEP) para os CEPs da amostra
    ceps = set()
    for df in amostras.values():
        c = find_col(df.columns, "CEP")
        if c is not None:
            ceps |= set(df[c].astype(str).str.replace(r"\D", "", regex=True).str.zfill(8))
    ceps = sorted(c for c in ceps if re.fullmatch(r"\d{8}", c) and c != "00000000")
    log("CEPs distintos na amostra:", len(ceps))
    with ThreadPoolExecutor(6) as ex:
        res = dict(zip(ceps, ex.map(viacep, ceps)))
    (OUT / "cep_correios.json").write_text(json.dumps(res, ensure_ascii=False, indent=0))
    # IPTU por SQL
    sqls = set()
    for df in amostras.values():
        c = find_col(df.columns, "SQL") or find_col(df.columns, "CADASTRO")
        if c is not None:
            sqls |= set(df[c].astype(str).str.replace(r"\D", "", regex=True))
    try:
        ip = iptu_cadastro(sqls)
        if ip is not None and len(ip):
            with gzip.open(OUT / "iptu.csv.gz", "wt", encoding="utf-8") as f:
                ip.to_csv(f, index=False)
            log("IPTU linhas casadas:", len(ip), ip.attrs.get("fonte"))
    except Exception as e:
        log("IPTU falhou:", e)
    log("ok")


if __name__ == "__main__":
    sys.exit(main())
