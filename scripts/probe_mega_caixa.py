"""Sondagem (só imprime): por que a Mega Leilões devolve HTTP 500 e a lista da Caixa não responde."""
import re
import time

from imoveis.http import Http
from imoveis.sources import mega

http = Http(0.5, 1.0)


def show(name, url, **kw):
    t0 = time.time()
    try:
        r = http.get(url, retries=1, **kw)
        body = r.text
        title = re.search(r"<title>(.*?)</title>", body, re.S)
        print(f"[{name}] HTTP {r.status_code} · {len(body)} bytes · {time.time() - t0:.1f}s · título {title.group(1).strip()[:80] if title else '-'!r}"
              f" · lotes {len(mega.lot_links(body))} · início {body[:160]!r}")
    except Exception as e:
        print(f"[{name}] ERRO após {time.time() - t0:.1f}s: {type(e).__name__}: {str(e)[:200]}")


for name, url in {
    "mega home": "https://www.megaleiloes.com.br/",
    "mega imóveis": "https://www.megaleiloes.com.br/imoveis",
    "mega aptos SP": mega.LIST_URL.format(uf="sp", city="sao-paulo"),
    "mega aptos SP p2": mega.LIST_URL.format(uf="sp", city="sao-paulo") + "?pagina=2",
    "mega aptos": "https://www.megaleiloes.com.br/imoveis/apartamentos",
    "mega aptos SP estado": "https://www.megaleiloes.com.br/imoveis/apartamentos/sp",
    "mega busca": "https://www.megaleiloes.com.br/imoveis/apartamentos/sp/sao-paulo?tov=igbr",
}.items():
    show(name, url)

for i in range(2):
    show(f"caixa csv #{i + 1}", "https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_SP.csv", timeout=150)
show("caixa home", "https://venda-imoveis.caixa.gov.br/sistema/download-lista.asp", timeout=60)
