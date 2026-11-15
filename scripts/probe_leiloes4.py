"""Sondagem 4 (só imprime): coletores de leilão contra os sites reais (Zuk com 'Carregar mais', Mega, Caixa)."""
import collections

from imoveis.common import Config
from imoveis.http import Http
from imoveis.sources import caixa, mega, zuk

cfg = Config.load()
http = Http(0.8, 1.5)


def safe(name, fn):
    try:
        fn()
    except Exception as e:
        import traceback
        print(f"[{name}] ERRO {type(e).__name__}: {e}")
        traceback.print_exc()


def show(prefix, l):
    print(f"  {prefix} {l.neighborhood} | {l.address} | área {l.area} | qts {l.bedrooms} | preço {l.price} | aval {l.appraisal} | "
          f"rodadas {l.rounds} | tipo {l.auction_kind} | ocupado {l.occupied} | fin {l.financing} | {l.url[:110]}")


def z():
    html, more = zuk.load_all(http, zuk.capital_url(cfg, "apartamentos"))
    links = sorted(zuk.extract_all_links(html))
    nbs = collections.Counter(u.split("/")[4] for u in links)
    print(f"[zuk] {len(links)} lotes ({more} rodadas de 'Carregar mais'); alvo: {zuk.extract_links(html, cfg)}")
    print("[zuk] bairros:", nbs.most_common(40))
    for u in links[:4]:
        u = zuk.SITE + u
        r = http.get(u)
        l = zuk.parse_detail(r.text, u, u.rsplit("/", 1)[-1], u.split("/")[6], cfg)
        show("[zuk lote]", l) if l else print("  [zuk lote] descartado (não é apartamento)", u)


def m():
    base = mega.LIST_URL.format(uf="sp", city="sao-paulo")
    lots = {}
    for p in range(1, 6):
        r = http.get(base + (f"?pagina={p}" if p > 1 else ""))
        f = mega.lot_links(r.text)
        if not set(f) - set(lots):
            break
        lots.update(f)
    w = mega.wanted_slugs(cfg)
    alvo = {c: v for c, v in lots.items() if mega.neighborhood_of(v[1], w)}
    print(f"[mega] {len(lots)} lotes; nos bairros-alvo: {len(alvo)} {list(alvo)}")
    for c, (u, s) in (list(alvo.items()) + list(lots.items()))[:4]:
        d = mega.parse_detail(http.get(u).text)
        print(f"  [mega lote] {c} {s[:70]} -> {d}")


def c():
    found = caixa.fetch(http, cfg)
    ok = [l for l in found if cfg.matches(l)]
    print(f"[caixa] {len(found)} apartamentos na capital; nos critérios do painel: {len(ok)}")
    for l in ok[:5]:
        show("[caixa]", l)


safe("zuk", z)
safe("mega", m)
safe("caixa", c)
