from __future__ import annotations

import html
import logging
import re
from typing import Final

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

log = logging.getLogger(__name__)

# URL/magnet muy permisivo pero práctico
URL_RE: Final = re.compile(r"(?i)\b((?:magnet:\?xt=urn:[a-z0-9:]+)|(?:https?://[^\s]+))")


def _is_bot_or_service(update: Update) -> bool:
    if update.effective_message is None:
        return True
    if update.effective_user and update.effective_user.is_bot:
        return True
    # Ignorar mensajes de servicio (joined, left, pinned, etc.)
    msg = update.effective_message
    return any(
        [
            msg.new_chat_members,
            msg.left_chat_member,
            msg.pinned_message,
            msg.group_chat_created,
            msg.supergroup_chat_created,
            msg.channel_chat_created,
            msg.migrate_to_chat_id,
            msg.migrate_from_chat_id,
        ]
    )


async def _send_typing(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    try:
        await ctx.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        log.debug("No se pudo enviar typing()", exc_info=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_bot_or_service(update):
        return
    await _send_typing(context, update.effective_chat.id)
    text = (
        "👋 **TG Super Downloader** listo.\n\n"
        "Envíame un **enlace** (http/https), un **magnet** o **reenvía** un mensaje con archivos. "
        "Comandos útiles:\n"
        "• `/help` — guía rápida\n"
        "• `/status` — estado de cola\n"
        "• `/list` — tareas\n"
        "• `/pause` / `/resume` — control global\n"
        "• `/cancel <id>` — cancelar tarea específica\n"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _is_bot_or_service(update):
        return
    await _send_typing(context, update.effective_chat.id)
    text = (
        "📖 **Ayuda rápida**\n\n"
        "• Pega un **enlace** o **magnet** y lo encolo.\n"
        "• Reenvía mensajes con **medios** y los capturo.\n"
        "• También acepto listas (pega varios enlaces)."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def on_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fallback para TODO: texto, reenvíos y medios.
    No toca tu pipeline real todavía: solo responde algo útil.
    """
    if _is_bot_or_service(update):
        return

    msg = update.effective_message
    chat_id = update.effective_chat.id
    await _send_typing(context, chat_id)

    # 1) Texto: detectar URL/magnet
    if msg.text or msg.caption:
        blob = (msg.text or msg.caption or "").strip()
        found = URL_RE.findall(blob)
        if found:
            # Aquí, en fase 2, llamaremos a tu encolador real
            pretty = "\n".join(f"• {html.escape(u)}" for u in found[:10])
            reply = (
                "✅ He detectado estos enlaces/magnets y los **encolaré**:\n"
                f"{pretty}\n\n"
                "_(Esta es una confirmación; si alguno falla, te aviso.)_"
            )
            await msg.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
            return

    # 2) Medios: documento/foto/video/audio/etc.
    if any([msg.document, msg.photo, msg.video, msg.audio, msg.voice, msg.video_note, msg.sticker]):
        kind = (
            "archivo"
            if msg.document
            else (
                "foto"
                if msg.photo
                else (
                    "video"
                    if msg.video
                    else (
                        "audio"
                        if msg.audio
                        else (
                            "nota de voz"
                            if msg.voice
                            else "video corto" if msg.video_note else "sticker"
                        )
                    )
                )
            )
        )
        reply = f"📥 He recibido tu **{kind}**.\nLo pasaré por el **módulo de descargas**."
        await msg.reply_text(reply)
        return

    # 3) Si no encaja en nada, contesta algo amable
    await msg.reply_text(
        "👀 Te leo. Envíame un enlace/magnet o reenvía el mensaje con el archivo. "
        "Si necesitas ayuda, usa /help."
    )


def register_basic_handlers(app: Application) -> None:
    # /start y /help siempre deben existir
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # Fallback: cualquier mensaje que no capturen otros handlers “especiales”
    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.StatusUpdate.ALL & ~filters.User(username=["bot"]),  # defensivo
            on_any_message,
        ),
        group=50,  # muy al final de la cadena
    )
