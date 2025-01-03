from databases import Database
from shared.db import (
    IntervalRepository,
    PgRepository,
    SourceRepository,
    create_db_string,
)
from shared.resources import SharedResources
from shared.utils import SHARED_CONFIG_PATH
from telethon import TelegramClient
from telethon.sessions import StringSession

import config
from entities import Channel, Folder, ProcessedIntervals, Source
from exporters import init_exporters


class Context:
    def __init__(self):
        self.config = config.Config()
        self.creds = self.config.telegram
        self.client = TelegramClient(
            StringSession(self.creds.session),
            self.creds.api_id,
            self.creds.api_hash,
            system_version="4.16.30-vxCUSTOM",
        )
        self.shared_settings = SharedResources(
            f"{SHARED_CONFIG_PATH}/settings.json"
        )
        self.pg = Database(
            create_db_string(self.config.database),
        )
        self.folder_repository = PgRepository(self.pg, Folder)
        self.source_repository = SourceRepository(self.pg, Source)
        self.channel_repository = PgRepository(self.pg, Channel)
        self.intervals_repository = IntervalRepository(
            self.pg, ProcessedIntervals
        )

    async def init_db(self):
        await self.pg.connect()

    async def dispose_db(self):
        await self.pg.disconnect()

    def init_exporters(self):
        self.exporters = init_exporters(self.config.exporters)


ctx = Context()
