from __future__ import annotations

import io
import logging
import os
import re
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from services.email_service import send_email_with_pdf
from services.pdf_service import build_pdf_bytes
from services.presenter import format_trip_output, trip_to_pdf_lines
from services.trip_logic_wikivoyage import (
    DestinationIsCountryError,
    RITMOS_VALIDOS,
    TripPreferences,
    build_itinerary_from_wikivoyage,
    detect_country_destination,
)
from services.utils import parse_date_br

load_dotenv()

TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
if not TOKEN:
    raise SystemExit("ERRO: defina TELEGRAM_BOT_TOKEN (ou BOT_TOKEN) no .env")

PUBLIC_BACKEND_BASE = os.getenv("PUBLIC_BACKEND_BASE", "").strip().rstrip("/")
MINIAPP_URL = os.getenv("MINIAPP_URL", "").strip()
if not MINIAPP_URL and PUBLIC_BACKEND_BASE:
    MINIAPP_URL = f"{PUBLIC_BACKEND_BASE}/miniapp"

TEST_BYPASS_ENABLED = os.getenv("TEST_BYPASS_ENABLED", "0").strip() == "1"
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram/webhook").strip()
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

if not WEBHOOK_PATH.startswith("/"):
    WEBHOOK_PATH = f"/{WEBHOOK_PATH}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Estados
(NOME, EMAIL, DESTINO, DATA_IDA, DATA_VOLTA, RITMO, CONFIRMAR) = range(7)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
tg_app: Application | None = None


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default).strip()
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        logging.warning("Valor invalido em %s=%r. Usando %s", name, raw, default)
        return Decimal(default)


def _env_float(name: str, default: str) -> float:
    raw = os.getenv(name, default).strip()
    try:
        return float(raw)
    except ValueError:
        logging.warning("Valor invalido em %s=%r. Usando %s", name, raw, default)
        return float(default)


def _env_user_ids(name: str) -> set[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()
    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.add(int(part))
        except ValueError:
            logging.warning("Ignorando user_id invalido em %s: %r", name, part)
    return values


COST_ROTEIRO_COMMAND = _env_decimal("COST_ROTEIRO_COMMAND", "1.00")
WALLET_HTTP_TIMEOUT = _env_float("WALLET_HTTP_TIMEOUT", "20")
TEST_BYPASS_USER_IDS = _env_user_ids("TEST_BYPASS_USER_IDS")


def money_fmt(value: Decimal) -> str:
    return f"R$ {value.quantize(Decimal('0.01'))}"


def miniapp_kb() -> InlineKeyboardMarkup | None:
    if not MINIAPP_URL:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Recarregar via Pix", web_app=WebAppInfo(url=MINIAPP_URL))]
        ]
    )


def is_bypass_user(telegram_id: int) -> bool:
    return TEST_BYPASS_ENABLED and telegram_id in TEST_BYPASS_USER_IDS


async def get_balance_api(telegram_id: int) -> Decimal:
    if not PUBLIC_BACKEND_BASE:
        raise RuntimeError("PUBLIC_BACKEND_BASE_not_configured")
    async with httpx.AsyncClient(timeout=WALLET_HTTP_TIMEOUT) as client:
        response = await client.get(
            f"{PUBLIC_BACKEND_BASE}/api/balance",
            params={"telegram_id": telegram_id},
        )
    response.raise_for_status()
    data = response.json()
    return Decimal(str(data["balance"]))


async def spend_api(telegram_id: int, amount: Decimal) -> dict | None:
    if not PUBLIC_BACKEND_BASE:
        raise RuntimeError("PUBLIC_BACKEND_BASE_not_configured")
    async with httpx.AsyncClient(timeout=WALLET_HTTP_TIMEOUT) as client:
        response = await client.post(
            f"{PUBLIC_BACKEND_BASE}/api/spend",
            json={"telegram_id": telegram_id, "amount": str(amount)},
        )
    if response.status_code == 402:
        return None
    response.raise_for_status()
    return response.json()


async def charge_or_block(update: Update, amount: Decimal, service_name: str) -> bool:
    if not update.effective_user or not update.message:
        return False

    if amount <= 0:
        return True
    if is_bypass_user(update.effective_user.id):
        return True

    try:
        spend_result = await spend_api(update.effective_user.id, amount)
    except Exception as exc:
        await update.message.reply_text(f"Erro ao cobrar saldo: {type(exc).__name__}: {exc}")
        return False

    if spend_result is not None:
        return True

    try:
        balance = await get_balance_api(update.effective_user.id)
        balance_text = money_fmt(balance)
    except Exception:
        balance_text = "indisponivel"

    await update.message.reply_text(
        f"Saldo insuficiente para {service_name}.\n"
        f"Custo: {money_fmt(amount)}\n"
        f"Seu saldo: {balance_text}\n\n"
        "Recarregue para continuar:",
        reply_markup=miniapp_kb(),
    )
    return False


async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.effective_user or not update.message:
        return
    try:
        balance = await get_balance_api(update.effective_user.id)
    except Exception as exc:
        await update.message.reply_text(f"Erro ao consultar saldo: {type(exc).__name__}: {exc}")
        return
    await update.message.reply_text(f"Seu saldo: {money_fmt(balance)}", reply_markup=miniapp_kb())


async def cmd_recarregar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.message:
        return
    if not MINIAPP_URL:
        await update.message.reply_text("MINIAPP_URL nao configurada no .env.")
        return
    await update.message.reply_text("Clique para recarregar via Pix:", reply_markup=miniapp_kb())


async def cmd_servicos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.message:
        return
    await update.message.reply_text(
        "Servicos pagos:\n"
        f"- Gerar roteiro IA: {money_fmt(COST_ROTEIRO_COMMAND)}\n\n"
        "Comandos: /saldo /recarregar",
        reply_markup=miniapp_kb(),
    )


async def cmd_meuid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.effective_user or not update.message:
        return
    await update.message.reply_text(f"Seu Telegram ID: {update.effective_user.id}")


def init_user_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prefs"] = {
        "nome": "",
        "email": "",
        "destino": "",
        "data_ida": "",
        "data_volta": "",
        "ritmo": "",
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    init_user_state(context)
    if not update.message:
        return NOME
    await update.message.reply_text(
        "Fala! Antes de comecar, me diz seu nome?\n"
        f"Custo para gerar roteiro: {money_fmt(COST_ROTEIRO_COMMAND)}",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NOME


async def set_nome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nome = (update.message.text or "").strip()
    if len(nome) < 2:
        await update.message.reply_text("Pode me dizer seu nome (apelido tambem serve)?")
        return NOME

    context.user_data["prefs"]["nome"] = nome

    await update.message.reply_text(
        "Boa! Agora me manda seu e-mail pra eu te enviar o PDF no final.\n"
        "Ex: nome@gmail.com\n\n"
        "Se nao quiser enviar por e-mail, digita: pular",
        reply_markup=ReplyKeyboardRemove(),
    )
    return EMAIL


async def set_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = (update.message.text or "").strip()
    low = txt.lower()

    if low in ["pular", "skip", "nao", "não", "sem", "depois"]:
        context.user_data["prefs"]["email"] = ""
    else:
        if not EMAIL_RE.match(txt):
            await update.message.reply_text("Esse e-mail parece invalido. Tenta de novo ou digita 'pular'.")
            return EMAIL
        context.user_data["prefs"]["email"] = txt

    nome = context.user_data["prefs"]["nome"]
    await update.message.reply_text(
        f"Fechado, {nome}!\n\nAgora manda o destino (cidade).\n"
        "Ex: Barcelona, Rio de Janeiro, Cairo, Tokyo.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return DESTINO


async def set_destino(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    destino = (update.message.text or "").strip()
    if len(destino) < 2:
        await update.message.reply_text("Manda uma cidade valida. Ex: Cairo.")
        return DESTINO

    msg_country = await detect_country_destination(destino)
    if msg_country:
        await update.message.reply_text(msg_country)
        await update.message.reply_text("Agora me diga a CIDADE (ex: Cairo, Luxor, Aswan).")
        return DESTINO

    context.user_data["prefs"]["destino"] = destino
    await update.message.reply_text("Data de ida? (ex: 10/03/2026)")
    return DATA_IDA


async def set_data_ida(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        d = parse_date_br(update.message.text)
        context.user_data["prefs"]["data_ida"] = d.strftime("%d/%m/%Y")
    except Exception:
        await update.message.reply_text("Nao entendi a data. Tenta assim: 10/03/2026")
        return DATA_IDA

    await update.message.reply_text("Data de volta? (ex: 15/03/2026)")
    return DATA_VOLTA


async def set_data_volta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ida = parse_date_br(context.user_data["prefs"]["data_ida"])
        volta = parse_date_br(update.message.text)

        if volta <= ida:
            await update.message.reply_text(
                "Essa volta esta antes (ou igual) a ida. A volta precisa ser depois.\n"
                "Tenta de novo (ex: 15/03/2026)."
            )
            return DATA_VOLTA

        context.user_data["prefs"]["data_volta"] = volta.strftime("%d/%m/%Y")
    except Exception:
        await update.message.reply_text("Nao entendi a data. Tenta assim: 15/03/2026")
        return DATA_VOLTA

    kb = ReplyKeyboardMarkup([["leve", "medio"], ["intenso"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Qual ritmo? (leve / medio / intenso)", reply_markup=kb)
    return RITMO


async def set_ritmo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ritmo = (update.message.text or "").strip().lower()

    if ritmo == "medio" and "medio" not in RITMOS_VALIDOS and "médio" in RITMOS_VALIDOS:
        ritmo = "médio"

    if ritmo not in RITMOS_VALIDOS:
        await update.message.reply_text("Escolhe um: leve / medio / intenso")
        return RITMO

    context.user_data["prefs"]["ritmo"] = ritmo

    p = context.user_data["prefs"]
    resumo = (
        "Resumo\n"
        f"- Nome: {p['nome']}\n"
        f"- Email: {p['email'] if p['email'] else 'nao informado'}\n"
        f"- Destino: {p['destino']}\n"
        f"- Datas: {p['data_ida']} -> {p['data_volta']}\n"
        f"- Ritmo: {p['ritmo']}\n"
        f"- Custo: {money_fmt(COST_ROTEIRO_COMMAND)}\n\n"
        "Confirmar e gerar roteiro? (sim/nao)"
    )
    kb = ReplyKeyboardMarkup([["sim", "nao"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(resumo, reply_markup=kb)
    return CONFIRMAR


async def confirm_and_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ans = (update.message.text or "").strip().lower()
    if ans not in ["sim", "s", "yes"]:
        await update.message.reply_text("Beleza! Se quiser recomecar: /start", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if not await charge_or_block(update, COST_ROTEIRO_COMMAND, "gerar roteiro IA"):
        return CONFIRMAR

    p = context.user_data["prefs"]
    prefs = TripPreferences(
        nome=p["nome"],
        destino=p["destino"],
        data_ida=p["data_ida"],
        data_volta=p["data_volta"],
        ritmo=p["ritmo"],
    )

    await update.message.reply_text("Boa! To montando seu roteiro...", reply_markup=ReplyKeyboardRemove())

    try:
        trip = await build_itinerary_from_wikivoyage(prefs)
    except DestinationIsCountryError as exc:
        await update.message.reply_text(str(exc))
        await update.message.reply_text("Me manda a CIDADE (ex: Cairo).")
        return DESTINO
    except Exception as exc:
        await update.message.reply_text(f"Deu ruim ao gerar o roteiro: {exc}")
        return ConversationHandler.END

    out = format_trip_output(trip)
    max_chars = 3500
    chunks = [out[i : i + max_chars] for i in range(0, len(out), max_chars)]
    for chunk in chunks:
        await update.message.reply_text(chunk)

    title = f"Roteiro de Viagem - {trip.get('nome', '')}".strip()
    subtitle = f"{trip['destino']} | {trip['ida']} -> {trip['volta']}"
    pdf_lines = trip_to_pdf_lines(trip)
    pdf_bytes = build_pdf_bytes(title=title, subtitle=subtitle, lines=pdf_lines)

    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename="roteiro.pdf"),
        caption=f"Aqui esta seu roteiro em PDF, {trip.get('nome', '')}!",
    )

    user_email = (p.get("email") or "").strip()
    if user_email:
        status = send_email_with_pdf(
            to_email=user_email,
            subject=f"Seu roteiro: {trip['destino']} ({trip['ida']} -> {trip['volta']})",
            body=(
                f"Ola {trip.get('nome', '')},\n\n"
                f"Segue em anexo seu roteiro para {trip['destino']}.\n"
                "Se quiser, eu adapto por bairro/onde voce vai ficar.\n"
            ),
            pdf_bytes=pdf_bytes,
        )
        await update.message.reply_text(status)
    else:
        await update.message.reply_text("Voce pulou o e-mail, entao so enviei o PDF aqui no Telegram.")

    await update.message.reply_text("Quer gerar outro? /start")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    del context
    await update.message.reply_text("Fechado. Se quiser recomecar: /start", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    del update
    logging.exception("ERR: %s", context.error)


def build_telegram_application() -> Application:
    request = HTTPXRequest(connect_timeout=30, read_timeout=30, write_timeout=30, pool_timeout=30)
    application = Application.builder().token(TOKEN).request(request).build()

    application.add_handler(CommandHandler("saldo", cmd_saldo))
    application.add_handler(CommandHandler("recarregar", cmd_recarregar))
    application.add_handler(CommandHandler("servicos", cmd_servicos))
    application.add_handler(CommandHandler("meuid", cmd_meuid))

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_nome)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_email)],
            DESTINO: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_destino)],
            DATA_IDA: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_data_ida)],
            DATA_VOLTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_data_volta)],
            RITMO: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_ritmo)],
            CONFIRMAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_and_generate)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(conv)
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_error_handler(error_handler)
    return application


@asynccontextmanager
async def lifespan(_: FastAPI):
    global tg_app

    tg_app = build_telegram_application()
    await tg_app.initialize()
    await tg_app.start()

    if WEBHOOK_BASE_URL:
        webhook_url = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"
        await tg_app.bot.set_webhook(
            url=webhook_url,
            secret_token=WEBHOOK_SECRET if WEBHOOK_SECRET else None,
            drop_pending_updates=True,
        )
        logging.info("Webhook configurado em %s", webhook_url)
    else:
        logging.warning("WEBHOOK_BASE_URL nao definido; set_webhook nao foi chamado.")

    print("Bot rodando Ok")
    try:
        yield
    finally:
        if tg_app is not None:
            try:
                await tg_app.bot.delete_webhook(drop_pending_updates=False)
            except Exception:
                logging.exception("Falha ao remover webhook")
            await tg_app.stop()
            await tg_app.shutdown()
            tg_app = None


web_app = FastAPI(title="Projeto IA Viagens Telegram Bot", lifespan=lifespan)


@web_app.get("/")
async def root():
    return {"ok": True, "mode": "webhook", "health": "/health", "webhook_path": WEBHOOK_PATH}


@web_app.get("/health")
async def health():
    return {"ok": True}


@web_app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    if tg_app is None:
        raise HTTPException(status_code=503, detail="telegram_app_not_ready")

    if WEBHOOK_SECRET:
        received_secret = request.headers.get("x-telegram-bot-api-secret-token", "")
        if received_secret != WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="invalid_secret_token")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid_json:{exc}") from exc

    update = Update.de_json(payload, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}


@web_app.exception_handler(Exception)
async def all_exception_handler(_: Request, exc: Exception):
    return JSONResponse({"error": str(exc)}, status_code=500)


def main():
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("bot:web_app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
