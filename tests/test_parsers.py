import json

import pytest

from imoveis import __main__ as app
from imoveis.common import Config, Listing, to_float
from imoveis.sources import grupozap, quintoandar, zuk


@pytest.fixture
def cfg():
    return Config(neighborhoods=["Vila Nova Conceição", "Itaim Bibi"], min_area=100)


def test_to_float():
    assert to_float("R$ 2.500.000") == 2500000
    assert to_float("1.234,56") == 1234.56
    assert to_float("120") == 120
    assert to_float("135.5") == 135.5
    assert to_float(None) is None


ZAP_ITEM = {
    "listing": {
        "id": "2712345678",
        "title": "Apartamento com 3 quartos",
        "unitTypes": ["APARTMENT"],
        "usableAreas": [180],
        "pricingInfos": [
            {"businessType": "SALE", "price": "4500000", "monthlyCondoFee": "3200", "yearlyIptu": "12000"},
            {"businessType": "RENTAL", "price": "18000", "monthlyCondoFee": "3200",
             "rentalInfo": {"monthlyRentalTotalPrice": "22000"}},
        ],
        "address": {"neighborhood": "Vila Nova Conceição", "street": "Rua Afonso Braz", "streetNumber": "100"},
        "bedrooms": [3],
        "parkingSpaces": [3],
    },
    "link": {"href": "/imovel/venda-apartamento-3-quartos-vila-nova-conceicao-id-2712345678/"},
}


def test_grupozap_sale_and_rent(cfg):
    sale = grupozap.parse_listing(ZAP_ITEM, "zap", "venda")
    assert sale.price == 4500000 and sale.area == 180 and sale.bedrooms == 3
    assert sale.url.startswith("https://www.zapimoveis.com.br/imovel/")
    assert cfg.matches(sale)
    rent = grupozap.parse_listing(ZAP_ITEM, "vivareal", "aluguel")
    assert rent.total_monthly == 22000 and rent.url.startswith("https://www.vivareal.com.br/")
    assert sale.key != rent.key


def test_grupozap_filters(cfg):
    small = json.loads(json.dumps(ZAP_ITEM))
    small["listing"]["usableAreas"] = [80]
    assert not cfg.matches(grupozap.parse_listing(small, "zap", "venda"))
    other = json.loads(json.dumps(ZAP_ITEM))
    other["listing"]["address"]["neighborhood"] = "Moema"
    assert not cfg.matches(grupozap.parse_listing(other, "zap", "venda"))
    house = json.loads(json.dumps(ZAP_ITEM))
    house["listing"]["unitTypes"] = ["HOME"]
    assert grupozap.parse_listing(house, "zap", "venda") is None


def test_quintoandar(cfg):
    h = {"id": 893456789, "area": 140, "rent": 15000, "totalCost": 19500, "type": "Apartamento",
         "address": "Rua Tabapuã", "neighbourhood": "Itaim Bibi", "bedrooms": 3, "parkingSpots": 2,
         "forRent": True, "forSale": True, "salePrice": 3900000}
    rent = quintoandar.parse_house(h, "aluguel")
    assert rent.total_monthly == 19500 and rent.url == "https://www.quintoandar.com.br/imovel/893456789"
    assert cfg.matches(rent)
    sale = quintoandar.parse_house(h, "venda")
    assert sale.price == 3900000 and sale.url.endswith("/comprar")
    assert quintoandar.parse_house({**h, "type": "Casa"}, "aluguel") is None


ZUK_LIST = """
<a href="https://www.portalzuk.com.br/imovel/sp/sao-paulo/itaim-bibi/rua-joaquim-floriano-500/31234-198765">x</a>
<a href="/imovel/sp/sao-paulo/vila-nova-conceicao/rua-domingos-leme-300/31555-200001">y</a>
<a href="/imovel/sp/sao-paulo/moema/rua-x-1/31000-100000">z</a>
"""

ZUK_DETAIL = """<html><head><meta property="og:title" content="Apartamento 152 m² - Itaim Bibi"></head>
<body><h1>Apartamento</h1><p>Área útil: 152,30 m²</p><p>Área total 260,00 m²</p>
<p>1º Leilão 20/11/2026 às 10:00 Lance mínimo: R$ 3.200.000,00</p>
<p>2º Leilão 27/11/2026 às 10:00 Lance mínimo: R$ 1.920.000,00</p></body></html>"""


def test_zuk(cfg):
    links = zuk.extract_links(ZUK_LIST, cfg)
    assert set(links) == {"31234-198765", "31555-200001"}
    l = zuk.parse_detail(ZUK_DETAIL, links["31234-198765"], "31234-198765", "itaim-bibi", cfg)
    assert l.area == 152.3 and l.price == 1920000 and l.neighborhood == "Itaim Bibi"
    assert "20/11/2026" in l.extra and cfg.matches(l)
    casa = ZUK_DETAIL.replace("Apartamento", "Casa")
    assert zuk.parse_detail(casa, "u", "1", "itaim-bibi", cfg) is None


def test_full_cycle(tmp_path, monkeypatch, cfg):
    monkeypatch.setattr(app, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(app, "CURRENT", tmp_path / "anuncios.json")
    monkeypatch.setattr(app, "REPORT", tmp_path / "ANUNCIOS.md")
    from imoveis import dashboard
    monkeypatch.setattr(dashboard, "CATALOG", tmp_path / "catalogo.json")
    monkeypatch.setattr(dashboard, "HISTORY", tmp_path / "historico.json")
    monkeypatch.setattr(dashboard, "OUTPUT", tmp_path / "dados.json")
    monkeypatch.setattr(app.Config, "load", classmethod(lambda c: cfg))
    sent = []
    monkeypatch.setattr(app.Notifier, "send", lambda self, subj, items, footer="": sent.append((subj, items)))

    a = Listing("zap", "1", "https://z/1", "venda", neighborhood="Itaim Bibi", area=120, price=3_000_000)
    b = Listing("zap", "2", "https://z/2", "venda", neighborhood="Itaim Bibi", area=130, price=2_000_000)
    batches = iter([{"zap": [a]}, {"zap": [a, b]}, {"zap": [Listing(**{**a.to_dict(), "price": 2_800_000}), b]}])
    monkeypatch.setattr(app, "collect", lambda c, s: (next(batches), {}))

    app.run()  # carga inicial: só aviso de ativação
    assert len(sent) == 1 and "ativado" in sent[0][0]
    app.run()  # b é novo
    assert "novo" in sent[1][0] and "https://z/2" in sent[1][1][0][0]
    app.run()  # a caiu de preço 6,7%
    assert "baixaram" in sent[2][0]
    assert "Itaim Bibi" in (tmp_path / "ANUNCIOS.md").read_text()
    data = json.loads((tmp_path / "dados.json").read_text())
    rows = {r["id"]: r for r in data["listings"]}
    assert rows["1"]["initial"] and not rows["2"]["initial"]
    assert [p for _, p in rows["1"]["hist"]] == [3_000_000, 2_800_000]
    assert rows["1"]["active"] and rows["1"]["ppm2"] == round(2_800_000 / 120, 2)
    assert data["history"][-1]["groups"]["venda|Itaim Bibi"]["n"] == 2


def test_whatsapp_callmebot(monkeypatch):
    from imoveis import notify
    monkeypatch.setenv("CALLMEBOT_PHONE", "+5511999998888")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "123456")
    for var in ("TELEGRAM_BOT_TOKEN", "NTFY_TOPIC", "SMTP_USER"):
        monkeypatch.delenv(var, raising=False)
    calls = []

    class Resp:
        status_code, text = 200, "Message queued."

    monkeypatch.setattr(notify.Http, "get", lambda self, url, **kw: calls.append(kw["params"]) or Resp())
    monkeypatch.setattr(notify.Http, "_wait", lambda self: None)
    l = Listing("zap", "1", "https://z/1", "venda", neighborhood="Itaim Bibi", area=120, price=3_000_000)
    n = notify.Notifier()
    assert n.configured
    n.send("🏠 1 anúncio novo", [notify.describe(l)])
    assert len(calls) == 1
    assert calls[0]["phone"] == "+5511999998888" and calls[0]["apikey"] == "123456"
    assert "*🏠 1 anúncio novo*" in calls[0]["text"] and "https://z/1" in calls[0]["text"]
    # muitos anúncios: no máximo 6 mensagens
    calls.clear()
    n.send("muitos", [notify.describe(l)] * 200)
    assert len(calls) == 6 and "mais" in calls[-1]["text"]


def test_plausibility(cfg):
    typo = Listing("quintoandar", "1", "u", "venda", neighborhood="Itaim Bibi", area=3000, price=5_800_000)
    assert not cfg.matches(typo)
    cheap = Listing("quintoandar", "2", "u", "venda", neighborhood="Itaim Bibi", area=150, price=150_000)
    assert not cfg.matches(cheap)
    ok = Listing("quintoandar", "3", "u", "aluguel", neighborhood="Itaim Bibi", area=120, price=None, total_monthly=9959)
    assert cfg.matches(ok)
    from imoveis.notify import describe
    text = describe(Listing("quintoandar", "4", "u", "aluguel", neighborhood="Itaim Bibi", area=108,
                            total_monthly=13941, bedrooms=1, parking=1))[0]
    assert "Aluguel ?" not in text and "1 quarto ·" in text and "1 vaga" in text


def test_budget_alerts(tmp_path, monkeypatch):
    cfg = Config(neighborhoods=["Itaim Bibi"], min_area=100, max_sale=2_250_000, max_rent=10_000)
    monkeypatch.setattr(app, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(app, "CURRENT", tmp_path / "anuncios.json")
    monkeypatch.setattr(app, "REPORT", tmp_path / "ANUNCIOS.md")
    from imoveis import dashboard
    monkeypatch.setattr(dashboard, "CATALOG", tmp_path / "catalogo.json")
    monkeypatch.setattr(dashboard, "HISTORY", tmp_path / "historico.json")
    monkeypatch.setattr(dashboard, "OUTPUT", tmp_path / "dados.json")
    monkeypatch.setattr(app.Config, "load", classmethod(lambda c: cfg))
    sent = []
    monkeypatch.setattr(app.Notifier, "send", lambda self, subj, items, footer="": sent.append((subj, items)))

    def L(id_, business, price=None, total=None):
        return Listing("zap", id_, f"https://z/{id_}", business, neighborhood="Itaim Bibi", area=120,
                       price=price, total_monthly=total)

    base = [L("1", "venda", 2_000_000)]
    batches = iter([
        {"zap": base},
        {"zap": base + [L("2", "venda", 3_000_000), L("3", "aluguel", 7_000, 9_500), L("4", "aluguel", 9_000, 12_000)]},
        {"zap": base + [L("2", "venda", 2_200_000), L("3", "aluguel", 7_000, 9_500), L("4", "aluguel", 9_000, 12_000)]},
    ])
    monkeypatch.setattr(app, "collect", lambda c, s: (next(batches), {}))
    app.run(); sent.clear()
    app.run()  # 2 (R$ 3 mi) e 4 (R$ 12 mil/mês) estão acima do orçamento: só 3 avisa
    assert len(sent) == 1 and [t for t, _ in sent[0][1]][0].endswith("https://z/3")
    sent.clear()
    app.run()  # 2 baixou para R$ 2,2 mi e entrou no orçamento
    assert len(sent) == 1 and "entrou no seu orçamento" in sent[0][1][0][0]
    rows = {r["id"]: r for r in json.loads((tmp_path / "dados.json").read_text())["listings"]}
    assert rows["2"]["in_budget"] and not rows["4"]["in_budget"]
    assert "https://z/4" not in (tmp_path / "ANUNCIOS.md").read_text()


def test_quintoandar_rent_from_total():
    h = {"id": 1, "area": 120, "totalCost": 12500, "iptuPlusCondominium": 2500, "type": "Apartamento",
         "neighbourhood": "Itaim Bibi"}
    l = quintoandar.parse_house(h, "aluguel")
    assert l.price == 10000 and l.total_monthly == 12500


def test_grupozap_param_ladder(cfg, monkeypatch):
    """Se a API recusar os parâmetros (400), recua para um conjunto mais simples e lembra dele."""
    grupozap._working_rung.clear(); grupozap._page_size.clear(); grupozap._sort.clear()
    calls = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._body = code, body
            self.text = json.dumps(body)

        def json(self):
            return self._body

    class FakeHttp:
        def get(self, url, params=None, headers=None):
            calls.append(params.copy())
            if "addressZone" in params:
                return R(400, {"err": "bad"})
            return R(200, {"search": {"totalCount": 1, "result": {"listings": [ZAP_ITEM]}}})

    items = grupozap._fetch_api(FakeHttp(), cfg, "zap", "venda", "Itaim Bibi")
    # tenta as ordenações com todos os filtros, depois simplifica voltando à ordenação preferida
    assert [("addressZone" in c, c.get("sort")) for c in calls] == [
        (True, "createdAt DESC"), (True, "updatedAt DESC"), (True, None), (False, "createdAt DESC")]
    assert len(items) == 1 and calls[3]["usableAreasMin"] == 100 and calls[3]["unitTypes"] == "APARTMENT"
    calls.clear()
    grupozap._fetch_api(FakeHttp(), cfg, "zap", "aluguel", "Itaim Bibi")
    assert len(calls) == 1  # já começa pelo conjunto que funcionou


def test_quintoandar_real_fields(cfg):
    """Formato observado no site em 11/10/2026."""
    h = {"id": "893060793", "rentPrice": 7500, "area": 168, "bedrooms": 3, "totalCost": 11414,
         "forSale": True, "forRent": False, "salePrice": 3098560, "parkingSpots": 2, "condoIptu": 3624,
         "address": {"address": "Rua Tapinas", "city": "São Paulo"}, "regionName": "Itaim Bibi",
         "neighbourhood": "Itaim Bibi", "type": "Apartamento"}
    sale = quintoandar.parse_house(h, "venda")
    assert sale.price == 3098560 and sale.address == "Rua Tapinas" and sale.condo == 3624
    assert quintoandar.parse_house(h, "aluguel") is None  # forRent False
    rent = quintoandar.parse_house({**h, "forRent": True, "salePrice": 0}, "aluguel")
    assert rent.price == 7500 and rent.total_monthly == 11414
    assert quintoandar.parse_house({**h, "salePrice": 0}, "venda") is None


def test_vivareal_dedup(monkeypatch, cfg):
    a = Listing("zap", "1", "u", "venda", neighborhood="Itaim Bibi", area=120, price=2_000_000)
    b = Listing("vivareal", "1", "u", "venda", neighborhood="Itaim Bibi", area=120, price=2_000_000)
    c = Listing("vivareal", "2", "u", "venda", neighborhood="Itaim Bibi", area=130, price=2_100_000)
    monkeypatch.setattr(grupozap, "fetch", lambda http, cfg, src: [a] if src == "zap" else [b, c])
    monkeypatch.setattr(quintoandar, "fetch", lambda http, cfg: [])
    monkeypatch.setattr(zuk, "fetch", lambda *a, **k: [])
    results, errors = app.collect(cfg, {"seen": {}, "zuk_rejected": []})
    assert [l.id for l in results["vivareal"]] == ["2"]


def test_grupozap_page_size_fallback(cfg):
    """Mensagem real da API: 'Size is above acceptable limit' → reduz a página, mantém os filtros."""
    grupozap._working_rung.clear(); grupozap._page_size.clear(); grupozap._sort.clear()
    sizes = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._body, self.text = code, body, json.dumps(body)

        def json(self):
            return self._body

    class FakeHttp:
        def get(self, url, params=None, headers=None):
            sizes.append((params["size"], "addressZone" in params))
            if params["size"] > 15:
                return R(400, {"err": "{ details: { message: 'Size is above acceptable limit' } }"})
            return R(200, {"search": {"totalCount": 1, "result": {"listings": [ZAP_ITEM]}}})

    assert len(grupozap._fetch_api(FakeHttp(), cfg, "zap", "venda", "Itaim Bibi")) == 1
    assert sizes == [(24, True), (15, True)]


class _R:
    def __init__(self, code, text):
        self.status_code, self.text = code, text

    def json(self):
        return json.loads(self.text)


def test_quintoandar_keeps_partial_on_pagination_limit(cfg):
    """Mensagem real: 'Pagination exceeds the maximum limit' — mantém o que já veio."""
    class FakeHttp:
        def post(self, url, json=None, headers=None):
            off = json["pagination"]["offset"]
            if off >= 72:
                return _R(400, '{"code":400,"message":"Validation failed: [searchCriteria.pagination: '
                               'Pagination exceeds the maximum limit]"}')
            hits = [{"_source": {"id": off + i, "area": 120}} for i in range(36)]
            return _R(200, __import__("json").dumps({"hits": {"hits": hits, "total": {"value": 5000}}}))

    houses = quintoandar._fetch_api(FakeHttp(), cfg, "venda")
    assert len(houses) == 72 * len(cfg.neighborhoods)


def test_zuk_discovers_page_param(cfg):
    page1 = '<a href="/imovel/sp/sao-paulo/moema/rua-a-1/1-1">x</a>'
    page2 = '<a href="/imovel/sp/sao-paulo/itaim-bibi/rua-b-2/2-2">y</a>'
    detail = '<meta property="og:title" content="Apartamento 120 m²"><p>Área útil: 120 m²</p>'

    class FakeHttp:
        def get(self, url, **kw):
            if "/imovel/" in url:
                return _R(200, detail)
            if "robots" in url or "sitemap" in url:
                return _R(404, "")
            if url.endswith("?pagina=2"):
                return _R(200, page2)
            if "?pagina=3" in url:
                return _R(200, page2)  # nada novo: para
            return _R(200, page1)      # base e parâmetros ignorados

    found = zuk.fetch(FakeHttp(), cfg, set(), set())
    assert [l.id for l in found] == ["2-2"] and found[0].neighborhood == "Itaim Bibi"


def test_new_neighborhood_is_initial_load(tmp_path, monkeypatch):
    """Bairro incluído depois: anúncios dele entram como carga inicial, sem aviso; os seguintes avisam."""
    cfg = Config(neighborhoods=["Itaim Bibi", "Jardim Paulista"], min_area=100)
    monkeypatch.setattr(app, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(app, "CURRENT", tmp_path / "anuncios.json")
    monkeypatch.setattr(app, "REPORT", tmp_path / "ANUNCIOS.md")
    from imoveis import dashboard
    monkeypatch.setattr(dashboard, "CATALOG", tmp_path / "catalogo.json")
    monkeypatch.setattr(dashboard, "HISTORY", tmp_path / "historico.json")
    monkeypatch.setattr(dashboard, "OUTPUT", tmp_path / "dados.json")
    monkeypatch.setattr(app.Config, "load", classmethod(lambda c: cfg))
    (tmp_path / "state.json").write_text(json.dumps({"seen": {}, "initialized": ["zap"], "initialized_nbs": {"zap": ["itaim bibi"]}}))
    sent = []
    monkeypatch.setattr(app.Notifier, "send", lambda self, subj, items, footer="": sent.append((subj, items)))

    def L(i, nb):
        return Listing("zap", i, f"https://z/{i}", "venda", neighborhood=nb, area=120, price=2_000_000)

    batches = iter([{"zap": [L("1", "Itaim Bibi"), L("2", "Jardim Paulista")]},
                    {"zap": [L("1", "Itaim Bibi"), L("2", "Jardim Paulista"), L("3", "Jardim Paulista")]}])
    monkeypatch.setattr(app, "collect", lambda c, s: (next(batches), {}))
    app.run()
    news = [t for subj, items in sent if "novo" in subj for t, _ in items]
    assert news and all("https://z/2" not in t for t in news) and any("https://z/1" in t for t in news)
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["seen"]["zap:venda:2"]["initial"] and "jardim paulista" in st["initialized_nbs"]["zap"]
    sent.clear(); app.run()
    news = [t for subj, items in sent if "novo" in subj for t, _ in items]
    assert len(news) == 1 and "https://z/3" in news[0]


def test_grupozap_zero_results_drops_zone(cfg):
    """Zona/locationId fixos podem não bater com o bairro: 0 resultados -> tenta sem eles."""
    grupozap._working_rung.clear(); grupozap._page_size.clear(); grupozap._sort.clear()
    calls = []

    class FakeHttp:
        def get(self, url, params=None, headers=None):
            calls.append("addressZone" in params)
            body = {"search": {"totalCount": 0, "result": {"listings": []}}} if "addressZone" in params else \
                   {"search": {"totalCount": 1, "result": {"listings": [ZAP_ITEM]}}}
            return _R(200, json.dumps(body))

    assert len(grupozap._fetch_api(FakeHttp(), cfg, "zap", "venda", "Jardim Paulista")) == 1
    assert calls == [True, False]
    calls.clear()
    grupozap._fetch_api(FakeHttp(), cfg, "zap", "venda", "Itaim Bibi")
    assert calls[0] is True   # memória é por bairro: outro bairro volta a tentar com zona


def test_quintoandar_tiles_large_neighborhood():
    """Jardim Paulista é dividido em faixas; cada uma é paginada; ids repetidos viram um anúncio só."""
    cfg3 = Config(neighborhoods=["Jardim Paulista"], min_area=100)
    seen_vps = []

    class FakeHttp:
        def post(self, url, json=None, headers=None):
            vp = json["filters"]["location"]["viewport"]; seen_vps.append(vp["west"])
            hits = [{"_source": {"id": 1, "area": 120, "salePrice": 2_000_000, "type": "Apartamento", "neighbourhood": "Jardim Paulista"}}]
            return _R(200, __import__("json").dumps({"hits": {"hits": hits, "total": {"value": 1}}}))

    houses = quintoandar._fetch_api(FakeHttp(), cfg3, "venda")
    assert len(set(seen_vps)) == 3 and len(houses) == 3
    assert len([l for l in quintoandar.fetch(FakeHttp(), cfg3) if l.business == "venda"]) == 1


def test_focus_parse_latest_snapshot():
    from imoveis import macro
    rows = [
        {"Indicador": "Selic", "Data": "2026-10-24", "DataReferencia": "2026", "Mediana": 15.0, "numeroRespondentes": 80},
        {"Indicador": "Selic", "Data": "2026-10-31", "DataReferencia": "2026", "Mediana": 15.0, "numeroRespondentes": 90},
        {"Indicador": "Selic", "Data": "2026-10-31", "DataReferencia": "2027", "Mediana": 12.25, "numeroRespondentes": 88},
        {"Indicador": "Selic", "Data": "2026-10-31", "DataReferencia": "2028", "Mediana": 10.5, "numeroRespondentes": 70},
    ]
    out = macro.parse_focus(rows)
    assert out["data"] == "2026-10-31" and out["anos"] == {2026: 15.0, 2027: 12.25, 2028: 10.5} and out["respondentes"] == 90


def test_focus_update_keeps_previous_on_failure(tmp_path, monkeypatch):
    from imoveis import macro
    monkeypatch.setattr(macro, "MACRO", tmp_path / "macro.json")
    (tmp_path / "macro.json").write_text(json.dumps({"atualizado": "2026-10-31T10:00:00+00:00", "selic": {"data": "2026-10-31", "anos": {"2026": 15.0}}}))

    class Down:
        def get(self, *a, **k):
            return _R(503, "fora do ar")

    out = macro.update(Down())
    assert out["selic"]["anos"] == {"2026": 15.0} and out["erro"] and out["atualizado"] == "2026-10-31T10:00:00+00:00"


def test_focus_url_uses_percent20():
    from imoveis import macro
    u = macro.odata_url({"$filter": "Indicador eq 'IGP-M' and baseCalculo eq 0", "$orderby": "Data desc"})
    assert "+" not in u.split("?", 1)[1] and "Indicador%20eq%20'IGP-M'" in u and "Data%20desc" in u


def test_dedupe_key():
    from imoveis import dedupe
    a = {"business": "venda", "neighborhood": "Itaim Bibi", "address": "Rua Joaquim Floriano, 100 - Itaim", "area": 120.4, "price": 2_000_000}
    b = {**a, "address": "R. Joaquim Floriano", "area": 120.0}            # QuintoAndar: sem número
    assert dedupe.key(a) == dedupe.key(b)
    assert dedupe.key({**a, "price": 2_010_000}) != dedupe.key(a)
    assert dedupe.key({**a, "address": ""}) is None
    assert dedupe.key({**a, "business": "leilao"}) is None
    r = {"business": "aluguel", "neighborhood": "Itaim Bibi", "address": "Rua A B", "area": 100, "price": 8000, "total_monthly": 10_000}
    assert dedupe.key(r) == dedupe.key({**r, "price": None})              # aluguel compara pelo total


def test_duplicates_grouped_and_not_alerted(tmp_path, monkeypatch):
    cfg = Config(neighborhoods=["Itaim Bibi"], min_area=100, max_sale=2_250_000, max_rent=13_000)
    monkeypatch.setattr(app, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(app, "CURRENT", tmp_path / "anuncios.json")
    monkeypatch.setattr(app, "REPORT", tmp_path / "ANUNCIOS.md")
    from imoveis import dashboard
    monkeypatch.setattr(dashboard, "CATALOG", tmp_path / "catalogo.json")
    monkeypatch.setattr(dashboard, "HISTORY", tmp_path / "historico.json")
    monkeypatch.setattr(dashboard, "OUTPUT", tmp_path / "dados.json")
    monkeypatch.setattr(app.Config, "load", classmethod(lambda c: cfg))
    sent = []
    monkeypatch.setattr(app.Notifier, "send", lambda self, subj, items, footer="": sent.append((subj, items)))

    def L(src, id_, addr="Rua Tabapuã 500", price=2_000_000):
        return Listing(src, id_, f"https://{src}/{id_}", "venda", neighborhood="Itaim Bibi", address=addr, area=130, price=price)

    batches = iter([
        {"zap": [L("zap", "1")], "quintoandar": [L("quintoandar", "9", "Rua Tabapuã", 1_500_000)]},
        # republicação no ZAP + mesma unidade no QuintoAndar + um imóvel realmente novo
        {"zap": [L("zap", "1"), L("zap", "2"), L("zap", "3", price=1_900_000)], "quintoandar": [L("quintoandar", "9", "Rua Tabapuã", 1_500_000), L("quintoandar", "8", "Rua Tabapuã")]},
    ])
    monkeypatch.setattr(app, "collect", lambda c, s: (next(batches), {}))
    app.run(); sent.clear()
    app.run()
    alerted = [t for subj, items in sent if "novo" in subj for t, _ in items]
    assert len(alerted) == 1 and alerted[0].endswith("https://zap/3")
    data = json.loads((tmp_path / "dados.json").read_text())
    rows = {r["key"]: r for r in data["listings"]}
    unit = rows["zap:venda:1"]
    assert {c["url"] for c in unit["also"]} == {"https://zap/2", "https://quintoandar/8"}
    assert unit["initial"] and "zap:venda:2" not in rows and len(rows) == 3
    assert data["dedupe"] == {"grupos": 1, "copias": 2, "por_fonte": {"zap": 1, "quintoandar": 1}}


def test_rent_ppm2_uses_total_and_history_is_rebased():
    from imoveis import dashboard
    assert dashboard.ppm2({"business": "aluguel", "price": 8000, "total_monthly": 10_000, "area": 100}) == 100
    assert dashboard.ppm2({"business": "aluguel", "price": None, "total_monthly": 9000, "area": 100}) == 90
    assert dashboard.ppm2({"business": "venda", "price": 2_000_000, "total_monthly": None, "area": 100}) == 20_000
    catalog = {
        "zap:aluguel:1": {"source": "zap", "business": "aluguel", "neighborhood": "Itaim Bibi", "area": 100, "price": 8000, "total_monthly": 10_000},
        "quintoandar:aluguel:2": {"source": "quintoandar", "business": "aluguel", "neighborhood": "Itaim Bibi", "area": 100, "price": None, "total_monthly": 12_000},
    }
    state = {"seen": {
        "zap:aluguel:1": {"first_seen": "2026-10-11T10:00:00+00:00", "last_seen": "2026-11-03T10:00:00+00:00",
                          "hist": [["2026-10-11T10:00:00+00:00", 11_000], ["2026-10-20T10:00:00+00:00", 10_000]]},
        "quintoandar:aluguel:2": {"first_seen": "2026-10-15T10:00:00+00:00", "last_seen": "2026-11-03T10:00:00+00:00",
                                  "hist": [["2026-10-15T10:00:00+00:00", 12_000]]},
    }}
    hist = [{"date": d, "groups": {"aluguel|Itaim Bibi": {"n": 1, "ppm2": 80.0}, "venda|Itaim Bibi": {"n": 1, "ppm2": 20_000}}}
            for d in ("2026-10-12", "2026-10-16", "2026-10-21")]
    out = dashboard.rebase_rent_history(hist, catalog, state)
    assert [h["groups"]["aluguel|Itaim Bibi"]["ppm2"] for h in out] == [110, 115, 110]   # preço vigente em cada dia
    assert all(h["groups"]["venda|Itaim Bibi"]["ppm2"] == 20_000 and h["rent_basis"] == "total" for h in out)
    out[0]["groups"]["aluguel|Itaim Bibi"]["ppm2"] = 1.0
    assert dashboard.rebase_rent_history(out, catalog, state)[0]["groups"]["aluguel|Itaim Bibi"]["ppm2"] == 1.0  # só uma vez


def test_removed_neighborhood_leaves_dashboard_and_state(tmp_path, monkeypatch):
    """Bairro retirado do config: some do painel e do estado; se voltar, entra de novo como carga inicial."""
    cfgs = {"v": Config(neighborhoods=["Itaim Bibi", "Jardim Paulista"], min_area=100)}
    monkeypatch.setattr(app, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(app, "CURRENT", tmp_path / "anuncios.json")
    monkeypatch.setattr(app, "REPORT", tmp_path / "ANUNCIOS.md")
    from imoveis import dashboard
    monkeypatch.setattr(dashboard, "CATALOG", tmp_path / "catalogo.json")
    monkeypatch.setattr(dashboard, "HISTORY", tmp_path / "historico.json")
    monkeypatch.setattr(dashboard, "OUTPUT", tmp_path / "dados.json")
    monkeypatch.setattr(app.Config, "load", classmethod(lambda c: cfgs["v"]))
    monkeypatch.setattr(app.Notifier, "send", lambda self, subj, items, footer="": None)

    def L(i, nb):
        return Listing("zap", i, f"https://z/{i}", "venda", neighborhood=nb, area=120, price=2_000_000)

    batches = iter([{"zap": [L("1", "Itaim Bibi"), L("2", "Jardim Paulista")]}, {"zap": [L("1", "Itaim Bibi")]}])
    monkeypatch.setattr(app, "collect", lambda c, s: (next(batches), {}))
    app.run()
    cfgs["v"] = Config(neighborhoods=["Itaim Bibi"], min_area=100)
    app.run()
    rows = json.loads((tmp_path / "dados.json").read_text())["listings"]
    st = json.loads((tmp_path / "state.json").read_text())
    assert [r["id"] for r in rows] == ["1"]
    assert "zap:venda:2" not in st["seen"] and st["initialized_nbs"]["zap"] == ["itaim bibi"]
    assert "zap:venda:2" not in json.loads((tmp_path / "catalogo.json").read_text())
