# Run once on your own computer to create TG_SESSION. Use the Telegram account that is a member of the channel.
from telethon.sessions import StringSession
from telethon.sync import TelegramClient

api_id = int(input("API ID: "))
api_hash = input("API hash: ").strip()
with TelegramClient(StringSession(), api_id, api_hash) as c:
    print("\nTG_SESSION=" + c.session.save())
