import argparse
import asyncio
import sys

from tgdl.config.settings import settings


def main():
    parser = argparse.ArgumentParser("tgdl")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("panel", help="Inicia el Panel FastAPI")
    sub.add_parser("bot", help="Inicia el bot de Telegram")
    sub.add_parser("control", help="(Reservado) Control local")

    args = parser.parse_args()

    if args.cmd == "panel":
        try:
            import uvicorn

            # Nota: en Windows, Ctrl+C lanza KeyboardInterrupt; lo manejamos abajo.
            uvicorn.run(
                "tgdl.panel.api:app",
                host=settings.PANEL_HOST,
                port=settings.PANEL_PORT,
                reload=False,
                # loop="asyncio"  # (opcional) explícito; uvicorn detecta asyncio por defecto
            )
            return 0
        except KeyboardInterrupt:
            print("\n[i] Panel detenido por el usuario.")
            return 0
        except Exception as e:
            print(f"[!] Error al iniciar el panel: {e!r}")
            return 1

    elif args.cmd == "bot":
        try:
            from tgdl.adapters.telegram.bot_app import main as bot_main

            asyncio.run(bot_main())
            return 0
        except KeyboardInterrupt:
            print("\n[i] Bot detenido por el usuario.")
            return 0
        except Exception as e:
            print(f"[!] Error al iniciar el bot: {e!r}")
            return 1

    elif args.cmd == "control":
        print("Los endpoints de control viven ahora en 127.0.0.1:8765 y arrancan junto al bot.")
        return 0

    else:
        parser.print_help()
        # código 2 suele indicar 'uso incorrecto de CLI'
        return 2


if __name__ == "__main__":
    sys.exit(main())
