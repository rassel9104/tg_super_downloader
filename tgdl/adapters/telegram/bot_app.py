from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telethon import TelegramClient
from telethon.sessions import StringSession

from tgdl.adapters.downloaders import ytdlp
from tgdl.adapters.downloaders.aria2 import add_uri as aria2_add
from tgdl.adapters.downloaders.aria2 import aria2_enabled
from tgdl.adapters.downloaders.aria2 import (
    pause_all as aria2_pause_all,
)
from tgdl.adapters.downloaders.aria2 import remove as aria2_remove
from tgdl.adapters.downloaders.aria2 import tell_status as aria2_tell
from tgdl.adapters.downloaders.aria2 import (
    unpause_all as aria2_unpause_all,
)
from tgdl.config.settings import settings
from tgdl.core.db import (
    _connect,
    db_add,
    db_clear_all,  # <- NUEVO
    db_clear_progress,
    db_get_all_queued,
    db_get_due,
    db_get_flag,
    db_get_progress_rows,  # <— NUEVO (para notificaciones)
    db_init,
    db_list,
    db_purge_finished,
    db_requeue_paused_reschedule_now,
    db_retry_errors,
    db_set_ext_id,
    db_set_flag,
    db_update_progress,
    db_update_status,
    is_paused,
)
from tgdl.utils.resolvers import resolve_mediafire_direct, resolve_sourceforge_direct

# Logger del módulo (evita F821 y nos da trazas controladas)
logger = logging.getLogger(__name__)

try:
    from tgdl.config.settings import settings  # si ya existe en tu proyecto
except Exception:
    settings = None


def _get_playlist_limit(default: int = 24) -> int:
    try:
        if settings is not None:
            v = getattr(settings, "YTDLP_MAX_PLAYLIST_ITEMS", None)
            if v is not None and str(v).strip():
                return int(v)
    except Exception:
        pass
    try:
        return int(os.getenv("YTDLP_MAX_PLAYLIST_ITEMS", str(default)))
    except Exception:
        return default


# Cache de elecciones de playlist (token -> url original)
PLAYLIST_CHOICES: dict[str, str] = {}

# ========= Estado/Flags globales =========
PAUSE_EVT: asyncio.Event = asyncio.Event()
RUN_TASK: asyncio.Task | None = None
RUNNING: dict[str, any] = {"ytdlp_proc": None}  # guardamos el subproceso activo de yt-dlp si lo hay

# ========= Constantes y utilidades =========
TZ = ZoneInfo(settings.TIMEZONE)
URL_RE = re.compile(r"(https?://\S+|magnet:\?xt=urn:btih:[A-Za-z0-9]+[^ \n]*)", re.IGNORECASE)
TG_LINK_RE = re.compile(r"https?://t\.me/(c/)?([^/]+)/(\d+)", re.IGNORECASE)
LINK_RE = re.compile(r"(?i)\b((?:magnet:\?xt=urn:[a-z0-9:]+)|(?:https?://[^\s]+))")

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


def _fmt_size(n: int) -> str:
    try:
        n = int(n)
    except Exception:
        return "0 B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    i = 0
    x = float(n)
    while x >= 1024.0 and i < len(units) - 1:
        x /= 1024.0
        i += 1
    return f"{x:.1f} {units[i]}"


async def _track_aria2_progress(
    gid: str,
    chat_id: int,
    bot,
    *,
    every_sec: int | None = None,
    min_pct_step: int = 10,
) -> None:
    """
    Hace polling a aria2.tellStatus(gid) y envía mensajes al chat con progreso y resultado.
    - Envía update cada `min_pct_step`% o cada 60s si no hubo cambio suficiente.
    - Finaliza al llegar a status 'complete' o 'error' o si el GID desaparece.
    No lanza excepciones; registra y sale silenciosamente ante errores persistentes.
    """
    from tgdl.core.logging import logger

    interval = max(5, int(every_sec or int(os.getenv("A2_PROGRESS_EVERY", "20") or "20")))
    last_pct = -1
    last_ts = 0.0
    started = time.time()

    def _pick_file(st: dict) -> tuple[str, int, int]:
        files = st.get("files") or []
        name = ""
        if files:
            # aria2 suele reportar path completo
            p = (files[0].get("path") or "").strip()
            name = Path(p).name or p
        total = int(st.get("totalLength") or 0)
        done = int(st.get("completedLength") or 0)
        return name, total, done

    progress_msg = None
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                st = aria2_tell(gid) or {}
            except Exception as e:
                logger.warning("progress aria2_tell failed gid=%s err=%r", gid, e)
                # sigue intentando; si el GID fue removido, aria2_tell puede fallar o devolver {}
                st = {}

            status = (st.get("status") or "").lower()
            name, total, done = _pick_file(st)
            pct = 0
            if total > 0:
                pct = min(100, (done * 100) // total)

            # envío periódico (10% por defecto) o keepalive cada 60s
            now = time.time()
            if (pct >= last_pct + min_pct_step) or (now - last_ts >= 60 and status == "active"):
                # Mensaje único editable para anti-spam
                txt = (
                    f"⬇️ Descargando: *{(name or gid)}*\n"
                    f"{pct}%  ({_fmt_size(done)} / {_fmt_size(total)})"
                )
                with contextlib.suppress(Exception):
                    if progress_msg is None:
                        progress_msg = await bot.send_message(
                            chat_id=chat_id, text=txt, parse_mode="Markdown"
                        )
                    else:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=progress_msg.message_id,
                            text=txt,
                            parse_mode="Markdown",
                        )
                last_pct = pct
                last_ts = now

            if status in {"complete", "error", "removed"}:
                # Mensaje final
                ico = "✅" if status == "complete" else ("⛔" if status == "removed" else "❌")
                detail = st.get("errorMessage") or st.get("errorCode") or ""
                final = (
                    f"{ico} *{name or gid}*\n"
                    f"{'Completado' if status == 'complete' else 'Cancelado' if status == 'removed' else 'Error'}"
                    f"{f' — {detail}' if detail else ''}\n"
                    f"Tiempo: {int(now - started)}s  Tamaño: {_fmt_size(total)}"
                )
                with contextlib.suppress(Exception):
                    if progress_msg is None:
                        await bot.send_message(chat_id=chat_id, text=final, parse_mode="Markdown")
                    else:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=progress_msg.message_id,
                            text=final,
                            parse_mode="Markdown",
                        )
                break
    except asyncio.CancelledError:
        # Silencioso si cancelamos el seguimiento (p. ej., /cancel)
        pass
    except Exception as e:
        logger.error("progress tracker crashed gid=%s err=%r", gid, e)


async def _await_aria2_and_notify(qid: int, gid: str, notify_chat_id: int | None, bot) -> str:
    """
    Espera cooperativamente a que aria2 complete/erroree/sea removido.
    Actualiza progreso en DB y usa _track_aria2_progress para UX.
    Retorna el status final ('complete'|'error'|'removed'|'unknown').
    """
    status = "unknown"
    # Lanzar tracker de mensaje editable (sin bloquear) si hay chat
    tracker = None
    try:
        if notify_chat_id:
            tracker = asyncio.create_task(
                _track_aria2_progress(gid, notify_chat_id, bot, every_sec=10, min_pct_step=10)
            )
        while True:
            await asyncio.sleep(2)
            st = {}
            try:
                st = aria2_tell(gid) or {}
            except Exception:
                st = {}
            status = (st.get("status") or "").lower()
            total = int(st.get("totalLength") or 0)
            done = int(st.get("completedLength") or 0)
            # Persistimos progreso para el panel
            try:
                t = total if total and total >= done else (done or 0)
                db_update_progress(qid, (t if t > 0 else None), done)
            except Exception:
                pass
            if status in {"complete", "error", "removed"}:
                break
            # Cortesía: si no hay status, asumimos removido/cancelado
            if not status:
                status = "removed"
                break
    finally:
        if tracker:
            with contextlib.suppress(Exception):
                tracker.cancel()
    return status


def _slugify(name: str) -> str:
    # minimal slug seguro para rutas
    out = "".join(c if c.isalnum() or c in " ._-+" else "_" for c in name.strip())
    return re.sub(r"\s+", " ", out).strip()


async def safe_edit(
    query,
    text=None,
    reply_markup=None,
    parse_mode: str | None = ParseMode.HTML,
    disable_web_page_preview: bool | None = True,
):
    """Edita un mensaje y silencia el error 'Message is not modified'."""
    try:
        await query.edit_message_text(
            text=text if text is not None else (query.message.text or ""),
            reply_markup=reply_markup if reply_markup is not None else query.message.reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            try:
                await query.answer("Sin cambios")
            except Exception:
                pass
        else:
            raise


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
        if any(
            d in low
            for d in [
                "youtube.com/watch",
                "youtu.be/",
                "youtube.com/playlist",
                "youtube.com/shorts",
                "youtube.com/channel/",
                "youtube.com/@",
                "youtube.com/c/",
            ]
        ):
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
    row1 = [
        InlineKeyboardButton("▶️ Comenzar ahora", callback_data="act:run"),
        InlineKeyboardButton("📋 Ver cola", callback_data="act:list"),
    ]
    row2 = [
        InlineKeyboardButton("📊 Estado", callback_data="act:status"),
        InlineKeyboardButton("⏰ Cambiar hora", callback_data="act:when"),
    ]
    row3 = [
        InlineKeyboardButton(
            ("▶️ Reanudar" if paused else "⏸️ Pausar"),
            callback_data=("act:resume" if paused else "act:pause"),
        ),
        InlineKeyboardButton("🗓️ Schedule", callback_data="act:sched:open"),
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
        "ℹ️ Comparte un enlace; si es una lista de YouTube te preguntaré si quieres sólo el video o la lista completa.\n\n"
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


# ========= Handlers auxiliares (playlists, etc) =========
def _is_youtube(u: str) -> bool:
    low = u.lower()
    return (
        "youtu.be/" in low
        or "youtube.com/watch" in low
        or "youtube.com/playlist" in low
        or "youtube.com/shorts" in low
        or "youtube.com/channel/" in low
        or "youtube.com/@" in low
        or "youtube.com/c/" in low
    )


def _has_playlistish(u: str) -> tuple[bool, str]:
    """
    Devuelve (True, 'radio'|'list') si la URL parece playlist (list=...) o radio (start_radio=1).
    Para /playlist?... forzamos 'list'.
    """
    try:
        parsed = urlparse(u)
        q = parse_qs(parsed.query)
        if parsed.path.startswith("/playlist") and "list" in q:
            return True, "list"
        if "start_radio" in q and (q["start_radio"][0] in ("1", "true", "yes")):
            return True, "radio"
        if "list" in q:
            return True, "list"
        return False, ""
    except Exception:
        return False, ""


def _mk_playlist_choice_kb(token: str) -> InlineKeyboardMarkup:
    # Backward-compat: mantenemos 'pl:one'/'pl:all' como "Encolar"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("▶️ Sólo este — Ahora", callback_data=f"pl:one-now:{token}"),
                InlineKeyboardButton("📚 Lista — Ahora", callback_data=f"pl:all-now:{token}"),
            ],
            [
                InlineKeyboardButton("🕒 Sólo este — Encolar", callback_data=f"pl:one-q:{token}"),
                InlineKeyboardButton("🕒 Lista — Encolar", callback_data=f"pl:all-q:{token}"),
            ],
            [InlineKeyboardButton("❌ Cancelar", callback_data=f"pl:cancel:{token}")],
        ]
    )


# ========= Estado global del bot para coordinar con HTTP control =========


@dataclass
class BotCtx:
    app: Any | None
    loop: asyncio.AbstractEventLoop | None
    tclient: TelegramClient | None


BOT = BotCtx(app=None, loop=None, tclient=None)

# ========= Scheduler / Flags persistentes =========
SCHEDULER = None  # se setea en main()


def get_flag_int(key: str, default: int) -> int:
    try:
        v = int(db_get_flag(key, str(default)))
        return v
    except Exception:
        return default


def load_sched_config():
    """Lee configuración persistente de horario/ventana."""
    start = get_flag_int("SCHEDULE_HOUR", settings.SCHEDULE_HOUR)
    enabled = db_get_flag("SCHED_ENABLED", "1") == "1"
    s_start = get_flag_int("SCHED_START", start)
    s_stop = get_flag_int("SCHED_STOP", (start + 3) % 24)  # por defecto ventana de 3h
    return {
        "start": start,
        "enabled": enabled,
        "win_start": s_start,
        "win_stop": s_stop,
    }


def reconfigure_scheduler(app):
    """Reconstruye el scheduler según flags persistentes."""
    global SCHEDULER
    if SCHEDULER is None:
        # Aún no ha sido inicializado; evita AttributeError si se llama antes de tiempo
        return

    cfg = load_sched_config()
    from apscheduler.triggers.cron import CronTrigger

    SCHEDULER.remove_all_jobs()

    if cfg["enabled"]:
        SCHEDULER.add_job(run_cycle, CronTrigger(hour=cfg["win_start"], minute=0), args=[app])

        def _auto_pause():
            db_set_flag("PAUSED", "1")

        SCHEDULER.add_job(_auto_pause, CronTrigger(hour=cfg["win_stop"], minute=0))
    # Si no hay ventana (24/7), no programamos nada periódico aquí.


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


async def _progress_notifier(app, chat_id, stop_evt: asyncio.Event):
    last_sent: dict[int, float] = {}
    while not stop_evt.is_set():
        rows = db_get_progress_rows(50)
        now = asyncio.get_event_loop().time()
        lines = []
        count = 0
        for r in rows:
            qid = r["qid"]
            total = r.get("total") or 0
            done = r.get("downloaded") or 0
            if total <= 0 or done <= 0:
                continue
            if (now - last_sent.get(qid, 0)) < 12:
                continue
            last_sent[qid] = now
            pct = done / total * 100.0
            lines.append(
                f"#{qid} {pct:.1f}%  {done / 1024 / 1024:.1f}MB / {total / 1024 / 1024:.1f}MB"
            )
            count += 1
            if count >= 5:
                break
        if lines:
            try:
                await app.bot.send_message(
                    chat_id=chat_id, text="⏳ Progreso:\n" + "\n".join(lines)
                )
            except Exception as e:
                print(f"[DBG] notify progress error: {e!r}")
        await asyncio.sleep(3)


async def run_cycle(app, force_all: bool = False, notify_chat_id: int | None = None):
    outdir = Path(settings.DOWNLOAD_DIR)
    outdir.mkdir(parents=True, exist_ok=True)
    outdir_base = Path(settings.DOWNLOAD_DIR)

    if is_paused():
        print("[DBG] Ciclo omitido: PAUSADO")
        return
    # limpiar/asegurar estado de pausa
    PAUSE_EVT.clear()

    now = datetime.now(tz=TZ)
    rows = db_get_all_queued() if force_all else db_get_due(now)

    notify_stop_evt = asyncio.Event()
    notifier_task = None
    if notify_chat_id:
        notifier_task = asyncio.create_task(
            _progress_notifier(app, notify_chat_id, notify_stop_evt)
        )

    print(f"[DBG] run_cycle start | force_all={force_all} | items={len(rows)}")

    tclient: TelegramClient = BOT.tclient
    tasks = []
    for qid, kind, payload_json in rows:
        # Diagnóstico por ítem (suave, no ruidoso)
        try:
            _p = json.loads(payload_json)
            if kind == "url":
                _u = (_p.get("url") or "")[:140]
                print(f"[DBG] item#{qid} kind=url | aria2_enabled={aria2_enabled()} | url={_u!r}")
        except Exception:
            pass
        # Chequeo cooperativo de pausa antes de arrancar cada item
        if is_paused() or PAUSE_EVT.is_set():
            db_update_status(qid, "paused")
            continue

        async def _worker(qid=qid, kind=kind, payload_json=payload_json):
            async with WORK_SEM:
                try:
                    payload = json.loads(payload_json)
                    # normaliza el chat de notificación para TODO el item
                    row_notify_chat_id = payload.get("notify_chat_id") or notify_chat_id

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
                            import tempfile

                            import httpx

                            from tgdl.utils.retry import retry

                            @retry("http", tries=4, base_delay=0.6)
                            async def _pull(u: str) -> bytes:
                                async with httpx.AsyncClient(timeout=30.0) as cli:
                                    r = await cli.get(u)
                                    r.raise_for_status()
                                    return r.content

                            blob = await _pull(url)
                            with tempfile.NamedTemporaryFile(delete=False, suffix=".torrent") as tf:
                                tf.write(blob)
                                tf.flush()
                                tpath = Path(tf.name)

                            from tgdl.adapters.downloaders.aria2 import (
                                add_torrent as aria2_add_torrent,
                            )

                            gid = aria2_add_torrent(tpath, outdir)
                            db_set_ext_id(qid, gid)
                            # Esperar finalización aria2 antes de marcar done
                            final = await _await_aria2_and_notify(
                                qid, gid, row_notify_chat_id, app.bot
                            )
                            ok = final == "complete"
                            try:
                                tpath.unlink(missing_ok=True)
                            except Exception:
                                pass

                        elif "mediafire.com/file/" in low:
                            try:
                                direct, hdrs = await resolve_mediafire_direct(url)
                                if direct and aria2_enabled():
                                    gid = aria2_add(direct, outdir, headers=hdrs)
                                    db_set_ext_id(qid, gid)
                                    final = await _await_aria2_and_notify(
                                        qid, gid, row_notify_chat_id, app.bot
                                    )
                                    ok = final == "complete"
                                else:
                                    ok = False
                            except Exception as e:
                                print(f"[DBG] mediafire error: {e!r}")
                                ok = False

                        elif "sourceforge.net/" in low:
                            try:
                                direct, hdrs = await resolve_sourceforge_direct(url)
                                if aria2_enabled():
                                    # Fallback si el resolver no entregó URL directa
                                    target = direct or (
                                        url
                                        if low.endswith("/download")
                                        else (url.rstrip("/") + "/download")
                                    )
                                    if not hdrs:
                                        hdrs = {
                                            "Referer": url,
                                            "User-Agent": (
                                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                                            ),
                                            "Accept": "*/*",
                                        }
                                    gid = aria2_add(target, outdir, headers=hdrs)
                                    db_set_ext_id(qid, gid)
                                    # Espera cooperativa hasta completar (como MediaFire)
                                    final = await _await_aria2_and_notify(
                                        qid, gid, row_notify_chat_id, app.bot
                                    )
                                    ok = final == "complete"
                                else:
                                    print(
                                        "[DBG] SourceForge: aria2_enabled()=False. Revisa RPC URL/secret/estado de aria2."
                                    )
                                    ok = False
                            except Exception as e:
                                print(f"[DBG] sourceforge error: {e!r}")
                                ok = False

                        elif any(
                            d in low
                            for d in [
                                "youtube.com/watch",
                                "youtu.be/",
                                "youtube.com/playlist",
                                "youtube.com/shorts",
                                "youtube.com/channel/",
                                "youtube.com/@",
                                "youtube.com/c/",
                            ]
                        ):
                            # ==== yt-dlp cancelable ====
                            RUNNING["ytdlp_proc"] = None

                            def _on_start(p):
                                RUNNING["ytdlp_proc"] = p

                            # 1) Decisión desde payload, si existe
                            allow_playlist = payload.get("allow_playlist", None)

                            # 2) Blindaje playlists/radios sin elección explícita
                            def _has_playlistish_q(u: str) -> bool:
                                try:
                                    q = parse_qs(urlparse(u).query)
                                    if "start_radio" in q and (
                                        q["start_radio"][0] in ("1", "true", "yes")
                                    ):
                                        return True
                                    return "list" in q
                                except Exception:
                                    return False

                            if allow_playlist is None and _has_playlistish_q(url):
                                allow_playlist = False  # fuerza vídeo único

                            print(
                                f"[YTDLP][guard] url_has_playlistish={_has_playlistish_q(url)} | allow_playlist={allow_playlist}"
                            )

                            progress_msg = None
                            last_pct_sent = -1
                            last_edit_ts = 0.0

                            if PAUSE_EVT.is_set():
                                db_update_status(qid, "paused")

                            async def _send_or_edit(txt: str):
                                nonlocal progress_msg
                                try:
                                    if not row_notify_chat_id:
                                        return
                                    if progress_msg is None:
                                        progress_msg = await app.bot.send_message(
                                            chat_id=row_notify_chat_id, text=txt
                                        )
                                    else:
                                        progress_msg = await app.bot.edit_message_text(
                                            chat_id=row_notify_chat_id,
                                            message_id=progress_msg.message_id,
                                            text=txt,
                                        )
                                except Exception:
                                    pass

                            async def _send_playlist_info(ev: dict):
                                title = ev.get("title") or "Playlist"
                                sample = ev.get("sample") or []
                                limit = _get_playlist_limit()  # <-- lee .env aquí
                                lines = [
                                    f"📚 <b>{title}</b> — mostrando hasta {limit} ítems (cap configurado)."
                                ]
                                if sample:
                                    lines.append("Primeros ítems:")
                                    for s in sample:
                                        lines.append(
                                            f"  #{s.get('index', '?')}: {s.get('title', '(sin título)')}"
                                        )
                                await _send_or_edit("\n".join(lines))

                            def _tg_progress_cb(ev: dict):
                                if ev.get("event") == "playlist_info":
                                    asyncio.create_task(_send_playlist_info(ev))
                                    return
                                if ev.get("event") == "batch":
                                    done = ev.get("done", 0)
                                    asyncio.create_task(
                                        _send_or_edit(f"✅ {done} archivo(s) completados…")
                                    )
                                    return
                                nonlocal last_pct_sent, last_edit_ts
                                pct = int(ev.get("percent", 0))
                                nowt = asyncio.get_event_loop().time()
                                if (pct == last_pct_sent) or ((nowt - last_edit_ts) < 3.0):
                                    return
                                last_pct_sent = pct
                                last_edit_ts = nowt
                                txt = f"⬇️ Descargando… {pct}%"
                                if ev.get("speed"):
                                    txt += f" — {ev['speed']}"
                                if ev.get("eta"):
                                    txt += f" — ETA {ev['eta']}"
                                asyncio.create_task(_send_or_edit(txt))

                            # Mensaje inicial
                            if row_notify_chat_id:
                                await _send_or_edit("⬇️ Preparando descarga…")

                            try:
                                ok = await ytdlp.download_proc(
                                    url,
                                    outdir,
                                    on_start=_on_start,
                                    cancel_evt=PAUSE_EVT,
                                    allow_playlist=bool(payload.get("allow_playlist", False)),
                                    progress_cb=_tg_progress_cb,
                                    max_items=int(payload.get("max_items") or 0)
                                    or _get_playlist_limit(),
                                )
                            except TypeError as _e:
                                logging.warning(
                                    "download_proc() no acepta 'progress_cb'; reintentando sin callback: %s",
                                    _e,
                                )
                                ok = await ytdlp.download_proc(
                                    url,
                                    outdir,
                                    on_start=_on_start,
                                    cancel_evt=PAUSE_EVT,
                                    allow_playlist=bool(payload.get("allow_playlist", False)),
                                )

                            # Mensaje final
                            if row_notify_chat_id:
                                if ok:
                                    await _send_or_edit("✅ Descarga completada.")
                                else:
                                    await _send_or_edit("❌ Error en la descarga.")

                        else:
                            if aria2_enabled():
                                try:
                                    gid = aria2_add(url, outdir)
                                    db_set_ext_id(qid, gid)
                                    final = await _await_aria2_and_notify(
                                        qid, gid, row_notify_chat_id, app.bot
                                    )
                                    ok = final == "complete"
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
                        # Mensaje final ya lo gestiona el tracker editable; no duplicar

                    elif kind == "tg_link":
                        url = payload["url"]
                        outdir = pick_outdir(kind, payload, outdir_base)
                        res = None
                        try:
                            res = await telethon_download_by_link(tclient, url, outdir, qid)
                            if res and res.suffix.lower() == ".torrent":
                                from tgdl.adapters.downloaders.aria2 import (
                                    add_torrent as aria2_add_torrent,
                                )

                                gid = aria2_add_torrent(res, outdir)
                                db_set_ext_id(qid, gid)
                                try:
                                    res.unlink(missing_ok=True)
                                except Exception:
                                    pass
                                db_update_status(qid, "done")
                                db_clear_progress(qid)
                        except PauseSignal:
                            db_update_status(qid, "paused")

                        if res and res.exists():
                            db_update_status(qid, "done")
                            db_clear_progress(qid)
                            if row_notify_chat_id:
                                try:
                                    await app.bot.send_message(
                                        chat_id=row_notify_chat_id,
                                        text=f"✅ link listo: {res.name}",
                                    )
                                except Exception as e:
                                    print(f"[DBG] notify error: {e!r}")
                        else:
                            db_update_status(qid, "error")

                    elif kind == "tg_ref":
                        outdir = pick_outdir(kind, payload, outdir_base)
                        chat_id = int(payload["chat_id"])
                        mid = int(payload["message_id"])
                        res = None
                        try:
                            res = await telethon_download_by_ref(tclient, chat_id, mid, outdir, qid)
                            if res and res.suffix.lower() == ".torrent":
                                from tgdl.adapters.downloaders.aria2 import (
                                    add_torrent as aria2_add_torrent,
                                )

                                gid = aria2_add_torrent(res, outdir)
                                db_set_ext_id(qid, gid)
                                try:
                                    res.unlink(missing_ok=True)
                                except Exception:
                                    pass
                                db_update_status(qid, "done")
                                db_clear_progress(qid)
                        except PauseSignal:
                            db_update_status(qid, "paused")

                        if res and res.exists():
                            db_update_status(qid, "done")
                            db_clear_progress(qid)
                            if row_notify_chat_id:
                                try:
                                    await app.bot.send_message(
                                        chat_id=row_notify_chat_id, text=f"✅ ref listo: {res.name}"
                                    )
                                except Exception as e:
                                    print(f"[DBG] notify error: {e!r}")
                        else:
                            db_update_status(qid, "error")

                    elif kind == "self_ref":
                        outdir = pick_outdir(kind, payload, outdir_base)
                        chat_id = int(payload["chat_id"])
                        mid = int(payload["message_id"])
                        res = None
                        try:
                            # mismo mecanismo que tg_ref pero desde el propio chat del usuario
                            res = await telethon_download_by_ref(tclient, chat_id, mid, outdir, qid)
                            if res and res.suffix.lower() == ".torrent":
                                from tgdl.adapters.downloaders.aria2 import (
                                    add_torrent as aria2_add_torrent,
                                )

                                gid = aria2_add_torrent(res, outdir)
                                db_set_ext_id(qid, gid)
                                try:
                                    res.unlink(missing_ok=True)
                                except Exception:
                                    pass
                                db_update_status(qid, "done")
                                db_clear_progress(qid)
                        except PauseSignal:
                            db_update_status(qid, "paused")

                        if res and res.exists():
                            db_update_status(qid, "done")
                            db_clear_progress(qid)
                            if row_notify_chat_id:
                                try:
                                    await app.bot.send_message(
                                        chat_id=row_notify_chat_id,
                                        text=f"✅ archivo listo: {res.name}",
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
                    await asyncio.sleep(0)  # ceder control

        tasks.append(asyncio.create_task(_worker()))

    for t in tasks:
        try:
            await t
        except Exception as e:
            print(f"[DBG] worker fail: {e!r}")

    if notifier_task:
        notify_stop_evt.set()
        try:
            await notifier_task
        except Exception:
            pass

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
        "• Link de Telegram (<code>https://t.me/c/.../123</code>)\n"
        "• URLs http/https/magnet (aria2)\n"
        "• YouTube (yt-dlp)\n"
        "• Medios reenviados\n\n"
        "<b>Comandos útiles:</b>\n"
        "<code>/menu</code> — mostrar botones\n"
        "<code>/schedule</code> — 24/7 o ventana horaria (Start/Stop)\n"
        "<code>/when HH</code> — cambia la hora base (persistente)\n"
        "<code>/now</code> — ejecutar ciclo ya\n"
        "<code>/pause</code>, <code>/resume</code>\n"
        "<code>/status</code>, <code>/list</code>, <code>/retry</code>, <code>/purge</code>, <code>/cancel ID</code>, <code>/clear</code>\n"
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
            await safe_edit(query, txt, kb)
        elif data == "act:resume":
            await cmd_resume(update, context)
            txt, kb = refresh_menu_html()
            await safe_edit(query, txt, kb)
        elif data == "act:status":
            await safe_edit(query, fmt_status_message_html(), mk_main_menu(is_paused()))
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

        elif data == "act:sched:always":
            db_set_flag("SCHED_ENABLED", "0")
            reconfigure_scheduler(context.application)
            txt, kb = mk_sched_menu()
            await safe_edit(query, txt, kb)

        elif data == "act:sched:open":
            txt, kb = mk_sched_menu()
            await safe_edit(query, txt, kb)

        elif data == "act:sched:window":
            db_set_flag("SCHED_ENABLED", "1")
            reconfigure_scheduler(context.application)
            txt, kb = mk_sched_menu()
            await safe_edit(query, txt, kb)

        elif data.startswith("act:sched:start:"):
            try:
                h = int(data.split(":")[-1])
                assert 0 <= h < 24
                db_set_flag("SCHED_START", str(h))
                # sincroniza SCHEDULE_HOUR también (opcional pero útil para coherencia)
                db_set_flag("SCHEDULE_HOUR", str(h))
                reconfigure_scheduler(context.application)
                txt, kb = mk_sched_menu()
                await safe_edit(query, txt, kb)
            except Exception:
                await query.edit_message_text(
                    "Valor inválido para Start.", reply_markup=mk_sched_menu()[1]
                )

        elif data.startswith("act:sched:stop:"):
            try:
                h = int(data.split(":")[-1])
                assert 0 <= h < 24
                db_set_flag("SCHED_STOP", str(h))
                reconfigure_scheduler(context.application)
                txt, kb = mk_sched_menu()
                await safe_edit(query, txt, kb)
            except Exception:
                await query.edit_message_text(
                    "Valor inválido para Stop.", reply_markup=mk_sched_menu()[1]
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
            await safe_edit(query, txt, kb)
        elif data.startswith("pl:"):
            # pl:one:<token> | pl:all:<token> | pl:cancel:<token>
            try:
                _, action, token = data.split(":", 2)
            except ValueError:
                await query.edit_message_text("Solicitud inválida.")
                return

            url = PLAYLIST_CHOICES.pop(token, None)
            if not url:
                await query.edit_message_text("Esta solicitud expiró. Envía el enlace de nuevo.")
                return

            if action == "cancel":
                await query.edit_message_text("Operación cancelada.")
                return

            allow_playlist = action in ("all", "all-q", "all-now")
            run_now = action.endswith("-now")

            # Programación
            if run_now:
                scheduled_at = datetime.now(tz=TZ) - timedelta(minutes=1)
            else:
                scheduled_at = datetime.now(tz=TZ).replace(
                    hour=settings.SCHEDULE_HOUR, minute=0, second=0, microsecond=0
                )
                if scheduled_at <= datetime.now(tz=TZ):
                    scheduled_at += timedelta(days=1)

            db_add(
                "url",
                {
                    "url": url,
                    "allow_playlist": allow_playlist,
                    "notify_chat_id": query.message.chat_id,
                },
                scheduled_at,
            )

            # Mensaje UX: guía siguiente paso

            is_always = db_get_flag("SCHED_ENABLED", "1") == "0"
            rows = db_list(limit=9999)  # rápido, para contar
            qcount = len(rows) if rows else 1

            if is_always:
                hint = (
                    "Encolado. Modo 24/7: iniciaré en breve. "
                    "Usa /list para ver la cola o /pause para pausar."
                )
            else:
                hint = (
                    f"Encolado. La cola iniciará automáticamente a las "
                    f"{settings.SCHEDULE_HOUR:02d}:00. Usa /list para ver la cola o /now para ejecutar ahora."
                )

            await query.edit_message_text(
                "📹 Se descargará "
                + ("📚 la lista completa." if allow_playlist else "▶️ sólo este video.")
                + f"\nTienes {qcount} elemento(s) en cola.\n"
                + hint
            )
            if run_now:
                # Lanzar ciclo inmediato
                try:
                    asyncio.create_task(
                        launch_cycle_background(
                            context.application,
                            force_all=True,
                            notify_chat_id=query.message.chat_id,
                        )
                    )
                except Exception:
                    pass
        elif data in ("pl:one", "pl:all"):
            # Backward-compat: tratar como '...-q'
            suffix = "-q"
            new = data.replace("pl:", "pl:") + suffix
            # Reinvocar manejador con el nuevo action
            query.data = new
            await cb_router(update, context)
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
        cur = get_flag_int("SCHEDULE_HOUR", settings.SCHEDULE_HOUR)
        await update.message.reply_text(f"Hora actual programada: {cur:02d}:00")
        return
    try:
        hh = int(context.args[0])
        assert 0 <= hh < 24
        db_set_flag("SCHEDULE_HOUR", str(hh))
        # si no tienes ventana custom, sincroniza win_start con SCHEDULE_HOUR
        db_set_flag("SCHED_START", str(hh))
        reconfigure_scheduler(context.application)
        await update.message.reply_text(f"✅ Nueva hora programada: {hh:02d}:00")
    except Exception:
        await update.message.reply_text("Formato: /when 2  (para 02:00)")


def mk_sched_menu():
    cfg = load_sched_config()
    status = "Ventana" if cfg["enabled"] else "24/7"
    row1 = [
        InlineKeyboardButton("🟢 24/7 (sin programación)", callback_data="act:sched:always"),
        InlineKeyboardButton("🕒 Ventana diaria", callback_data="act:sched:window"),
    ]
    # horas rápidas
    hrs = [0, 3, 6, 12, 18, 21]
    row2 = [
        InlineKeyboardButton(f"Start {h:02d}", callback_data=f"act:sched:start:{h}") for h in hrs
    ]
    row3 = [
        InlineKeyboardButton(f"Stop  {h:02d}", callback_data=f"act:sched:stop:{h}") for h in hrs
    ]
    rows = [row1, row2, row3]
    return (
        f"Modo actual: <b>{status}</b>\nStart: <b>{cfg['win_start']:02d}:00</b> — Stop: <b>{cfg['win_stop']:02d}:00</b>",
        InlineKeyboardMarkup(rows),
    )


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt, kb = mk_sched_menu()
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _safe_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    msg = getattr(update, "effective_message", None)
    if msg:
        await msg.reply_text(text)
        return
    chat_id = getattr(update, "effective_chat", None)
    if chat_id:
        try:
            await context.application.bot.send_message(chat_id=chat_id.id, text=text)
        except Exception:
            pass


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_set_flag("PAUSED", "1")
    try:
        aria2_pause_all()
    except Exception as e:
        print(f"[DBG] aria2_pause_all: {e!r}")
    PAUSE_EVT.set()
    proc = RUNNING.get("ytdlp_proc")
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except Exception:
            pass
    await _safe_reply(
        update, context, "⏸️ Pausado. La tarea activa será detenida y el resto quedado en 'paused'."
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

    # Si hay GID de aria2: snapshot -> remove -> unlink
    if ext_id:
        try:
            st = {}
            try:
                st = aria2_tell(ext_id) or {}
            except Exception:
                st = {}
            try:
                aria2_remove(ext_id)
            except Exception as e:
                await update.message.reply_text(f"aria2 remove falló: {e!r}")
            for f in st.get("files") or []:
                p = (f.get("path") or "").strip()
                if not p:
                    continue
                try:
                    path = Path(p)
                    # borrar archivo parcial si existe
                    path.unlink(missing_ok=True)
                    # borrar sidecars comunes: .aria2 y fragmentos *.part/*.ytdl del mismo directorio
                    sidecar = Path(str(path) + ".aria2")
                    sidecar.unlink(missing_ok=True)
                    try:
                        for cand in path.parent.glob(path.name + ".*"):
                            if cand.suffix.lower() in (".part", ".ytdl"):
                                cand.unlink(missing_ok=True)
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception as e:
            await update.message.reply_text(f"aria2 remove falló: {e!r}")

    db_update_status(qid, "canceled")
    await update.message.reply_text(f"❌ Cancelado #{qid}")

    # Limpieza defensiva de temporales yt-dlp (*.part, *.ytdl)
    try:
        from tgdl.adapters.downloaders import ytdlp as _y

        _y.cleanup_temporals(Path(settings.DOWNLOAD_DIR), hours=12)
    except Exception as e:
        print(f"[DBG] cleanup yt-dlp temporals: {e!r}")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_set_flag("PAUSED", "0")
    db_requeue_paused_reschedule_now()
    try:
        aria2_unpause_all()
    except Exception as e:
        print(f"[DBG] aria2_unpause_all: {e!r}")
    PAUSE_EVT.clear()
    await _safe_reply(update, context, "▶️ Reanudado. Lanzando ciclo en segundo plano…")
    await launch_cycle_background(
        context.application,
        force_all=True,
        notify_chat_id=update.effective_chat.id if update.effective_chat else None,
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

    # Contadores para UX
    c_tg_urls = 0
    c_web_urls = 0
    c_media = 0
    enqueued_any = False

    # 1) Enlaces de mensajes de Telegram
    tg_urls = re.findall(r"https?://t\.me/[^\s]+", text, flags=re.IGNORECASE)
    for u in tg_urls:
        db_add("tg_link", {"url": u}, scheduled_at)
        c_tg_urls += 1
        enqueued_any = True

    # 2) URLs/magnets (excluye t.me)
    urls = [u for u in extract_urls(text) if not u.lower().startswith("https://t.me/")]
    for u in urls:
        is_pl, kind = _has_playlistish(u)
        if _is_youtube(u) and is_pl:
            # Pre-vista de la lista antes de decidir
            token = secrets.token_hex(8)
            PLAYLIST_CHOICES[token] = u
            label = "lista (Radio)" if kind == "radio" else "lista"
            try:
                limit = _get_playlist_limit()
                meta = await ytdlp.probe_playlist(u, limit=limit)
                title = meta.get("title") or "Playlist"
                sample = meta.get("sample") or []
                lines = [f"📚 <b>{title}</b> — {len(sample)} de {meta.get('count') or '?'} ítems:"]
                for s in sample:
                    lines.append(f"• #{s.get('index', '?')} — {s.get('title', '(sin título)')}")
                await m.reply_text(
                    "\n".join(lines),
                    parse_mode=ParseMode.HTML,
                    reply_markup=_mk_playlist_choice_kb(token),
                    disable_web_page_preview=True,
                )
            except Exception:
                # No dejamos que un fallo DNS tumbe intake; si falla, seguimos el flujo sin preview.
                try:
                    await m.reply_text(
                        f"Detecté un enlace de YouTube con {label}. ¿Qué deseas descargar?",
                        reply_markup=_mk_playlist_choice_kb(token),
                        disable_web_page_preview=True,
                    )
                except Exception as _e:
                    logger.warning("reply_text failed (preview playlist): %r", _e)
            # No encolamos todavía este enlace. Pasamos al siguiente (si lo hubiera).
            continue

        # Enlaces "normales": encola directo
        db_add("url", {"url": u, "notify_chat_id": m.chat_id}, scheduled_at)
        c_web_urls += 1
        enqueued_any = True

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

    if m and (m.document or m.video or m.audio or m.photo or m.voice or m.video_note):
        db_add(
            "self_ref",
            {"chat_id": m.chat_id, "message_id": m.message_id, "suggested_name": suggested},
            scheduled_at,
        )
        c_media += 1
        enqueued_any = True

    # 4) Origen reenviado (si el canal permite revelar origen) -> tg_ref
    try:
        fo = getattr(m, "forward_origin", None)
        if fo and getattr(fo, "type", "") == "channel":
            chat_id = fo.chat.id
            mid = fo.message_id
            db_add("tg_ref", {"chat_id": chat_id, "message_id": mid}, scheduled_at)
            enqueued_any = True
    except Exception as e:
        print(f"[DBG] forward_origin error: {e!r}")

    # 5) Si no hay insumos accionables → NO encolar y NO disparar ciclo.
    if not enqueued_any:
        await m.reply_text(
            "👋 Te leo. Envíame un **enlace http/https**, un **magnet** o reenvía el **archivo**.\n"
            "Ejemplos:\n"
            "• https://ejemplo.com/video.mp4\n"
            "• magnet:?xt=urn:btih:...\n"
            "• Reenvía un mensaje con el archivo o video\n"
            "Usa /help si necesitas más detalles o\n"
            "Usa /Menu para opciones rapidas."
        )
        return

    # 6) Si no hay programación (24/7): solo reprograma si encolaste algo
    try:
        if db_get_flag("SCHED_ENABLED", "1") == "0":
            from tgdl.core.db import _connect

            now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
            with _connect() as conn:
                conn.execute(
                    "UPDATE queue SET scheduled_at=? WHERE status='queued' AND scheduled_at>?",
                    (now_iso, now_iso),
                )
                conn.commit()
            await launch_cycle_background(
                context.application, force_all=True, notify_chat_id=update.effective_chat.id
            )
    except Exception as e:
        print(f"[DBG] intake 24/7 error: {e!r}")

    # 7) Respuesta amigable con desglose
    parts = []
    if c_tg_urls:
        parts.append(f"{c_tg_urls} enlace(s) de Telegram")
    if c_web_urls:
        parts.append(f"{c_web_urls} URL/magnet(s)")
    if c_media:
        parts.append(f"{c_media} medio(s) reenviado(s)")
    summary = " + ".join(parts) if parts else "tarea(s)"

    # ¿24/7 o ventana?
    is_always = db_get_flag("SCHED_ENABLED", "1") == "0"
    rows = db_list(limit=9999)
    qcount = len(rows) if rows else 1

    if is_always:
        next_hint = (
            "Usa /list para ver la cola, /pause para pausar o /now para forzar un ciclo inmediato."
        )
    else:
        next_hint = (
            f"La cola iniciará automáticamente a las {settings.SCHEDULE_HOUR:02d}:00. "
            "Usa /list para ver la cola o /now para ejecutar ahora."
        )

    await m.reply_text(
        f"✅ {summary} encolado(s).\n"
        f"Actualmente tienes {qcount} elemento(s) en la cola.\n"
        f"{next_hint}"
    )


# ========= HTTP control (FastAPI en 127.0.0.1:8765) =========


def start_control_server():
    api = FastAPI(title="tg_downloader_control")

    @api.post("/cancel/{qid}")
    def http_cancel(qid: int):
        # Cancelación cooperativa:
        # 1) si es aria2 y tiene GID -> remove
        try:
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
    global SCHEDULER  # <-- IMPORTANTE

    # DB y carpeta
    db_init()
    Path(settings.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

    # Telethon (usuario)
    if not settings.USE_TELETHON:
        tclient = None
    else:
        if not (settings.API_ID and settings.API_HASH):
            raise SystemExit("Falta API_ID/API_HASH en .env")

        import platform

        from telethon.errors import AuthKeyDuplicatedError

        try:
            if settings.TELETHON_SESSION_MODE.lower() == "file":
                # Sesión por máquina (recomendado si usas varias PCs)
                sess_dir = Path(settings.SESSIONS_DIR)
                sess_dir.mkdir(parents=True, exist_ok=True)
                host = platform.node() or "host"
                sess_name = f"{settings.TELETHON_SESSION_BASE}_{host}"
                sess_path = sess_dir / (sess_name + ".session")
                tclient = TelegramClient(str(sess_path), settings.API_ID, settings.API_HASH)
            else:
                # Modo 'string' (como antes)
                if not settings.TELETHON_STRING:
                    raise SystemExit("Falta TELETHON_STRING (o cambia TELETHON_SESSION_MODE=file).")
                tclient = TelegramClient(
                    StringSession(settings.TELETHON_STRING),
                    settings.API_ID,
                    settings.API_HASH,
                )

            await tclient.connect()
            if not await tclient.is_user_authorized():
                # Login guiado (solo file-mode)
                phone = os.getenv("TELETHON_PHONE") or input("Teléfono (+1...): ").strip()
                await tclient.start(phone=phone)
                if not await tclient.is_user_authorized():
                    raise SystemExit("No fue posible autorizar la sesión (verifica código/2FA).")

        except AuthKeyDuplicatedError:
            # Mensaje claro y salida controlada
            raise SystemExit(
                "Telethon: esta sesión se usó simultáneamente en otra IP. "
                "Soluciones:\n"
                " - Usa TELETHON_SESSION_MODE=file para tener sesiones por máquina, o\n"
                " - Genera una TELETHON_STRING distinta en esta PC."
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
    app.add_handler(CommandHandler("schedule", cmd_schedule))

    # Scheduler
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.start()
    SCHEDULER = scheduler  # <-- ahora sí afecta a la global

    # Guardar contexto global para el HTTP control
    BOT.app = app
    BOT.loop = asyncio.get_running_loop()
    BOT.tclient = tclient

    start_control_server()

    # Reconfigurar una vez que SCHEDULER YA existe
    reconfigure_scheduler(app)

    print(
        f"[i] Bot listo. Descarga diaria a las {settings.SCHEDULE_HOUR:02d}:00 ({settings.TIMEZONE})."
    )

    # Inicio explícito del bot de telegram
    await app.initialize()
    await app.start()
    try:
        settings.SCHEDULE_HOUR = get_flag_int("SCHEDULE_HOUR", settings.SCHEDULE_HOUR)  # type: ignore[attr-defined]
    except Exception:
        pass
    await app.updater.start_polling()
    try:
        await asyncio.Future()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await tclient.disconnect()
