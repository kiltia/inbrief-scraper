#!/usr/bin/env python3

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import Config

creds = Config().telegram

with TelegramClient(
    StringSession(),
    creds.api_id,
    creds.api_hash,
    system_version="4.16.30-vxCUSTOM",
) as client:
    with open(".env", "a") as env_file:
        env_file.write(f"TELEGRAM__SESSION={client.session.save()}")
