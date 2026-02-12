from __future__ import annotations

import io
import logging
import os
import re

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    InputFile,
    KeyboardButton,
    MenuButtonCommands,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Conversation states
(HOME, NOME, EMAIL, DESTINO, DATA_IDA, DATA_VOLTA, RITMO, CONFIRMAR) = range(8)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
START_TEXTS = {"iniciar", "comecar", "start"}
MENU_TEXTS = {"menu"}
START_SHORTCUT_FILTER = filters.Regex(r"(?i)^(iniciar|comecar|start)$")
MENU_SHORTCUT_FILTER = filters.Regex(r"(?i)^menu$")
TEXT_FLOW_FILTER = filters.TEXT & ~filters.COMMAND & ~START_SHORTCUT_FILTER & ~MENU_SHORTCUT_FILTER

WELCOME_TEXT = (
    "<b>Planejador IA de Viagens</b>\n"
    "Seu roteiro personalizado em poucos minutos.\n\n"
    "<b>O que este bot pode fazer?</b>\n"
    "- Montar um roteiro dia a dia\n"
    "- Ajustar sugestoes ao seu ritmo (leve, medio ou intenso)\n"
    "- Gerar PDF e enviar aqui no Telegram\n"
    "- Enviar por e-mail (opcional)\n\n"
    "<i>Toque em Iniciar para usar este bot.</i>"
)

MENU_TEXT = (
    "<b>Menu</b>\n"
    "- Iniciar: comeca um novo roteiro\n"
    "- /start: voltar para a tela inicial\n"
    "- /cancel: cancelar o fluxo atual\n"
    "- /meuid: ver seu Telegram ID"
)

BOT_COMMANDS = [
    BotCommand("start", "Iniciar"),
    BotCommand("iniciar", "Iniciar"),
    BotCommand("menu", "Abrir menu"),
    BotCommand("cancel", "Cancelar"),
    BotCommand("meuid", "Ver meu ID"),
]


def home_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton("Iniciar"), KeyboardButton("Menu")]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def show_menu_message(target_message) -> None:
    await target_message.reply_text(
        MENU_TEXT,
        parse_mode="HTML",
        reply_markup=home_kb(),
    )


async def begin_flow(update: Update) -> int:
    if not update.message:
        return HOME
    await update.message.reply_text(
        "Perfeito! Antes de comecar, me diz seu nome?",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NOME


async def start_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    del context
    if not update.message:
        return HOME

    text = (update.message.text or "").strip().lower()
    if text in MENU_TEXTS:
        await show_menu_message(update.message)
        return HOME

    if text not in START_TEXTS:
        await update.message.reply_text("Toque em Iniciar ou Menu para continuar.", reply_markup=home_kb())
        return HOME

    return await begin_flow(update)


async def shortcut_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    init_user_state(context)
    return await begin_flow(update)


async def shortcut_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    del context
    if not update.message:
        return HOME
    await show_menu_message(update.message)
    return HOME


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
        return HOME
    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=home_kb(),
    )
    return HOME


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.message:
        return
    await show_menu_message(update.message)


async def setup_bot_commands(application: Application) -> None:
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
        await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logging.info("Comandos e menu do bot configurados com sucesso.")
    except Exception:
        logging.exception("Falha ao configurar comandos/menu do bot.")


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
        "\n"
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
    application = Application.builder().token(TOKEN).request(request).post_init(setup_bot_commands).build()

    application.add_handler(CommandHandler("meuid", cmd_meuid))
    application.add_handler(CommandHandler("menu", cmd_menu))

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("iniciar", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND & START_SHORTCUT_FILTER, shortcut_start),
            MessageHandler(filters.TEXT & ~filters.COMMAND & MENU_SHORTCUT_FILTER, shortcut_menu),
        ],
        states={
            HOME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, start_from_text),
            ],
            NOME: [MessageHandler(TEXT_FLOW_FILTER, set_nome)],
            EMAIL: [MessageHandler(TEXT_FLOW_FILTER, set_email)],
            DESTINO: [MessageHandler(TEXT_FLOW_FILTER, set_destino)],
            DATA_IDA: [MessageHandler(TEXT_FLOW_FILTER, set_data_ida)],
            DATA_VOLTA: [MessageHandler(TEXT_FLOW_FILTER, set_data_volta)],
            RITMO: [MessageHandler(TEXT_FLOW_FILTER, set_ritmo)],
            CONFIRMAR: [MessageHandler(TEXT_FLOW_FILTER, confirm_and_generate)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("menu", cmd_menu),
            CommandHandler("start", start),
            CommandHandler("iniciar", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND & START_SHORTCUT_FILTER, shortcut_start),
            MessageHandler(filters.TEXT & ~filters.COMMAND & MENU_SHORTCUT_FILTER, shortcut_menu),
        ],
        allow_reentry=True,
    )

    application.add_handler(conv)
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_error_handler(error_handler)
    return application


def main():
    app = build_telegram_application()
    print("Bot rodando Ok")
    app.run_polling(drop_pending_updates=True, close_loop=False)


if __name__ == "__main__":
    main()
