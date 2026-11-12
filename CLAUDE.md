# Instruções para o Claude

## Delegação de modelos

Prioridade: qualidade antes de custo. Delegar serve para poupar contexto
e tempo, nunca para baixar o nível de quem decide.

Quando delegar
- Tarefa pequena ou com etapas acopladas: execute direto, sem subagents.
- Delegue apenas quando houver 3+ subtarefas independentes, cada uma
  grande o bastante para compensar o briefing, ou muita leitura/busca
  que poluiria o contexto principal.
- Dois subagents nunca escrevem no mesmo arquivo.

Qual modelo
- Haiku: busca e leitura de arquivos; mudanças mecânicas e repetitivas
  com padrão já definido. Devolve caminhos, linhas e trechos literais,
  não conclusões.
- Sonnet: implementação, testes, refatoração e debugging com causa
  provável conhecida, dentro de um único dashboard.
- Não delegar (fica na sessão principal): arquitetura, requisitos
  ambíguos, debugging sem causa clara, regras de cálculo e leitura de
  dados (CVM, contador do Mercado Livre, crédito, macro), e mudança que
  cruze dashboards ou altere o formato de dados entre pipeline e página.
- Na dúvida entre dois níveis, use o mais forte.

Briefing de cada subagent
- Autocontido: objetivo, arquivos relevantes, restrições, critério de
  aceite e o que NÃO alterar. O subagent não vê esta conversa.
- O subagent não faz commit, push nem PR, não dispara workflows e não
  delega.
- Peça retorno curto: o que mudou, onde, e pendências. Sem colar
  arquivos inteiros.

Verificação (sempre na sessão principal)
- Leia o diff, rode os testes do módulo, regenere os arquivos gerados e
  confira no navegador quando a mudança afeta uma página. Não confie no
  relato do subagent.
- Falha = teste quebrado, critério de aceite não atingido ou alteração
  fora do escopo. Se faltou informação no briefing, corrija o briefing e
  repita uma vez; se o briefing estava completo, suba de modelo ou
  assuma a tarefa.

Aprovação
- Antes de delegar qualquer tarefa que escreva código, mostre a divisão
  em uma tabela curta (tarefa, modelo, arquivos, motivo) e aguarde minha
  aprovação. Busca e leitura não precisam de aprovação.
- Sem mim na conversa (tarefa agendada, evento de PR): não delegue
  escrita.

### Neste repositório (radar de apartamentos)

Subagents prontos em `.claude/agents/`:
- `leitor` (Haiku, só leitura): buscas no código, nos dados e nos logs.
- `implementador` (Sonnet): mudança dentro de uma única área abaixo, com testes.

Áreas (cada uma conta como "um dashboard" para as regras acima):
- Painel: `docs/index.html` e `docs/transacoes.js`.
- Coletores de portal: `imoveis/sources/*.py` (um portal por arquivo).
- Avisos: `imoveis/notify.py` e o texto das mensagens em `imoveis/__main__.py`.
- Página do mapa: `docs/area.html`.

Ficam sempre na sessão principal (regras de cálculo, leitura de dados e contrato pipeline → página):
- Formato de `docs/dados.json`, `docs/transacoes.json` e `data/*.json` (`imoveis/dashboard.py`,
  `imoveis/itbi.py`) e qualquer campo novo que a página leia.
- Simulador comprar × alugar e FGTS (`docs/sim.js`), Focus/macro (`imoveis/macro.py`).
- ITBI: leitura das planilhas da Prefeitura, fator de área 0,72, filtros e métricas (`imoveis/itbi.py`, `docs/itbi.js`).
- Critérios de aviso: orçamento, área de interesse, abaixo da mediana (`imoveis/bargain.py`), queda de preço,
  resumo diário, carga inicial e estado (`imoveis/__main__.py`, `imoveis/common.py`, `imoveis/geo.py`,
  `imoveis/dedupe.py`), e `config.yaml`.

Verificação:
- `python -m pytest -q tests` e `node tests/js/sim.test.mjs && node tests/js/itbi.test.mjs`.
- Mudança no painel: abrir `docs/` num servidor local (`python -m http.server` dentro de `docs/`) com Playwright,
  no computador e no celular (390 px), sem erro no console nem rolagem horizontal.
- Os sites dos portais, da Prefeitura e do CallMeBot não respondem deste ambiente: sondagens rodam no GitHub
  Actions (workflow "Sondagem dos sites", entrada `modulo`), disparadas pela sessão principal.
