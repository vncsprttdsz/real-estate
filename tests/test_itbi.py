"""Pipeline de transações (ITBI): leitura, filtros, mapeamento por CEP, janela, outliers e anti-sobrescrita."""
import json

import pytest

pd = pytest.importorskip("pandas")
from imoveis import itbi  # noqa: E402

HEAD = ["N° do Cadastro (SQL)", "Nome do Logradouro", "Número", "Complemento", "Bairro", "Referência", "CEP",
        "Natureza de Transação", "Valor de Transação (declarado pelo contribuinte)", "Data de Transação",
        "Valor Venal de Referência", "Proporção Transmitida (%)", "Valor Venal de Referência (proporcional)",
        "Base de Cálculo adotada", "Tipo de Financiamento", "Valor Financiado", "Cartório de Registro",
        "Matrícula do Imóvel", "Situação do SQL", "Área do Terreno (m2)", "Testada (m)", "Fração Ideal",
        "Área Construída (m2)", "Uso (IPTU)", "Descrição do uso (IPTU)", "Padrão (IPTU)", "Descrição do padrão (IPTU)", "ACC (IPTU)"]


def row(sql=1, cep=4532010, nat="1.Compra e venda", valor=2_000_000, data="2026-05-10", prop=100, area=200, uso=20, venal=1_800_000, acc=2005, num=100):
    return [sql, "R JOAQUIM FLORIANO", num, "AP 51", None, None, cep, nat, valor, pd.Timestamp(data), venal, prop, venal, valor,
            None, 0, "4º", 1, "Ativo", 900, 0, .01, area, uso, "APTO", 23, "RES VERT", acc]


ST = {"lote": 5, "bairros": ["Itaim Bibi", "Jardim Paulista"], "alias": {"itaim bibi": "Itaim Bibi", "chacara itaim": "Itaim Bibi", "jardim paulista": "Jardim Paulista"},
      "prefixos": ("014", "045"), "janela": 24, "fator": .72, "pct": (1, 99), "n_min": 5, "novo_anos": 2}


def test_extract_filters_and_headerless_sheet():
    good = pd.DataFrame([row(1), row(2, nat="12.Dação em pagamento"), row(3, prop=50), row(4, uso=24), row(5, cep=5426000), row(6, valor=0)], columns=HEAD)
    # aba sem cabeçalho (como OUT-2024): a 1ª linha de dados vira o "cabeçalho" do pandas
    raw = pd.DataFrame([row(7, data="2026-06-02"), row(8, data="2026-06-03")], columns=HEAD)
    headless = pd.DataFrame(raw.values[1:], columns=[str(v) for v in raw.values[0]])
    headless["extra"] = None                                     # coluna vazia a mais no fim (tolerada)
    book = {"JUN-2026": headless, "MAI-2026": good, "LEGENDA": pd.DataFrame({"a": [1]})}   # sem cabeçalho vem antes
    year, rows, stats = itbi.extract(book, ST)
    assert year == 2026
    assert sorted(r["sql"] for r in rows) == ["1", "7", "8"]      # só apto, compra e venda, 100%, CEP da região, valor > 0
    r = next(r for r in rows if r["sql"] == "1")
    assert r["cep"] == "04532010" and r["padrao"] == "C" and r["acc"] == 2005 and r["data"] == "2026-05-10"


def test_extract_rejects_unknown_layout():
    with pytest.raises(ValueError):
        itbi.extract({"JAN-2026": pd.DataFrame({"N° do Cadastro (SQL)": [1], "Outra": [2]})}, ST)


def test_scrape_links_and_sheet_year():
    html = '<a href="/cidade/x/itbi/GUIAS DE ITBI PAGAS %2803112026%29.xlsx">Excel</a><a href="/outra.pdf">pdf</a>'
    assert itbi.scrape_links(html) == ["https://prefeitura.sp.gov.br/cidade/x/itbi/GUIAS DE ITBI PAGAS %2803112026%29.xlsx"]
    assert itbi.sheet_year(["JAN-2025", "FEV-2025", "LEGENDA", "Tabela de USOS"]) == 2025   # arquivo "(28012026)" é de 2025


def _r(sql, cep, valor, data, area=100, aba="MAI-2026", venal=None, acc=2000):
    return {"sql": str(sql), "data": data, "aba": aba, "logradouro": "R A", "numero": 1, "complemento": "", "cep": cep,
            "area": area, "valor": valor, "venal": venal, "financiado": False, "acc": acc, "padrao": "C"}


def test_build_output_mapping_window_outliers_flags():
    cache = {"04532010": "Itaim Bibi", "04532020": "Itaim Bibi", "01401000": "Jardim Paulista", "04500000": "Moema"}
    rows = [_r(i, "04532010", 1_500_000 + i * 1000, "2026-05-01") for i in range(120)]
    rows += [_r(900, "04532099", 1_611_111, "2026-05-02"),                    # CEP sem resposta: herda o bairro do CEP5
             _r(901, "04500000", 1_611_111, "2026-05-02"),                    # Moema: fora dos bairros
             _r(902, "04532010", 50_000, "2026-05-03"),                       # R$/m² muito baixo: outlier
             _r(903, "04532010", 1_500_000, "2026-05-04", venal=1_500_000),   # declarado = venal
             _r(904, "04532010", 1_400_000, "2026-05-04", venal=1_700_000),   # declarado < venal
             _r(905, "04532010", 1_550_000, "2026-05-05", acc=2025),          # prédio novo
             _r(906, "04532010", 1_550_000, "2020-01-05", aba="JAN-2020"),    # fora da janela
             _r(907, "04532010", 1_560_000, "2026-10-02", aba="SET-2026")]    # pago antes da escritura
    rows.append(dict(rows[0]))                                                  # duplicata exata
    out = itbi.build_output(rows, ST, {}, cache)
    F = {c: i for i, c in enumerate(out["campos"])}
    by = {(x[F["valor"]], x[F["data"]]): x for x in out["linhas"]}
    assert out["qualidade"]["duplicatas"] == 1
    assert out["parametros"]["mes_fim"] == "2026-09" and out["parametros"]["mes_inicio"] == "2024-10"
    assert (1_611_111, "2026-05-02") in by and ST["bairros"][by[(1_611_111, "2026-05-02")][F["bairro"]]] == "Itaim Bibi"
    assert sum(1 for x in out["linhas"] if x[F["valor"]] == 1_611_111) == 1     # Moema ficou de fora
    assert "o" in by[(50_000, "2026-05-03")][F["flags"]]
    assert "e" in by[(1_500_000, "2026-05-04")][F["flags"]] and "v" in by[(1_400_000, "2026-05-04")][F["flags"]]
    assert "n" in by[(1_550_000, "2026-05-05")][F["flags"]]
    assert (1_550_000, "2020-01-05") not in by and (1_560_000, "2026-10-02") in by
    assert out["qualidade"]["cobertura_cep"] == 1.0


class FakeResp:
    def __init__(self, code=200, text="", content=b""):
        self.status_code, self.text, self.content = code, text, content


def test_update_keeps_cache_when_download_fails_or_shrinks(tmp_path, monkeypatch):
    monkeypatch.setattr(itbi, "DIR", tmp_path)
    monkeypatch.setattr(itbi, "REGISTRY", tmp_path / "arquivos.json")
    monkeypatch.setattr(itbi, "CEPS", tmp_path / "ceps.json")
    monkeypatch.setattr(itbi, "OUTPUT", tmp_path / "out.json")
    monkeypatch.setattr(itbi, "load_settings", lambda path=None: ST)
    monkeypatch.setattr(itbi, "lookup_ceps", lambda ceps, cache, http, budget_s=0: cache)
    (tmp_path / "ceps.json").write_text(json.dumps({"04532010": "Itaim Bibi"}))
    y = __import__("datetime").datetime.now().year
    old = [_r(i, "04532010", 1_500_000 + i, f"{y}-02-01", aba=f"FEV-{y}") for i in range(40)]
    (tmp_path / f"transacoes_{y}.json").write_text(json.dumps({"ano": y, "linhas": old}))
    (tmp_path / "arquivos.json").write_text(json.dumps({"u-velho": {"url": "u-velho", "ano": y, "ativo": True, "sha256": "x"}}))
    page = '<a href="https://p/itbi/novo.xlsx">x</a>'
    # 1) download vazio: mantém
    class H1:
        def get(self, url, **kw):
            return FakeResp(text=page) if url == itbi.PAGE else FakeResp(200, content=b"")
    out = itbi.update(H1())
    assert len(out["linhas"]) == 40 and json.loads((tmp_path / f"transacoes_{y}.json").read_text())["linhas"] == old
    # 2) arquivo novo com muito menos linhas que o cache: rejeita
    monkeypatch.setattr(itbi, "read_book", lambda c: {})
    monkeypatch.setattr(itbi, "extract", lambda book, st: (y, old[:5], {"abas": {}, "linhas_total": 5, "linhas_escopo": 5}))
    class H2:
        def get(self, url, **kw):
            return FakeResp(text=page) if url == itbi.PAGE else FakeResp(200, content=b"x" * 200_000)
    out = itbi.update(H2())
    reg = json.loads((tmp_path / "arquivos.json").read_text())
    assert len(out["linhas"]) == 40 and "rejeitado" in reg["https://p/itbi/novo.xlsx"]["ultima_tentativa"]["erro"] and reg["u-velho"]["ativo"]
    # 2b) falha de leitura no próprio link ativo: registra a tentativa e mantém o cache ativo
    monkeypatch.setattr(itbi, "extract", lambda book, st: (_ for _ in ()).throw(ValueError("aba sem cabeçalho")))
    page2 = '<a href="https://p/itbi/u-velho.xlsx">x</a>'
    reg["https://p/itbi/u-velho.xlsx"] = {**reg.pop("u-velho"), "url": "https://p/itbi/u-velho.xlsx"}
    (tmp_path / "arquivos.json").write_text(json.dumps(reg))
    class H4:
        def get(self, url, **kw):
            return FakeResp(text=page2) if url == itbi.PAGE else FakeResp(200, content=b"y" * 200_000)
    out = itbi.update(H4(), force=True)
    reg = json.loads((tmp_path / "arquivos.json").read_text())
    assert len(out["linhas"]) == 40 and reg["https://p/itbi/u-velho.xlsx"]["ativo"] and "cabeçalho" in reg["https://p/itbi/u-velho.xlsx"]["ultima_tentativa"]["erro"]
    # 3) página fora do ar: erro, nada gravado
    class H3:
        def get(self, url, **kw):
            return FakeResp(503)
    with pytest.raises(RuntimeError):
        itbi.update(H3())


def test_bulk_purchases_flagged():
    cache = {"04532010": "Itaim Bibi"}
    rows = [{**_r(i, "04532010", 1_500_000 + i, "2026-05-01"), "numero": i + 1} for i in range(30)]
    for i in range(6):   # 6 unidades do mesmo prédio na mesma data
        rows.append({**_r(100 + i, "04532010", 1_700_000 + i, "2026-05-28"), "logradouro": "R GALENO DE REVOREDO", "numero": 20})
    rows.append({**_r(200, "04532010", 1_650_000, "2026-05-29"), "logradouro": "R GALENO DE REVOREDO", "numero": 20})
    out = itbi.build_output(rows, ST | {"lote": 5}, {}, cache)
    F = {c: i for i, c in enumerate(out["campos"])}
    bulk = [x for x in out["linhas"] if "l" in x[F["flags"]]]
    assert len(bulk) == 6 and out["qualidade"]["lotes"] == 6 and all(x[F["data"]] == "2026-05-28" for x in bulk)
