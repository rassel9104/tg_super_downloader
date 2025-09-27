# session_setup.py
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

print("== Generar TELETHON_STRING ==")
api_id = int(input("API_ID: ").strip())
api_hash = input("API_HASH: ").strip()
phone = input("Tu número con código de país (ej. +17865551234): ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    client.start(phone=phone)
    s = client.session.save()
    print("\n=== TELETHON_STRING (cópialo al .env) ===\n")
    print(s)
    print("\n=========================================\n")
