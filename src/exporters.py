import logging

import redis
from rb_tocase import Case

from config import ExporterConfig

logger = logging.getLogger("scraper")


class BaseExporter:
    @classmethod
    def get_label(self):
        return Case.to_kebab(self.__name__)


exporters: list[BaseExporter] = []


def init_exporters(config: ExporterConfig):
    global exporters
    required_exporters = config.required_exporters
    candidates = BaseExporter.__subclasses__()
    logger.info(f"Required exporters: {required_exporters}")
    for exporter in candidates:
        if exporter.get_label() not in required_exporters:
            continue
        logger.info(f"Started loading {exporter.get_label()}")
        try:
            obj = exporter(config)
            exporters.append(obj)
        except Exception as e:
            logger.error(
                f"Got {type(e).__name__} exception while initializing {exporter.get_label()}: {e}"
            )
            continue

        logger.info(f"Finished loading {exporter.get_label()}")


def get_exporters(names: list[str]) -> list[BaseExporter]:
    global exporters
    if names is None:
        return exporters
    required = list(
        filter(lambda entry: entry.get_label() in names, exporters)
    )
    return required


class RedisExporter(BaseExporter):
    def __init__(self, config: ExporterConfig):
        self.client = redis.Redis(
            config.redis.host, config.redis.port, db=0, protocol=3
        )

    def export(self, request_id, json_dump):
        self.client.set(
            str(request_id),
            json_dump,
        )
