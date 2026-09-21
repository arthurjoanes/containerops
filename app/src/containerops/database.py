from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection

from containerops.config import Settings


@contextmanager
def connect(settings: Settings) -> Iterator[Connection[tuple[object, ...]]]:
    # Uma conexão curta por operação evita transações ociosas e se recupera após restart.
    with psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.password(),
        connect_timeout=2,
        application_name="containerops",
        options="-c statement_timeout=3000 -c lock_timeout=2000",
    ) as connection:
        yield connection
