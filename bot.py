from __future__ import annotations

import io
import logging
import os
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx
from dotenv import load_dotenv
from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    KeyboardButton,
    MenuButtonCommands,
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
    AIServiceUnavailableError,
    RITMOS_VALIDOS,
    TripPreferences,
    build_itinerary_from_wikivoyage,
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


def _env_decimal(name: str, default: str) -> Decimal:
    raw = (os.getenv(name) or default).strip()
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        logging.warning("Valor invalido em %s=%r. Usando %s", name, raw, default)
        return Decimal(default)


def _env_float(name: str, default: str) -> float:
    raw = (os.getenv(name) or default).strip()
    try:
        return float(raw)
    except ValueError:
        logging.warning("Valor invalido em %s=%r. Usando %s", name, raw, default)
        return float(default)


def _env_user_ids(name: str) -> set[int]:
    raw = (os.getenv(name) or "").strip()
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


def _env_bool(name: str, default: str = "0") -> bool:
    return (os.getenv(name) or default).strip().lower() in {"1", "true", "yes", "on"}


def _parse_gostos_text(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    low = raw.lower()
    if low in {"nenhum", "nao", "sem", "indiferente", "tanto faz"}:
        return []

    parts = re.split(r"[,;/|\n]+", raw)
    out: list[str] = []
    seen = set()
    for part in parts:
        clean = re.sub(r"\s+", " ", part.strip().lower())
        if len(clean) < 2:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= 10:
            break
    return out


def _fold_text(text: str) -> str:
    raw = (text or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _split_long_text(text: str, max_chars: int = 3500) -> list[str]:
    content = (text or "").strip()
    if len(content) <= max_chars:
        return [content] if content else []

    chunks: list[str] = []
    remaining = content

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining.strip())
            break

        cut = remaining.rfind("\n\n", 0, max_chars + 1)
        if cut < int(max_chars * 0.40):
            cut = remaining.rfind("\n", 0, max_chars + 1)
        if cut < int(max_chars * 0.40):
            cut = remaining.rfind(". ", 0, max_chars + 1)
            if cut != -1:
                cut += 1
        if cut < int(max_chars * 0.40):
            cut = max_chars

        chunk = remaining[:cut].strip()
        if not chunk:
            chunk = remaining[:max_chars].strip()
            cut = len(chunk)

        chunks.append(chunk)
        remaining = remaining[cut:].lstrip()

    return [c for c in chunks if c]


COST_ROTEIRO_COMMAND = _env_decimal("COST_ROTEIRO_COMMAND", "1.00")
WALLET_HTTP_TIMEOUT = _env_float("WALLET_HTTP_TIMEOUT", "20")
BALANCE_ENFORCEMENT_ENABLED = _env_bool("BALANCE_ENFORCEMENT_ENABLED", "0")
TEST_BYPASS_ENABLED = _env_bool("TEST_BYPASS_ENABLED", "0")
TEST_BYPASS_USER_IDS = _env_user_ids("TEST_BYPASS_USER_IDS")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Conversation states
(HOME, NOME, EMAIL, DESTINO, DATA_IDA, DATA_VOLTA, RITMO, GOSTOS, CONFIRMAR) = range(9)

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
    "<i>Vamos comecar agora.</i>"
)

DATE_PAST_ERROR_TEXT = "Não é possivel gerar uma data menor que a atual"

MENU_TEXT = (
    "<b>Menu</b>\n"
    "- Iniciar: comeca um novo roteiro\n"
    "- /start: reiniciar o fluxo do roteiro\n"
    "- /cancel: cancelar o fluxo atual\n"
    "- /meuid: ver seu Telegram ID\n"
    "- /testemode: ver status do bypass de teste"
)

BOT_COMMANDS = [
    BotCommand("start", "Iniciar"),
    BotCommand("iniciar", "Iniciar"),
    BotCommand("menu", "Abrir menu"),
    BotCommand("cancel", "Cancelar"),
    BotCommand("meuid", "Ver meu ID"),
    BotCommand("testemode", "Status modo teste"),
]


def home_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton("Iniciar"), KeyboardButton("Menu")]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


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
    if not TEST_BYPASS_ENABLED:
        return False
    if not TEST_BYPASS_USER_IDS:
        return True
    return telegram_id in TEST_BYPASS_USER_IDS


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
    if not update.message or not update.effective_user:
        return False

    if amount <= 0:
        return True

    if not BALANCE_ENFORCEMENT_ENABLED:
        return True

    if is_bypass_user(update.effective_user.id):
        await update.message.reply_text("Modo teste ativo para seu usuario. Saldo ignorado neste teste.")
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


async def cmd_testemode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del context
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    bypass_for_user = is_bypass_user(user_id)
    bypass_scope = (
        "todos os usuarios"
        if TEST_BYPASS_ENABLED and not TEST_BYPASS_USER_IDS
        else "apenas IDs em TEST_BYPASS_USER_IDS"
    )
    lines = [
        "Status do modo teste:",
        f"- BALANCE_ENFORCEMENT_ENABLED: {'1' if BALANCE_ENFORCEMENT_ENABLED else '0'}",
        f"- TEST_BYPASS_ENABLED: {'1' if TEST_BYPASS_ENABLED else '0'} ({bypass_scope})",
        f"- Seu Telegram ID: {user_id}",
        f"- Seu bypass ativo: {'SIM' if bypass_for_user else 'NAO'}",
        f"- Custo configurado: {money_fmt(COST_ROTEIRO_COMMAND)}",
    ]

    if BALANCE_ENFORCEMENT_ENABLED and bypass_for_user:
        lines.append("")
        lines.append("Voce esta liberado para testar sem saldo.")
    elif BALANCE_ENFORCEMENT_ENABLED and not bypass_for_user:
        lines.append("")
        lines.append("Saldo sera exigido para gerar roteiro.")
    else:
        lines.append("")
        lines.append("Cobranca por saldo esta desativada globalmente.")

    await update.message.reply_text("\n".join(lines), reply_markup=miniapp_kb())


def init_user_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prefs"] = {
        "nome": "",
        "email": "",
        "destino": "",
        "data_ida": "",
        "data_volta": "",
        "ritmo": "",
        "gostos": [],
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    init_user_state(context)
    if not update.message:
        return NOME
    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    return await begin_flow(update)


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

    context.user_data["prefs"]["destino"] = destino
    await update.message.reply_text("Data de ida? (ex: 10/03/2026)")
    return DATA_IDA


async def set_data_ida(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        d = parse_date_br(update.message.text)
        if d < date.today():
            await update.message.reply_text(DATE_PAST_ERROR_TEXT)
            return DATA_IDA
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

        if volta < date.today():
            await update.message.reply_text(DATE_PAST_ERROR_TEXT)
            return DATA_VOLTA

        if ida > volta:
            await update.message.reply_text("A data de ida nao pode ser maior que a de volta.")
            return DATA_VOLTA

        if ida == volta:
            await update.message.reply_text("A data de volta precisa ser depois da ida.")
            return DATA_VOLTA

        context.user_data["prefs"]["data_volta"] = volta.strftime("%d/%m/%Y")
    except Exception:
        await update.message.reply_text("Nao entendi a data. Tenta assim: 15/03/2026")
        return DATA_VOLTA

    kb = ReplyKeyboardMarkup([["leve", "medio"], ["intenso"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Qual ritmo? (leve / medio / intenso)", reply_markup=kb)
    return RITMO


async def set_ritmo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ritmo_raw = (update.message.text or "").strip()
    ritmo_fold = _fold_text(ritmo_raw)
    ritmo = next((item for item in RITMOS_VALIDOS if _fold_text(item) == ritmo_fold), "")
    if not ritmo:
        await update.message.reply_text("Escolhe um: leve / medio / intenso")
        return RITMO

    context.user_data["prefs"]["ritmo"] = ritmo
    await update.message.reply_text(
        "Agora me conta seus gostos pessoais para eu priorizar no roteiro.\n"
        "Ex: praia, museus, gastronomia, vida noturna, trilhas, historia.\n"
        "Pode separar por virgula. Se nao tiver preferencia, responde: nenhum"
    )
    return GOSTOS


async def set_gostos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    gostos = _parse_gostos_text(update.message.text or "")
    context.user_data["prefs"]["gostos"] = gostos

    p = context.user_data["prefs"]
    gostos_text = ", ".join(p["gostos"]) if p["gostos"] else "sem preferencia"
    resumo = (
        "Resumo\n"
        f"- Nome: {p['nome']}\n"
        f"- Email: {p['email'] if p['email'] else 'nao informado'}\n"
        f"- Destino: {p['destino']}\n"
        f"- Datas: {p['data_ida']} -> {p['data_volta']}\n"
        f"- Ritmo: {p['ritmo']}\n"
        f"- Gostos: {gostos_text}\n"
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

    if not await charge_or_block(update, COST_ROTEIRO_COMMAND, "gerar roteiro IA"):
        return CONFIRMAR

    p = context.user_data["prefs"]
    prefs = TripPreferences(
        nome=p["nome"],
        destino=p["destino"],
        data_ida=p["data_ida"],
        data_volta=p["data_volta"],
        ritmo=p["ritmo"],
        gostos=p.get("gostos") or [],
    )

    await update.message.reply_text("Boa! To montando seu roteiro...", reply_markup=ReplyKeyboardRemove())

    try:
        trip = await build_itinerary_from_wikivoyage(prefs)
    except AIServiceUnavailableError:
        await update.message.reply_text("Serviço indisponivel no momento pro cliente")
        return ConversationHandler.END
    except Exception as exc:
        await update.message.reply_text(f"Deu ruim ao gerar o roteiro: {exc}")
        return ConversationHandler.END

    out = format_trip_output(trip)
    chunks = _split_long_text(out, max_chars=3500)
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
    application.add_handler(CommandHandler("testemode", cmd_testemode))

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
            GOSTOS: [MessageHandler(TEXT_FLOW_FILTER, set_gostos)],
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


