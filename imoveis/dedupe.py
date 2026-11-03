"""Agrupa anúncios duplicados: a mesma unidade em mais de um portal ou republicada no mesmo portal.

Chave: negócio + bairro + rua (sem número, abreviações expandidas) + área (m² inteiros) + preço
(venda: preço pedido; aluguel: valor total, ou o aluguel quando o total não vem). Anúncios sem
rua, área ou preço, e leilões, nunca são agrupados. O QuintoAndar não informa o número do
prédio, por isso a chave usa só a rua; duas unidades diferentes na mesma rua, com mesma área e
exatamente o mesmo preço, seriam tratadas como uma — caso raro, aceito em troca de pegar as
republicações.
"""
from __future__ import annotations

import re

from .common import norm

ABBR = {"r": "rua", "av": "avenida", "al": "alameda", "pca": "praca", "tv": "travessa", "est": "estrada", "rod": "rodovia"}


def street(address: str | None) -> str:
    s = norm(address)
    s = re.split(r"[,\-–(]", s)[0]                    # "Rua X, 123 - Itaim" → "rua x"
    s = re.sub(r"\b(n[ºo°.]?|s/?n|numero)\b", " ", s)
    s = re.sub(r"\d+", " ", s)
    s = re.sub(r"[^a-z ]", " ", s)
    words = s.split()
    if words and words[0] in ABBR:
        words[0] = ABBR[words[0]]
    return " ".join(words)


def key(d: dict) -> str | None:
    business = d.get("business")
    if business not in ("venda", "aluguel"):
        return None
    st, area = street(d.get("address")), d.get("area")
    price = d.get("price") if business == "venda" else (d.get("total_monthly") or d.get("price"))
    if len(st) < 4 or not area or not price:
        return None
    return f"{business}|{norm(d.get('neighborhood'))}|{st}|{round(area)}|{round(price)}"
