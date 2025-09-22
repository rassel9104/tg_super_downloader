# tgdl/adapters/downloaders/ytdlp.py
from __future__ import annotations
from pathlib import Path
import subprocess, shutil, asyncio

from tgdl.config.settings import settings

# ==== Modo existente (sincrónico) se mantiene ====

class _YDLLogger:
    def debug(self, msg):
        if "Deleting original file" in msg:
            return
        print(f"[YTDLP] {msg}")
    def warning(self, msg): print(f"[YTDLP][WARN] {msg}")
    def error(self, msg):   print(f"[YTDLP][ERR] {msg}")

def _download_via_module(url: str, outdir: Path) -> bool:
    try:
        from yt_dlp import YoutubeDL
    except Exception as e:
        print(f"[YTDLP] módulo no disponible: {e!r}")
        return False

    ydl_opts = {
        "outtmpl": str(outdir / "%(title).80s.%(ext)s"),
        "restrictfilenames": True,
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "concurrent_fragment_downloads": 4,
        "logger": _YDLLogger(),
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
    }
    with YoutubeDL(ydl_opts) as ydl:
        errors = ydl.download([url])
    ok = (errors == 0)
    if not ok:
        print(f"[YTDLP] terminó con errores={errors}")
    return ok

def _download_via_exec(url: str, outdir: Path) -> bool:
    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if not exe:
        print("[YTDLP] no se encontró yt-dlp en PATH")
        return False
    cmd = [
        exe,
        "-o", str(outdir / "%(title).80s.%(ext)s"),
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--concurrent-fragments", "4",
        url,
    ]
    print(f"[YTDLP] exec: {' '.join(cmd)}")
    try:
        cp = subprocess.run(cmd, capture_output=False, check=False)
        if cp.returncode != 0:
            print(f"[YTDLP][ERR] código {cp.returncode}")
            return False
        return True
    except Exception as e:
        print(f"[YTDLP] excepción exec: {e!r}")
        return False

def download(url: str, outdir: Path | None = None) -> bool:
    outdir = Path(outdir or settings.DOWNLOAD_DIR)
    outdir.mkdir(parents=True, exist_ok=True)
    if _download_via_module(url, outdir):
        return True
    return _download_via_exec(url, outdir)

# ==== NUEVO: runner async cancelable (subproceso) ====

async def download_proc(url: str, outdir: Path, on_start=None, cancel_evt: asyncio.Event | None = None) -> bool:
    """
    Lanza yt-dlp como subproceso (async) y permite cancelación (terminate) si cancel_evt está activo.
    Devuelve True si terminó con 0; False si cancelado o error.
    """
    outdir = Path(outdir or settings.DOWNLOAD_DIR)
    outdir.mkdir(parents=True, exist_ok=True)

    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if not exe:
        # Si no hay binario, último recurso: ejecutar módulo en thread (no cancelable)
        return await asyncio.to_thread(download, url, outdir)

    cmd = [
        exe,
        "-o", str(outdir / "%(title).80s.%(ext)s"),
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--concurrent-fragments", "4",
        url,
    ]
    print(f"[YTDLP][ASYNC] {' '.join(cmd)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.STDOUT,
    )
    if on_start:
        try:
            on_start(proc)
        except Exception:
            pass

    # Poll cooperativo para cancelación
    while True:
        try:
            rc = await asyncio.wait_for(proc.wait(), timeout=0.5)
            return rc == 0
        except asyncio.TimeoutError:
            if cancel_evt and cancel_evt.is_set():
                # Terminar proceso y reportar cancelación
                try:
                    proc.terminate()  # Windows: envía CTRL-BREAK amable; si no, kill
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    proc.kill()
                return False
