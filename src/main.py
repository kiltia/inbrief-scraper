import logging
import os
import uuid

from databases import Database
from faststream import ContextRepo, FastStream
from faststream.kafka import KafkaBroker
from shared.db import IntervalRepository, PgRepository, create_db_string
from shared.entities import Channel, Folder, ProcessedIntervals, Source
from shared.logger import configure_logging
from shared.models import (
    ResponseState,
    ScrapeRequest,
    ScrapeResponse,
)
from shared.resources import SharedResources
from shared.utils import SHARED_CONFIG_PATH
from telethon import TelegramClient
from telethon.sessions import StringSession

import config
from scraper import scrape_channels

KAFKA_HOST = os.environ.get("KAFKA_HOST", "kafka")

broker = KafkaBroker(KAFKA_HOST)
app = FastStream(broker)


logger = logging.getLogger("scraper")


@app.on_startup
async def startup(context: ContextRepo):
    configure_logging()
    await ctx.init_db()
    await ctx.client.start()


@app.on_shutdown
async def shutdown(context: ContextRepo):
    ctx.client.disconnect()
    await ctx.dispose_db()


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
        self.source_repository = PgRepository(self.pg, Source)
        self.channel_repository = PgRepository(self.pg, Channel)
        self.intervals_repository = IntervalRepository(
            self.pg, ProcessedIntervals
        )

    async def init_db(self):
        await self.pg.connect()

    async def dispose_db(self):
        await self.pg.disconnect()


ctx = Context()


@broker.publisher("inbrief.scraped.out.json")
@broker.subscriber("inbrief.scraper.in.json")
async def scrape(
    request: ScrapeRequest,
) -> ScrapeResponse:
    logger.info("Started serving scrapping request")

    request_id = uuid.uuid4()

    actions = await scrape_channels(ctx, request, request_id)

    return ScrapeResponse(
        request_id=request_id,
        state=ResponseState.SUCCESS,
        actions=actions,
    )
