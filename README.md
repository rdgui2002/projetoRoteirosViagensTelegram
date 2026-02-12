# projetoRoteirosViagensTelegram

Deploy no Render (Polling / Worker):

- Tipo de servico: `Worker`
- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`

Variaveis minimas:

- `TELEGRAM_BOT_TOKEN`

Variaveis opcionais (envio por e-mail):

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_FROM`
