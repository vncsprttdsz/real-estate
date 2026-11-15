"""Sondagem 3 (só imprime): JS do 'Carregar mais' do Zuk, URLs por bairro; links, paginação e detalhe da Mega Leilões."""
import re
import html as H

from imoveis.common import Config
from imoveis.http import Http
from imoveis.sources import zuk

cfg = Config.load()
http = Http(0.8, 1.5)


def text(h):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", h)))


def safe(name, fn):
    try:
        fn()
    except Exception as e:
        print(f"[{name}] ERRO {type(e).__name__}: {e}")


def zuk_js():
    base = zuk.capital_url(cfg)
    r = http.get(base)
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', r.text)
    print("[zuk] scripts:", [s for s in srcs if "portalzuk" in s or s.startswith("/")][:20])
    for s in srcs:
        if "googleapis" in s or "gtag" in s or "facebook" in s:
            continue
        u = s if s.startswith("http") else zuk.SITE + s
        if "portalzuk" not in u:
            continue
        try:
            js = http.get(u, retries=1).text
        except Exception:
            continue
        for m in list(re.finditer(r"carregarMais|carregar-mais|loadMore", js))[:3]:
            print(f"[zuk js {u[-60:]}]", js[max(0, m.start() - 300):m.end() + 1200].replace("\n", " ")[:1500])
    inline = [m.start() for m in re.finditer(r"btn_carregarMais", r.text)]
    for i in inline[1:4]:
        print("[zuk inline]", r.text[max(0, i - 600):i + 1500].replace("\n", " ")[:2100])
    for path in ("/leilao-de-imoveis/c/todos-imoveis/sp/capital/sao-paulo/itaim-bibi", "/leilao-de-imoveis/c/todos-imoveis/sp/sao-paulo/itaim-bibi",
                 "/leilao-de-imoveis/c/apartamentos/sp/capital/sao-paulo", "/leilao-de-imoveis/c/todos-imoveis/sp/capital/sao-paulo?bairro=itaim-bibi"):
        rr = http.get(zuk.SITE + path, retries=1)
        ls = zuk.extract_all_links(rr.text)
        print(f"[zuk] {path}: HTTP {rr.status_code} · {len(ls)} links · bairros {sorted({u.split('/')[4] for u in ls})[:12]}")


def mega():
    mb = "https://www.megaleiloes.com.br/imoveis/apartamentos/sp/sao-paulo"
    r = http.get(mb)
    hrefs = sorted(set(re.findall(r'href="([^"]+)"', r.text)))
    lot = [h for h in hrefs if re.search(r"j\d{5,}|/imoveis/apartamentos/", h, re.I)]
    print(f"[mega] hrefs de lote ({len(lot)}):", lot[:12])
    codes = set(re.findall(r"\bJ\d{6}\b", r.text))
    print(f"[mega] códigos de lote na p1: {len(codes)}")
    for q in ("?pagina=2", "?page=2", "?pag=2"):
        rr = http.get(mb + q, retries=1)
        c2 = set(re.findall(r"\bJ\d{6}\b", rr.text))
        print(f"[mega] {q}: HTTP {rr.status_code} · {len(c2)} códigos · novos {len(c2 - codes)}")
    t = text(r.text)
    print("[mega] total/paginação:", re.findall(r"(\d[\d.]*\s*(?:lotes?|resultados?|im[óo]veis)\s*(?:encontrados?)?)", t)[:5],
          re.findall(r'href="([^"]*(?:pagina|page)=\d+[^"]*)"', r.text)[:5])
    for b in ("itaim-bibi", "vila-nova-conceicao"):
        rr = http.get(f"{mb}/{b}", retries=1)
        print(f"[mega] /{b}: HTTP {rr.status_code} · {len(set(re.findall(r'J\d{6}', rr.text)))} códigos")
    if lot:
        u = lot[0] if lot[0].startswith("http") else "https://www.megaleiloes.com.br" + lot[0]
        d = text(http.get(u).text)
        print("[mega lote] url:", u, "| chars:", len(d))
        for pat in (r"avalia[çc][ãa]o", r"1[ªaº]\s*pra[çc]a", r"2[ªaº]\s*pra[çc]a", r"ocupad|desocupad", r"financi|fgts|parcel",
                    r"[áa]rea (útil|privativa|constru)", r"dormit|quartos|su[íi]tes", r"comiss[ãa]o", r"processo|judicial|extrajudicial",
                    r"d[ée]bito|iptu|condom", r"latitude|lat\W|google.com/maps"):
            ms = [d[max(0, m.start() - 110):m.end() + 140] for m in list(re.finditer(pat, d, re.I))[:2]]
            print(f"[mega lote] /{pat}/:", ms)


safe("zuk", zuk_js)
safe("mega", mega)
