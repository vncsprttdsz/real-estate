"""Sondagem 5 (só imprime): resposta do 'Carregar mais' do Zuk, contexto de ocupação no Zuk e praças/avaliação na Mega."""
import re

from imoveis.common import Config
from imoveis.http import Http
from imoveis.sources import mega, zuk

cfg = Config.load()
http = Http(0.8, 1.5)


def ctx(t, pat, n=4, w=120):
    return [t[max(0, m.start() - w):m.end() + w] for m in list(re.finditer(pat, t, re.I))[:n]]


def safe(name, fn):
    try:
        fn()
    except Exception as e:
        print(f"[{name}] ERRO {type(e).__name__}: {e}")


def zuk_more():
    url = zuk.capital_url(cfg, "apartamentos")
    r = http.get(url)
    h = r.text
    tok = re.search(r'name="_token"\s+value="([^"]+)"', h)
    meta = re.search(r'name="csrf-token"\s+content="([^"]+)"', h)
    print("[zuk] token input:", bool(tok), "| meta csrf:", bool(meta))
    print("[zuk] Ziggy carrega:", ctx(h, r"carrega[.-]mais", 4, 150))
    print("[zuk] _token contextos:", ctx(h, r"_token", 3, 80))
    print("[zuk] ordenar:", ctx(h, r'id="ordenar"', 1, 300))
    print("[zuk] cards:", len(re.findall(r'card_lotes_div', h)), "parceiro=0:", len(re.findall(r'data-parceiro="0"', h)),
          "| arr_bounds/_clo/count_lotes_zuk:", ctx(h, r"arr_bounds\s*=|_clo\s*=|count_lotes_zuk\s*=", 3, 80))
    t = (tok or meta).group(1) if (tok or meta) else ""
    for u in ("https://www.portalzuk.com.br/carrega-mais", "https://www.portalzuk.com.br/carregamais", "https://www.portalzuk.com.br/carrega/mais"):
        rr = http.post(u, data={"limit": 30, "count_imovel_zuk": 30, "path": url, "order": "", "div_parceiro_count": 0, "_token": t},
                       headers={"X-Requested-With": "XMLHttpRequest", "X-CSRF-TOKEN": t, "Referer": url}, retries=1)
        print(f"[zuk POST {u}] HTTP {rr.status_code} · {len(rr.text)} bytes · links {len(zuk.extract_all_links(rr.text))} · início: {rr.text[:200]!r}")


def zuk_occ():
    url = zuk.capital_url(cfg, "apartamentos")
    links = sorted(zuk.extract_all_links(http.get(url).text))[:3]
    for u in links:
        t = zuk.page_text(http.get(zuk.SITE + u).text)
        print(f"[zuk ocup] {u[-40:]}:", ctx(t, r"ocupad", 6, 90))


def mega_detail():
    for u in ("https://www.megaleiloes.com.br/imoveis/apartamentos/sp/sao-paulo/apartamento-67-m2-prox-a-av-nove-de-julho-itaim-bibi-sao-paulo-sp-j130657",
              "https://www.megaleiloes.com.br/imoveis/apartamentos/sp/sao-paulo/apartamento-47-m2-01-vaga-altos-de-vila-prudente-sao-paulo-sp-x131268"):
        t = mega.page_text(http.get(u).text)
        print(f"[mega {u[-8:]}] praças:", ctx(t, r"pra[çc]a|leil[ãa]o\s*\d|1[ºª]|2[ºª]", 6, 90))
        print(f"[mega {u[-8:]}] avaliação:", ctx(t, r"avalia", 4, 90))
        print(f"[mega {u[-8:]}] ocupação:", ctx(t, r"ocupad", 4, 90))
        print(f"[mega {u[-8:]}] quartos/área:", ctx(t, r"dormit|quarto|su[íi]te|[áa]rea", 4, 70))


safe("zuk more", zuk_more)
safe("zuk occ", zuk_occ)
safe("mega", mega_detail)
