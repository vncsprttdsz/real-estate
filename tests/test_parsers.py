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
