from __future__ import annotations
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from urllib.parse import urlparse

from zoneinfo import ZoneInfo
from fastapi import FastAPI
import uvicorn

from tgdl.adapters.downloaders.aria2 import tell_status as aria2_tell

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
import logging
from telegram.constants import ParseMode

from telethon import TelegramClient
from telethon.sessions import StringSession

from tgdl.config.settings import settings
from tgdl.core.db import (
    db_init,
    db_set_flag,
    is_paused,
    db_add,
    db_get_due,
    db_get_all_queued,
    db_update_status,
    db_list,
    db_purge_finished,
    db_retry_errors,
    db_requeue_paused_reschedule_now,
    db_update_progress,
    db_clear_progress,
    db_clear_all,  # <- NUEVO
)

from tgdl.adapters.downloaders.aria2 import remove as aria2_remove

from tgdl.adapters.downloaders.aria2 import (
    aria2_enabled,
    add_uri as aria2_add,
    pause_all as aria2_pause_all,
    unpause_all as aria2_unpause_all,
)
from tgdl.adapters.downloaders import ytdlp

# ========= Estado/Flags globales =========
PAUSE_EVT: asyncio.Event = asyncio.Event()
RUN_TASK: asyncio.Task | None = None
RUNNING: dict[str, any] = {"ytdlp_proc": None}  # guardamos el subproceso activo de yt-dlp si lo hay

# ========= Constantes y utilidades =========
TZ = ZoneInfo(settings.TIMEZONE)
URL_RE = re.compile(r"(https?://\S+|magnet:\?xt=urn:btih:[A-Za-z0-9]+[^ \n]*)", re.IGNORECASE)
TG_LINK_RE = re.compile(r"https?://t\.me/(c/)?([^/]+)/(\d+)", re.IGNORECASE)

MAX_WORKERS = 2
WORK_SEM = asyncio.Semaphore(MAX_WORKERS)


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    return URL_RE.findall(text)


def parse_tg_link(url: str):
    m = TG_LINK_RE.search(url)
    if not m:
        return None, None
    is_c = bool(m.group(1))
    a = m.group(2)
    mid = int(m.group(3))
    if is_c:
        chat_id = int(f"-100{a}")
        return chat_id, mid
    else:
        return a, mid


# ========= Telethon helpers (descarga de media TG) =========


class PauseSignal(RuntimeError):
    """Señal interna para cortar descargas al pausar el sistema."""


def _progress_cb_factory(qid: int):
    def _cb(downloaded: int, total: int):
        if is_paused():
            raise PauseSignal("Paused by user")
        # Normalizamos progreso y persistimos
        t = total if total and total >= downloaded else (downloaded if downloaded else 0)
        db_update_progress(qid, (t if t > 0 else None), downloaded)

    return _cb


def _slugify(name: str) -> str:
    # minimal slug seguro para rutas
    out = "".join(c if c.isalnum() or c in " ._-+" else "_" for c in name.strip())
    return re.sub(r"\s+", " ", out).strip()


async def _infer_channel_title(entity, msg) -> str:
    title = getattr(entity, "title", None)
    if title:
        return title
    chat = getattr(msg, "chat", None)
    if chat and getattr(chat, "title", None):
        return chat.title
    fwd = getattr(msg, "forward", None) or getattr(msg, "fwd_from", None)
    if fwd:
        fn = getattr(fwd, "from_name", None)
        if fn:
            return fn
    return "Telegram"


async def telethon_download_core(
    client: TelegramClient, msg, entity, subdir: Path, suggested: str | None, qid: int
):
    channel_title = await _infer_channel_title(entity, msg)
    subdir = subdir / _slugify(channel_title)
    subdir.mkdir(parents=True, exist_ok=True)
    dest = subdir / (suggested or "")
    try:
        path = await client.download_media(
            msg,
            file=str(dest if suggested else subdir),
            progress_callback=_progress_cb_factory(qid),
        )
        return Path(path) if path else None
    except PauseSignal:
        raise
    except Exception as e:
        print(f"[DBG] Error descarga: {e!r}")
        return None


async def telethon_download_by_link(client: TelegramClient, url: str, dest_dir: Path, qid: int):
    who, mid = parse_tg_link(url)
    if who is None:
        print("[DBG] Enlace de Telegram no reconocido:", url)
        return None
    entity = await client.get_entity(who)
    msg = await client.get_messages(entity, ids=mid)
    if not msg or not getattr(msg, "media", None):
        return None
    suggested = None
    if getattr(msg, "document", None) and getattr(msg.document, "attributes", None):
        for a in msg.document.attributes:
            fn = getattr(a, "file_name", None)
            if fn:
                suggested = fn
                break
    if not suggested:
        suggested = getattr(getattr(msg, "video", None), "file_name", None) or getattr(
            getattr(msg, "audio", None), "file_name", None
        )
    return await telethon_download_core(client, msg, entity, dest_dir, suggested, qid)


async def telethon_download_by_ref(
    client: TelegramClient, chat_id: int, message_id: int, dest_dir: Path, qid: int
):
    entity = await client.get_entity(chat_id)
    msg = await client.get_messages(entity, ids=message_id)
    if not msg or not getattr(msg, "media", None):
        return None
    suggested = None
    if getattr(msg, "document", None) and getattr(msg.document, "attributes", None):
        for a in msg.document.attributes:
            fn = getattr(a, "file_name", None)
            if fn:
                suggested = fn
                break
    if not suggested:
        suggested = getattr(getattr(msg, "video", None), "file_name", None) or getattr(
            getattr(msg, "audio", None), "file_name", None
        )
    return await telethon_download_core(client, msg, entity, dest_dir, suggested, qid)


def pick_outdir(kind: str, payload: dict[str, Any], base: Path) -> Path:
    """
    Reglas:
    - YouTube -> base / "youtube"
    - Magnet  -> base / "torrents"
    - .torrent -> base / "torrents"
    - HTTP → base / host
    - Telegram -> base / Canal (esto ya lo hacer _infer_channel_title/_slugify)
    """
    base.mkdir(parents=True, exist_ok=True)
    if kind == "url":
        u = payload.get("url", "")
        low = u.lower()
        if any(d in low for d in ["youtube.com/watch", "youtu.be/"]):
            return base / "youtube"
        if low.startswith("magnet:") or low.endswith(".torrent"):
            return base / "torrents"
        try:
            host = urlparse(u).hostname or "http"
            host = host.replace("www.", "")
            return base / _slugify(host)
        except Exception:
            return base / "http"
    # para TG, devolvemos base; el subdir ya lo añade telethon_download_core (por canal)
    return base


# ========= UI Helpers (menús y mensajes bonitos) =========


def mk_main_menu(paused: bool) -> InlineKeyboardMarkup:
    # Botones principales
    row1 = [
        InlineKeyboardButton("🚀 Ejecutar ahora", callback_data="act:run"),
        InlineKeyboardButton("📋 Ver cola", callback_data="act:list"),
    ]
    row2 = [
        InlineKeyboardButton("📊 Estado", callback_data="act:status"),
        InlineKeyboardButton("⏰ Cambiar hora", callback_data="act:when"),
    ]
    # Botón de pausa o reanudar según estado
    row3 = [
        InlineKeyboardButton(
            ("▶️ Reanudar" if paused else "⏸️ Pausar"),
            callback_data=("act:resume" if paused else "act:pause"),
        )
    ]
    return InlineKeyboardMarkup([row1, row2, row3])


def mk_when_menu() -> InlineKeyboardMarkup:
    # Horas rápidas: 00, 03, 06, 12, 18, 21
    quick = [0, 3, 6, 12, 18, 21]
    rows = []
    row = []
    for h in quick:
        row.append(InlineKeyboardButton(f"{h:02d}:00", callback_data=f"act:when:{h}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Volver", callback_data="act:back")])
    return InlineKeyboardMarkup(rows)


def fmt_start_message_html() -> str:
    return (
        "👋 <b>TG Super Downloader</b>\n"
        "Descargas desde Telegram, YouTube y enlaces directos. Envíame:\n"
        "• Links de Telegram (<code>https://t.me/...</code>)\n"
        "• URLs http/https/magnet\n"
        "• Medios reenviados (video/audio/documento)\n\n"
        f"⏰ Programado diario a las <b>{settings.SCHEDULE_HOUR:02d}:00</b> (<code>{settings.TIMEZONE}</code>).\n"
        "Usa los botones para control rápido o /help."
    )


def fmt_status_message_html() -> str:
    p = is_paused()
    return (
        "📊 <b>Estado actual</b>\n"
        f"• Modo: {'<b>PAUSADO</b> ⏸️' if p else '<b>ACTIVO</b> ▶️'}\n"
        f"• Hora programada: <b>{settings.SCHEDULE_HOUR:02d}:00</b> (<code>{settings.TIMEZONE}</code>)\n"
    )


# ========= Estado global del bot para coordinar con HTTP control =========


@dataclass
class BotCtx:
    app: Any | None
    loop: asyncio.AbstractEventLoop | None
    tclient: TelegramClient | None


BOT = BotCtx(app=None, loop=None, tclient=None)

# ========= Ciclo en Progreso? =========
RUN_TASK: asyncio.Task | None = None


async def launch_cycle_background(app, force_all: bool = False, notify_chat_id: int | None = None):
    global RUN_TASK
    if RUN_TASK and not RUN_TASK.done():
        # Ya hay un ciclo corriendo; no lances otro
        return False
    # Programa el ciclo como tarea background y devuelve inmediatamente
    RUN_TASK = asyncio.create_task(
        run_cycle(app, force_all=force_all, notify_chat_id=notify_chat_id)
    )
    return True


# ========= Ciclo programado =========


async def run_cycle(app, force_all: bool = False, notify_chat_id: int | None = None):
    outdir = Path(settings.DOWNLOAD_DIR)
    outdir.mkdir(parents=True, exist_ok=True)
    outdir_base = Path(settings.DOWNLOAD_DIR)

    if is_paused():
        print(f"[DBG] Ciclo omitido: PAUSADO")
        return
    # limpiar/asegurar estado de pausa
    PAUSE_EVT.clear()

    now = datetime.now(tz=TZ)
    rows = db_get_all_queued() if force_all else db_get_due(now)
    print(f"[DBG] run_cycle start | force_all={force_all} | items={len(rows)}")

    tclient: TelegramClient = BOT.tclient
    tasks = []
    for qid, kind, payload_json in rows:
        # Chequeo cooperativo de pausa antes de arrancar cada item
        if is_paused() or PAUSE_EVT.is_set():
            db_update_status(qid, "paused")
            continue

        async def _worker(qid=qid, kind=kind, payload_json=payload_json):
            async with WORK_SEM:
                try:
                    payload = json.loads(payload_json)
                    if kind == "url":
                        url = payload["url"]
                        low = url.lower()
                        ok = False
                        outdir = pick_outdir("url", payload, outdir_base)
                        await asyncio.sleep(0)  # cede el control al loop
                        if "mega.nz/" in low:
                            print("[URL] MEGA no soportado en este proyecto.")
                            ok = False
                        elif low.endswith(".torrent"):
                            # Descarga el .torrent a temp y envíalo a aria2
                            import requests, tempfile

                            with tempfile.NamedTemporaryFile(delete=False, suffix=".torrent") as tf:
                                r = requests.get(url, timeout=30)
                                r.raise_for_status()
                                tf.write(r.content)
                                tpath = Path(tf.name)
                            from tgdl.adapters.downloaders.aria2 import (
                                add_torrent as aria2_add_torrent,
                            )

                            gid = aria2_add_torrent(tpath, outdir)
                            from tgdl.core.db import db_set_ext_id

                            db_set_ext_id(qid, gid)
                            try:
                                tpath.unlink(missing_ok=True)
                            except Exception:
                                pass
                            ok = True

                        elif any(d in low for d in ["youtube.com/watch", "youtu.be/"]):
                            # ==== yt-dlp cancelable ====
                            RUNNING["ytdlp_proc"] = None

                            def _on_start(p):
                                RUNNING["ytdlp_proc"] = p

                            ok = await ytdlp.download_proc(
                                url, outdir, on_start=_on_start, cancel_evt=PAUSE_EVT
                            )
                            if PAUSE_EVT.is_set():
                                db_update_status(qid, "paused")
                                # no limpiamos progress para mantener info
                        else:
                            if aria2_enabled():
                                try:
                                    gid = aria2_add(url, outdir)
                                    from tgdl.core.db import db_set_ext_id

                                    db_set_ext_id(qid, gid)
                                    ok = True
                                except Exception as e:
                                    print(f"[DBG] aria2 error: {e!r}")
                                    ok = False
                            else:
                                print("[DBG] aria2 no disponible y URL no es yt-dlp")
                                ok = False

                        db_update_status(
                            qid, "done" if ok else ("paused" if PAUSE_EVT.is_set() else "error")
                        )
                        if ok or (not PAUSE_EVT.is_set()):
                            db_clear_progress(qid)

                        if notify_chat_id:
                            try:
                                await app.bot.send_message(
                                    chat_id=notify_chat_id,
                                    text=("✅ url lista" if ok else "❌ url falló") + f": {url}",
                                )
                            except Exception as e:
                                print(f"[DBG] notify error: {e!r}")

                    elif kind == "tg_link":
                        url = payload["url"]
                        outdir = pick_outdir(kind, payload, outdir_base)
                        try:
                            res = await telethon_download_by_link(tclient, url, outdir, qid)
                            if res and res.suffix.lower() == ".torrent":
                                from tgdl.adapters.downloaders.aria2 import (
                                    add_torrent as aria2_add_torrent,
                                )

                                gid = aria2_add_torrent(res, outdir)
                                from tgdl.core.db import db_set_ext_id

                                db_set_ext_id(qid, gid)
                                # Borra el .torrent si ya no lo quieres
                                try:
                                    res.unlink(missing_ok=True)
                                except Exception:
                                    pass
                                db_update_status(qid, "done")
                                db_clear_progress(qid)
                                # (notifica si quieres)

                        except PauseSignal:
                            db_update_status(qid, "paused")

                        if res and res.exists():
                            db_update_status(qid, "done")
                            db_clear_progress(qid)
                            if notify_chat_id:
                                try:
                                    await app.bot.send_message(
                                        chat_id=notify_chat_id, text=f"✅ link listo: {res.name}"
                                    )
                                except Exception as e:
                                    print(f"[DBG] notify error: {e!r}")
                        else:
                            db_update_status(qid, "error")

                    elif kind == "tg_ref":
                        outdir = pick_outdir(kind, payload, outdir_base)
                        chat_id = int(payload["chat_id"])
                        mid = int(payload["message_id"])
                        try:
                            res = await telethon_download_by_ref(tclient, chat_id, mid, outdir, qid)
                            if res and res.suffix.lower() == ".torrent":
                                from tgdl.adapters.downloaders.aria2 import (
                                    add_torrent as aria2_add_torrent,
                                )

                                gid = aria2_add_torrent(res, outdir)
                                from tgdl.core.db import db_set_ext_id

                                db_set_ext_id(qid, gid)
                                # Borra el .torrent si ya no lo quieres
                                try:
                                    res.unlink(missing_ok=True)
                                except Exception:
                                    pass
                                db_update_status(qid, "done")
                                db_clear_progress(qid)
                                # (notifica si quieres)

                        except PauseSignal:
                            db_update_status(qid, "paused")

                        if res and res.exists():
                            db_update_status(qid, "done")
                            db_clear_progress(qid)
                            if notify_chat_id:
                                try:
                                    await app.bot.send_message(
                                        chat_id=notify_chat_id, text=f"✅ ref listo: {res.name}"
                                    )
                                except Exception as e:
                                    print(f"[DBG] notify error: {e!r}")
                        else:
                            db_update_status(qid, "error")

                    elif kind == "self_ref":
                        outdir = pick_outdir(kind, payload, outdir_base)
                        chat_id = int(payload["chat_id"])
                        mid = int(payload["message_id"])
                        try:
                            # mismo mecanismo que tg_ref pero desde el propio chat del usuario
                            res = await telethon_download_by_ref(tclient, chat_id, mid, outdir, qid)
                            if res and res.suffix.lower() == ".torrent":
                                from tgdl.adapters.downloaders.aria2 import (
                                    add_torrent as aria2_add_torrent,
                                )

                                gid = aria2_add_torrent(res, outdir)
                                from tgdl.core.db import db_set_ext_id

                                db_set_ext_id(qid, gid)
                                # Borra el .torrent si ya no lo quieres
                                try:
                                    res.unlink(missing_ok=True)
                                except Exception:
                                    pass
                                db_update_status(qid, "done")
                                db_clear_progress(qid)
                                # (notifica si quieres)

                        except PauseSignal:
                            db_update_status(qid, "paused")

                        if res and res.exists():
                            db_update_status(qid, "done")
                            db_clear_progress(qid)
                            if notify_chat_id:
                                try:
                                    await app.bot.send_message(
                                        chat_id=notify_chat_id, text=f"✅ archivo listo: {res.name}"
                                    )
                                except Exception as e:
                                    print(f"[DBG] notify error: {e!r}")
                        else:
                            db_update_status(qid, "error")

                    else:
                        print(f"[DBG] kind desconocido: {kind}")
                        db_update_status(qid, "error")

                except Exception as e:
                    print(f"[DBG] excepcion en ciclo id={qid}: {e!r}")
                    db_update_status(qid, "error")

                    # Fi# Ceder control para no bloquear el loop
                    await asyncio.sleep(0)

        tasks.append(asyncio.create_task(_worker()))

    for t in tasks:
        try:
            await t
        except Exception as e:
            print(f"[DBG] worker fail: {e!r}")

    print("[DBG] run_cycle end")
    RUNNING["ytdlp_proc"] = None
    PAUSE_EVT.clear()


# ========= Handlers del bot =========


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    paused = is_paused()
    await update.message.reply_text(
        fmt_start_message_html(),
        parse_mode=ParseMode.HTML,
        reply_markup=mk_main_menu(paused),
        disable_web_page_preview=True,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "🆘 <b>Ayuda rápida</b>\n\n"
        "<b>Qué puedo enviar:</b>\n"
        "• Link de mensaje de Telegram (<code>https://t.me/c/.../123</code>)\n"
        "• URLs http/https/magnet (aria2)\n"
        "• YouTube/compatibles (yt-dlp)\n"
        "• Medios reenviados\n\n"
        "<b>Comandos útiles:</b>\n"
        "<code>/menu</code> — mostrar botones\n"
        "<code>/when HH</code> — cambia la hora (24h)\n"
        "<code>/now</code> — ejecutar ciclo ya\n"
        "<code>/pause</code> — pausar todo\n"
        "<code>/resume</code> — reanudar\n"
        "<code>/status</code> — ver estado\n"
        "<code>/list</code> — ver cola\n"
        "<code>/retry</code> — reintentar errores\n"
        "<code>/purge</code> — limpiar done/error\n"
        "<code>/cancel ID</code> — cancelar ítem\n"
        "<code>/clear</code> — limpiar TODO\n"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    paused = is_paused()
    await update.message.reply_text(
        "Menú principal:",
        reply_markup=mk_main_menu(paused),
        disable_web_page_preview=True,
    )


async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = (query.data or "").strip()
    await query.answer()

    def refresh_menu_html():
        return fmt_start_message_html(), mk_main_menu(is_paused())

    try:
        if data == "act:run":
            started = await launch_cycle_background(
                context.application, force_all=True, notify_chat_id=update.effective_chat.id
            )
            if started:
                await query.edit_message_text(
                    "🚀 Ciclo lanzado en segundo plano. Te avisaré al finalizar."
                )
            else:
                await query.edit_message_text("⚠️ Ya hay un ciclo ejecutándose.")
        elif data == "act:pause":
            await cmd_pause(update, context)
            txt, kb = refresh_menu_html()
            await query.edit_message_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif data == "act:resume":
            await cmd_resume(update, context)
            txt, kb = refresh_menu_html()
            await query.edit_message_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif data == "act:status":
            await query.edit_message_text(
                fmt_status_message_html(),
                parse_mode=ParseMode.HTML,
                reply_markup=mk_main_menu(is_paused()),
            )
        elif data == "act:list":
            rows = db_list(limit=15)
            if not rows:
                await query.edit_message_text(
                    "📋 Cola: (vacía)", reply_markup=mk_main_menu(is_paused())
                )
            else:
                lines = ["📋 <b>Cola reciente</b>\n"]
                for qid, kind, payload, status, sched in rows:
                    title = ""
                    try:
                        payload_d = json.loads(payload)
                        title = payload_d.get("suggested_name") or payload_d.get("url") or ""
                    except Exception:
                        pass
                    title = title or (payload[:60] + "…")
                    lines.append(f"• #{qid} [{kind}] {status} — {sched}\n  <code>{title}</code>")
                await query.edit_message_text(
                    "\n".join(lines),
                    parse_mode=ParseMode.HTML,
                    reply_markup=mk_main_menu(is_paused()),
                    disable_web_page_preview=True,
                )
        elif data == "act:when":
            await query.edit_message_text(
                f"⏰ Hora actual: <b>{settings.SCHEDULE_HOUR:02d}:00</b>\nElige una hora rápida:",
                parse_mode=ParseMode.HTML,
                reply_markup=mk_when_menu(),
            )
        elif data.startswith("act:when:"):
            try:
                hh = int(data.split(":")[2])
                assert 0 <= hh < 24
                settings.SCHEDULE_HOUR = hh  # persistencia la haremos en la fase funcional
                await query.edit_message_text(
                    f"✅ Nueva hora programada: <b>{settings.SCHEDULE_HOUR:02d}:00</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=mk_main_menu(is_paused()),
                )
            except Exception:
                await query.edit_message_text("❌ Valor inválido", reply_markup=mk_when_menu())
        elif data == "act:back":
            txt, kb = refresh_menu_html()
            await query.edit_message_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await query.edit_message_text(
                "🤔 Acción no reconocida.", reply_markup=mk_main_menu(is_paused())
            )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Error: <code>{e!r}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=mk_main_menu(is_paused()),
        )


async def cmd_when(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(f"Hora actual: {settings.SCHEDULE_HOUR:02d}:00")
        return
    try:
        hh = int(context.args[0])
        assert 0 <= hh < 24
        # Nota: persistencia de SCHEDULE_HOUR podría ir a kv si quieres hacerlo duradero
        settings.SCHEDULE_HOUR = hh  # type: ignore[attr-defined]
        await update.message.reply_text(f"✅ Nueva hora: {settings.SCHEDULE_HOUR:02d}:00")
    except Exception:
        await update.message.reply_text("Formato: /when 2  (para 02:00)")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1) flag en DB
    db_set_flag("PAUSED", "1")
    # 2) pausar aria2
    try:
        aria2_pause_all()
    except Exception as e:
        print(f"[DBG] aria2_pause_all: {e!r}")
    # 3) disparar evento de pausa (yt-dlp) + terminar subproceso si está vivo
    PAUSE_EVT.set()
    proc = RUNNING.get("ytdlp_proc")
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except Exception:
            pass
    await update.message.reply_text(
        "⏸️ Pausado. La tarea activa será detenida y el resto quedado en 'paused'."
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /cancel 123  (id de la cola)")
        return
    try:
        qid = int(context.args[0])
    except Exception:
        await update.message.reply_text("ID inválido. Ejemplo: /cancel 123")
        return

    # leer ext_id y kind
    try:
        from tgdl.core.db import _connect

        with _connect() as conn:
            cur = conn.execute("SELECT ext_id, kind FROM queue WHERE id=?", (qid,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text(f"No existe el id #{qid}")
                return
            ext_id, kind = row[0], row[1]
    except Exception as e:
        await update.message.reply_text(f"Error DB: {e!r}")
        return

    # Si es yt-dlp activo, termínalo
    if kind == "url" and RUNNING.get("ytdlp_proc") and RUNNING["ytdlp_proc"].returncode is None:
        try:
            RUNNING["ytdlp_proc"].terminate()
        except Exception:
            pass

    # Si hay GID de aria2, remove
    if ext_id:
        try:
            aria2_remove(ext_id)
            try:
                st = aria2_tell(ext_id)
                for f in st.get("files") or []:
                    for p in (f.get("path"),):
                        if not p:
                            continue
                        try:
                            Path(p).unlink(missing_ok=True)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[DBG] cleanup aria2 files: {e!r}")
        except Exception as e:
            await update.message.reply_text(f"aria2 remove falló: {e!r}")

    db_update_status(qid, "canceled")
    await update.message.reply_text(f"❌ Cancelado #{qid}")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_set_flag("PAUSED", "0")
    db_requeue_paused_reschedule_now()
    # reanudar aria2
    try:
        aria2_unpause_all()
    except Exception as e:
        print(f"[DBG] aria2_unpause_all: {e!r}")
    PAUSE_EVT.clear()
    await update.message.reply_text("▶️ Reanudado. Lanzando ciclo en segundo plano…")
    await launch_cycle_background(
        context.application, force_all=True, notify_chat_id=update.effective_chat.id
    )


# Limpiar por completo la cola
async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Señal de pausa + detener procesos
    db_set_flag("PAUSED", "1")
    PAUSE_EVT.set()
    try:
        aria2_pause_all()
    except Exception:
        pass
    proc = RUNNING.get("ytdlp_proc")
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except Exception:
            pass
    # Limpiar DB
    db_clear_all()
    await update.message.reply_text("🧹 Cola y progreso limpiados completamente. (Estado: PAUSADO)")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = is_paused()
    await update.message.reply_text(f"Estado: {'PAUSADO' if p else 'ACTIVO'}")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_list(limit=20)
    if not rows:
        await update.message.reply_text("📋 Cola: (vacía)")
        return
    lines = ["📋 <b>Cola reciente</b>\n"]
    for qid, kind, payload, status, sched in rows:
        try:
            payload_d = json.loads(payload)
        except Exception:
            payload_d = {}
        title = payload_d.get("suggested_name") or payload_d.get("url") or f"{payload[:60]}…"
        lines.append(f"• #{qid} [{kind}] {status} — {sched}\n  <code>{title}</code>")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_retry_errors()
    await update.message.reply_text("🔁 Reintentando elementos en error (puestos en queued).")


async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_purge_finished()
    await update.message.reply_text("🧹 Cola limpiada (done/error).")


async def cmd_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    started = await launch_cycle_background(
        context.application, force_all=True, notify_chat_id=update.effective_chat.id
    )
    if started:
        await update.message.reply_text("🚀 Ciclo lanzado en segundo plano. Te aviso al finalizar.")
    else:
        await update.message.reply_text("⚠️ Ya hay un ciclo ejecutándose.")


# ========= Error handler global =========
async def on_error(update: object, context):
    logging.exception("Unhandled exception in bot", exc_info=context.error)
    try:
        # Trata de notificar al usuario (si hay chat/message)
        if hasattr(context, "bot") and update:
            # diferentes tipos de update pueden no tener message
            target = None
            try:
                if getattr(update, "message", None):
                    target = update.message
                elif getattr(update, "callback_query", None) and update.callback_query.message:
                    target = update.callback_query.message
            except Exception:
                target = None

            if target:
                await target.reply_text(
                    "⚠️ Ocurrió un error de formato. He ajustado el parseo para evitarlo.\n"
                    "Vuelve a intentar la acción, por favor."
                )
    except Exception:
        pass  # no bloquees por errores en el handler


async def intake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.message
    now = datetime.now(tz=TZ)
    scheduled_at = now.replace(hour=settings.SCHEDULE_HOUR, minute=0, second=0, microsecond=0)
    if scheduled_at <= now:
        scheduled_at += timedelta(days=1)

    text = (m.text or m.caption or "") if m else ""

    # 1) Enlaces de mensajes de Telegram
    tg_urls = re.findall(r"https?://t\.me/[^\s]+", text, flags=re.IGNORECASE)
    for u in tg_urls:
        db_add("tg_link", {"url": u}, scheduled_at)

    # 2) URLs/magnets (excluye t.me)
    urls = [u for u in extract_urls(text) if not u.lower().startswith("https://t.me/")]
    for u in urls:
        db_add("url", {"url": u}, scheduled_at)

    # 3) Media reenviada al bot -> self_ref
    suggested = None
    if m and m.document:
        suggested = m.document.file_name
    elif m and m.video:
        suggested = getattr(m.video, "file_name", None)
    elif m and m.audio:
        suggested = m.audio.file_name
    elif m and m.photo:
        suggested = "photo.jpg"

    if m and (m.document or m.video or m.audio or m.photo):
        db_add(
            "self_ref",
            {"chat_id": m.chat_id, "message_id": m.message_id, "suggested_name": suggested},
            scheduled_at,
        )

    # 4) Origen reenviado (si el canal permite revelar origen) -> tg_ref
    try:
        fo = getattr(m, "forward_origin", None)
        if fo and getattr(fo, "type", "") == "channel":
            chat_id = fo.chat.id
            mid = fo.message_id
            db_add("tg_ref", {"chat_id": chat_id, "message_id": mid}, scheduled_at)
    except Exception as e:
        print(f"[DBG] forward_origin error: {e!r}")

    await m.reply_text(
        f"🗂️ Encolado para {scheduled_at.strftime('%Y-%m-%d %H:%M')} ({settings.TIMEZONE})."
    )


# ========= HTTP control (FastAPI en 127.0.0.1:8765) =========


def start_control_server():
    api = FastAPI(title="tg_downloader_control")

    @api.post("/cancel/{qid}")
    def http_cancel(qid: int):
        # Cancelación cooperativa:
        # 1) si es aria2 y tiene GID -> remove
        try:
            from tgdl.core.db import _connect

            with _connect() as conn:
                cur = conn.execute("SELECT ext_id, kind FROM queue WHERE id=?", (qid,))
                row = cur.fetchone()
                if not row:
                    return {"ok": False, "error": "not-found"}
                ext_id, kind = row[0], row[1]
                # detener yt-dlp si es el activo (RUNNING)
                if (
                    kind == "url"
                    and RUNNING.get("ytdlp_proc")
                    and RUNNING["ytdlp_proc"].returncode is None
                ):
                    try:
                        RUNNING["ytdlp_proc"].terminate()
                    except Exception:
                        pass
                # aria2: remove si hay ext_id
                if ext_id:
                    try:
                        aria2_remove(ext_id)
                        try:
                            st = aria2_tell(ext_id)
                            for f in st.get("files") or []:
                                for p in (f.get("path"),):
                                    if not p:
                                        continue
                                    try:
                                        Path(p).unlink(missing_ok=True)
                                    except Exception:
                                        pass
                        except Exception as e:
                            print(f"[DBG] cleanup aria2 files: {e!r}")
                    except Exception as e:
                        print(f"[DBG] aria2 remove failed: {e!r}")
                db_update_status(qid, "canceled")
                return {"ok": True, "id": qid, "canceled": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @api.post("/pause")
    def http_pause():
        db_set_flag("PAUSED", "1")
        return {"ok": True, "paused": True}

    @api.post("/resume")
    def http_resume():
        db_set_flag("PAUSED", "0")
        db_requeue_paused_reschedule_now()
        app = BOT.app
        loop = BOT.loop
        if app is not None and loop is not None:
            asyncio.run_coroutine_threadsafe(launch_cycle_background(app, force_all=True), loop)
        return {"ok": True, "paused": False, "running": True}

    @api.post("/run")
    def http_run():
        app = BOT.app
        loop = BOT.loop
        if app is None or loop is None:
            return {"ok": False, "error": "app-not-ready"}
        asyncio.run_coroutine_threadsafe(launch_cycle_background(app, force_all=True), loop)
        return {"ok": True, "running": True}

    # Levantar uvicorn en un hilo aparte
    def _run():
        uvicorn.run(api, host="127.0.0.1", port=8765, log_level="warning")

    import threading

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    print("[i] Endpoints locales en http://127.0.0.1:8765  (/pause, /resume, /run)")


# ========= Main (corutina) =========


async def main():
    # DB y carpeta
    db_init()
    Path(settings.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

    # Telethon (usuario)
    if not (settings.API_ID and settings.API_HASH and settings.TELETHON_STRING):
        raise SystemExit("Falta API_ID/API_HASH/TELETHON_STRING en .env")
    tclient = TelegramClient(
        StringSession(settings.TELETHON_STRING), settings.API_ID, settings.API_HASH
    )
    await tclient.connect()
    if not await tclient.is_user_authorized():
        raise SystemExit(
            "La sesión de Telethon no está autorizada. Ejecuta session_setup.py de nuevo."
        )

    # Bot de Telegram
    if not settings.BOT_TOKEN:
        raise SystemExit("Falta BOT_TOKEN en .env")
    app = ApplicationBuilder().token(settings.BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("when", cmd_when))
    app.add_handler(CommandHandler("now", cmd_now))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("retry", cmd_retry))
    app.add_handler(CommandHandler("purge", cmd_purge))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), intake))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CallbackQueryHandler(cb_router))
    app.add_error_handler(on_error)

    # Programa diario (hora configurable)
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(run_cycle, CronTrigger(hour=settings.SCHEDULE_HOUR, minute=0), args=[app])
    # Pausa automática opcional (mismo comportamiento que tu versión previa)
    scheduler.add_job(lambda: db_set_flag("PAUSED", "1"), CronTrigger(hour=6, minute=30))
    scheduler.start()

    # Guardar contexto global para el HTTP control
    BOT.app = app
    BOT.loop = asyncio.get_running_loop()
    BOT.tclient = tclient

    start_control_server()

    print(
        f"[i] Bot listo. Descarga diaria a las {settings.SCHEDULE_HOUR:02d}:00 ({settings.TIMEZONE})."
    )

    # Inicio explícito del bot (modo polling)
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        await asyncio.Future()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await tclient.disconnect()
