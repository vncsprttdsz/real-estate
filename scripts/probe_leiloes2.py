"""Sondagem 2 (só imprime): paginação do Zuk, estrutura de lotes da Mega Leilões e detalhe da Caixa."""
import re
import html as H

from imoveis.common import Config
from imoveis.http import Http
from imoveis.sources import zuk

cfg = Config.load()
http = Http(0.8, 1.5)
TAG = re.compile(r"<[^>]+>")


def text(h):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    return re.sub(r"\s+", " ", H.unescape(TAG.sub(" ", h)))


def around(t, pat, n=3, w=160):
    out = []
    for m in re.finditer(pat, t, re.I):
        out.append(t[max(0, m.start() - w):m.end() + w].replace("\n", " "))
        if len(out) >= n:
            break
    return out


# ---------- Zuk ----------
base = zuk.capital_url(cfg)
r1 = http.get(base)
p1 = zuk.extract_all_links(r1.text)
print(f"[zuk] p1: {len(p1)} links")
for q in ("?page=2", "?pagina=2", "/2", "?p=2", "?pg=2", "?offset=30", "?ordem=recente&page=2"):
    try:
        r = http.get(base + q, retries=1)
        p = zuk.extract_all_links(r.text)
        print(f"[zuk] {q}: HTTP {r.status_code} · {len(p)} links · novos vs p1: {len(p - p1)}")
    except Exception as e:
        print("[zuk]", q, "ERRO", e)
for pat in (r"carregar mais|load[-_ ]?more|ver mais", r"/api/[a-z0-9/_-]+", r"ajax[a-z]*\(|\$\.(get|post)\(|fetch\(", r"data-(url|page|next|total)=\"[^\"]*\"",
            r"total\s*de\s*\d+\s*im[óo]veis|\d+\s*im[óo]veis\s*encontrados|\d+\s*resultados"):
    print(f"[zuk] /{pat}/:", [s[:300] for s in around(r1.text, pat, 3, 120)])
print("[zuk] selects:", re.findall(r'<select[^>]*name="([^"]+)"', r1.text)[:15])
for m in list(re.finditer(r"carregarMais", r1.text))[:6]:
    print("[zuk carregarMais]", r1.text[max(0, m.start() - 200):m.end() + 900].replace("\n", " ")[:1100])
for pat in (r"url\s*:\s*['\"][^'\"]+['\"]", r"route\(['\"][^'\"]+", r"https://www\.portalzuk\.com\.br/[a-z-]+/[a-z-]*(busca|filtro|carregar|lista)[^'\"\s]*"):
    print(f"[zuk] /{pat}/:", sorted(set(re.findall(pat, r1.text)))[:15])
print("[zuk] forms:", re.findall(r'<form[^>]*action="([^"]+)"', r1.text)[:5])
# página de um lote: campos
if p1:
    u = sorted(p1)[0]
    u = u if u.startswith("http") else zuk.SITE + u
    d = text(http.get(u).text)
    for pat in (r"avalia[çc][ãa]o", r"1[ºo°]?\s*leil[ãa]o|primeiro leil", r"2[ºo°]?\s*leil[ãa]o|segundo leil", r"ocupad|desocupad",
                r"financi|fgts", r"[áa]rea (útil|privativa|constru)", r"dormit|quartos", r"comiss[ãa]o", r"judicial|extrajudicial|aliena[çc][ãa]o fiduci", r"d[ée]bito|iptu|condom[íi]nio"):
        print(f"[zuk lote] /{pat}/:", around(d, pat, 2, 110))
    print("[zuk lote] url:", u)

# ---------- Mega Leilões ----------
def mega():
    mb = "https://www.megaleiloes.com.br/imoveis/apartamentos/sp/sao-paulo"
    r = http.get(mb)
    links = sorted(set(re.findall(r'href="(https://www\.megaleiloes\.com\.br/imoveis/apartamentos/sp/sao-paulo/[^"#?]+)"', r.text)))
    print(f"[mega] p1: {len(links)} links de lote; exemplos: {links[:5]}")
    for q in ("?pagina=2", "?page=2"):
        rr = http.get(mb + q, retries=1)
        l2 = set(re.findall(r'href="(https://www\.megaleiloes\.com\.br/imoveis/apartamentos/sp/sao-paulo/[^"#?]+)"', rr.text))
        print(f"[mega] {q}: HTTP {rr.status_code} · {len(l2)} links · novos: {len(l2 - set(links))}")
    t = text(r.text)
    print("[mega] total:", around(t, r"\d+\s*(lotes|im[óo]veis|resultados)", 3, 60))
    print("[mega] card exemplo:", around(t, r"Itaim", 2, 300))
    if links:
        d = text(http.get(links[0]).text)
        for pat in (r"avalia[çc][ãa]o", r"1[ªaº]?\s*(pra[çc]a|leil[ãa]o)", r"2[ªaº]?\s*(pra[çc]a|leil[ãa]o)", r"ocupad|desocupad", r"financi|fgts",
                    r"[áa]rea (útil|privativa|constru)", r"dormit|quartos", r"comiss[ãa]o", r"processo|judicial|extrajudicial", r"d[ée]bito"):
            print(f"[mega lote] /{pat}/:", around(d, pat, 2, 110))
        print("[mega lote] url:", links[0])



try:
    mega()
except Exception as e:
    print('[mega] ERRO', e)

# ---------- Caixa: página de detalhe ----------
try:
    rc = http.get("https://venda-imoveis.caixa.gov.br/listaweb/Lista_imoveis_SP.csv", timeout=90)
    raw = rc.content.decode("latin-1", errors="replace")
    m = re.search(r"https://venda-imoveis\.caixa\.gov\.br/sistema/detalhe-imovel\.asp\?hdnimovel=\d+", raw)
    if m:
        d = http.get(m.group(0), retries=1, timeout=40)
        dt = text(d.text)
        print(f"[caixa detalhe] HTTP {d.status_code} · {len(dt)} chars")
        for pat in (r"ocupad|desocupad|situa[çc][ãa]o", r"quartos|dormit", r"data|leil[ãa]o", r"matr[íi]cula|edital", r"FGTS|financiamento", r"d[ée]bito|condom"):
            print(f"[caixa detalhe] /{pat}/:", around(dt, pat, 2, 110))
except Exception as e:
    print("[caixa detalhe] ERRO", e)
