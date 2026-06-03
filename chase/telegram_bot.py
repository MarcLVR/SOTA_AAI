"""
DAVE Telegram bot — handles /start and inline button callbacks.

Run:  python3 chase/telegram_bot.py
Stop: Ctrl+C
"""

import logging
import os
import sys
import threading
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

    def _esc(s: str) -> str:
        return s.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")

    if result.get("success"):
        msg = f"✅ *Correcciones aplicadas* en `{_esc(result['document'])}`\n\n"
        for r in result["results"]:
            msg += f"• {_esc(r['finding'])} — resuelto en {r['attempts']} intento(s)\n"
    else:
        msg = f"⚠️ *Corrección parcial* en `{_esc(result['document'])}`\n\n"
        for r in result["results"]:
            icon = "✅" if r["success"] else "❌"
            msg += f"{icon} {_esc(r['finding'])}\n"

    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled exception: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        err_text = f"{type(context.error).__name__}: {context.error}"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Error interno de DAVE\n{err_text[:200]}",
        )


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

def start_bot_background() -> None:
    """Start the polling bot in a daemon thread. Silent no-op if token is absent.

    Uses the low-level async API instead of run_polling() because run_polling()
    calls signal.set_wakeup_fd() which only works in the main thread.
    On repeated 409 Conflict (previous instance still alive), gives up after
    5 retries and logs a clear warning instead of retrying forever.
    """
    if not BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
        return

    def _run():
        import asyncio
        from telegram.error import Conflict as TgConflict

        async def _async_main():
            # Steal the connection from any lingering poller by making a
            # short getUpdates call — Telegram kicks the old long-poll out.
            from telegram import Bot as _Bot
            try:
                async with _Bot(token=BOT_TOKEN) as _bot:
                    await _bot.get_updates(offset=-1, timeout=1)
                    log.info("Telegram: previous poller evicted, starting fresh")
            except Exception:
                pass
            await asyncio.sleep(2)

            app = Application.builder().token(BOT_TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CallbackQueryHandler(handle_callback))
            app.add_error_handler(error_handler)

            async with app:
                await app.start()
                await app.updater.start_polling(drop_pending_updates=True)
                log.info("DAVE bot arrancando — @DAVEValidatorBot")
                await asyncio.Event().wait()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_async_main())
        except Exception as exc:
            log.error("Telegram bot crashed: %s", exc)
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True, name="telegram-bot")
    t.start()
    log.info("Telegram bot started in background thread")


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
