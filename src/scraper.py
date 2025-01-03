import asyncio
import json
import logging
from concurrent.futures._base import TimeoutError
from datetime import datetime
from uuid import UUID

from pydantic import TypeAdapter
from telethon.errors.rpcbaseerrors import BadRequestError
from telethon.errors.rpcerrorlist import ChannelPrivateError, MsgIdInvalidError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.chatlists import CheckChatlistInviteRequest

from context import Context
from entities import Channel, Folder, ProcessedIntervals, Source
from models import (
    ResponsePayload,
    ScrapeAction,
    ScrapeInfo,
    ScrapeRequest,
)

logger = logging.getLogger("scraper")


def get_required_intervals(
    overlaps: list[dict], l_bound: datetime, r_bound: datetime
) -> tuple[ScrapeAction, list[tuple[datetime, datetime]]]:
    if len(overlaps) > 0:
        full_overlaps = list(
            filter(
                lambda x: x["l_bound"] <= l_bound and x["r_bound"] >= r_bound,
                overlaps,
            )
        )

        # Case 1: Requested interval completely cached
        if len(full_overlaps) > 0:
            return ScrapeAction.CACHED, []

        action = ScrapeAction.PARTIAL_SCAN

        # Case 2: Additional computations are needed
        intersections = sorted(
            overlaps, key=lambda x: (x["l_bound"], x["r_bound"])
        )

        required = []
        if intersections[0]["l_bound"] > l_bound:
            required.append((l_bound, intersections[0]["l_bound"]))
        if intersections[-1]["r_bound"] < r_bound:
            required.append((intersections[-1]["r_bound"], r_bound))
        r_cur = intersections[0]["r_bound"]
        for entry in intersections[1:]:
            l_bound = entry["l_bound"]
            r_bound = entry["r_bound"]

            if l_bound > r_cur:
                required.append((r_cur, l_bound))
                r_cur = r_bound
            else:
                r_cur = max(r_cur, r_bound)
    else:
        action = ScrapeAction.FULL_SCAN
        # Case 3: No cache found
        required = [(l_bound, r_bound)]

    return action, required


def get_worker(
    channel_entity,
    ctx,
    social: bool,
    request_id: UUID,
):
    client = ctx.client

    async def get_content(message) -> Source | None:
        if message.message in ["", None]:
            return None
        logger.debug(f"Started getting content for {message.id}")
        logger.debug("Parsing data from Telegram")
        content = {
            "source_id": message.id,
            "text": message.message,
            "ts": message.date,
            "reference": f"t.me/{channel_entity.username}/{message.id}",
            "channel_id": channel_entity.id,
            "views": message.views,
            "request_id": request_id,
        }
        if social:
            logger.debug(f"Started getting social content for {message.id}")
            comments = []
            try:
                async for msg in client.iter_messages(
                    channel_entity, reply_to=message.id
                ):
                    text = msg.text
                    if text is not None:
                        comments.append(text)
            except MsgIdInvalidError:
                logger.warning(
                    "Got invalid message ID while parsing, skipping..."
                )
            except ValueError as e:
                logger.warning(e)
            content.update({"comments": comments})
            if message.reactions is None:
                content.update({"reactions": []})
            else:
                content.update(
                    {
                        "reactions": [
                            {
                                "emoticon": reaction.reaction.emoticon,
                                "count": reaction.count,
                            }
                            for reaction in message.reactions.results
                        ]
                    }
                )
            logger.debug(f"Ended getting social content for {message.id}")

            content["reactions"] = json.dumps(content["reactions"])

        logger.debug(f"Ended parsing message {message.id}")
        return Source.model_validate(content)

    return get_content


async def get_content_from_channel(
    channel_entity,
    ctx,
    end_date,
    offset_date=None,
    **kwargs,
) -> list[Source]:
    batch = []
    api_iterator = ctx.client.iter_messages(
        channel_entity, offset_date=offset_date
    )
    get_content = get_worker(channel_entity, ctx, **kwargs)
    async for message in api_iterator:
        try:
            if message.date < end_date:
                break
            batch.append(get_content(message))
        except TimeoutError:
            logging.error("Received timeout when processing chunk, skipping.")
            continue

    logger.debug(f"Got {len(batch)} messages in total. Processing...")

    batch = await asyncio.gather(*batch)
    return list(filter(lambda x: x is not None, batch))


async def retrieve_channels(ctx, chat_folder_link: str) -> list[int]:
    logger.debug(f"Retrieving channels from link: {chat_folder_link}")

    slug = chat_folder_link.split("/")[-1]
    try:
        channels = (await ctx.client(CheckChatlistInviteRequest(slug))).chats
        ids = list(map(lambda x: x.id, channels))
        entity = Folder(chat_folder_link=chat_folder_link, channels=ids)

        await ctx.folder_repository.add_or_update(entity, ["channels"])
        logger.debug(f"Updated channels from link: {chat_folder_link}")
    except BadRequestError:
        logger.warning(
            f"An error occured when updating channels from link: {chat_folder_link}"
        )
        entity = (
            await ctx.folder_repository.get(
                "chat_folder_link", chat_folder_link
            )
        )[0]
        ids = entity.channels
    return ids


async def scrape_channels(
    ctx: Context,
    request: ScrapeRequest,
    request_id: UUID,
) -> tuple[ResponsePayload, dict[int, ScrapeInfo]]:
    logger.debug("Getting all required embedders")

    client = ctx.client
    skipped_channel_ids: list[int] = []
    result: dict[int, ScrapeInfo] = {}
    left_bound = request.left_bound
    right_bound = request.right_bound

    channels = await retrieve_channels(ctx, request.chat_folder_link)
    gathered_sources: list[Source] = []
    cached_sources: list[Source] = []
    for channel_id in channels:
        try:
            channel_entity = await client.get_entity(channel_id)
        except ChannelPrivateError:
            logger.debug(
                f"The channel {channel_id} appears to be private, "
                f"and we aren't allowed to access it, skipping."
            )
            skipped_channel_ids.append(channel_id)
            result[channel_id] = ScrapeInfo(
                action=ScrapeAction.FAILED, count=0
            )
            continue

        info = (await client(GetFullChannelRequest(channel_id))).full_chat
        logger.debug(f"Scraping channel: {channel_entity.id}")
        channel = Channel(
            channel_id=info.id,
            title=channel_entity.title,
            about=info.about,
            subscribers=info.participants_count,
        )
        await ctx.channel_repository.add_or_update(
            channel, fields=["title", "about", "subscribers"]
        )

        overlaps = await ctx.intervals_repository.get_intersections(
            left_bound, right_bound or datetime.now(), channel_id
        )

        logger.debug(f"Got overlaps: {overlaps}")

        action, required = get_required_intervals(
            overlaps, left_bound, right_bound
        )

        logger.debug(f"Evaluated required intervals: {required}")

        for l_bound, r_bound in required:
            logger.debug(f"Processing interval {l_bound} - {r_bound}")
            response = await get_content_from_channel(
                channel_entity,
                ctx,
                end_date=l_bound,
                offset_date=r_bound,
                social=request.social,
                request_id=request_id,
            )
            logger.debug(
                f"Got response for interval {l_bound} - {r_bound}, count: {len(response)}"
            )
            gathered_sources.extend(response)

            await ctx.source_repository.add(response, ignore_conflict=True)
            await ctx.intervals_repository.add(
                ProcessedIntervals(
                    l_bound=l_bound,
                    r_bound=r_bound,
                    request_id=request_id,
                    channel_id=channel_id,
                )
            )

            cached = TypeAdapter(list[Source]).validate_python(
                await ctx.source_repository.get_cached(
                    channel_id, left_bound, right_bound
                )
            )

            cached_sources.extend(cached)
            result[channel_id] = ScrapeInfo(
                action=action,
                count=len(response),
            )

    # TODO(nrydanov): Add cached sources to payload output
    return ResponsePayload(
        cached=cached_sources, gathered=gathered_sources
    ), result
