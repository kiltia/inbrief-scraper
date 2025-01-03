import logging
from uuid import UUID

import redis
from rb_tocase import Case

from config import ExporterConfig

logger = logging.getLogger("scraper")


class BaseExporter:
    @classmethod
    def get_label(cls):
        return Case.to_kebab(cls.__name__).removesuffix("-exporter")

    def export(self, request_id: UUID, json_dump: str):
        raise NotImplementedError

    def __init__(self, _: ExporterConfig):
        pass


def init_exporters(config: ExporterConfig):
    exporters: list[BaseExporter] = []
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
    return exporters


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


# TODO(nrydanov): Add S3 exporter
class JsonExporter(BaseExporter):
    def __init__(self, config: ExporterConfig):
        from pathlib import Path

        self.path = config.json_exporter.path
        Path(self.path).mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_label(cls):
        return "json"

    def export(self, request_id, json_dump):
        with open(f"{self.path}/{request_id}.json", "w") as f:
            f.write(json_dump)
