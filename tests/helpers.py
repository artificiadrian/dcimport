"""Test helpers that compose the two import phases exactly as the CLI does."""

import asyncio

from dcimport.db import MediaDatabase
from dcimport.importer import (
    DEFAULT_DB_NAME,
    execute_import,
    plan_import,
    resolve_layout,
)


def persistently_fail(source, path, times=3):
    """Queue `times` download failures for `path` (enough to exhaust the retries)."""

    for _ in range(times):
        source.fail_next_download(path, OSError("usb died"))


def scan(source, output_path):
    """Run the read-only scan phase against the library's database; returns the ImportPlan."""

    async def _run():
        output_path.mkdir(parents=True, exist_ok=True)
        db = MediaDatabase(output_path / DEFAULT_DB_NAME)
        try:
            return await plan_import(source, db)
        finally:
            db.close()

    return asyncio.run(_run())


def run_import(
    source, output_path, *, layout=None, force_layout=False, convert_heic=False
):
    """Scan then download, the way the CLI composes the two phases; returns the download results."""

    async def _run():
        output_path.mkdir(parents=True, exist_ok=True)
        db = MediaDatabase(output_path / DEFAULT_DB_NAME)
        try:
            resolved_layout = resolve_layout(db, layout, force_layout)
            plan = await plan_import(source, db)
            return [
                result
                async for result in execute_import(
                    source,
                    db,
                    plan,
                    output_path,
                    resolved_layout,
                    convert_heic=convert_heic,
                )
            ]
        finally:
            db.close()

    return asyncio.run(_run())
