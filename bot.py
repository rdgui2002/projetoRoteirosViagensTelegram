from __future__ import annotations

import io
import logging
import os
import re

from dotenv import load_dotenv
from telegram import InputFile, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from services.trip_logic_wikivoyage import (
    TripPreferences,
    build_itinerary_from_wikivoyage,
    detect_country_destination,
    DestinationIsCountryError,
    RITMOS_VALIDOS,
)
from services.presenter import format_trip_output, trip_to_pdf_lines
from services.pdf_service import build_pdf_bytes
from services.email_service import send_email_with_pdf
from services.utils import parse_date_br

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("ERRO: defina TELEGRAM_BOT_TOKEN no .env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Estados
(NOME, EMAIL, DESTINO, DATA_IDA, DATA_VOLTA, RITMO, CONFIRMAR) = range(7)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    await update.message.reply_text(
        "Fala! 👋 Antes de começar, me diz seu nome?",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NOME


async def set_nome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nome = (update.message.text or "").strip()
    if len(nome) < 2:
        await update.message.reply_text("Pode me dizer seu nome (apelido também serve)?")
        return NOME

    context.user_data["prefs"]["nome"] = nome

    await update.message.reply_text(
        "Boa! Agora me manda seu e-mail pra eu te enviar o PDF no final.\n"
        "Ex: nome@gmail.com\n\n"
        "Se não quiser enviar por e-mail, digita: pular",
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
            await update.message.reply_text("Esse e-mail parece inválido. Tenta de novo ou digita 'pular'.")
            return EMAIL
        context.user_data["prefs"]["email"] = txt

    nome = context.user_data["prefs"]["nome"]
    await update.message.reply_text(
        f"Fechado, {nome}! ✈️\n\nAgora manda o destino (cidade).\n"
        "Ex: Barcelona, Rio de Janeiro, Cairo, Tokyo.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return DESTINO


async def set_destino(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    destino = (update.message.text or "").strip()
    if len(destino) < 2:
        await update.message.reply_text("Manda uma cidade válida. Ex: Cairo.")
        return DESTINO

    # Corrige NA HORA se for país/região
    msg_country = await detect_country_destination(destino)
    if msg_country:
        await update.message.reply_text(msg_country)
        await update.message.reply_text("Agora me diga a CIDADE (ex: Cairo, Luxor, Aswan). 👇")
        return DESTINO

    context.user_data["prefs"]["destino"] = destino
    await update.message.reply_text("Data de ida? (ex: 10/03/2026)")
    return DATA_IDA


async def set_data_ida(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        d = parse_date_br(update.message.text)
        context.user_data["prefs"]["data_ida"] = d.strftime("%d/%m/%Y")
    except Exception:
        await update.message.reply_text("Não entendi a data. Tenta assim: 10/03/2026")
        return DATA_IDA

    await update.message.reply_text("Data de volta? (ex: 15/03/2026)")
    return DATA_VOLTA


async def set_data_volta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ida = parse_date_br(context.user_data["prefs"]["data_ida"])
        volta = parse_date_br(update.message.text)

        if volta <= ida:
            await update.message.reply_text(
                "Essa volta tá antes (ou igual) à ida. A volta precisa ser depois.\n"
                "Tenta de novo (ex: 15/03/2026)."
            )
            return DATA_VOLTA

        context.user_data["prefs"]["data_volta"] = volta.strftime("%d/%m/%Y")
    except Exception:
        await update.message.reply_text("Não entendi a data. Tenta assim: 15/03/2026")
        return DATA_VOLTA

    kb = ReplyKeyboardMarkup([["leve", "médio"], ["intenso"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Qual ritmo? (leve / médio / intenso)", reply_markup=kb)
    return RITMO


async def set_ritmo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ritmo = (update.message.text or "").strip().lower()
    if ritmo == "medio":
        ritmo = "médio"

    if ritmo not in RITMOS_VALIDOS:
        await update.message.reply_text("Escolhe um: leve / médio / intenso")
        return RITMO

    context.user_data["prefs"]["ritmo"] = ritmo

    p = context.user_data["prefs"]
    resumo = (
        "🔎 Resumo\n"
        f"• Nome: {p['nome']}\n"
        f"• Email: {p['email'] if p['email'] else 'não informado'}\n"
        f"• Destino: {p['destino']}\n"
        f"• Datas: {p['data_ida']} → {p['data_volta']}\n"
        f"• Ritmo: {p['ritmo']}\n\n"
        "Confirmar e gerar roteiro? (sim/não)"
    )
    kb = ReplyKeyboardMarkup([["sim", "não"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(resumo, reply_markup=kb)
    return CONFIRMAR


async def confirm_and_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ans = (update.message.text or "").strip().lower()
    if ans not in ["sim", "s", "yes"]:
        await update.message.reply_text("Beleza! Se quiser recomeçar: /start", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    p = context.user_data["prefs"]
    prefs = TripPreferences(
        nome=p["nome"],
        destino=p["destino"],
        data_ida=p["data_ida"],
        data_volta=p["data_volta"],
        ritmo=p["ritmo"],
    )

    await update.message.reply_text("Boa! Tô montando seu roteiro… ✈️", reply_markup=ReplyKeyboardRemove())

    try:
        trip = await build_itinerary_from_wikivoyage(prefs)
    except DestinationIsCountryError as e:
        await update.message.reply_text(str(e))
        await update.message.reply_text("Me manda a CIDADE (ex: Cairo). 👇")
        return DESTINO
    except Exception as e:
        await update.message.reply_text(f"Deu ruim ao gerar o roteiro: {e}")
        return ConversationHandler.END

    # Texto no chat (SEM Markdown)
    out = format_trip_output(trip)
    MAX = 3500
    chunks = [out[i:i + MAX] for i in range(0, len(out), MAX)]
    for c in chunks:
        await update.message.reply_text(c)

    # PDF
    title = f"Roteiro de Viagem - {trip.get('nome', '')}".strip()
    subtitle = f"{trip['destino']} | {trip['ida']} → {trip['volta']}"
    pdf_lines = trip_to_pdf_lines(trip)
    pdf_bytes = build_pdf_bytes(title=title, subtitle=subtitle, lines=pdf_lines)

    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename="roteiro.pdf"),
        caption=f"📄 Aqui está seu roteiro em PDF, {trip.get('nome','')}!",
    )

    # Email informado pelo usuário
    user_email = (p.get("email") or "").strip()
    if user_email:
        status = send_email_with_pdf(
            to_email=user_email,
            subject=f"Seu roteiro: {trip['destino']} ({trip['ida']} → {trip['volta']})",
            body=(
                f"Olá {trip.get('nome','')},\n\n"
                f"Segue em anexo seu roteiro para {trip['destino']}.\n"
                "Se quiser, eu adapto por bairro/onde você vai ficar.\n"
            ),
            pdf_bytes=pdf_bytes,
        )
        await update.message.reply_text(status)
    else:
        await update.message.reply_text("📧 Você pulou o e-mail — então só enviei o PDF aqui no Telegram.")

    await update.message.reply_text("Quer gerar outro? /start")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Fechado. Se quiser recomeçar: /start", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("ERR: %s", context.error)


def main():
    request = HTTPXRequest(connect_timeout=30, read_timeout=30, write_timeout=30, pool_timeout=30)
    app = Application.builder().token(TOKEN).request(request).build()

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

    app.add_handler(conv)
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_error_handler(error_handler)

    print("✅ Bot rodando. Aperte Ctrl+C para parar.")
    app.run_polling(drop_pending_updates=True, close_loop=False)


if __name__ == "__main__":
    main()
