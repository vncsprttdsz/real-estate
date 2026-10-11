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
