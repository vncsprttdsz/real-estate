# Monitor de imóveis — Vila Nova Conceição e Itaim Bibi

Verifica automaticamente, a cada 2 horas (das 7h às 23h), novos anúncios de
**apartamentos com 100 m² ou mais**, para **compra, aluguel e leilão**, em:

| Site | O que monitora |
|---|---|
| [ZAP Imóveis](https://www.zapimoveis.com.br) | venda e aluguel |
| [Viva Real](https://www.vivareal.com.br) | venda e aluguel |
| [QuintoAndar](https://www.quintoandar.com.br) | venda e aluguel |
| [Portal Zuk](https://www.portalzuk.com.br) | leilões |

Você recebe um alerta quando:
- entra um **anúncio novo** dentro dos critérios;
- um anúncio que já existia **baixa de preço** (3% ou mais, configurável);
- algum site **parar de responder** por 3 ciclos seguidos (para você saber que o monitor precisa de ajuste).

A lista completa do que está anunciado hoje fica em [`ANUNCIOS.md`](ANUNCIOS.md), atualizada a cada ciclo.

## Como ativar (uma vez só)

### 1. Escolha como quer receber os alertas

Configure **pelo menos um** canal em *Settings → Secrets and variables → Actions → New repository secret*:

**Opção A — ntfy (mais simples, push no celular, sem cadastro)**
1. Instale o app **ntfy** (Android/iOS).
2. Invente um nome de tópico difícil de adivinhar, ex.: `imoveis-vnc-8f3k2q`, e assine esse tópico no app.
3. Crie o secret `NTFY_TOPIC` com esse nome.

**Opção B — Telegram**
1. No Telegram, fale com **@BotFather**, envie `/newbot` e guarde o token → secret `TELEGRAM_BOT_TOKEN`.
2. Mande qualquer mensagem para o seu bot novo e abra
   `https://api.telegram.org/bot<TOKEN>/getUpdates`; o número em `"chat":{"id":...}` → secret `TELEGRAM_CHAT_ID`.

**Opção C — E-mail (Gmail)**
1. Crie uma [senha de app](https://myaccount.google.com/apppasswords) no Google.
2. Secrets: `SMTP_USER` (seu Gmail), `SMTP_PASSWORD` (a senha de app), `EMAIL_TO` (destinatário(s), separados por vírgula).

### 2. Teste e ligue
1. Faça o merge deste código na branch principal (os agendamentos do GitHub só rodam na branch padrão).
2. Em *Actions → Monitor de imóveis → Run workflow*, marque **"Só enviar uma mensagem de teste"** e confira se chegou.
3. Rode de novo sem marcar nada. Na primeira execução ele faz a **carga inicial** (registra o que já está anunciado,
   sem disparar centenas de alertas) e manda uma mensagem "Monitor de imóveis ativado". Daí em diante, só novidades.

## Ajustando os critérios

Edite [`config.yaml`](config.yaml): bairros, área mínima, venda/aluguel, preço máximo, sites ligados e
porcentagem para alerta de queda de preço. O próximo ciclo já usa os valores novos.

## Se um site parar de funcionar

Esses sites mudam o layout/API de tempos em tempos e às vezes bloqueiam robôs. Cada coletor tem um plano B
(API → página do site), mas se você receber o alerta "fonte com erro":
1. Rode o workflow manualmente com **"Salvar respostas brutas"** marcado.
2. Baixe o artefato `debug` gerado — ele mostra exatamente o que o site devolveu — e use para ajustar o coletor
   em `imoveis/sources/`.

## Rodando localmente

```bash
pip install -r requirements.txt
python -m imoveis --dry-run     # mostra o que encontraria, sem notificar nem salvar
python -m pytest tests          # testes dos parsers
```
