from typing import Annotated
from datetime import datetime
import logging
import os
from contextlib import asynccontextmanager

from asgi_correlation_id import CorrelationIdMiddleware
from databases import Database
from embedders import init_embedders
from fastapi import FastAPI, status, Header
from fastapi.responses import Response
from telethon import TelegramClient
from telethon.sessions import StringSession

from config import Credentials
from openai_api import get_async_client
from scraper import scrape_channels, retrieve_channels
from shared.db import PgRepository, create_db_string, IntervalRepository
from shared.entities import Channel, Folder, Source, ProcessedIntervals
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
        self.intervals_repository = IntervalRepository(self.pg, ProcessedIntervals)

    async def init_db(self):
        await self.pg.connect()

    async def dispose_db(self):
        await self.pg.disconnect()


ctx = Context()


def get_required_intervals(
    intersections: list[dict], l_bound: datetime, r_bound: datetime
):
    if len(intersections) > 0:
        full_intersections = list(
            filter(
                lambda x: x["l_bound"] <= l_bound and x["r_bound"] >= r_bound,
                intersections,
            )
        )

        # Case 1: Requested interval completely
        if len(full_intersections) > 0:
            return []

        # Case 2: Additional computations are needed
        intersections = sorted(intersections, key=lambda x: x["l_bound"])

        r_cur = intersections[0]["r_bound"]
        required = []
        for entry in intersections[1:]:
            l_bound = entry["l_bound"]
            r_bound = entry["r_bound"]

            if l_bound > r_cur:
                required.append((r_cur, l_bound))
                r_cur = r_bound
            else:
                r_cur = max(r_cur, r_bound)
    else:
        # Case 3: No cache found
        required = [(l_bound, r_bound)]

    return required


@app.post(ScraperRoutes.SCRAPE)
async def scrape(
    request: ScrapeRequest,
    request_id: Annotated[str, Header()] = "5b497dc1-1401-4d99-96bc-c6c05b4ab42c",
) -> ScrapeResponse:
    logger.info("Started serving scrapping request")

    intersections = await ctx.intervals_repository.get_intersections(
        request.end_date, request.offset_date
    )

    required = get_required_intervals(
        intersections, request.end_date, request.offset_date
    )

    params = request.model_dump()
    sources = []
    for l_bound, r_bound in required:
        params["end_date"] = l_bound
        params["offset_date"] = r_bound
        output, skipped_channel_ids = await scrape_channels(ctx, **params)

        if output:
            source_adapter = TypeAdapter(Source)

            def convert_to_entity(x: SourceOutput) -> Source:
                dumped = x.model_dump()

                dumped["date"] = dumped["date"].strftime(DB_DATE_FORMAT)
                dumped["embeddings"] = json.dumps(dumped["embeddings"])

                return source_adapter.validate_python(dumped)

            logger.debug("Saving scraped sources to database")

            await ctx.source_repository.add(
                list(map(convert_to_entity, output)), ignore_conflict=True
            )

        logger.debug("All data was saved to database successfully")
        sources.extend(output)

    await ctx.intervals_repository.add(
        ProcessedIntervals(
            l_bound=request.end_date,
            r_bound=request.offset_date if request.offset_date else datetime.now(),
            request_id=request_id,
        )
    )

    if len(sources) > 0:
        return ScrapeResponse(
            sources=sources,
            skipped_channel_ids=skipped_channel_ids,
        )
    else:
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            content={"message": "Nothing was found"},
        )


@app.get(ScraperRoutes.SYNC)
async def sync(link: str):
    logger.debug("Started serving sync request")
    response = await retrieve_channels(ctx, link)
    return response
