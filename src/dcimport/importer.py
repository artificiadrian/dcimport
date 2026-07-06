import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from dcimport import heic
from dcimport.immutable import immutable
from dcimport.layout import DEFAULT_LAYOUT, Layout, parse_layout

DCIM_PATH = "/DCIM"
INCLUDE_EXTENSIONS = ("jpg", "jpeg", "png", "mov", "mp4", "heic")
DEFAULT_DB_NAME = "media.db"

LAYOUT_SETTING = "layout"

LIVE_PHOTO_IMAGE_EXTENSIONS = (".heic", ".jpg", ".jpeg")
LIVE_PHOTO_VIDEO_EXTENSION = ".mov"


class LayoutConflictError(Exception):
    """A layout was requested that differs from the one stored in the library's database."""

    def __init__(self, stored: str, requested: str):
        super().__init__(
            f"Requested layout '{requested}' differs from this library's stored layout '{stored}'."
        )
        self.stored = stored
        self.requested = requested


@immutable
class FileStat:
    """Parsed metadata of a file on the device."""

    size: int
    mtime: datetime


@immutable
class DirEntry:
    """A directory entry on the device; never downloaded."""


class MediaSource(Protocol):
    """A device filesystem that media files can be listed, stat'ed and downloaded from."""

    def list_files(self, path: PurePosixPath) -> AsyncIterator[PurePosixPath]:
        """Recursively yield all entries (files and directories) under `path`."""
        ...

    async def stat(self, path: PurePosixPath) -> FileStat | DirEntry:
        """Return the metadata of the entry at `path`."""
        ...

    async def download(self, path: PurePosixPath, target: Path) -> None:
        """Download the file at `path` to the local `target` path."""
        ...


class Database(Protocol):
    """The library database as the importer needs it: dedup lookup, recording, settings."""

    def contains(
        self, afc_path: PurePosixPath, st_size: int, st_mtime: datetime
    ) -> bool: ...

    def record(
        self, afc_path: PurePosixPath, st_size: int, st_mtime: datetime
    ) -> None: ...

    def get_setting(self, key: str) -> str | None: ...

    def set_setting(self, key: str, value: str) -> None: ...


@immutable
class NewFile:
    """A new media file that has been downloaded from the device."""

    afc_path: PurePosixPath
    local_path: Path
    stat: FileStat


@immutable
class FailedFile:
    """A file whose download failed after all retries; the import continues with the next file."""

    afc_path: PurePosixPath
    stat: FileStat
    error: str


@immutable
class PlannedFile:
    """A media file found on the device during scanning."""

    afc_path: PurePosixPath
    stat: FileStat


@immutable
class ImportPlan:
    """Result of scanning the device: what to download, what is already imported,
    and what is ignored (directories and excluded extensions)."""

    to_download: tuple[PlannedFile, ...]
    existing: tuple[PlannedFile, ...]
    ignored: tuple[PurePosixPath, ...]

    @property
    def total_bytes(self):
        """Total size of all files that would be downloaded."""

        return sum(f.stat.size for f in self.to_download)


def resolve_layout(db: Database, requested: str | None, force: bool):
    """Resolve the layout for this run: the stored one by default, storing on first
    use; a differing request errors unless `force`, which updates the stored value.
    Device-independent — call it before connecting so bad layouts fail fast.

    Raises:
        InvalidLayoutError: If `requested` is malformed.
        LayoutConflictError: If `requested` differs from the stored one and not `force`."""

    stored = db.get_setting(LAYOUT_SETTING)

    if (
        stored is not None
        and requested is not None
        and requested != stored
        and not force
    ):
        raise LayoutConflictError(stored=stored, requested=requested)

    template = requested if requested is not None else (stored or DEFAULT_LAYOUT)
    layout = parse_layout(template)

    if template != stored:
        db.set_setting(LAYOUT_SETTING, template)

    return layout


def _is_live_photo_video(path: PurePosixPath, image_keys: set[tuple[str, str]]):
    """A Live Photo's video half: a .mov whose stem matches an image in the same directory."""

    return (
        path.suffix.lower() == LIVE_PHOTO_VIDEO_EXTENSION
        and (str(path.parent), path.stem.lower()) in image_keys
    )


def _available_path(target: Path, reserved: set[Path]):
    """Return `target` if free, otherwise the first `name_1`, `name_2`, … that is.
    `reserved` holds paths already claimed by this run but not yet on disk."""

    n = 0
    candidate = target

    while candidate.exists() or candidate in reserved:
        n += 1
        candidate = target.with_name(f"{target.stem}_{n}{target.suffix}")

    return candidate


async def plan_import(
    source: MediaSource,
    db: Database,
    dcim_path: str = DCIM_PATH,
    include_extensions: Sequence[str] = INCLUDE_EXTENSIONS,
    skip_live_videos: bool = False,
    since: datetime | None = None,
    until: datetime | None = None,
):
    """Scan `source` and classify every entry. Read-only: downloads and writes nothing.
    With `skip_live_videos`, the video halves of Live Photos are ignored. `since`/`until`
    bound the files to import by modification time (inclusive)."""

    wanted_extensions = {
        normalized
        for ext in include_extensions
        if (normalized := ext.lower().lstrip("."))
    }
    entries = [entry async for entry in source.list_files(PurePosixPath(dcim_path))]

    image_keys = {
        (str(path.parent), path.stem.lower())
        for path in entries
        if path.suffix.lower() in LIVE_PHOTO_IMAGE_EXTENSIONS
    }

    to_download, existing, ignored = [], [], []

    for path in entries:
        # filter on extension before stat'ing to save a USB round-trip per file
        if path.suffix.lower()[1:] not in wanted_extensions:
            ignored.append(path)
            continue

        if skip_live_videos and _is_live_photo_video(path, image_keys):
            ignored.append(path)
            continue

        stat = await source.stat(path)

        if isinstance(stat, DirEntry):
            ignored.append(path)
            continue

        if (since is not None and stat.mtime < since) or (
            until is not None and stat.mtime > until
        ):
            ignored.append(path)
            continue

        planned = PlannedFile(afc_path=path, stat=stat)

        if db.contains(path, stat.size, stat.mtime):
            existing.append(planned)
        else:
            to_download.append(planned)

    return ImportPlan(
        to_download=tuple(to_download),
        existing=tuple(existing),
        ignored=tuple(ignored),
    )


async def _download_to_temp(
    source: MediaSource,
    path: PurePosixPath,
    temp_path: Path,
    expected_size: int,
    retries: int,
):
    """Download `path` into `temp_path`, retrying on error or a short read. Returns
    None on success, or the last error after exhausting retries. Cleans up the temp
    file and re-raises on cancellation/KeyboardInterrupt (never retried)."""

    download_error = None

    for _attempt in range(retries + 1):
        try:
            await source.download(path, temp_path)
        except Exception as e:
            temp_path.unlink(missing_ok=True)
            download_error = e
            continue
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

        actual_size = temp_path.stat().st_size

        if actual_size != expected_size:
            # a silent short read (early EOF) would otherwise be recorded as a
            # successful import; treat it as a failure so it retries / re-imports
            temp_path.unlink(missing_ok=True)
            download_error = OSError(
                f"incomplete download: got {actual_size} of {expected_size} bytes"
            )
            continue

        return None

    return download_error


async def _download_file(
    source: MediaSource,
    db: Database,
    planned: PlannedFile,
    target_path: Path,
    converting: bool,
    download_retries: int,
):
    """Download one planned file into place; returns NewFile or FailedFile."""

    path, stat = planned.afc_path, planned.stat
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # download to a temp name and rename atomically, so an interrupted
    # transfer never leaves a truncated file under the final name
    temp_path = target_path.with_name(target_path.name + ".part")

    download_error = await _download_to_temp(
        source, path, temp_path, stat.size, download_retries
    )

    if download_error is not None:
        return FailedFile(afc_path=path, stat=stat, error=str(download_error))

    if converting:
        converted_path = temp_path.with_name(target_path.name + ".converted.part")

        try:
            heic.convert_heic_to_jpeg(temp_path, converted_path)
        except Exception as e:
            converted_path.unlink(missing_ok=True)
            return FailedFile(
                afc_path=path, stat=stat, error=f"HEIC conversion failed: {e}"
            )
        except BaseException:
            # cancellation/KeyboardInterrupt: don't leave a half-converted file
            converted_path.unlink(missing_ok=True)
            raise
        finally:
            temp_path.unlink(missing_ok=True)

        temp_path = converted_path

    try:
        os.utime(temp_path, (datetime.now().timestamp(), stat.mtime.timestamp()))
        os.replace(temp_path, target_path)

        # record only after the file is fully in place, so a crash anywhere
        # above means the file is re-imported on the next run
        db.record(path, stat.size, stat.mtime)
    except Exception as e:
        # a failure finalizing one file (e.g. a momentarily locked db) must not
        # escape and cancel the whole TaskGroup — degrade it to a FailedFile
        temp_path.unlink(missing_ok=True)
        return FailedFile(afc_path=path, stat=stat, error=f"failed to finalize: {e}")
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    return NewFile(afc_path=path, local_path=target_path, stat=stat)


async def execute_import(
    source: MediaSource,
    db: Database,
    plan: ImportPlan,
    output_path: Path,
    layout: Layout,
    download_retries: int = 2,
    convert_heic: bool = False,
    concurrency: int = 4,
):
    """Download every file in `plan.to_download` into `output_path` under the resolved
    `layout`, recording each in `db` once it is fully in place. Up to `concurrency`
    files transfer at once; yields one result per attempted file, in completion order.

    Raises:
        MissingHeicSupportError: If `convert_heic` is set but pillow-heif is not installed."""

    if convert_heic and not heic.heic_support_available():
        raise heic.MissingHeicSupportError()

    output_path.mkdir(parents=True, exist_ok=True)

    # reserve every target path up front so same-named files cannot collide
    reserved: set[Path] = set()
    jobs: list[tuple[PlannedFile, Path, bool]] = []

    for planned in plan.to_download:
        converting = convert_heic and planned.afc_path.suffix.lower() == ".heic"
        target_name = (
            f"{planned.afc_path.stem}.jpg" if converting else planned.afc_path.name
        )

        target_path = _available_path(
            output_path / layout.render(name=target_name, mtime=planned.stat.mtime),
            reserved,
        )
        reserved.add(target_path)
        jobs.append((planned, target_path, converting))

    semaphore = asyncio.Semaphore(concurrency)

    async def download_one(planned: PlannedFile, target_path: Path, converting: bool):
        async with semaphore:
            return await _download_file(
                source, db, planned, target_path, converting, download_retries
            )

    # TaskGroup cancels the remaining downloads if one raises (only cancellation,
    # KeyboardInterrupt or SystemExit escape download_one; failures become FailedFile)
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(download_one(*job)) for job in jobs]

        for completed in asyncio.as_completed(tasks):
            yield await completed
