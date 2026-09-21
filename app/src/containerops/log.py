import json
import logging
import sys
from datetime import UTC, datetime


def configure() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr, force=True)


def event(service: str, version: str, name: str, **fields: object) -> None:
    logging.getLogger("containerops").info(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "service": service,
                "version": version,
                "event": name,
                **fields,
            },
            ensure_ascii=False,
            default=str,
        )
    )
