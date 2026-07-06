from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from dcimport.db import MediaDatabase


@pytest.fixture
def open_db() -> Iterator[Callable[[Path], MediaDatabase]]:
    """Open MediaDatabases that are closed automatically at test teardown."""

    dbs: list[MediaDatabase] = []

    def _open(path: Path) -> MediaDatabase:
        db = MediaDatabase(path)
        dbs.append(db)
        return db

    yield _open

    for db in dbs:
        db.close()
