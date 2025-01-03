from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field
from shared.models.api import BaseRequest, BaseResponse

from entities import Source


class ScrapeRequest(BaseRequest):
    chat_folder_link: str = "https://t.me/addlist/W9JQ42l78Kc5MTAy"
    right_bound: Annotated[
        datetime, Field(default_factory=lambda _: datetime.now().astimezone())
    ]
    left_bound: datetime
    social: bool = False
    exporters: list[str] = []


class ScrapeAction(str, Enum):
    FULL_SCAN = "full_scan"
    PARTIAL_SCAN = "partial_scan"
    CACHED = "cached"
    FAILED = "fail"


class ScrapeInfo(BaseModel):
    action: ScrapeAction
    count: int


class ScrapeResponse(BaseResponse):
    actions: dict[int, ScrapeInfo]


class ResponsePayload(BaseModel):
    cached: list[Source]
    gathered: list[Source]
