# Diagnóstico — base de ITBI da Prefeitura de SP (etapa 1)

Gerado em 04/11/2026 a partir de `scripts/itbi_probe.py` (workflow manual "Diagnóstico ITBI").
Arquivos brutos não ficam no repositório; ver `arquivos.json` (URL, bytes, sha256, data do download).

## 1. Repositório atual

- **Stack:** Python 3.12 (curl_cffi, PyYAML) para a coleta; painel estático em `docs/` (HTML + JS puro, gráficos SVG
  próprios, sem build) publicado pelo GitHub Pages; motor do simulador em `docs/sim.js`.
- **Coleta:** `imoveis/` — uma fonte por módulo em `imoveis/sources/` (ZAP/Viva Real pela API Glue, QuintoAndar pela
  API de busca, Portal Zuk por HTML). `imoveis/__main__.py` compara com `data/state.json`, notifica (WhatsApp/CallMeBot,
  Telegram, ntfy, e-mail) e chama `dashboard.build`, que grava `data/catalogo.json`, `data/historico.json` e
  `docs/dados.json`. Projeções do Focus em `data/macro.json` (`imoveis/macro.py`).
- **Configuração:** `config.yaml` (bairros, área mínima, orçamento, fontes).
- **Agendamento/deploy:** `.github/workflows/monitor.yml` (cron a cada 2 h, 7h–23h; commita `data/`, `docs/` e
  `ANUNCIOS.md`). O GitHub Pages publica `docs/` do branch padrão. Observação: o agendador do GitHub tem disparado o
  monitor a cada 7–17 h, não a cada 2 h.

## 2. Arquivos da Prefeitura

Página: https://prefeitura.sp.gov.br/web/fazenda/w/acesso_a_informacao/31501 — 21 links .xlsx raspados (2006 a 2026).

- Os nomes **não** seguem padrão: o de 2026 é `GUIAS DE ITBI PAGAS (03112026).xlsx` (data de publicação no nome) e o de
  2025 é `GUIAS DE ITBI PAGAS (28012026) XLS.xlsx`. O ano tem de ser lido pelas abas (`JAN-2025` …), não pelo nome.
  O nome do arquivo do ano corrente muda a cada atualização — a ingestão deve rastrear o link pela página e o conteúdo
  pelo sha256.
- 2026: 9 abas mensais (JAN–SET-2026) + LEGENDA, EXPLICAÇÕES, Tabela de USOS, Tabela de PADRÕES. ~15–23 mil linhas por
  aba (cidade inteira). Arquivos de 20–47 MB.
- 2024: a aba OUT-2024 não tem linha de cabeçalho (o cabeçalho é aplicado pela posição das colunas da aba anterior).
  FEV-2026 traz a grafia "Descrição do pardão (IPTU)" e algumas abas têm uma coluna vazia extra ("Unnamed: 28").

### Colunas (nomes exatos, aba JAN-2026)

| Coluna | Tipo | Nulos |
|---|---|---|
| N° do Cadastro (SQL) | inteiro | 0% |
| Nome do Logradouro | texto (abreviado: "R", "AV", "AL"; títulos "SEN", "DR") | 0% |
| Número | inteiro | 0% |
| Complemento | texto ("AP 94 E 1VG", "AP 1211 TORRE C") | 9,6% |
| Bairro | texto livre | **41,2%** |
| Referência | texto livre | 59,5% |
| CEP | inteiro (sem zero à esquerda) | 0% |
| Natureza de Transação | texto ("1.Compra e venda", …; 26 valores) | 0% |
| Valor de Transação (declarado pelo contribuinte) | número | 0% |
| Data de Transação | data | 0% |
| Valor Venal de Referência | número | 0% |
| Proporção Transmitida (%) | número | 0% |
| Valor Venal de Referência (proporcional) | número | 0% |
| Base de Cálculo adotada | número | 0% |
| Tipo de Financiamento | texto (SFH, MCMV, Consórcio, SFI…) | 54,5% |
| Valor Financiado | número | 0% |
| Cartório de Registro | texto | 0% |
| Matrícula do Imóvel | inteiro | 0% |
| Situação do SQL | texto | 0% |
| Área do Terreno (m2) | inteiro | 0% |
| Testada (m) | número | 0% |
| Fração Ideal | número | 0% |
| Área Construída (m2) | inteiro | 0% |
| Uso (IPTU) | código (20 = apartamento em condomínio) | 0% |
| Descrição do uso (IPTU) | texto | 0% |
| Padrão (IPTU) | código (21–25 = residencial vertical A–E) | 0% |
| Descrição do padrão (IPTU) | texto | 0% |
| ACC (IPTU) | ano de construção corrigido | 20,1% (cidade); 0% nos apartamentos da região |

Não há coluna própria de ano de construção: o ACC (IPTU) é o substituto. Perfil completo (todas as abas, tipos, %
nulos, distintos) em `perfil.json`; tabelas de apoio em `apoio_*.csv`.

## 3. Mapeamento para os bairros

Amostra: CEPs 014xx/045xx, 2024–2026, apartamentos (uso 20), compra e venda, 100% transmitido = 11.448 transações.

| Método | Cobertura nos bairros-alvo | Observação |
|---|---|---|
| Coluna "Bairro" do ITBI | 26,6% (precisão 65%) | 39% vazia; texto livre ("TORRE A", "SETOR 1"); 441 falsos positivos fora da região (ex.: "JD AMERICA" de outros bairros) |
| **CEP → bairro dos Correios (ViaCEP)** | **98,8%** direto; 100% com o bairro majoritário do CEP de 5 dígitos | determinístico; é a nomenclatura usada pelos portais |
| SQL × cadastro do IPTU / GeoSampa | não testado | desnecessário: a planilha já traz os campos do IPTU; o bairro do IPTU é o mesmo texto livre |

Transações nos últimos 12 meses (out/2025–set/2026): Jardim Paulista 799 · Itaim Bibi 436 (inclui Chácara Itaim) ·
Vila Nova Conceição 329 · Jardim Europa 75 · Jardim Paulistano 43 · Jardim América 19. Vizinhos a pé da Faria Lima
(opcionais): Vila Olímpia 466 · Cerqueira César 368.

Defasagem: 88% das transações aparecem na aba do próprio mês e 99% até o mês seguinte; o último mês publicado chega
~10% incompleto.

## 4. Definição de área

A "Área Construída" do ITBI vem do IPTU e inclui a fração das áreas comuns (e, quando vinculada, a vaga). Casando
anúncios de venda com transações do mesmo endereço (rua + número), 12 meses:

- 356 prédios nas duas bases. Área do anúncio ÷ área construída do ITBI: **mediana 0,71** (P25 0,59 · P75 0,85);
  em 50 prédios de tipologia homogênea, 0,77.
- O fator varia muito de prédio para prédio; um fator único (≈ 0,72) serve para a média do bairro, não para um prédio.

Efeito no desconto (mediana R$/m² ITBI ÷ mediana R$/m² anúncio − 1):

| Bairro | Bruto (áreas sem ajuste) | Com fator 0,72 |
|---|---|---|
| Itaim Bibi | −49,7% | −30,1% |
| Jardim Paulista | −44,9% | −23,5% |
| Vila Nova Conceição | −58,6% | −42,5% (efeito de composição: anúncios mais caros que o estoque negociado) |

Comparação sem depender de área — mesmo prédio, unidades com área equivalente (±10%), valor pago ÷ preço pedido:
**mediana −25%** (P25 −40%, P75 −8%; 147 prédios), estável entre os fatores 0,66 e 0,78 (−23% a −27%).

## 5. Qualidade

- Declarado = venal de referência: 0,3% · declarado < venal: 11,8% (sinalizar) · venal = 0: 0,8%.
- R$/m² < 30% da mediana do bairro: 2,1% (ex.: "AP 509 E VAGA" por R$ 60 mil) — típico de partilha, valor parcial
  ou erro; > 3× a mediana: 20 casos.
- Duplicatas exatas (SQL + data + valor): 2; mesmo SQL + data com valor diferente: 7.
- 24% com financiamento; 8,4% em prédios com ACC ≥ 2023 (entregas de lançamentos, preço possivelmente contratado
  na planta).

## Errata (04/11/2026, após a ingestão completa)

As amostras deste diagnóstico (`amostra_*.csv.gz`) perderam parte das linhas de alguns meses: nas abas em que o CEP
vem como número decimal ("4532010.0"), o script de diagnóstico juntou o ".0" aos dígitos e o prefixo do CEP saiu
errado (fev/mar/abr/set-2026 e abr/jun/set/out/nov-2025 ficaram com ~30–60% das linhas). O pipeline de produção
(`imoveis/itbi.py`) trata o CEP corretamente. Refeito com os dados completos (out/2025–set/2026, 2.344 transações
sem outliers nem compras em bloco):

- Área do anúncio ÷ área construída do ITBI: mediana **0,71** em 434 prédios (P25 0,59 · P75 0,85); 0,77 em 81
  prédios de tipologia homogênea. O fator 0,72 se mantém.
- Desconto mesmo prédio/tipologia (valor pago ÷ pedido − 1): mediana **−23%** (P25 −39% · P75 −7%; 187 prédios).
- Compras em bloco (5+ unidades do mesmo prédio na mesma data) passaram a ser excluídas das métricas.
