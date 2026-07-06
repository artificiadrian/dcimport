import asyncio
from datetime import datetime
from pathlib import PurePosixPath

from dcimport.importer import plan_import
from tests.fake_source import DEFAULT_MTIME as MTIME
from tests.fake_source import FakeSource, InMemoryDb


def make_source():
    source = FakeSource()
    source.add("/DCIM/100APPLE/IMG_0001.JPG", data=b"12345", mtime=MTIME)
    source.add("/DCIM/100APPLE/IMG_0002.MOV", data=b"1234567890", mtime=MTIME)
    source.add("/DCIM/100APPLE/IMG_0003.AAE", data=b"xml", mtime=MTIME)
    return source


def test_plan_lists_new_files_with_total_bytes():
    plan = asyncio.run(plan_import(make_source(), InMemoryDb()))

    assert sorted(p.afc_path.name for p in plan.to_download) == [
        "IMG_0001.JPG",
        "IMG_0002.MOV",
    ]
    assert plan.total_bytes == 15


def test_plan_separates_already_imported_files():
    db = InMemoryDb()
    db.record(PurePosixPath("/DCIM/100APPLE/IMG_0001.JPG"), 5, MTIME)

    plan = asyncio.run(plan_import(make_source(), db))

    assert [p.afc_path.name for p in plan.existing] == ["IMG_0001.JPG"]
    assert [p.afc_path.name for p in plan.to_download] == ["IMG_0002.MOV"]
    assert plan.total_bytes == 10


def test_plan_counts_ignored_entries():
    plan = asyncio.run(plan_import(make_source(), InMemoryDb()))

    ignored_names = [p.name for p in plan.ignored]
    assert "IMG_0003.AAE" in ignored_names
    assert "100APPLE" in ignored_names


def test_extensions_match_regardless_of_case_and_leading_dot():
    plan = asyncio.run(
        plan_import(make_source(), InMemoryDb(), include_extensions=("JPG", ".mov"))
    )

    assert sorted(p.afc_path.name for p in plan.to_download) == [
        "IMG_0001.JPG",
        "IMG_0002.MOV",
    ]


def test_empty_extension_token_does_not_stat_directories():
    source = make_source()

    asyncio.run(plan_import(source, InMemoryDb(), include_extensions=("jpg", "")))

    # an empty token must not match extensionless entries (e.g. directories),
    # which would otherwise cost a wasted stat round-trip before being ignored
    assert source.stat_calls["/DCIM/100APPLE"] == 0


def test_plan_downloads_nothing_and_records_nothing(tmp_path):
    source = make_source()
    db = InMemoryDb()

    asyncio.run(plan_import(source, db))

    assert source.download_calls == {}
    assert db.imported == set()
    assert list(tmp_path.iterdir()) == []


def _dated_source():
    source = FakeSource()
    source.add("/DCIM/100APPLE/OLD.JPG", mtime=datetime(2023, 1, 1))
    source.add("/DCIM/100APPLE/NEW.JPG", mtime=datetime(2024, 6, 1))
    return source


def test_since_filters_out_older_files():
    plan = asyncio.run(
        plan_import(_dated_source(), InMemoryDb(), since=datetime(2024, 1, 1))
    )

    assert [p.afc_path.name for p in plan.to_download] == ["NEW.JPG"]
    assert PurePosixPath("/DCIM/100APPLE/OLD.JPG") in plan.ignored


def test_until_filters_out_newer_files():
    plan = asyncio.run(
        plan_import(_dated_source(), InMemoryDb(), until=datetime(2024, 1, 1))
    )

    assert [p.afc_path.name for p in plan.to_download] == ["OLD.JPG"]
    assert PurePosixPath("/DCIM/100APPLE/NEW.JPG") in plan.ignored


def test_scan_stats_files_concurrently():
    source = FakeSource()
    for i in range(6):
        source.add(f"/DCIM/100APPLE/IMG_000{i}.JPG")

    asyncio.run(plan_import(source, InMemoryDb()))

    assert source.max_concurrent_stats >= 2


def test_scan_reports_running_count():
    counts = []

    asyncio.run(
        plan_import(make_source(), InMemoryDb(), on_scan_progress=counts.append)
    )

    # one callback per stat'd candidate (the two wanted files), counting up
    assert counts == [1, 2]
