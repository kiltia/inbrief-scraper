import asyncio
import json
import logging
from concurrent.futures._base import TimeoutError
from typing import List, Tuple

from embedders import OpenAi, get_embedders
from telethon.errors.rpcbaseerrors import BadRequestError
from telethon.errors.rpcerrorlist import ChannelPrivateError, MsgIdInvalidError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.chatlists import CheckChatlistInviteRequest

from shared.entities import Channel, Folder, Source
from shared.models import SourceOutput

logger = logging.getLogger("scraper")


def get_worker(
    channel_entity,
    ctx,
    embedders,
    social: bool,
    **kwargs,
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
            "date": message.date,
            "reference": f"t.me/{channel_entity.username}/{message.id}",
            "channel_id": channel_entity.id,
            "embeddings": {},
            "views": message.views,
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
                logger.warn(
                    "Got invalid message ID while parsing, skipping..."
                )
            except ValueError as e:
                logger.warn(e)
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

        logger.debug(f"Started generating embeddings for {message.id}")
        # TODO(nrydanov): Move embedding retrieval out of this function
        # to enable batch processing on GPU to increase overall performance
        for emb in embedders:
            if isinstance(emb, OpenAi):
                embeddings = (
                    await emb.aget_embeddings(
                        [message.message], ctx.openai_client
                    )
                )[0]
            else:
                embeddings = emb.get_embeddings([message.message])[0]
            content["embeddings"].update({emb.get_label(): embeddings})

        logger.debug(f"Ended generating embeddings for {message.id}")
        logger.debug(f"Ended parsing message {message.id}")
        return SourceOutput.parse_obj(content)

    return get_content


async def get_content_from_channel(
    channel_entity,
    ctx,
    embedders,
    end_date,
    offset_date=None,
    **kwargs,
) -> List[Source]:
    batch = []
    api_iterator = ctx.client.iter_messages(
        channel_entity, offset_date=offset_date
    )
    get_content = get_worker(channel_entity, ctx, embedders, **kwargs)
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


async def retrieve_channels(ctx, chat_folder_link: str):
    logger.debug(f"Retrieving channels from link: {chat_folder_link}")

    slug = chat_folder_link.split("/")[-1]
    try:
        channels = (await ctx.client(CheckChatlistInviteRequest(slug))).chats
        ids = list(map(lambda x: x.id, channels))
        entity = Folder(chat_folder_link=chat_folder_link, channels=ids)

        await ctx.folder_repository.add_or_update(entity, ["channels"])
        logger.info(
            f"Successful update channels from link: {chat_folder_link}"
        )
    except BadRequestError:
        logger.info(
            f"Warning when updating channels from link: {chat_folder_link}"
        )
        entity = (
            await ctx.folder_repository.get(
                "chat_folder_link", chat_folder_link
            )
        )[0]
        ids = entity.channels
    return ids


async def scrape_channels(
    ctx,
    channels: List[int],
    required_embedders: List[str],
    **parse_args,
) -> Tuple[List[Source], List[int]]:
    logger.debug("Getting all required embedders")

    client = ctx.client
    embedders = get_embedders(required_embedders)
    result: List[Source] = []
    skipped_channel_ids: List[int] = []
    for channel_id in channels:
        try:
            channel_entity = await client.get_entity(channel_id)
        except ChannelPrivateError:
            logger.debug(
                f"The channel {channel_id} appears to be private, "
                f"and we aren't allowed to access it, skipping."
            )
            skipped_channel_ids.append(channel_id)
            continue

        info = (await client(GetFullChannelRequest(channel_id))).full_chat
        logger.debug(f"Parsing channel: {channel_entity.id}")

        channel = Channel(
            channel_id=info.id,
            title=channel_entity.title,
            about=info.about,
            subscribers=info.participants_count,
        )
        await ctx.channel_repository.add_or_update(
            channel, fields=["title", "about", "subscribers"]
        )
        response = await get_content_from_channel(
            channel_entity,
            ctx,
            embedders,
            **parse_args,
        )
        result = result + response

    return result, skipped_channel_ids
