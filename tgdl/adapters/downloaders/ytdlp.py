# tgdl/adapters/downloaders/ytdlp.py  (CRLF)
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from tgdl.core.logging import logger

# Ajusta si tu settings vive en otra ruta
try:
    from tgdl.config.settings import settings
except Exception:

    class _Dummy:
        def __init__(self):
            self.YTDLP_FORMAT = "bv*+ba/b"
            self.YTDLP_MERGE_FORMAT = "mp4"
            self.YTDLP_CONCURRENT_FRAGMENTS = 1
            self.YTDLP_THROTTLED_RATE = 1048576
            self.YTDLP_HTTP_CHUNK_SIZE = 1048576
            self.YTDLP_COOKIES = None
            self.YTDLP_PROXY = None
            self.YTDLP_FORCE_IPV4 = False
            self.YTDLP_COOKIES_MODE: str = "file"  # 'browser', 'file', 'off', 'auto'

        YTDLP_BROWSER = "edge"  # 'chrome', 'chromium', 'edge', 'firefox', 'brave'
        YTDLP_BROWSER_PROFILE = "Default"
        YTDLP_COOKIES_FILE: str = r"data\cookies\youtube.txt"
        YTDLP_WRITE_SUBS = True
        YTDLP_SUB_LANGS = "es,es-419,es-ES"
        YTDLP_CONVERT_SUBS = "srt"  # 'srt', 'vtt', ''
        YTDLP_METADATA_NFO = True
        YTDLP_WRITE_THUMB = True
        YTDLP_SUBS_REQUIRED = False
        YTDLP_SLEEP_REQUESTS = 0  # segundos (float) a esperar entre requests
        YTDLP_SUBFOLDERS = "channel"  # 'playlist', 'channel', 'none'

    settings = _Dummy()

# ================= Utils =================


def _env_int(name: str, default: int) -> int:
    try:
        v = os.getenv(name)
        return int(v) if v not in (None, "", "None") else default
    except Exception:
        return default


def _cookies_path_valid() -> str | None:
    try:
        ck = getattr(settings, "YTDLP_COOKIES", None)
        if not ck:
            return None
        p = Path(ck)
        return str(p) if p.exists() else None
    except Exception:
        return None


def _looks_403(lines: list[str]) -> bool:
    pat = re.compile(r"\b(HTTP\s*403|Forbidden)\b", re.I)
    return any(pat.search(x or "") for x in lines)


def _url_has_playlistish(url: str) -> bool:
    try:
        return "list" in parse_qs(urlparse(url).query)
    except Exception:
        return False


# ================= Common args =================


def _common_args(
    url: str,
    outtmpl: str,
    use_cookies: bool,
    allow_playlist: bool,
    max_items: int | None = None,
    *,
    with_subs: bool = True,
    fmt_override: str | None = None,
    extractor_override: str | None = None,
) -> list[str]:
    fmt = fmt_override or getattr(settings, "YTDLP_FORMAT", "bv*+ba/b")
    mrg = getattr(settings, "YTDLP_MERGE_FORMAT", "mp4")
    cfd = str(getattr(settings, "YTDLP_CONCURRENT_FRAGMENTS", 1))
    thr = str(getattr(settings, "YTDLP_THROTTLED_RATE", 1048576))
    chn = str(getattr(settings, "YTDLP_HTTP_CHUNK_SIZE", 1048576))

    args: list[str] = [
        "-o",
        outtmpl,
        "-f",
        fmt,
        "--merge-output-format",
        mrg,
        "--concurrent-fragments",
        cfd,
        "--retries",
        "15",
        "--fragment-retries",
        "15",
        "--throttled-rate",
        thr,
        "--http-chunk-size",
        chn,
        "--sleep-requests",
        "0.2",
        "--socket-timeout",
        "30",
        "--geo-bypass",
        "--geo-bypass-country",
        "US",
        "--extractor-args",
        "youtube:player_client=android,web",
        "--progress-template",
        "download:%(progress._percent_str)s %(progress._speed_str)s ETA %(progress._eta_str)s",
        "--progress-template",
        "postprocess:%(progress._percent_str)s post",
        "--no-warnings",
    ]
    # Opcional: User-Agent / Referer para YouTube (reduce 403 en algunos CDNs)
    ua = getattr(settings, "YTDLP_USER_AGENT", None)
    if ua:
        args += ["--user-agent", ua]
    if getattr(settings, "YTDLP_ADD_REFERER", False):
        args += ["--add-header", "Referer: https://www.youtube.com/"]
    # Suavizado contra 429 global (si se configuró)
    if getattr(settings, "YTDLP_SLEEP_REQUESTS", 0) and settings.YTDLP_SLEEP_REQUESTS > 0:
        args += ["--sleep-requests", str(settings.YTDLP_SLEEP_REQUESTS)]
    # --- Cookies / autenticación (YouTube) ---
    if use_cookies:
        args += _cookies_args()
    # --- Subtítulos (español / auto) ---
    if with_subs and getattr(settings, "YTDLP_WRITE_SUBS", False):
        # Saneamos lista de lenguas
        raw = (
            (getattr(settings, "YTDLP_SUB_LANGS", "es,es-419,es-ES") or "")
            .strip()
            .strip('"')
            .strip("'")
        )
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        if len(codes) == 1 and codes[0] in {"*", "all"}:
            sub_arg = "all"
        else:
            codes = [c for c in codes if c != "*"]
            sub_arg = ",".join(codes) if codes else "es,es-419,es-ES"
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", sub_arg]
        conv = (getattr(settings, "YTDLP_CONVERT_SUBS", "srt") or "").strip()
        if conv:
            args += ["--convert-subs", conv]
    # --- Metadata/thumbnail ---
    if getattr(settings, "YTDLP_METADATA_NFO", True):
        args += ["--write-info-json"]
    if getattr(settings, "YTDLP_WRITE_THUMB", True):
        args += ["--write-thumbnail", "--convert-thumbnails", "jpg"]

    if not allow_playlist:
        args.append("--no-playlist")
    else:
        if max_items and max_items > 0:
            # Límite robusto (soporta Mix/Radio, índices desplazados, etc.)
            args += ["--playlist-start", "1"]
            args += ["--playlist-end", str(max_items)]
            args += ["--playlist-items", f"1-{max_items}"]  # redundante pero inofensivo
            args += ["--max-downloads", str(max_items)]  # corte duro universal

    if use_cookies:
        ck = _cookies_path_valid()
        if ck:
            args += ["--cookies", ck]

    proxy = getattr(settings, "YTDLP_PROXY", None)
    if proxy:
        args += ["--proxy", proxy]

    if getattr(settings, "YTDLP_FORCE_IPV4", False):
        args += ["--force-ipv4"]

    # Permitir sobreescribir extractor-args (p.ej. 403 → usar sólo 'web')
    if extractor_override:
        args += ["--extractor-args", extractor_override]

    args.append(url)
    return args


# --- Detectores de error comunes ---
_RE_FMT_UNAVAILABLE = re.compile(r"Requested\s+format\s+is\s+not\s+available", re.I)


def _looks_fmt_unavailable(lines: list[str]) -> bool:
    return any(_RE_FMT_UNAVAILABLE.search(ln or "") for ln in (lines or []))


# 429 en subtítulos (patrones vistos en yt-dlp)
_RE_SUBS_429 = re.compile(
    r"(Unable to download video subtitles.*429|HTTP\s+Error\s+429.*subtitles?)",
    re.I,
)


def _looks_subs_429(lines: list[str]) -> bool:
    return any(_RE_SUBS_429.search(ln or "") for ln in (lines or []))


_RE_SIGNIN_BOT = re.compile(r"Sign in to confirm you(?:'|’)re not a bot", re.I)


def _looks_signin_bot(lines: list[str]) -> bool:
    return any(_RE_SIGNIN_BOT.search(ln or "") for ln in (lines or []))


# ================= outtmpl helper =================
def _default_outtmpl(outdir: Path) -> str:
    """
    Plantilla de salida por defecto para yt-dlp.
    Usamos un nombre Windows-safe y dejamos que yt-dlp gestione el slug del título.
    """
    return str(outdir / "%(title).200B [%(id)s].%(ext)s")


def _resolve_yt_dlp() -> str:
    """
    Windows-first: prioriza el yt-dlp del venv (junto a sys.executable),
    luego PATH (shutil.which), y por último 'yt-dlp'.
    """
    try:
        exe = Path(sys.executable)
        for name in ("yt-dlp.exe", "yt-dlp"):
            cand = exe.with_name(name)
            if cand.exists():
                return str(cand)
    except Exception:
        pass
    return shutil.which("yt-dlp") or "yt-dlp"


# ================= Cookies helpers =================
def _cookies_args() -> list[str]:
    """
    Construye flags de cookies para yt-dlp según settings.
    Prioridad:
      - browser: --cookies-from-browser <browser>:<profile>
      - file (si existe): --cookies <path>
      - off: sin cookies
      - auto: browser si YTDLP_BROWSER está definido; si no, file si existe; si no, off
    """
    mode = (getattr(settings, "YTDLP_COOKIES_MODE", "browser") or "browser").lower()
    browser = (getattr(settings, "YTDLP_BROWSER", "edge") or "edge").lower()
    profile = getattr(settings, "YTDLP_BROWSER_PROFILE", "Default") or "Default"
    cookie_file = (
        getattr(settings, "YTDLP_COOKIES_FILE", r"data\cookies\youtube.txt")
        or "data\cookies\youtube.txt"
    )

    def _browser_args():
        # FORMATO: --cookies-from-browser edge:Default
        return ["--cookies-from-browser", f"{browser}:{profile}"]

    def _file_args():
        return ["--cookies", cookie_file]

    if mode == "browser":
        return _browser_args()
    if mode == "file":
        return _file_args() if os.path.exists(cookie_file) else []
    if mode == "off":
        return []
    # auto
    if browser:
        return _browser_args()
    if os.path.exists(cookie_file):
        return _file_args()
    return []


def _looks_dpapi(lines: list[str]) -> bool:
    return any("Failed to decrypt with DPAPI" in ln for ln in (lines or []))


def _subfolder_mode() -> str:
    # 'playlist' (default), 'channel', 'none'
    try:
        v = getattr(settings, "YTDLP_SUBFOLDERS", None)
        if not v:
            v = os.getenv("YTDLP_SUBFOLDERS", "playlist")
        return str(v).strip().lower()
    except Exception:
        return "playlist"


def _is_channel_url(url: str) -> bool:
    low = url.lower()
    return any(p in low for p in ["youtube.com/channel/", "youtube.com/@", "youtube.com/c/"])


def _make_outtmpl(outdir: Path, url: str, allow_playlist: bool) -> str:
    """
    Decide plantilla - crea subcarpetas legibles sin tocar la DB:
    - playlist mode:   out/ %(playlist_title)s/%(title)s [id].ext
    - channel mode:    out/ %(uploader)s/%(title)s [id].ext (fallback %(channel)s)
    - none/default:    out/ %(title)s [id].ext
    """
    base = str(outdir).rstrip("\\/")
    mode = _subfolder_mode()
    if allow_playlist and mode == "playlist":
        return f"{base}/%(playlist_title)s/%(title).200B [%(id)s].%(ext)s"
    if _is_channel_url(url) and mode in ("channel", "playlist"):
        return f"{base}/%(uploader)s/%(title).200B [%(id)s].%(ext)s"
    return f"{base}/%(title).200B [%(id)s].%(ext)s"


# --- utilidades de limpieza (Windows-friendly) ---
def cleanup_temporals(root: Path, hours: int = 12) -> int:
    """
    Elimina archivos temporales comunes de yt-dlp (*.part, *.ytdl) modificados
    en las últimas `hours`. Retorna el conteo de archivos eliminados.
    """
    import time

    now = time.time()
    cutoff = now - hours * 3600
    exts = {".part", ".ytdl", ".ytdl.part"}
    n = 0
    try:
        for p in Path(root).rglob("*"):
            try:
                if p.suffix.lower() in exts and p.stat().st_mtime >= cutoff:
                    p.unlink(missing_ok=True)
                    n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n


async def probe_playlist(url: str, limit: int = 10) -> dict[str, Any]:
    yt = shutil.which("yt-dlp") or "yt-dlp"
    limit = max(1, int(limit or 10))
    cmd = [
        yt,
        "--flat-playlist",
        "--print",
        "playlist:title",
        "--print",
        "playlist_count",
        "--print",
        "%(playlist_index)s|%(id)s|%(title)s",
        url,
    ]
    logger.info("[YTDLP][probe] launching flat-playlist")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        lines = (out or b"").decode("utf-8", "ignore").splitlines()

        title: str | None = None
        count: int | None = None
        sample: list[dict[str, Any]] = []

        for ln in lines:
            s = (ln or "").strip()
            if not s:
                continue
            if s.isdigit():
                if count is None:
                    count = int(s)
                continue
            if "|" in s:
                parts = s.split("|", 2)
                if len(parts) == 3:
                    try:
                        idx = int(parts[0])
                    except Exception:
                        idx = None
                    sample.append({"index": idx, "id": parts[1], "title": parts[2]})
                if len(sample) >= limit:
                    pass
                continue
            if title is None:
                title = s

        logger.info("[YTDLP][probe] title=%r count=%s sample=%d", title, count, len(sample))
        return {"title": title, "count": count, "sample": sample[:limit]}
    except Exception as e:
        logger.error("[YTDLP][probe][ERR] %r", e)
        return {"title": None, "count": None, "sample": []}


# ================= Descarga principal =================


async def download_proc(
    url: str,
    outdir: Path,
    on_start: Callable[[asyncio.subprocess.Process], None] | None = None,
    cancel_evt: asyncio.Event | None = None,
    allow_playlist: bool = False,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    max_items: int | None = None,
) -> bool:
    logger.info(
        "[YTDLP][guard] playlistish=%s allow_playlist=%s", _url_has_playlistish(url), allow_playlist
    )

    # Normaliza tope y batch
    if max_items is None:
        max_items = _env_int("YTDLP_MAX_PLAYLIST_ITEMS", 0) or None
    BATCH_NOTIFY_EVERY = _env_int("YTDLP_BATCH_EVERY", 4)  # notificar cada N archivos completados

    # Pre-sondeo para avisar títulos al usuario
    if allow_playlist and progress_cb is not None:
        limit = max_items or _env_int("YTDLP_MAX_PLAYLIST_ITEMS", 24)
        meta = await probe_playlist(url, limit=limit)
        with contextlib.suppress(Exception):
            progress_cb(
                {
                    "event": "playlist_info",
                    "title": meta.get("title"),
                    "count": meta.get("count"),
                    "sample": meta.get("sample") or [],
                }
            )

    async def _run(
        use_cookies: bool,
        *,
        with_subs: bool = True,
        fmt_override: str | None = None,
        extractor_override: str | None = None,
    ) -> tuple[bool, list[str]]:
        yt = _resolve_yt_dlp()
        outtmpl = _make_outtmpl(outdir, url, allow_playlist)
        args = [
            yt,
            *_common_args(
                url=url,
                outtmpl=outtmpl,
                use_cookies=use_cookies,
                allow_playlist=allow_playlist,
                max_items=max_items,
                with_subs=with_subs,
                fmt_override=fmt_override,
                extractor_override=extractor_override,
            ),
        ]

        outdir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "[YTDLP][exec] bin=%s outtmpl=%s dir=%s cookies=%s",
            yt,
            outtmpl,
            str(outdir),
            use_cookies,
        )

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,  # Ensure stdout is set to a pipe
            stderr=asyncio.subprocess.STDOUT,
        )
        if on_start:
            with contextlib.suppress(Exception):
                on_start(proc)

        lines: list[str] = []
        max_secs = _env_int("YTDLP_MAX_RUN_SECS", 900)  # 15 min por defecto

        # Seguimiento por ítem
        items_started = 0
        items_done = 0

        # Patrones comunes
        re_item_start = re.compile(r"^\[download\]\s+Destination:\s+(.+)$", re.I)
        re_item_done = re.compile(r"^\[download\]\s+100%\s", re.I)
        re_item_skip = re.compile(r"^\[download\]\s+(.+?) has already been downloaded", re.I)
        re_merging = re.compile(r"^\[Merger\]\s+Merging formats into", re.I)

        async def _pump():
            nonlocal items_started, items_done
            while True:
                if cancel_evt and cancel_evt.is_set():
                    with contextlib.suppress(Exception):
                        proc.kill()
                    return
                chunk = await proc.stdout.readline()
                if not chunk:
                    break
                ln = chunk.decode("utf-8", "ignore").rstrip("\r\n")
                lines.append(ln)

                # === Consola: seguimiento por ítem ===
                m_start = re_item_start.search(ln)
                if m_start:
                    items_started += 1
                    logger.debug("[YTDLP][item] start #%d: %s", items_started, m_start.group(1))

                # MARCAMOS "hecho" por cualquiera de estas señales:
                if re_item_done.search(ln) or re_item_skip.search(ln) or re_merging.search(ln):
                    items_done += 1
                    logger.debug("[YTDLP][item] done  #%d", items_done)
                    # Telegram: batched (cada N)
                    if (
                        progress_cb
                        and BATCH_NOTIFY_EVERY > 0
                        and (items_done % BATCH_NOTIFY_EVERY == 0)
                    ):
                        with contextlib.suppress(Exception):
                            progress_cb({"event": "batch", "done": items_done})

                # progreso básico (porcentaje/speed/ETA) — opcional
                if progress_cb:
                    m = re.search(r"(\d{1,3}(?:\.\d)?)%\s+([^\s]+/s).+?ETA\s+([0-9:]{2,})", ln)
                    if m:
                        try:
                            pct = float(m.group(1))
                        except Exception:
                            pct = 0.0
                        with contextlib.suppress(Exception):
                            progress_cb(
                                {
                                    "event": "progress",
                                    "percent": int(pct),
                                    "speed": m.group(2),
                                    "eta": m.group(3),
                                }
                            )

        try:
            await asyncio.wait_for(_pump(), timeout=max_secs)
            rc = await asyncio.wait_for(proc.wait(), timeout=60)
            # rc==101 => "max-downloads alcanzado" (cuenta como ÉXITO)
            ok = rc == 0 or rc == 101
            if not ok:
                tail = lines[-40:] if len(lines) > 40 else lines
                logger.error(
                    "[YTDLP][done] rc=%s lines=%d tail=%s", rc, len(lines), "\n".join(tail)
                )
            else:
                logger.info("[YTDLP][done] rc=%s started=%d done=%d", rc, items_started, items_done)
            return ok, lines
        except TimeoutError:
            logger.error("[YTDLP][TOUT] killing process after %ss", max_secs)
            with contextlib.suppress(Exception):
                proc.kill()
            return False, lines
        except Exception as e:
            logger.error("[YTDLP][ERR] %r", e)
            with contextlib.suppress(Exception):
                proc.kill()
            return False, lines

    # 1) con cookies (navegador o file, según settings/_cookies_args)
    ok1, lines = await _run(use_cookies=True, with_subs=True)
    if ok1:
        if getattr(settings, "YTDLP_METADATA_NFO", True):
            with contextlib.suppress(Exception):
                _emit_nfo_for_recent(outdir)
        return True
    # 1.b) Si el fallo es "Requested format is not available", reintenta con formato de fallback
    if _looks_fmt_unavailable(lines):
        logger.warning("[YTDLP][retry] sin match de formato → último intento con 'best'")
        ok_fmt, lines_fmt = await _run(
            use_cookies=True, with_subs=True, fmt_override="bestvideo*+bestaudio/best"
        )
        if ok_fmt:
            return True
        lines = (lines or []) + (lines_fmt or [])

    # Si falló por DPAPI y estamos en 'auto'/'browser': intentar con cookies de archivo si existen
    if _looks_dpapi(lines) and settings.YTDLP_COOKIES_MODE in {"auto", "browser"}:
        cookie_file = _resolve_cookie_file()
        if cookie_file.exists():
            logger.warning(
                "[YTDLP][retry] DPAPI detectado → usando cookies de archivo: %s", str(cookie_file)
            )
            saved_mode = settings.YTDLP_COOKIES_MODE
            saved_file = getattr(settings, "YTDLP_COOKIES_FILE", None)
            try:
                # Asegura que _common_args pase --cookies con la ruta correcta
                settings.YTDLP_COOKIES_FILE = str(cookie_file)
                okf, linesf = await _run(use_cookies=True, with_subs=True)
            finally:
                settings.YTDLP_COOKIES_MODE = saved_mode
                # Restaurar valor previo (o limpiar si no existía)
                try:
                    if saved_file is None:
                        delattr(settings, "YTDLP_COOKIES_FILE")
                    else:
                        settings.YTDLP_COOKIES_FILE = saved_file
                except Exception:
                    pass
            if okf:
                if getattr(settings, "YTDLP_METADATA_NFO", True):
                    with contextlib.suppress(Exception):
                        _emit_nfo_for_recent(outdir)
                return True
            lines = (lines or []) + (linesf or [])
    # Si hubo 403 con cookies, probamos mismo cookies con formato más laxo y player_client web
    if _looks_403(lines):
        logger.warning("[YTDLP][retry] 403 con cookies → reintento 'best' + extractor web")
        ok_c403, lines_c403 = await _run(
            use_cookies=True,
            with_subs=True,
            fmt_override="best",
            extractor_override="youtube:player_client=web",
        )
        if ok_c403:
            if getattr(settings, "YTDLP_METADATA_NFO", True):
                with contextlib.suppress(Exception):
                    _emit_nfo_for_recent(outdir)
            return True
        lines = (lines or []) + (lines_c403 or [])

    # Si el error fue 429 en subtítulos y los subs NO son obligatorios → reintentar sin subtítulos
    if _looks_subs_429(lines) and not getattr(settings, "YTDLP_SUBS_REQUIRED", False):
        logger.warning("[YTDLP][retry] 429 en subtítulos → reintento sin subtítulos")
        ok_subless, _ = await _run(use_cookies=False, with_subs=False)
        if ok_subless:
            if getattr(settings, "YTDLP_METADATA_NFO", True):
                with contextlib.suppress(Exception):
                    _emit_nfo_for_recent(outdir)
            return True

    # 2) sin cookies (fallback general)
    if _looks_403(lines) or _looks_signin_bot(lines):
        logger.warning("[YTDLP][retry] 403 detectado; reintentando SIN cookies…")
    else:
        logger.warning("[YTDLP][retry] rc!=0; reintentando SIN cookies como fallback…")
    ok2, lines2 = await _run(use_cookies=False, with_subs=True)
    if ok2:
        if getattr(settings, "YTDLP_METADATA_NFO", True):
            with contextlib.suppress(Exception):
                _emit_nfo_for_recent(outdir)
        return True
    # NUEVO: si el 429 en subtítulos aparece AQUÍ (tras pasar a sin cookies), reintenta sin subtítulos
    if _looks_subs_429(lines2) and not getattr(settings, "YTDLP_SUBS_REQUIRED", False):
        logger.warning("[YTDLP][retry] 429 en subtítulos (sin cookies) → reintento sin subtítulos")
        ok2_subless, _ = await _run(use_cookies=False, with_subs=False)
        if ok2_subless:
            if getattr(settings, "YTDLP_METADATA_NFO", True):
                with contextlib.suppress(Exception):
                    _emit_nfo_for_recent(outdir)
            return True

    # Tail final si también falla sin cookies
    tail = lines2 or lines
    tail = tail[-40:] if len(tail) > 40 else tail
    logger.error("[YTDLP][fail] sin cookies también falló. Tail=%s", "\n".join(tail))
    return False


# ================= NFO helpers (Jellyfin/Emby/Kodi) =================

_MEDIA_EXTS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".m4v",
    ".mov",
    ".avi",
    ".flv",
    ".ts",
    ".m2ts",
    ".mp3",
    ".m4a",
    ".aac",
    ".opus",
    ".ogg",
    ".wav",
    ".flac",
}
# Ruta por defecto (safe en Windows) para cookies YouTube (Netscape)
DEFAULT_COOKIES_FILE = Path("data") / "cookies" / "youtube.txt"


def _resolve_cookie_file() -> Path:
    """
    Devuelve la ruta de cookies (settings.YTDLP_COOKIES_FILE si está definida y no vacía),
    o bien la ruta por defecto data/cookies/youtube.txt.
    """
    try:
        v = getattr(settings, "YTDLP_COOKIES_FILE", None)
        if v:
            p = Path(str(v))
            return p
    except Exception:
        pass
    return DEFAULT_COOKIES_FILE


def _safe_name(s: str) -> str:
    if s is None:
        return ""
    # Windows-safe filename
    return re.sub(r'[<>:"/\\|?*\x00-\x1F]+', " ", s).strip().rstrip(".")


def _upload_date_parts(info: dict[str, Any]) -> tuple[str, str, str]:
    ud = (info.get("upload_date") or "").strip()
    if len(ud) >= 8:
        return ud[:4], ud[4:6], ud[6:8]
    return "", "", ""


def _pick_genre(info: dict[str, Any]) -> str:
    cats = info.get("categories") or []
    if isinstance(cats, list) and cats:
        return str(cats[0])
    return _env_str("YTDLP_NFO_GENRE", "YouTube")


def _pick_mpaa(info: dict[str, Any]) -> str | None:
    # Heurística ligera a partir de age_limit (yt-dlp). Ajusta si lo deseas.
    age = info.get("age_limit")
    try:
        age = int(age) if age is not None else None
    except Exception:
        age = None
    if age is None:
        return None
    if age <= 7:
        return "TV-G"
    if age <= 13:
        return "TV-PG"
    if age <= 16:
        return "TV-14"
    return "TV-MA"


def write_movie_nfo(info: dict[str, Any], nfo_path: Path) -> None:
    """movie.nfo (vídeo suelto). Runtime en segundos, credits=uploader, mpaa/rating si existen."""
    title = info.get("title") or info.get("fulltitle") or ""
    fulltitle = info.get("fulltitle") or title
    plot = info.get("description") or ""
    duration = int(info.get("duration") or 0)  # segundos
    y, m, d = _upload_date_parts(info)
    premiered = f"{y}-{m}-{d}" if y else ""
    credits = info.get("uploader") or info.get("channel") or ""
    rating = info.get("average_rating")
    mpaa = _pick_mpaa(info)
    genre = _pick_genre(info)

    root = ET.Element("movie")
    ET.SubElement(root, "title").text = _safe_text(title)
    ET.SubElement(root, "originaltitle").text = _safe_text(fulltitle)
    ET.SubElement(root, "plot").text = _safe_text(plot)
    if genre:
        ET.SubElement(root, "genre").text = _safe_text(genre)
    ET.SubElement(root, "tag").text = "YouTube"
    if premiered:
        ET.SubElement(root, "premiered").text = premiered
    if duration > 0:
        ET.SubElement(root, "runtime").text = str(duration)  # segundos
    if credits:
        ET.SubElement(root, "credits").text = _safe_text(credits)
    if rating is not None:
        ET.SubElement(root, "rating").text = (
            f"{float(rating):.1f}"
            if str(rating).replace(".", "", 1).isdigit()
            else _safe_text(rating)
        )
    if mpaa:
        ET.SubElement(root, "mpaa").text = mpaa
    # uniqueid (conservamos por interoperabilidad)
    uid_el = ET.SubElement(root, "uniqueid")
    uid_el.set("type", "youtube")
    uid_el.set("default", "true")
    uid_el.text = _safe_text(info.get("id") or "")
    ET.ElementTree(root).write(nfo_path, encoding="utf-8", xml_declaration=True)


def write_episode_nfo(info: dict[str, Any], nfo_path: Path, *, season: int, episode: int) -> None:
    """episodedetails.nfo (capítulo). Añadimos year/aired/season/episode; runtime en segundos opcional."""
    title = info.get("title") or info.get("fulltitle") or ""
    plot = info.get("description") or ""
    y, m, d = _upload_date_parts(info)
    aired = f"{y}-{m}-{d}" if y else ""
    duration = int(info.get("duration") or 0)
    root = ET.Element("episodedetails")
    ET.SubElement(root, "title").text = _safe_text(title)
    ET.SubElement(root, "plot").text = _safe_text(plot)
    if y:
        ET.SubElement(root, "year").text = y
    if aired:
        ET.SubElement(root, "aired").text = aired
    ET.SubElement(root, "season").text = str(int(season))
    ET.SubElement(root, "episode").text = str(int(episode))
    if duration > 0:
        ET.SubElement(root, "runtime").text = str(duration)  # segundos (opcional)
    ET.ElementTree(root).write(nfo_path, encoding="utf-8", xml_declaration=True)


def ensure_tvshow_nfo(folder: Path, sample_info: dict[str, Any]) -> None:
    """tvshow.nfo mínimo en carpeta del canal."""
    tv = folder / "tvshow.nfo"
    if tv.exists():
        return
    title = sample_info.get("uploader") or sample_info.get("channel") or "YouTube"
    plot = sample_info.get("uploader") or sample_info.get("channel") or ""
    root = ET.Element("tvshow")
    ET.SubElement(root, "title").text = _safe_text(title)
    ET.SubElement(root, "plot").text = _safe_text(plot)
    ET.ElementTree(root).write(tv, encoding="utf-8", xml_declaration=True)


def _match_media_for_json(j: Path) -> Path | None:
    stem = j.stem[:-5] if j.stem.endswith(".info") else j.stem
    for ext in _MEDIA_EXTS:
        cand = j.with_name(stem + ext)
        try:
            if cand.exists() and cand.is_file():
                return cand
        except Exception:
            continue
    return None


def _ensure_episode_layout(
    info: dict[str, Any], json_path: Path, media_path: Path
) -> tuple[Path, Path]:
    """
    Reubica capítulo a: <Canal>/<Season YYYY>/<Canal> - <YYYYMMDD> - <Título> [<ID>].ext
    Mueve .info.json y .jpg/.png homónimos. Devuelve (nuevo_media, nuevo_json).
    Idempotente: si ya está en destino con ese nombre, no hace nada.
    """
    uploader = _safe_name(info.get("uploader") or info.get("channel") or "YouTube")
    y, m, d = _upload_date_parts(info)
    year = y or "0000"
    ymd = f"{y}{m}{d}".strip() if y else ""
    title = _safe_name(info.get("title") or info.get("fulltitle") or "")
    vid = _safe_name(info.get("id") or "")
    base_dir = media_path.parent  # debería ser ...\Canal o similar
    # Si el padre ya es "Season <YYYY>", su canal es el padre del padre
    channel_dir = base_dir.parent if base_dir.name.lower().startswith("season ") else base_dir
    season_dir = channel_dir / f"Season {year}"
    season_dir.mkdir(parents=True, exist_ok=True)

    # Nombre destino
    ext = media_path.suffix
    new_stem = f"{uploader} - {ymd} - {title} [{vid}]" if ymd else f"{uploader} - {title} [{vid}]"
    new_media = season_dir / (new_stem + ext)
    new_json = season_dir / (new_stem + ".info.json")

    # Ya en destino con el mismo nombre → nada que hacer
    if media_path.resolve() == new_media.resolve():
        return media_path, json_path

    # Mover media (atómico)
    try:
        os.replace(str(media_path), str(new_media))
    except Exception:
        pass

    # Mover json
    try:
        os.replace(str(json_path), str(new_json))
    except Exception:
        pass

    # Mover thumbnail homónima si existe (.jpg/.png)
    for exti in (".jpg", ".png"):
        old_img = media_path.with_suffix(exti)
        if old_img.exists():
            try:
                os.replace(str(old_img), str(new_media.with_suffix(exti)))
            except Exception:
                pass

    return new_media, new_json


def _iso_date_from_upload(s: str | None) -> str:
    """yt-dlp upload_date -> YYYYMMDD → YYYY-MM-DD."""
    if not s or len(s) < 8:
        return ""
    y, m, d = s[:4], s[4:6], s[6:8]
    return f"{y}-{m}-{d}"


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v in (None, "", "None") else str(v)


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _safe_text(x: Any) -> str:
    return "" if x is None else str(x)


def _pick_thumb(info: dict[str, Any]) -> str | None:
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        thumbs_sorted = sorted(thumbs, key=lambda t: (t.get("height") or 0), reverse=True)
        return thumbs_sorted[0].get("url") or None
    # fallback: yt-dlp ya convirtió .webp→.jpg al lado del media
    # no sabemos el nombre exacto aquí; mantener URL si existe
    return info.get("thumbnail") or None


# Extensiones válidas de media (vídeo/audio) para emparejar con .info.json
_MEDIA_EXTS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".m4v",
    ".mov",
    ".avi",
    ".flv",
    ".ts",
    ".m2ts",
    ".mp3",
    ".m4a",
    ".aac",
    ".opus",
    ".ogg",
    ".wav",
    ".flac",
}


def _nfo_path_for_json(j: Path) -> Path:
    """
    Convierte 'Nombre [id].info.json' → 'Nombre [id].nfo'
    (quita el sufijo '.info' del stem antes de cambiar extensión).
    """
    stem = j.stem  # p.ej. "Item1 [id1].info"
    if stem.endswith(".info"):
        stem = stem[:-5]
    return j.with_name(stem + ".nfo")


def _emit_nfo_for_recent(outdir: Path) -> int:
    """
    Recorre recursivamente *.info.json, crea/actualiza NFO y normaliza series:
      - Vídeo suelto → movie.nfo (en misma carpeta del media).
      - Playlist/Radio → tvshow.nfo en carpeta del canal + episodios en Season YYYY con rename.
    Sólo si hay media vecino.
    """
    tv_mode = _env_str("YTDLP_NFO_TV_MODE", "auto").lower()  # auto|always|never
    season_by_year = True  # forzamos temporadas por año en series (según tu especificación)
    created = 0

    for j in Path(outdir).rglob("*.info.json"):
        try:
            info = __import__("json").loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue

        media = _match_media_for_json(j)
        if not media:
            continue  # no NFO sin media

        is_playlist = bool(
            info.get("playlist_title") or info.get("playlist_id") or info.get("playlist_index")
        )

        if (tv_mode != "never") and is_playlist:
            # Normalizar layout de capítulo a Canal/Season YYYY/Canal - YYYYMMDD - Título [ID].ext
            media, j = _ensure_episode_layout(info, j, media)
            channel_dir = media.parent.parent  # ...\Canal\Season YYYY
            ensure_tvshow_nfo(channel_dir, info)

            # NFO del episodio (junto al media)
            nfo = media.with_suffix(".nfo")
            y, m, d = _upload_date_parts(info)
            season = int(y) if (season_by_year and y) else 1
            # episodio := MMDD si hay fecha; si no, playlist_index
            episode = int(f"{m}{d}") if (m and d) else int(info.get("playlist_index") or 1)
            write_episode_nfo(info, nfo, season=season, episode=episode)
            created += 1 if nfo.exists() else 0
        else:
            # Vídeo suelto → movie.nfo en el mismo directorio del media
            nfo = media.with_suffix(".nfo")
            write_movie_nfo(info, nfo)
            created += 1 if nfo.exists() else 0

    return created
