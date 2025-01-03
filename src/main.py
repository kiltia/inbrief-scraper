import json
import logging
import os
import traceback
import uuid

import faststream
from faststream import ContextRepo, ExceptionMiddleware, FastStream
from faststream.kafka import KafkaBroker
from shared.logger import configure_logging
from shared.models.api import ResponseState

from context import ctx
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
async def startup(_: ContextRepo):
    configure_logging()
    logger.info("Started initializing scraper")
    ctx.init_exporters()
    await ctx.init_db()
    await ctx.client.start()


@app.on_shutdown
async def shutdown(_: ContextRepo):
    ctx.client.disconnect()
    await ctx.dispose_db()


@broker.publisher("inbrief.scraper.out.json")
@broker.subscriber("inbrief.scraper.in.json")
async def scraper_consumer(
    request: ScrapeRequest,
    request_id: uuid.UUID = faststream.Header(),
) -> ScrapeResponse:
    logger.info("Started serving scrapping request")

    payload, actions = await scrape_channels(ctx, request, request_id)

    payload_json = json.dumps(
        payload.model_dump(), default=str, sort_keys=True, ensure_ascii=False
    )

    for exporter in ctx.exporters:
        logger.info(f"Exporting to {exporter.get_label()}")
        exporter.export(request_id, payload_json)

    return ScrapeResponse(
        request_id=request_id,
        state=ResponseState.SUCCESS,
        actions=actions,
    )
