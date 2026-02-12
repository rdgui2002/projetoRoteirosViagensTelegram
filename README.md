# projetoRoteirosViagensTelegram

Deploy no Render (Webhook / Web Service):

- Tipo de servico: `Web Service`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn bot:web_app --host 0.0.0.0 --port $PORT`

Variaveis minimas:

- `TELEGRAM_BOT_TOKEN`
- `WEBHOOK_BASE_URL` (ex: `https://seu-servico.onrender.com`)
- `TELEGRAM_WEBHOOK_SECRET` (recomendado)
- `PUBLIC_BACKEND_BASE`
- `MINIAPP_URL`
- `COST_ROTEIRO_COMMAND`

Webhook default path:

- `/telegram/webhook`
