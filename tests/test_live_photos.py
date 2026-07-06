import asyncio

from dcimport.importer import plan_import
from tests.fake_source import FakeSource, InMemoryDb


def plan(source, **kwargs):
    return asyncio.run(plan_import(source, InMemoryDb(), **kwargs))


def test_live_photo_video_is_skipped():
    source = FakeSource()
    source.add("/DCIM/100APPLE/IMG_0001.HEIC")
    source.add("/DCIM/100APPLE/IMG_0001.MOV")

    result = plan(source, skip_live_videos=True)

    assert [p.afc_path.name for p in result.to_download] == ["IMG_0001.HEIC"]
    assert any(p.name == "IMG_0001.MOV" for p in result.ignored)


def test_standalone_video_is_kept():
    source = FakeSource()
    source.add("/DCIM/100APPLE/IMG_0002.MOV")

    result = plan(source, skip_live_videos=True)

    assert [p.afc_path.name for p in result.to_download] == ["IMG_0002.MOV"]


def test_pairing_requires_same_directory():
    source = FakeSource()
    source.add("/DCIM/100APPLE/IMG_0001.HEIC")
    source.add("/DCIM/101APPLE/IMG_0001.MOV")

    result = plan(source, skip_live_videos=True)

    assert sorted(p.afc_path.name for p in result.to_download) == [
        "IMG_0001.HEIC",
        "IMG_0001.MOV",
    ]


def test_pairing_is_case_insensitive():
    source = FakeSource()
    source.add("/DCIM/100APPLE/img_0001.heic")
    source.add("/DCIM/100APPLE/IMG_0001.MOV")

    result = plan(source, skip_live_videos=True)

    assert [p.afc_path.name for p in result.to_download] == ["img_0001.heic"]


def test_live_videos_kept_by_default():
    source = FakeSource()
    source.add("/DCIM/100APPLE/IMG_0001.HEIC")
    source.add("/DCIM/100APPLE/IMG_0001.MOV")

    result = plan(source)

    assert sorted(p.afc_path.name for p in result.to_download) == [
        "IMG_0001.HEIC",
        "IMG_0001.MOV",
    ]
