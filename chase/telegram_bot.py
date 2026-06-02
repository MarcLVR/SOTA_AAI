"""
DAVE Telegram bot — handles /start and inline button callbacks.

Run:  python3 chase/telegram_bot.py
Stop: Ctrl+C
"""

import logging
import os
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# Locate repo root (chase/ is one level below root) and put agents/ on the path
# so `from executor import execute_fix` resolves to agents/executor.py
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "agents"))

from notifier import load_cache, SEVERITY_ICON
from db import update_run_status, get_run
from executor import execute_fix

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("dave-bot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
NACHO_CHAT_ID = int(os.getenv("TELEGRAM_NACHO_CHAT_ID", "0") or "0")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    name = update.effective_user.first_name or "usuario"
    await update.message.reply_text(
        f"👋 Hola {name}, soy *DAVE* — Data Agentic Validation Engine.\n\n"
        f"Recibirás aquí las notificaciones de validación de documentos.\n\n"
        f"Tu Chat ID es: `{chat_id}`\n"
        f"Compártelo con Luca para que pueda añadirte al sistema.",
        parse_mode="Markdown",
    )
    log.info("/start — user=%s chat_id=%d", name, chat_id)


async def _run_executor(run_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, execute_fix, run_id)

    if result.get("success"):
        msg = f"✅ *Correcciones aplicadas* en `{result['document']}`\n\n"
        for r in result["results"]:
            msg += f"• {r['finding']} — resuelto en {r['attempts']} intento(s)\n"
    else:
        msg = f"⚠️ *Corrección parcial* en `{result['document']}`\n\n"
        for r in result["results"]:
            icon = "✅" if r["success"] else "❌"
            msg += f"{icon} {r['finding']}\n"

    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        action, run_id = query.data.split(":", 1)
    except ValueError:
        await query.edit_message_text("❌ Callback inválido.")
        return

    user = query.from_user.first_name or "desconocido"
    log.info("callback — action=%s run_id=%s user=%s", action, run_id, user)

    if action == "fix":
        update_run_status(int(run_id), "pending_fix")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"✅ *DAVE está aplicando las correcciones...*\n"
            f"Run #{run_id} · solicitado por {user}",
            parse_mode="Markdown",
        )
        result = await context.application.create_task(
            _run_executor(int(run_id), query.message.chat_id, context)
        )

    elif action == "manual":
        update_run_status(int(run_id), "manual")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"✏️ Marcado como *pendiente de corrección manual*.\n"
            f"DAVE te recordará en 48h si no se resuelve. (Run #{run_id})",
            parse_mode="Markdown",
        )

    elif action == "ignore":
        update_run_status(int(run_id), "ignored")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"🚫 Hallazgo ignorado. (Run #{run_id})",
            parse_mode="Markdown",
        )

    elif action == "info":
        cached = load_cache(int(run_id))
        if not cached:
            await query.message.reply_text("❌ No se encontraron detalles para este análisis.")
            return

        lines = [f"🔍 *Detalles del análisis*\n`{cached['document']}`\n"]
        for i, f in enumerate(cached["findings"], 1):
            icon = SEVERITY_ICON.get(f.get("severity", "medium"), "🟡")
            severity_label = {"high": "Alta", "medium": "Media", "low": "Baja"}.get(f.get("severity", "medium"), "Media")
            lines += [
                f"*Hallazgo {i}* {icon} Severidad: {severity_label}",
                f"• *Problema:* {f['title']}",
                f"• *Ubicación:* {f['location']}",
                f"• *Corrección sugerida:* {f['suggestion']}\n",
            ]

        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN no configurado")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))

    log.info("DAVE bot arrancando — @DAVEValidatorBot")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
