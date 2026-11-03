"""Sondagem: confere a API do Boletim Focus (BCB) usada pelo simulador. Só imprime; não altera estado."""
import json

from imoveis import macro
from imoveis.http import Http

http = Http(0.3, 0.6)
for key, nome in macro.INDICADORES.items():
    try:
        out = macro.fetch_indicador(http, nome)
        print(f"[focus] {nome}: OK {json.dumps(out, ensure_ascii=False)}")
    except Exception as e:
        print(f"[focus] {nome}: ERRO {e}")
        r = http.get(macro.FOCUS, params={"$top": "3", "$format": "json", "$filter": f"Indicador eq '{nome}'"}, retries=1)
        print(f"   bruto HTTP {r.status_code}: {r.text[:600]}")
r = http.get(macro.FOCUS, params={"$top": "200", "$format": "json", "$select": "Indicador", "$orderby": "Data desc"}, retries=1)
try:
    print("[focus] indicadores disponíveis:", sorted({x["Indicador"] for x in r.json()["value"]}))
except Exception as e:
    print("[focus] lista de indicadores falhou:", e, r.text[:300])
