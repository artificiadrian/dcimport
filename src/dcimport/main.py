import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import datetime, time
from importlib.metadata import version
from pathlib import Path

from rich.console import Console
from rich.filesize import decimal as human_size
from rich.markup import escape
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from dcimport.afc_utils import AfcSource, MultipleDevicesError, afc_connect
from dcimport.db import MediaDatabase
from dcimport.heic import MissingHeicSupportError, heic_support_available
from dcimport.immutable import immutable
from dcimport.importer import (
    DCIM_PATH,
    DEFAULT_DB_NAME,
    INCLUDE_EXTENSIONS,
    FailedFile,
    ImportPlan,
    LayoutConflictError,
    NewFile,
    execute_import,
    plan_import,
    resolve_layout,
)
from dcimport.layout import InvalidLayoutError, Layout, parse_layout


def _parse_date(value: str):
    """Parse a YYYY-MM-DD date for the --since/--until options."""

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as e:
        msg = f"invalid date '{value}', expected YYYY-MM-DD"
        raise argparse.ArgumentTypeError(msg) from e


def _parse_args():
    parser = argparse.ArgumentParser(description="Import media files from iPhone")

    parser.add_argument(
        "output",
        metavar="DIRECTORY",
        help="Directory where media files should be downloaded to",
        type=Path,
    )

    parser.add_argument(
        "--dcim-path",
        metavar="PATH",
        help="Directory on iPhone to scan for media files",
        type=str,
        default=DCIM_PATH,
    )

    parser.add_argument(
        "--db-path",
        metavar="PATH",
        help="Library database location (default: media.db in the output directory)",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--include-extensions",
        metavar="EXT,EXT,...",
        help="List of file extensions to include (comma-separated)",
        type=str,
        default=",".join(INCLUDE_EXTENSIONS),
    )

    parser.add_argument(
        "--layout",
        metavar="TEMPLATE",
        help="Filename/subfolder template of {name} and {mtime:...}; stored and reused (default: timestamped)",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--force",
        help="Allow changing the layout stored in the database",
        action="store_true",
    )

    parser.add_argument(
        "--udid",
        help="Device to import from, by UDID (needed when multiple are connected)",
        type=str,
        default=None,
    )

    parser.add_argument(
        "--skip-live-videos",
        help="Skip the video half of Live Photos",
        action="store_true",
    )

    parser.add_argument(
        "--convert-heic",
        help="Convert HEIC photos to JPEG (needs the 'heic' extra)",
        action="store_true",
    )

    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Only import files modified on or after this date",
        type=_parse_date,
        default=None,
    )

    parser.add_argument(
        "--until",
        metavar="YYYY-MM-DD",
        help="Only import files modified on or before this date",
        type=_parse_date,
        default=None,
    )

    parser.add_argument(
        "--manifest",
        metavar="PATH",
        help="Write a JSON report of imported and failed files",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--concurrency",
        metavar="N",
        help="Number of files to download in parallel",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--retries",
        metavar="N",
        help="Retries per file before giving up",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--verbose",
        help="Enable verbose output",
        action="store_true",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=version("dcimport"),
    )

    return parser.parse_args()


def cli():
    args = _parse_args()

    include_extensions = [ext.strip() for ext in args.include_extensions.split(",")]

    # a bare date bounds the whole day: --since starts at 00:00, --until ends at 23:59:59
    since = datetime.combine(args.since, time.min) if args.since else None
    until = datetime.combine(args.until, time.max) if args.until else None

    sys.exit(
        main(
            output_path=Path(args.output),
            dcim_path=args.dcim_path,
            db_path=args.db_path,
            include_extensions=include_extensions,
            layout=args.layout,
            force_layout=args.force,
            udid=args.udid,
            convert_heic=args.convert_heic,
            skip_live_videos=args.skip_live_videos,
            verbose=args.verbose,
            concurrency=args.concurrency,
            download_retries=args.retries,
            since=since,
            until=until,
            manifest=args.manifest,
        )
    )


@immutable
class ImportConfig:
    """All settings for one import run, parsed once from the CLI."""

    output_path: Path
    dcim_path: str
    db_path: Path | None
    include_extensions: Sequence[str]
    layout: str | None
    force_layout: bool
    udid: str | None
    convert_heic: bool
    skip_live_videos: bool
    verbose: bool
    concurrency: int
    download_retries: int
    since: datetime | None
    until: datetime | None
    manifest: Path | None


class _RunStats:
    """Running tally of a download phase; feeds the progress description and summary line.
    `existing` is fixed at scan time; `new`/`failed` accumulate as downloads complete."""

    def __init__(self, existing: int):
        self._existing = existing
        self._new = 0
        self._failed = 0

    @property
    def new(self):
        return self._new

    def record(self, result: NewFile | FailedFile):
        if isinstance(result, NewFile):
            self._new += 1
        else:
            self._failed += 1

    def has_failures(self):
        return self._failed > 0

    def line(self):
        text = (
            f"Imported [bold green]{self._new}[/bold green] new"
            f" and skipped [bold yellow]{self._existing}[/bold yellow] existing files"
        )

        if self._failed:
            text += f" ([bold red]{self._failed}[/bold red] failed)"

        return text


async def _download_all(
    console: Console,
    config: ImportConfig,
    stats: _RunStats,
    source: AfcSource,
    db: MediaDatabase,
    plan: ImportPlan,
    layout: Layout,
):
    """Download everything in `plan` with a progress bar, updating `stats` as it goes.
    Returns every download result (new and failed), in completion order."""

    results: list[NewFile | FailedFile] = []

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Importing", total=plan.total_bytes)

        async for file in execute_import(
            source,
            db,
            plan,
            config.output_path,
            layout,
            download_retries=config.download_retries,
            convert_heic=config.convert_heic,
            concurrency=config.concurrency,
        ):
            stats.record(file)
            results.append(file)

            if isinstance(file, FailedFile):
                progress.console.print(
                    f"[red]Failed to download[/red] [blue]{file.afc_path}[/blue]: {file.error}"
                )

            if config.verbose:
                progress.console.print(
                    f"{'Downloaded' if isinstance(file, NewFile) else 'Failed'} [dim]{file.afc_path}[/dim]"
                )

            progress.advance(task, file.stat.size)
            progress.update(
                task, description=f"Importing ({stats.new}/{len(plan.to_download)})"
            )

    return results


def _report_failures(console: Console, results: list[NewFile | FailedFile]):
    """List the files that could not be downloaded, so they aren't lost in the scrollback."""

    failed = [r for r in results if isinstance(r, FailedFile)]

    if not failed:
        return

    console.print(
        "\n[bold yellow]Some files could not be downloaded.[/]"
        " Re-run the same command to retry them:"
    )

    for file in failed:
        console.print(f"  - [blue]{file.afc_path}[/blue]: {file.error}")


def _write_manifest(path: Path, plan: ImportPlan, results: list[NewFile | FailedFile]):
    """Write a JSON report of the run: imported files, failures, and skip/ignore counts."""

    manifest = {
        "imported": [
            {
                "afc_path": str(r.afc_path),
                "local_path": str(r.local_path),
                "size": r.stat.size,
                "mtime": r.stat.mtime.isoformat(),
            }
            for r in results
            if isinstance(r, NewFile)
        ],
        "failed": [
            {"afc_path": str(r.afc_path), "size": r.stat.size, "error": r.error}
            for r in results
            if isinstance(r, FailedFile)
        ],
        "skipped_existing": len(plan.existing),
        "ignored": len(plan.ignored),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2))


async def _run_import(console: Console, config: ImportConfig):
    """Connect, scan, and download; returns the exit code. Failures the user
    needs to act on propagate as exceptions to `main`."""

    # fail fast on device-independent problems, before any side effects or the phone.
    # validate the layout's syntax up front so a typo doesn't create an output dir/db
    # (the conflict check needs the db and happens in resolve_layout below)
    if config.convert_heic and not heic_support_available():
        raise MissingHeicSupportError()

    if config.layout is not None:
        parse_layout(config.layout)

    config.output_path.mkdir(parents=True, exist_ok=True)
    db = MediaDatabase(config.db_path or config.output_path / DEFAULT_DB_NAME)

    try:
        layout = resolve_layout(db, config.layout, config.force_layout)

        source = None

        try:
            with console.status("Connecting to your device…"):
                source = await afc_connect(udid=config.udid)

            device_name = source.device_name or "your device"
            console.print(
                f"Importing media from [blue]{config.dcim_path}[/blue] on"
                f" [bold blue]{device_name}[/bold blue] to"
                f" [blue]{config.output_path.absolute()}[/blue]"
            )

            with console.status("Scanning for media files…") as status:
                plan = await plan_import(
                    source,
                    db,
                    config.dcim_path,
                    config.include_extensions,
                    skip_live_videos=config.skip_live_videos,
                    since=config.since,
                    until=config.until,
                    on_scan_progress=lambda n: status.update(
                        f"Scanning for media files… ({n} found)"
                    ),
                )

            console.print(
                f"Found [bold green]{len(plan.to_download)}[/bold green] new files ({human_size(plan.total_bytes)}),"
                f" [bold yellow]{len(plan.existing)}[/bold yellow] already imported,"
                f" [dim]{len(plan.ignored)} ignored[/dim]"
            )

            if not plan.to_download:
                console.print(
                    "[bold green]Already up to date.[/] Nothing new to import."
                )
                if config.manifest is not None:
                    _write_manifest(config.manifest, plan, [])
                return 0

            stats = _RunStats(existing=len(plan.existing))
            results = await _download_all(
                console, config, stats, source, db, plan, layout
            )

            _report_failures(console, results)

            if config.manifest is not None:
                _write_manifest(config.manifest, plan, results)

            if stats.has_failures():
                console.print(
                    f"[bold yellow]Import completed with failures.[/] {stats.line()}"
                )
                return 1

            console.print(f"[bold green]Import completed.[/] {stats.line()}")
            return 0
        finally:
            if source is not None:
                await source.close()
    finally:
        db.close()


def _maybe_traceback(console: Console, verbose: bool):
    if verbose:
        console.print_exception()


def main(
    output_path: Path,
    dcim_path: str = DCIM_PATH,
    db_path: Path | None = None,
    include_extensions: Sequence[str] = INCLUDE_EXTENSIONS,
    layout: str | None = None,
    force_layout: bool = False,
    udid: str | None = None,
    convert_heic: bool = False,
    skip_live_videos: bool = False,
    verbose: bool = False,
    concurrency: int = 4,
    download_retries: int = 2,
    since: datetime | None = None,
    until: datetime | None = None,
    manifest: Path | None = None,
):
    """Run the import and report progress on the console. Returns a process exit code
    (0 on success, 1 if the import failed or any file could not be downloaded)."""

    config = ImportConfig(
        output_path=output_path,
        dcim_path=dcim_path,
        db_path=db_path,
        include_extensions=include_extensions,
        layout=layout,
        force_layout=force_layout,
        udid=udid,
        convert_heic=convert_heic,
        skip_live_videos=skip_live_videos,
        verbose=verbose,
        concurrency=concurrency,
        download_retries=download_retries,
        since=since,
        until=until,
        manifest=manifest,
    )

    console = Console()

    try:
        exit_code = asyncio.run(_run_import(console, config))

    except MultipleDevicesError as e:
        console.print("\n[red]More than one device is connected:[/red]")

        for device_udid in e.udids:
            console.print(f"  - {device_udid}")

        console.print(
            "Pass [bold]--udid <UDID>[/bold] to pick the device to import from."
        )
        return 1

    except LayoutConflictError as e:
        console.print(
            f"\n[red]{escape(str(e))}[/red]\nPass [bold]--force[/bold] to switch this library to the new layout"
            " (already-imported files keep their names)."
        )
        return 1

    except InvalidLayoutError as e:
        console.print(f"\n[red]{escape(str(e))}[/red]")
        return 1

    except MissingHeicSupportError as e:
        console.print(f"\n[red]{escape(str(e))}[/red]")
        return 1

    except ConnectionError:
        _maybe_traceback(console, verbose)
        console.print(
            "\n[red]Could not connect to your device.[/red]\n"
            " - Check the USB connection.\n"
            " - Make sure your device is unlocked and trusts this computer.\n"
            " - On Windows, make sure the iTunes or Apple Devices app is installed and running.\n"
        )
        console.print("[bold red]Import failed.[/]")
        return 1

    except KeyboardInterrupt:
        console.print(
            "\n[bold yellow]Import cancelled.[/] Finished downloads were kept —"
            " re-run the same command to pick up where you left off."
        )
        return 130

    except Exception as e:
        _maybe_traceback(console, verbose)
        hint = (
            "See the traceback above for details."
            if verbose
            else f"{escape(str(e))}\nRe-run with [bold]--verbose[/bold] for a full traceback."
        )
        console.print(f"\n[red]An unexpected error occurred. {hint}[/red]\n")
        console.print("[bold red]Import failed.[/]")
        return 1

    else:
        return exit_code
