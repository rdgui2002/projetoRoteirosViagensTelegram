# projetoRoteirosViagensTelegram

Deploy no Render (Polling / Worker):

- Tipo de servico: `Worker`
- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`

Variaveis minimas:

- `TELEGRAM_BOT_TOKEN`

Variaveis opcionais (OpenAI para gerar roteiro por IA):

- `OPENAI_API_KEY`: ativa geracao de roteiro com OpenAI.
- `OPENAI_MODEL` (padrao `gpt-4o-mini`): modelo usado para montar o dia a dia.
- `OPENAI_API_BASE` (padrao `https://api.openai.com/v1`): endpoint base da API.
- `OPENAI_TIMEOUT` (padrao `45`): timeout da chamada em segundos.
- Sem OpenAI disponivel (chave ausente, erro de auth, quota/credito insuficiente), o bot responde:
  `Servico indisponivel no momento pro cliente`

Variavel opcional (marca no PDF):

- `PDF_WATERMARK_IMAGE_PATH`: caminho da imagem da marca do PDF (ex.: `assets/icone.png`).

Variaveis opcionais (envio por e-mail):

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_FROM`

Variaveis opcionais (saldo / modo teste):

- `BALANCE_ENFORCEMENT_ENABLED` (padrao `0`): quando `1`, exige saldo para gerar roteiro.
- `PUBLIC_BACKEND_BASE`: backend com endpoints `/api/balance` e `/api/spend`.
- `MINIAPP_URL`: link do miniapp de recarga via Pix.
- `COST_ROTEIRO_COMMAND` (padrao `1.00`): valor cobrado por roteiro.
- `TEST_BYPASS_ENABLED` (padrao `0`): ativa bypass de teste.
- `TEST_BYPASS_USER_IDS`: IDs Telegram separados por virgula. Se vazio e `TEST_BYPASS_ENABLED=1`, libera todos.

Teste sem saldo (recomendado para desenvolvimento):

1. Deixe `BALANCE_ENFORCEMENT_ENABLED=1` se quiser testar tambem o fluxo de cobranca.
2. Configure `TEST_BYPASS_ENABLED=1`.
3. Configure seu ID em `TEST_BYPASS_USER_IDS` (ou deixe vazio para liberar todos no ambiente local).
4. No Telegram, rode `/testemode` para validar se seu bypass esta ativo.

Arquitetura (resumo):

- `bot.py`: fluxo de conversa, coleta preferencias e responde no Telegram.
- `services/trip_logic_wikivoyage.py`: camada de orquestracao do roteiro (mantem nome legado por compatibilidade), valida entradas e chama IA.
- `services/openai_planner.py`: monta prompt, chama OpenAI, valida JSON e estrutura do roteiro.
- `services/presenter.py`: transforma o roteiro em texto natural para mensagem e PDF.
- `services/pdf_service.py`: renderiza o PDF final com layout e marca discreta.