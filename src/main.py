import json
import logging
import os
import traceback
import uuid

import faststream
from databases import Database
from faststream import ContextRepo, ExceptionMiddleware, FastStream
from faststream.kafka import KafkaBroker
from shared.db import IntervalRepository, PgRepository, create_db_string
from shared.logger import configure_logging
from shared.models.api import ResponseState
from shared.resources import SharedResources
from shared.utils import SHARED_CONFIG_PATH
from telethon import TelegramClient
from telethon.sessions import StringSession

import config
from entities import Channel, Folder, ProcessedIntervals, Source
from exporters import init_exporters
from models import (
    ScrapeRequest,
    ScrapeResponse,
)
from scraper import scrape_channels

KAFKA_HOST = os.environ.get("KAFKA_HOST", "kafka")

exc_middleware = ExceptionMiddleware()
broker = KafkaBroker(KAFKA_HOST, middlewares=[exc_middleware])
app = FastStream(broker)

logger = logging.getLogger("scraper")


@exc_middleware.add_handler(Exception, publish=True)
def error_handler(exc, message=faststream.Context()):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.error(tb)
    return {
        "state": ResponseState.FAILED,
        "request_id": message.headers.get("request_id"),
        "error": str(exc),
        "error_repr": repr(exc),
    }


@app.on_startup
async def startup(context: ContextRepo):
    configure_logging()
    logger.info("Started initializing scraper")
    ctx.init_exporters()
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

    def init_exporters(self):
        self.exporters = init_exporters(self.config.exporters)


ctx = Context()


@broker.publisher("inbrief.scraper.out.json")
@broker.subscriber("inbrief.scraper.in.json")
async def scraper_consumer(
    request: ScrapeRequest,
    request_id: uuid.UUID = faststream.Header(),
) -> ScrapeResponse:
    logger.info("Started serving scrapping request")

    payload, actions = await scrape_channels(ctx, request, request_id)

    # TODO(nrydanov): Add cached sources to payload?
    payload_json = json.dumps(
        list(map(lambda x: x.model_dump(), payload.gathered)), default=str
    )

    for exporter in ctx.exporters:
        logger.info(f"Exporting to {exporter.get_label()}")
        exporter.export(request_id, payload_json)

    return ScrapeResponse(
        request_id=request_id,
        state=ResponseState.SUCCESS,
        actions=actions,
    )
