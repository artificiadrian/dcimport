import json
from datetime import datetime

import pytest

import dcimport.main as main_module
from dcimport.afc_utils import MultipleDevicesError
from dcimport.main import main
from tests.fake_source import DEFAULT_MTIME as MTIME
from tests.fake_source import FakeSource
from tests.helpers import persistently_fail


@pytest.fixture
def source(monkeypatch):
    fake = FakeSource()
    fake.add("/DCIM/100APPLE/IMG_0001.JPG", data=b"jpegdata", mtime=MTIME)

    async def connect(*args, **kwargs):
        return fake

    monkeypatch.setattr(main_module, "afc_connect", connect)
    return fake


def test_main_imports_and_returns_zero(source, tmp_path):
    exit_code = main(tmp_path / "photos")

    assert exit_code == 0
    assert (
        tmp_path / "photos" / "2024-01-02_03-04-05_IMG_0001.JPG"
    ).read_bytes() == b"jpegdata"


def test_main_returns_one_when_downloads_fail(source, tmp_path):
    persistently_fail(source, "/DCIM/100APPLE/IMG_0001.JPG")

    exit_code = main(tmp_path / "photos")

    assert exit_code == 1


def test_multiple_devices_error_shows_udids_and_returns_one(
    monkeypatch, tmp_path, capsys
):
    async def connect(*args, **kwargs):
        raise MultipleDevicesError(udids=["00008101-AAAA", "00008101-BBBB"])

    monkeypatch.setattr(main_module, "afc_connect", connect)

    exit_code = main(tmp_path / "photos")

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "00008101-AAAA" in out
    assert "--udid" in out


def test_device_name_is_printed(source, tmp_path, capsys):
    source.device_name = "Adrian's iPhone"

    main(tmp_path / "photos")

    assert "Adrian's iPhone" in capsys.readouterr().out


def test_layout_conflict_returns_one(source, tmp_path):
    main(tmp_path / "photos", layout="{mtime:%Y}/{name}")

    exit_code = main(tmp_path / "photos", layout="{name}")

    assert exit_code == 1


def test_invalid_layout_fails_before_connecting(monkeypatch, tmp_path):
    connected = False

    async def connect(*args, **kwargs):
        nonlocal connected
        connected = True
        return FakeSource()

    monkeypatch.setattr(main_module, "afc_connect", connect)

    exit_code = main(tmp_path / "photos", layout="{bogus}")

    assert exit_code == 1
    assert not connected


def test_invalid_layout_creates_no_output_dir(monkeypatch, tmp_path):
    async def connect(*args, **kwargs):
        return FakeSource()

    monkeypatch.setattr(main_module, "afc_connect", connect)
    out = tmp_path / "photos"

    exit_code = main(out, layout="{bogus}")

    assert exit_code == 1
    assert not out.exists()


def test_already_up_to_date_returns_zero(source, tmp_path, capsys):
    main(tmp_path / "photos")

    exit_code = main(tmp_path / "photos")

    assert exit_code == 0
    assert "up to date" in capsys.readouterr().out.lower()


def test_concurrency_flag_limits_parallel_downloads(source, tmp_path):
    for i in range(5):
        source.add(f"/DCIM/100APPLE/IMG_010{i}.JPG")

    main(tmp_path / "photos", concurrency=1)

    assert source.max_concurrent_downloads == 1


def test_failed_files_are_listed(source, tmp_path, capsys):
    persistently_fail(source, "/DCIM/100APPLE/IMG_0001.JPG")

    exit_code = main(tmp_path / "photos")

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "IMG_0001.JPG" in out
    assert "could not be downloaded" in out


def test_since_flag_skips_older_files(monkeypatch, tmp_path):
    fake = FakeSource()
    fake.add("/DCIM/100APPLE/OLD.JPG", data=b"old", mtime=datetime(2020, 1, 1))
    fake.add("/DCIM/100APPLE/NEW.JPG", data=b"new", mtime=datetime(2024, 6, 1))

    async def connect(*args, **kwargs):
        return fake

    monkeypatch.setattr(main_module, "afc_connect", connect)
    out = tmp_path / "photos"

    main(out, since=datetime(2023, 1, 1))

    assert list(out.glob("*NEW.JPG"))
    assert not list(out.glob("*OLD.JPG"))


def test_manifest_records_imported_and_failed(source, tmp_path):
    source.add("/DCIM/100APPLE/IMG_0002.JPG", data=b"data")
    persistently_fail(source, "/DCIM/100APPLE/IMG_0002.JPG")
    manifest = tmp_path / "report.json"

    main(tmp_path / "photos", manifest=manifest)

    data = json.loads(manifest.read_text())
    assert [r["afc_path"] for r in data["imported"]] == ["/DCIM/100APPLE/IMG_0001.JPG"]
    assert [r["afc_path"] for r in data["failed"]] == ["/DCIM/100APPLE/IMG_0002.JPG"]
