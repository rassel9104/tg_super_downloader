import argparse
import asyncio
from tgdl.config.settings import settings

def main():
    parser = argparse.ArgumentParser("tgdl")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("panel", help="Inicia el Panel FastAPI")
    sub.add_parser("bot", help="Inicia el bot de Telegram")
    sub.add_parser("control", help="(Reservado) Control local")

    args = parser.parse_args()

    if args.cmd == "panel":
        import uvicorn
        uvicorn.run("tgdl.panel.api:app",
                    host=settings.PANEL_HOST, port=settings.PANEL_PORT, reload=False)

    elif args.cmd == "bot":
        from tgdl.adapters.telegram.bot_app import main as bot_main
        asyncio.run(bot_main())

    elif args.cmd == "control":
        print("Los endpoints de control viven ahora en 127.0.0.1:8765 y arrancan junto al bot.")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
