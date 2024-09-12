import logging
import os
from contextlib import asynccontextmanager

from asgi_correlation_id import CorrelationIdMiddleware
from databases import Database
from embedders import init_embedders
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from telethon import TelegramClient
from telethon.sessions import StringSession

from config import Credentials
from openai_api import get_async_client
from scraper import scrape_channels, retrieve_channels
from shared.db import PgRepository, create_db_string
from shared.entities import Channel, Folder, Source
from shared.logger import configure_logging
from shared.models import ScrapeRequest, ScrapeResponse, SourceOutput
from shared.resources import SharedResources
from shared.routes import ScraperRoutes
from shared.utils import SHARED_CONFIG_PATH
from shared.utils import DB_DATE_FORMAT
from pydantic import TypeAdapter
import json


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("Started loading embedders")
    init_embedders(ctx.shared_settings.components.embedders)
    await ctx.init_db()
    await ctx.client.start()
    yield
    await ctx.client.disconnect()
    await ctx.dispose_db()


logger = logging.getLogger("scraper")

app = FastAPI(lifespan=lifespan)
app.add_middleware(CorrelationIdMiddleware, validator=None)


class Context:
    def __init__(self):
        self.creds = Credentials()
        self.client = TelegramClient(
            StringSession(self.creds.session),
            self.creds.api_id,
            self.creds.api_hash,
            system_version="4.16.30-vxCUSTOM",
        )
        self.openai_client = get_async_client(os.getenv("OPENAI_API_KEY"))
        self.shared_settings = SharedResources(f"{SHARED_CONFIG_PATH}/settings.json")
        pg_pswd = os.getenv("POSTGRES_PASSWORD")
        pg_user = os.getenv("POSTGRES_USER")
        self.pg = Database(
            create_db_string(self.shared_settings.pg_creds, pg_pswd, pg_user)
        )
        self.folder_repository = PgRepository(self.pg, Folder)
        self.source_repository = PgRepository(self.pg, Source)
        self.channel_repository = PgRepository(self.pg, Channel)

    async def init_db(self):
        await self.pg.connect()

    async def dispose_db(self):
        await self.pg.disconnect()


ctx = Context()


@app.post(ScraperRoutes.SCRAPE)
async def scrape(request: ScrapeRequest) -> ScrapeResponse:
    logger.info("Started serving scrapping request")
    output, skipped_channel_ids = await scrape_channels(ctx, **request.model_dump())
    # TODO(nrydanov): Need to add caching there in case all posts for required
    # time period are already stored in database (#137)
    if output:
        source_adapter = TypeAdapter(Source)

        def convert_to_entity(x: SourceOutput) -> Source:
            dumped = x.model_dump()

            dumped["date"] = dumped["date"].strftime(DB_DATE_FORMAT)
            dumped["embeddings"] = json.dumps(dumped["embeddings"])

            return source_adapter.validate_python(dumped)

        await ctx.source_repository.add(
            list(map(convert_to_entity, output)), ignore_conflict=True
        )

        logger.debug("Data was saved to database successfully")
        return ScrapeResponse(
            sources=output,
            skipped_channel_ids=skipped_channel_ids,
        )
    else:
        return JSONResponse(
            status_code=status.HTTP_204_NO_CONTENT,
            content={"message": "Nothing was found"},
        )


@app.get(ScraperRoutes.SYNC)
async def sync(link: str):
    logger.debug("Started serving sync request")
    response = await retrieve_channels(ctx, link)
    return response
